"""Client-level W3C trace ID behaviour (spec 005)."""

import re

import pytest

from logtide_sdk import ClientOptions, LogTideClient
from logtide_sdk.enums import LogLevel
from logtide_sdk.models import LogEntry

W3C_TRACE = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def client():
    c = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
        )
    )
    yield c
    c._closed = True  # avoid network on close


def test_level_methods_accept_top_level_trace_id(client):
    client.info("svc", "hello", trace_id="4bf92f3577b34da6a3ce929d0e0e4736")
    entry = client._buffer[-1]
    assert entry.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert "trace_id" not in entry.metadata


def test_error_accepts_trace_id_with_exception(client):
    try:
        raise ValueError("boom")
    except ValueError as exc:
        client.error("svc", "failed", exc, trace_id="4bf92f3577b34da6a3ce929d0e0e4736")
    entry = client._buffer[-1]
    assert entry.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert entry.metadata["exception"]["type"] == "ValueError"


def test_span_id_is_serialized_top_level(client):
    client.info("svc", "hello", trace_id="a" * 32, span_id="00f067aa0ba902b7")
    entry = client._buffer[-1]
    assert entry.span_id == "00f067aa0ba902b7"
    assert entry.to_dict()["span_id"] == "00f067aa0ba902b7"
    assert "span_id" not in entry.to_dict()["metadata"]


def test_with_new_trace_id_generates_w3c_format(client):
    with client.with_new_trace_id():
        client.info("svc", "hello")
    entry = client._buffer[-1]
    assert entry.trace_id is not None
    assert W3C_TRACE.fullmatch(entry.trace_id), entry.trace_id


def test_auto_trace_id_generates_w3c_format():
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            auto_trace_id=True,
        )
    )
    try:
        client.info("svc", "hello")
        entry = client._buffer[-1]
        assert entry.trace_id is not None
        assert W3C_TRACE.fullmatch(entry.trace_id), entry.trace_id
    finally:
        client._closed = True


def test_log_entry_to_dict_emits_trace_fields():
    entry = LogEntry(
        service="svc",
        level=LogLevel.INFO,
        message="m",
        trace_id="b" * 32,
        span_id="c" * 16,
    )
    d = entry.to_dict()
    assert d["trace_id"] == "b" * 32
    assert d["span_id"] == "c" * 16


@pytest.mark.asyncio
async def test_async_client_accepts_top_level_trace_id():
    from logtide_sdk.async_client import AsyncLogTideClient

    client = AsyncLogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="test_key")
    )
    try:
        await client.info("svc", "hello", trace_id="d" * 32, span_id="e" * 16)
        entry = client._buffer[-1]
        assert entry.trace_id == "d" * 32
        assert entry.span_id == "e" * 16
    finally:
        client._closed = True
