"""OpenTelemetry tracing preset for LogTide (spec 005 §3, OTel-native path).

One call configures the official OpenTelemetry SDK to export spans to a
LogTide instance, and wires the active span's trace context into every log
entry captured by this SDK (correlation, spec 005 §4):

    from logtide_sdk.otel import configure_tracing

    provider = configure_tracing(
        dsn="https://lp_key@logs.example.com",
        service="checkout",
        environment="production",
    )

Requires the ``otel`` extra: ``pip install logtide-sdk[otel]``.
"""

from __future__ import annotations

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "OpenTelemetry is required for logtide_sdk.otel. "
        "Install it with: pip install logtide-sdk[otel]"
    ) from _err

from logtide_sdk.dsn import parse_dsn
from logtide_sdk.tracecontext import register_active_context_provider

__all__ = ["configure_tracing"]


def _active_span_context() -> tuple[str | None, str | None]:
    ctx = _otel_trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def configure_tracing(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    dsn: str | None = None,
    service: str,
    environment: str | None = None,
    release: str | None = None,
    traces_sample_rate: float = 1.0,
    set_global: bool = True,
) -> TracerProvider:
    """Configure OpenTelemetry tracing exporting to LogTide.

    Builds a ``TracerProvider`` with the LogTide resource identity, a
    parent-based ratio sampler, and a batching OTLP/HTTP exporter pointed at
    ``{api_url}/v1/otlp/traces`` with the ``X-API-Key`` header. Also registers
    the active-span lookup so log entries captured inside a span carry its
    ``trace_id``/``span_id``.

    Returns the provider (call ``provider.shutdown()`` on exit; when
    ``set_global`` is True it is also installed as the global provider).
    """
    if dsn:
        parts = parse_dsn(dsn)
        api_url = api_url or parts.api_url
        api_key = api_key or parts.api_key
    if not api_url or not api_key:
        raise ValueError("Either dsn or api_url + api_key must be provided")
    if not 0.0 <= traces_sample_rate <= 1.0:
        raise ValueError("traces_sample_rate must be between 0.0 and 1.0")

    attributes: dict[str, str] = {"service.name": service}
    if environment is not None:
        attributes["deployment.environment"] = environment
    if release is not None:
        attributes["service.version"] = release

    exporter = OTLPSpanExporter(
        endpoint=f"{api_url.rstrip('/')}/v1/otlp/traces",
        headers={"X-API-Key": api_key},
    )

    provider = TracerProvider(
        resource=Resource.create(attributes),
        sampler=ParentBased(TraceIdRatioBased(traces_sample_rate)),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))

    register_active_context_provider(_active_span_context)

    if set_global:
        try:
            _otel_trace.set_tracer_provider(provider)
        except Exception:  # already set: callers use the returned provider
            pass

    return provider
