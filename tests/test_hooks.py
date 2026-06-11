"""before_send hook and sampling (conformance C22/C23)."""

import pytest

from logtide_sdk import ClientOptions, LogTideClient


def make_client(**kwargs):
    return LogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="svc", **kwargs)
    )


def test_before_send_can_mutate(mocker):
    def scrub(entry):
        entry.metadata["password"] = "[redacted]"
        return entry

    client = make_client(before_send=scrub)
    try:
        client.info("login", {"password": "hunter2"})
        assert client._buffer[-1].metadata["password"] == "[redacted]"
    finally:
        client._closed = True


def test_before_send_can_drop():
    client = make_client(before_send=lambda entry: None)
    try:
        client.info("dropped")
        assert len(client._buffer) == 0
        assert client.get_metrics().logs_sent == 0
    finally:
        client._closed = True


def test_before_send_exception_does_not_break_capture():
    def broken(entry):
        raise RuntimeError("hook bug")

    client = make_client(before_send=broken)
    try:
        client.info("survives")
        # a buggy hook must not lose the entry or raise to the caller
        assert len(client._buffer) == 1
    finally:
        client._closed = True


def test_sample_rate_zero_sends_nothing():
    client = make_client(sample_rate=0.0)
    try:
        for _ in range(20):
            client.info("nope")
        assert len(client._buffer) == 0
    finally:
        client._closed = True


def test_sample_rate_one_sends_everything():
    client = make_client(sample_rate=1.0)
    try:
        for _ in range(5):
            client.info("yes")
        assert len(client._buffer) == 5
    finally:
        client._closed = True


def test_sample_rate_validation():
    with pytest.raises(ValueError):
        ClientOptions(api_url="x://h", api_key="k", sample_rate=1.5)
    with pytest.raises(ValueError):
        ClientOptions(api_url="x://h", api_key="k", sample_rate=-0.1)


@pytest.mark.asyncio
async def test_async_before_send_and_sampling():
    from logtide_sdk.async_client import AsyncLogTideClient

    client = AsyncLogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="lp_k",
            service="svc",
            before_send=lambda e: None,
        )
    )
    try:
        await client.info("dropped")
        assert len(client._buffer) == 0
    finally:
        client._closed = True
