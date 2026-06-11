"""Middleware trace propagation per spec 005 §2.

Inbound resolution order: W3C `traceparent` → legacy `X-Trace-ID` → generate.
The resolved trace ID must land on the top-level `trace_id` field of the
entry (never inside metadata), otherwise the platform cannot correlate.
"""

import re

import pytest

from logtide_sdk import ClientOptions, LogTideClient

VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{VALID_TRACE_ID}-00f067aa0ba902b7-01"
W3C_TRACE = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def client():
    c = LogTideClient(
        ClientOptions(
            api_url="http://localhost:8080",
            api_key="test_key",
            batch_size=1000,
        )
    )
    yield c
    c._closed = True


def entries(client):
    return list(client._buffer)


# ---------------------------------------------------------------- Flask


@pytest.fixture
def flask_app(client):
    flask = pytest.importorskip("flask")
    from logtide_sdk.middleware.flask import LogTideFlaskMiddleware

    app = flask.Flask(__name__)

    @app.route("/hello")
    def hello():
        return "ok"

    LogTideFlaskMiddleware(app, client=client, service_name="flask-test")
    return app


def test_flask_traceparent_inbound(flask_app, client):
    flask_app.test_client().get("/hello", headers={"traceparent": TRACEPARENT})
    assert entries(client), "middleware logged nothing"
    for entry in entries(client):
        assert entry.trace_id == VALID_TRACE_ID
        assert "trace_id" not in entry.metadata


def test_flask_legacy_header_fallback(flask_app, client):
    flask_app.test_client().get("/hello", headers={"X-Trace-ID": "legacy-123"})
    for entry in entries(client):
        assert entry.trace_id == "legacy-123"


def test_flask_generates_trace_id_when_absent(flask_app, client):
    flask_app.test_client().get("/hello")
    logged = entries(client)
    assert logged
    for entry in logged:
        assert entry.trace_id is not None
        assert W3C_TRACE.fullmatch(entry.trace_id), entry.trace_id
    # request + response lines belong to the same trace
    assert len({e.trace_id for e in logged}) == 1


# ---------------------------------------------------------------- Starlette


@pytest.fixture
def starlette_client(client):
    pytest.importorskip("starlette")
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from logtide_sdk.middleware.starlette import LogTideStarletteMiddleware

    async def hello(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/hello", hello)])
    app.add_middleware(
        LogTideStarletteMiddleware, client=client, service_name="starlette-test"
    )
    return TestClient(app)


def test_starlette_traceparent_inbound(starlette_client, client):
    starlette_client.get("/hello", headers={"traceparent": TRACEPARENT})
    assert entries(client)
    for entry in entries(client):
        assert entry.trace_id == VALID_TRACE_ID
        assert "trace_id" not in entry.metadata


def test_starlette_legacy_header_fallback(starlette_client, client):
    starlette_client.get("/hello", headers={"X-Trace-ID": "legacy-123"})
    for entry in entries(client):
        assert entry.trace_id == "legacy-123"


def test_starlette_generates_trace_id_when_absent(starlette_client, client):
    starlette_client.get("/hello")
    logged = entries(client)
    assert logged
    for entry in logged:
        assert entry.trace_id is not None
        assert W3C_TRACE.fullmatch(entry.trace_id), entry.trace_id


# ---------------------------------------------------------------- Django


@pytest.fixture
def django_middleware(client):
    django = pytest.importorskip("django")
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            ALLOWED_HOSTS=["*"],
            LOGTIDE_CLIENT=client,
            LOGTIDE_SERVICE_NAME="django-test",
        )
        django.setup()
    else:  # reuse configured settings across tests in this module
        settings.LOGTIDE_CLIENT = client

    from django.http import HttpResponse

    from logtide_sdk.middleware.django import LogTideDjangoMiddleware

    return LogTideDjangoMiddleware(lambda request: HttpResponse("ok"))


def test_django_traceparent_inbound(django_middleware, client):
    from django.test import RequestFactory

    django_middleware(RequestFactory().get("/hello", headers={"traceparent": TRACEPARENT}))
    assert entries(client)
    for entry in entries(client):
        assert entry.trace_id == VALID_TRACE_ID
        assert "trace_id" not in entry.metadata


def test_django_legacy_header_fallback(django_middleware, client):
    from django.test import RequestFactory

    django_middleware(RequestFactory().get("/hello", headers={"X-Trace-ID": "legacy-123"}))
    for entry in entries(client):
        assert entry.trace_id == "legacy-123"


def test_django_generates_trace_id_when_absent(django_middleware, client):
    from django.test import RequestFactory

    django_middleware(RequestFactory().get("/hello"))
    logged = entries(client)
    assert logged
    for entry in logged:
        assert entry.trace_id is not None
        assert W3C_TRACE.fullmatch(entry.trace_id), entry.trace_id


# --------------------------------------------------- per-request scope


def test_flask_requests_get_isolated_scopes(client):
    flask = pytest.importorskip("flask")
    from logtide_sdk.middleware.flask import LogTideFlaskMiddleware
    from logtide_sdk.scope import add_breadcrumb, set_user

    app = flask.Flask("scope-test")

    @app.route("/buy")
    def buy():
        set_user({"id": "u_1"})
        add_breadcrumb({"message": "added to cart"})
        client.error("svc", "purchase failed")
        return "ok"

    @app.route("/browse")
    def browse():
        client.info("svc", "browsing")
        return "ok"

    LogTideFlaskMiddleware(app, client=client, service_name="flask-scope")

    app.test_client().get("/buy")
    app.test_client().get("/browse")

    by_message = {e.message: e for e in client._buffer}
    failed = by_message["purchase failed"]
    assert failed.metadata["user"] == {"id": "u_1"}
    assert failed.metadata["breadcrumbs"][0]["message"] == "added to cart"

    browsing = by_message["browsing"]
    assert "user" not in browsing.metadata
    assert "breadcrumbs" not in browsing.metadata


def test_starlette_requests_get_isolated_scopes(client):
    pytest.importorskip("starlette")
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from logtide_sdk.middleware.starlette import LogTideStarletteMiddleware
    from logtide_sdk.scope import add_breadcrumb, set_user

    async def buy(request):
        set_user({"id": "u_9"})
        add_breadcrumb({"message": "checkout"})
        client.error("svc", "purchase failed")
        return PlainTextResponse("ok")

    async def browse(request):
        client.info("svc", "browsing")
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/buy", buy), Route("/browse", browse)])
    app.add_middleware(
        LogTideStarletteMiddleware, client=client, service_name="starlette-scope"
    )
    tc = TestClient(app)
    tc.get("/buy")
    tc.get("/browse")

    by_message = {e.message: e for e in client._buffer}
    failed = by_message["purchase failed"]
    assert failed.metadata["user"] == {"id": "u_9"}
    assert failed.metadata["breadcrumbs"][0]["message"] == "checkout"

    browsing = by_message["browsing"]
    assert "user" not in browsing.metadata
    assert "breadcrumbs" not in browsing.metadata
