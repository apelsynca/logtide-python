"""Scope, breadcrumbs, user context and session_id (spec 003 §5-6, 004 §4)."""

import asyncio

import pytest

from logtide_sdk import ClientOptions, LogTideClient
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


@pytest.fixture
def client():
    c = LogTideClient(
        ClientOptions(api_url="http://localhost:8080", api_key="lp_k", service="svc")
    )
    yield c
    c._closed = True


@pytest.fixture(autouse=True)
def clean_scope():
    """Each test starts from a fresh root scope."""
    with push_scope():
        yield


def last_entry(client):
    return client._buffer[-1]


# ------------------------------------------------------------- scope data


def test_scope_tags_user_extra_reach_the_entry(client):
    set_tag("region", "eu-west-1")
    set_extra("order_id", "42")
    set_user(User(id="u_1", username="alice"))

    client.info("hello")
    md = last_entry(client).metadata
    assert md["tags"] == {"region": "eu-west-1"}
    assert md["order_id"] == "42"
    assert md["user"] == {"id": "u_1", "username": "alice"}


def test_entry_metadata_wins_over_scope_extra(client):
    set_extra("k", "from-scope")
    client.info("hello", {"k": "from-entry"})
    assert last_entry(client).metadata["k"] == "from-entry"


def test_session_id_is_top_level(client):
    set_session_id("123e4567-e89b-42d3-a456-426614174000")
    client.info("hello")
    entry = last_entry(client)
    assert entry.session_id == "123e4567-e89b-42d3-a456-426614174000"
    assert entry.to_dict()["session_id"] == "123e4567-e89b-42d3-a456-426614174000"


def test_scope_trace_context_fills_missing(client):
    get_current_scope().set_trace_context("a" * 32, "b" * 16)
    client.info("hello")
    entry = last_entry(client)
    assert entry.trace_id == "a" * 32
    assert entry.span_id == "b" * 16


def test_explicit_trace_id_wins_over_scope(client):
    get_current_scope().set_trace_context("a" * 32, None)
    client.info("hello", trace_id="c" * 32)
    assert last_entry(client).trace_id == "c" * 32


# ------------------------------------------------------------ breadcrumbs


def test_breadcrumbs_attach_oldest_first(client):
    add_breadcrumb(Breadcrumb(type="http", category="request", message="GET /a"))
    add_breadcrumb(Breadcrumb(message="clicked", type="ui"))

    client.error("boom")
    crumbs = last_entry(client).metadata["breadcrumbs"]
    assert [c["message"] for c in crumbs] == ["GET /a", "clicked"]
    assert crumbs[0]["type"] == "http"
    assert crumbs[0]["timestamp"].endswith("Z")


def test_breadcrumb_dict_shorthand(client):
    add_breadcrumb({"message": "raw dict", "type": "custom"})
    client.info("hello")
    assert last_entry(client).metadata["breadcrumbs"][0]["message"] == "raw dict"


def test_breadcrumb_ring_buffer_evicts_oldest():
    scope = Scope(max_breadcrumbs=3)
    for i in range(5):
        scope.add_breadcrumb(Breadcrumb(message=f"m{i}"))
    assert [c.message for c in scope.breadcrumbs] == ["m2", "m3", "m4"]


def test_clear_breadcrumbs(client):
    add_breadcrumb(Breadcrumb(message="x"))
    get_current_scope().clear_breadcrumbs()
    client.info("hello")
    assert "breadcrumbs" not in last_entry(client).metadata


# -------------------------------------------------------------- isolation


def test_push_scope_isolates_and_restores(client):
    set_tag("outer", "yes")
    with push_scope() as inner:
        inner.set_tag("inner", "yes")
        client.info("inside")
        md = last_entry(client).metadata
        assert md["tags"] == {"outer": "yes", "inner": "yes"}

    client.info("outside")
    md = last_entry(client).metadata
    assert md["tags"] == {"outer": "yes"}


def test_scopes_are_isolated_across_async_tasks(client):
    async def worker(name):
        with push_scope():
            set_tag("task", name)
            await asyncio.sleep(0.01)
            client.info(f"from {name}")

    async def main():
        await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(main())
    tag_sets = {e.metadata["tags"]["task"] for e in client._buffer if "tags" in e.metadata}
    assert tag_sets == {"a", "b"}


# ----------------------------------------------------------------- user


def test_user_to_dict_skips_none_fields():
    assert User(id="u_1").to_dict() == {"id": "u_1"}


def test_user_dict_shorthand(client):
    set_user({"id": "u_2", "email": "a@b.c"})
    client.info("hello")
    assert last_entry(client).metadata["user"] == {"id": "u_2", "email": "a@b.c"}
