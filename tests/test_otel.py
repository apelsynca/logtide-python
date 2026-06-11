"""OTel tracing preset and active-span log correlation (C24/C26, spec 005 §3-4)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry import trace as otel_trace

from logtide_sdk import ClientOptions, LogTideClient
from logtide_sdk.otel import configure_tracing


class CapturingHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        CapturingHandler.requests.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"partialSuccess": {}}).encode())

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def ingest_server():
    CapturingHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", CapturingHandler.requests
    server.shutdown()


@pytest.fixture(autouse=True)
def fresh_otel_state():
    yield
    # configure_tracing registers a provider; detach so tests stay isolated
    from logtide_sdk import tracecontext

    tracecontext.register_active_context_provider(None)


def test_configure_tracing_exports_to_logtide(ingest_server):
    url, requests = ingest_server
    provider = configure_tracing(api_url=url, api_key="lp_test", service="otel-test")
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("checkout"):
        pass
    provider.force_flush()

    assert requests, "no export reached the server"
    assert requests[0]["path"] == "/v1/otlp/traces"
    assert requests[0]["headers"].get("x-api-key") == "lp_test"


def test_logs_inside_a_span_carry_its_trace_context(ingest_server):
    url, _ = ingest_server
    provider = configure_tracing(api_url=url, api_key="lp_test", service="otel-test")
    tracer = provider.get_tracer("test")

    client = LogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="svc")
    )
    try:
        with tracer.start_as_current_span("checkout") as span:
            client.info("inside span")
            ctx = span.get_span_context()

        entry = client._buffer[-1]
        assert entry.trace_id == format(ctx.trace_id, "032x")
        assert entry.span_id == format(ctx.span_id, "016x")
    finally:
        client._closed = True
        provider.shutdown()


def test_explicit_trace_id_wins_over_active_span(ingest_server):
    url, _ = ingest_server
    provider = configure_tracing(api_url=url, api_key="lp_test", service="otel-test")
    tracer = provider.get_tracer("test")

    client = LogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="svc")
    )
    try:
        with tracer.start_as_current_span("checkout"):
            client.info("explicit", trace_id="f" * 32)
        assert client._buffer[-1].trace_id == "f" * 32
    finally:
        client._closed = True
        provider.shutdown()


def test_traces_sample_rate_zero_records_nothing(ingest_server):
    url, requests = ingest_server
    provider = configure_tracing(
        api_url=url, api_key="lp_test", service="otel-test", traces_sample_rate=0.0
    )
    tracer = provider.get_tracer("test")

    for _ in range(10):
        with tracer.start_as_current_span("unsampled"):
            pass
    provider.force_flush()

    assert not requests, "unsampled spans must not be exported"


def test_resource_carries_service_identity(ingest_server):
    url, _ = ingest_server
    provider = configure_tracing(
        api_url=url,
        api_key="lp_test",
        service="otel-test",
        environment="staging",
        release="1.2.3",
    )
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "otel-test"
    assert attrs["deployment.environment"] == "staging"
    assert attrs["service.version"] == "1.2.3"
