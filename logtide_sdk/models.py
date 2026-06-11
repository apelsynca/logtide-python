"""Data models for LogTide SDK."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from logtide_sdk.dsn import parse_dsn
from logtide_sdk.enums import LogLevel


@dataclass
class PayloadLimitsOptions:
    """Options for controlling log payload sizes to prevent 413 errors."""

    max_field_size: int = 10 * 1024  # 10 KB — truncate individual string fields above this
    max_log_size: int = 100 * 1024  # 100 KB — drop metadata entirely if entry exceeds this
    exclude_fields: list[str] = field(default_factory=list)  # field names to redact
    truncation_marker: str = "...[TRUNCATED]"  # appended to truncated strings


@dataclass
class LogEntry:
    """Single log entry."""

    service: str
    level: LogLevel
    message: str
    time: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.time is None:
            self.time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif self.time.endswith("+00:00"):
            self.time = self.time[:-6] + "Z"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "service": self.service,
            "level": self.level.value,
            "message": self.message,
            "time": self.time,
            "metadata": self.metadata,
        }
        if self.trace_id is not None:
            result["trace_id"] = self.trace_id
        if self.span_id is not None:
            result["span_id"] = self.span_id
        if self.session_id is not None:
            result["session_id"] = self.session_id
        return result


@dataclass
class ClientOptions:
    """Configuration options for LogTide client.

    Provide either ``dsn`` or ``api_url`` + ``api_key``.
    """

    api_url: str = ""
    api_key: str = ""
    batch_size: int = 100
    flush_interval: int = 5000
    max_buffer_size: int = 10000
    max_retries: int = 3
    retry_delay_ms: int = 1000
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_ms: int = 30000
    enable_metrics: bool = True
    debug: bool = False
    global_metadata: dict[str, Any] = field(default_factory=dict)
    auto_trace_id: bool = False
    payload_limits: PayloadLimitsOptions | None = None
    dsn: str | None = None
    service: str | None = None
    before_send: Callable[["LogEntry"], "LogEntry | None"] | None = None
    sample_rate: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        if self.dsn:
            parts = parse_dsn(self.dsn)
            if not self.api_url:
                self.api_url = parts.api_url
            if not self.api_key:
                self.api_key = parts.api_key
        if not self.api_url or not self.api_key:
            raise ValueError(
                "Either dsn or api_url + api_key must be provided to ClientOptions"
            )


@dataclass
class QueryOptions:
    """Options for querying logs."""

    service: str | None = None
    level: LogLevel | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    limit: int = 100
    offset: int = 0
    q: str | None = None


@dataclass
class AggregatedStatsOptions:
    """Options for aggregated statistics."""

    from_time: datetime
    to_time: datetime
    interval: str = "1h"  # '1m' | '5m' | '1h' | '1d'
    service: str | None = None


@dataclass
class ClientMetrics:
    """SDK internal metrics."""

    logs_sent: int = 0
    logs_dropped: int = 0
    errors: int = 0
    retries: int = 0
    avg_latency_ms: float = 0.0
    circuit_breaker_trips: int = 0


@dataclass
class LogsResponse:
    """Response from logs query."""

    logs: list[dict[str, Any]]
    total: int


@dataclass
class AggregatedStatsResponse:
    """Response from aggregated statistics query."""

    timeseries: list[dict[str, Any]]
    top_services: list[dict[str, Any]]
    top_errors: list[dict[str, Any]]
