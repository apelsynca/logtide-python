"""LogTide SDK - Official Python SDK for LogTide."""

from logtide_sdk.client import LogTideClient
from logtide_sdk.dsn import DsnParseError, DsnParts, parse_dsn
from logtide_sdk.enums import CircuitState, LogLevel
from logtide_sdk.exceptions import BufferFullError, CircuitBreakerOpenError, LogTideError
from logtide_sdk.handler import LogTideHandler
from logtide_sdk.models import (
    AggregatedStatsOptions,
    AggregatedStatsResponse,
    ClientMetrics,
    ClientOptions,
    LogEntry,
    LogsResponse,
    PayloadLimitsOptions,
    QueryOptions,
)
from logtide_sdk.scope import (
    Breadcrumb,
    Scope,
    User,
    add_breadcrumb,
    get_current_scope,
    push_scope,
    set_extra,
    set_session_id,
    set_tag,
    set_user,
)
from logtide_sdk.serialization import serialize_exception
from logtide_sdk.tracecontext import (
    TraceContext,
    format_traceparent,
    generate_span_id,
    generate_trace_id,
    inject_traceparent,
    parse_traceparent,
    resolve_trace_id,
)

_has_async = False
try:
    from logtide_sdk.async_client import AsyncLogTideClient

    _has_async = True  # type: ignore[assignment]
except ImportError:
    pass

from logtide_sdk._version import VERSION as __version__

__all__ = [
    "AggregatedStatsOptions",
    "AggregatedStatsResponse",
    "Breadcrumb",
    "BufferFullError",
    "CircuitBreakerOpenError",
    "CircuitState",
    "ClientMetrics",
    "ClientOptions",
    "DsnParseError",
    "DsnParts",
    "LogEntry",
    "LogLevel",
    "LogTideClient",
    "LogTideError",
    "LogTideHandler",
    "LogsResponse",
    "PayloadLimitsOptions",
    "QueryOptions",
    "Scope",
    "TraceContext",
    "User",
    "add_breadcrumb",
    "format_traceparent",
    "generate_span_id",
    "generate_trace_id",
    "get_current_scope",
    "inject_traceparent",
    "parse_dsn",
    "parse_traceparent",
    "push_scope",
    "resolve_trace_id",
    "serialize_exception",
    "set_extra",
    "set_session_id",
    "set_tag",
    "set_user",
]

if _has_async:
    __all__.append("AsyncLogTideClient")
