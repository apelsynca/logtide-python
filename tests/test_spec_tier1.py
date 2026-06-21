"""Remaining Tier 1 spec items: DSN parsing (002 §3), service-in-options
(004 §3) and the metadata.sdk stamp (003 §3)."""

from collections.abc import AsyncGenerator

import pytest

import logtide_sdk
from logtide_sdk import ClientOptions, LogTideClient

# ------------------------------------------------- service in options


@pytest.fixture
def client() -> AsyncGenerator[LogTideClient]:
    c = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="lp_k",
            service="checkout",
        )
    )
    yield c
    c._closed = True


def test_log_methods_use_configured_service(client) -> None:
    client.info("user logged in")
    entry = client._buffer[-1]
    assert entry.service == "checkout"
    assert entry.message == "user logged in"


def test_log_methods_with_metadata_and_configured_service(client):
    client.error("boom", {"order_id": "42"})
    entry = client._buffer[-1]
    assert entry.service == "checkout"
    assert entry.message == "boom"
    assert entry.metadata["order_id"] == "42"


def test_per_call_service_still_works(client):
    client.info("payments", "captured")
    entry = client._buffer[-1]
    assert entry.service == "payments"
    assert entry.message == "captured"


def test_message_only_without_configured_service_raises():
    c = LogTideClient(ClientOptions(api_url="http://localhost:8080", api_key="lp_k"))
    try:
        with pytest.raises(ValueError):  # bad
            c.info("just a message")
    finally:
        c._closed = True


def test_error_with_exception_and_configured_service(client):
    try:
        raise RuntimeError("kaboom")
    except RuntimeError as exc:
        client.error("failed", exc)
    entry = client._buffer[-1]
    assert entry.service == "checkout"
    assert entry.metadata["exception"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_async_client_uses_configured_service():
    from logtide_sdk.async_client import AsyncLogTideClient

    c = AsyncLogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="worker")
    )
    try:
        await c.info("job done")
        entry = c._buffer[-1]
        assert entry.service == "worker"
        assert entry.message == "job done"
    finally:
        c._closed = True


# ---------------------------------------------------------- metadata.sdk


def test_entries_carry_sdk_metadata(client):
    client.info("hello")
    entry = client._buffer[-1]
    sdk = entry.metadata.get("sdk")
    assert sdk is not None
    assert sdk["name"] == "logtide-python"
    assert sdk["version"] == logtide_sdk.__version__


def test_user_supplied_sdk_metadata_wins(client):
    client.info("hello", {"sdk": "my-data"})
    entry = client._buffer[-1]
    assert entry.metadata["sdk"] == "my-data"
