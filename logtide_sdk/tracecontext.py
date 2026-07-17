"""W3C Trace Context utilities.

Implements the parts of https://www.w3.org/TR/trace-context/ the SDK needs:
parsing/formatting the ``traceparent`` header and generating W3C-format
trace/span identifiers. The legacy ``X-Trace-ID`` header is still accepted
as a fallback for backwards compatibility, but is deprecated.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

__all__ = [
    "LEGACY_TRACE_HEADER",
    "TRACEPARENT_HEADER",
    "TraceContext",
    "active_trace_context",
    "format_traceparent",
    "generate_span_id",
    "generate_trace_id",
    "inject_traceparent",
    "parse_traceparent",
    "register_active_context_provider",
    "resolve_trace_id",
]

TRACEPARENT_HEADER = "traceparent"
LEGACY_TRACE_HEADER = "X-Trace-ID"

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True)
class TraceContext:
    """Parsed ``traceparent`` header."""

    trace_id: str
    span_id: str
    sampled: bool


def generate_trace_id() -> str:
    """Return a new 32-char lowercase-hex W3C trace id (never all-zero)."""
    while True:
        trace_id = secrets.token_hex(16)
        if trace_id != "0" * 32:
            return trace_id


def generate_span_id() -> str:
    """Return a new 16-char lowercase-hex W3C span id (never all-zero)."""
    while True:
        span_id = secrets.token_hex(8)
        if span_id != "0" * 16:
            return span_id


def parse_traceparent(header: str | None) -> TraceContext | None:
    """Parse a ``traceparent`` header value.

    Returns None for missing or malformed values (wrong lengths, uppercase
    hex, all-zero ids, the forbidden ``ff`` version).
    """
    if not header:
        return None
    match = _TRACEPARENT_RE.match(header.strip())
    if match is None:
        return None
    if match["version"] == "ff":
        return None
    trace_id = match["trace_id"]
    span_id = match["span_id"]
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    sampled = (int(match["flags"], 16) & 0x01) == 0x01
    return TraceContext(trace_id=trace_id, span_id=span_id, sampled=sampled)


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """Build a ``traceparent`` header value (version 00)."""
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def resolve_trace_id(traceparent: str | None, legacy: str | None) -> str:
    """Resolve the inbound trace id per spec 005 §2.

    Order: valid ``traceparent`` → legacy ``X-Trace-ID`` value → newly
    generated W3C trace id.
    """
    ctx = parse_traceparent(traceparent)
    if ctx is not None:
        return ctx.trace_id
    if legacy:
        return legacy
    return generate_trace_id()


# --------------------------------------------------- active span lookup

# Optional provider returning the active span's (trace_id, span_id), used in
# the trace-context resolution order (explicit -> active span -> scope).
# Registered by logtide_sdk.otel when OpenTelemetry tracing is configured.
_active_context_provider = None


def register_active_context_provider(provider) -> None:
    """Register (or clear, with None) the active-span context provider."""
    global _active_context_provider
    _active_context_provider = provider


def active_trace_context() -> tuple[str | None, str | None]:
    """Return the active span's (trace_id, span_id), or (None, None)."""
    if _active_context_provider is None:
        return None, None
    try:
        return _active_context_provider()
    except Exception:
        return None, None


def inject_traceparent(headers) -> None:
    """Inject a W3C ``traceparent`` header for an outbound HTTP call (C25).

    Source order: active span (when OTel tracing is configured) -> current
    scope's trace context. A scope without a span id gets a fresh one. Does
    nothing when no trace context is active or the header is already set.

    Works with any mutable mapping (``requests``/``httpx`` headers dicts)::

        headers = {}
        inject_traceparent(headers)
        requests.get(url, headers=headers)
    """
    if "traceparent" in headers:
        return

    trace_id, span_id = active_trace_context()
    if trace_id is None:
        from logtide_sdk.scope import get_current_scope  # local: avoid cycle

        scope = get_current_scope()
        trace_id = scope.trace_id
        span_id = scope.span_id

    if trace_id is None:
        return
    if span_id is None:
        span_id = generate_span_id()

    headers[TRACEPARENT_HEADER] = format_traceparent(trace_id, span_id)
