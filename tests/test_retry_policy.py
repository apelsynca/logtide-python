"""Retry policy per spec 002 §6 (conformance C07/C08/C09).

Retryable: network errors, 408, 429, 5xx. Permanent client errors (other
4xx) are dropped after the first attempt. A Retry-After header overrides the
computed backoff delay.
"""

from unittest.mock import Mock

import pytest
import requests

from logtide_sdk import ClientOptions, LogTideClient
from logtide_sdk.models import LogEntry
from logtide_sdk.enums import LogLevel


def http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.headers = headers or {}
    return requests.HTTPError(f"HTTP {status}", response=response)


@pytest.fixture
def client():
    c = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="lp_k",
            service="svc",
            retry_delay_ms=1,
        )
    )
    yield c
    c._closed = True


def entry() -> LogEntry:
    return LogEntry(service="svc", level=LogLevel.INFO, message="m")


def test_no_retry_on_permanent_4xx(client, mocker):
    send = mocker.patch.object(client, "_send_logs", side_effect=http_error(400))
    sleep = mocker.patch("logtide_sdk.client.time.sleep")

    client._send_logs_with_retry([entry()])

    assert send.call_count == 1, "400 must not be retried"
    sleep.assert_not_called()
    metrics = client.get_metrics()
    assert metrics.logs_dropped == 1
    assert metrics.retries == 0


def test_no_retry_on_401(client, mocker):
    send = mocker.patch.object(client, "_send_logs", side_effect=http_error(401))
    client._send_logs_with_retry([entry()])
    assert send.call_count == 1


def test_retries_on_5xx_then_succeeds(client, mocker):
    send = mocker.patch.object(
        client, "_send_logs", side_effect=[http_error(500), None]
    )
    mocker.patch("logtide_sdk.client.time.sleep")

    client._send_logs_with_retry([entry()])

    assert send.call_count == 2
    metrics = client.get_metrics()
    assert metrics.logs_sent == 1
    assert metrics.retries == 1


def test_retries_on_408_and_429(client, mocker):
    send = mocker.patch.object(
        client, "_send_logs", side_effect=[http_error(408), http_error(429), None]
    )
    mocker.patch("logtide_sdk.client.time.sleep")
    client._send_logs_with_retry([entry()])
    assert send.call_count == 3


def test_retries_on_network_error(client, mocker):
    send = mocker.patch.object(
        client,
        "_send_logs",
        side_effect=[requests.ConnectionError("refused"), None],
    )
    mocker.patch("logtide_sdk.client.time.sleep")
    client._send_logs_with_retry([entry()])
    assert send.call_count == 2


def test_retry_after_overrides_backoff(client, mocker):
    mocker.patch.object(
        client,
        "_send_logs",
        side_effect=[http_error(429, {"Retry-After": "7"}), None],
    )
    sleep = mocker.patch("logtide_sdk.client.time.sleep")

    client._send_logs_with_retry([entry()])

    sleep.assert_called_once_with(7.0)


@pytest.mark.asyncio
async def test_async_no_retry_on_permanent_4xx(mocker):
    import aiohttp

    from logtide_sdk.async_client import AsyncLogTideClient

    client = AsyncLogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="svc")
    )
    try:
        error = aiohttp.ClientResponseError(
            request_info=Mock(), history=(), status=403, headers={}
        )
        send = mocker.patch.object(client, "_send_logs", side_effect=error)
        await client._send_logs_with_retry([entry()])
        assert send.call_count == 1, "403 must not be retried"
    finally:
        client._closed = True


@pytest.mark.asyncio
async def test_async_retry_after_overrides_backoff(mocker):
    import aiohttp

    from logtide_sdk.async_client import AsyncLogTideClient

    client = AsyncLogTideClient(
        ClientOptions(
            api_url="http://localhost:8080", api_key="lp_k", service="svc", retry_delay_ms=1
        )
    )
    try:
        error = aiohttp.ClientResponseError(
            request_info=Mock(), history=(), status=429, headers={"Retry-After": "5"}
        )
        mocker.patch.object(client, "_send_logs", side_effect=[error, None])
        sleep = mocker.patch("logtide_sdk.async_client.asyncio.sleep")
        await client._send_logs_with_retry([entry()])
        sleep.assert_called_once_with(5.0)
    finally:
        client._closed = True
