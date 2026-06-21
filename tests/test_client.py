"""Basic tests for LogTide SDK."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from logtide_sdk import (
    CircuitState,
    ClientOptions,
    LogTideClient,
)
from logtide_sdk.enums import LogLevel
from logtide_sdk.json_encoder import logtide_json_dumps
from logtide_sdk.models import LogEntry


def test_client_initialization():
    # leave it here as part of the test
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
        )
    )
    assert client is not None
    assert client.options.api_url == "http://localhost:8080"
    assert client.options.api_key == "test_key"
    client.close()


@pytest.fixture
def options() -> ClientOptions:
    return ClientOptions(
        api_url="http://localhost:8080",
        api_key="test_key",
    )


@pytest.fixture
def client(options: ClientOptions) -> LogTideClient:
    return LogTideClient(options)


def test_logging_methods(client: LogTideClient):
    # Test all log levels
    client.debug("test-service", "Debug message")
    client.info("test-service", "Info message", {"key": "value"})
    client.warn("test-service", "Warning message")
    client.error("test-service", "Error message")
    client.critical("test-service", "Critical message")

    # Verify logs are buffered
    assert len(client._buffer) == 5

    client.close()


def test_trace_id_context(client: LogTideClient):
    """Test trace ID context management."""
    # Test manual trace ID
    client.set_trace_id("trace-123")
    assert client.get_trace_id() == "trace-123"

    client.info("test", "Message with trace")
    assert client._buffer[0].trace_id == "trace-123"

    # Test context manager - should restore to "trace-123" after
    original_trace = client.get_trace_id()
    with client.with_trace_id("trace-456"):
        assert client.get_trace_id() == "trace-456"
        client.info("test", "Message in context")

    # Should restore previous trace ID
    assert client.get_trace_id() == original_trace

    client.close()


def test_auto_trace_id():
    """Test auto trace ID generation."""
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            auto_trace_id=True,
        )
    )

    client.info("test", "Message")
    assert client._buffer[0].trace_id is not None

    client.close()


def test_error_serialization(client: LogTideClient):
    """Test error serialization produces structured exception metadata."""
    try:
        raise ValueError("Test error")
    except Exception as e:
        client.error("test", "Error occurred", e)

    exc = client._buffer[0].metadata["exception"]
    assert exc["type"] == "ValueError"
    assert exc["message"] == "Test error"
    assert exc["language"] == "python"
    assert isinstance(exc["stacktrace"], list)

    client.close()


def test_buffer_management():
    """Test buffer size limits: logs are silently dropped when full."""
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            max_buffer_size=5,
        )
    )

    for i in range(5):
        client.info("test", f"Message {i}")

    # Buffer is now at capacity — 6th log must be dropped, not raise
    client.info("test", "Message 6")

    assert len(client._buffer) == 5
    assert client.get_metrics().logs_dropped == 1

    client.close()


def test_metrics():
    """Test metrics tracking."""
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            enable_metrics=True,
        )
    )

    metrics = client.get_metrics()
    assert metrics.logs_sent == 0
    assert metrics.logs_dropped == 0
    assert metrics.errors == 0

    # Reset metrics
    client.reset_metrics()

    client.close()


def test_circuit_breaker_state(client: LogTideClient):
    state = client.get_circuit_breaker_state()
    assert state == CircuitState.CLOSED

    client.close()


def test_global_metadata():
    client = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            global_metadata={"env": "test", "version": "1.0.0"},
        )
    )

    client.info("test", "Message")

    assert client._buffer[0].metadata["env"] == "test"
    assert client._buffer[0].metadata["version"] == "1.0.0"

    client.close()


@dataclass
class CustomDataClass:
    abc: int


def test_likes_json_metadata_data(client: LogTideClient, mocker: MockerFixture) -> None:
    apply_payload_limits_mock = mocker.spy(client, "_apply_payload_limits")

    # here class diff and set are not json serializeable (by default json)
    entry = LogEntry(
        service="service",
        level=LogLevel.DEBUG,
        message="some log entry",
        metadata={"someWeirdObject": {1, 2, 3, 4, 5}, "idkOther": CustomDataClass(abc=42)},
    )

    client.log(entry)
    apply_payload_limits_mock.assert_called_once_with(entry)


# TODO: test for when there is trace_id
def test_flush_with_unjsonable_payload_and_no_trace_id(
    client: LogTideClient, mocker: MockerFixture
) -> None:
    """Test custom json encoder here and no trace_id: null in json_string"""

    client._session.post = MagicMock()

    entry = LogEntry(
        service="kotiknyaa",
        level=LogLevel.DEBUG,
        message="Any message",
        metadata={"someWeirdObject": {1, 2, 3, 4, 5}, "idkOther": CustomDataClass(abc=42)},
    )
    client._buffer = [entry]

    client.flush()

    # called with right data
    json_string = logtide_json_dumps(
        {
            "logs": [
                {
                    "service": "kotiknyaa",
                    "level": "debug",
                    "message": "Any message",
                    "time": entry.time,
                    "metadata": {"someWeirdObject": [1, 2, 3, 4, 5], "idkOther": {"abc": 42}},
                }  # WARN: ORDER MATTERS
            ]
        }
    )
    client._session.post.assert_called_once_with(
        f"{client.options.api_url}/api/v1/ingest",
        headers=client._get_headers(),
        data=json_string,
        timeout=30,
    )
