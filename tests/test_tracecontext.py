"""Tests for W3C Trace Context utilities (spec 005)."""

import re

import pytest

from logtide_sdk.tracecontext import (
    format_traceparent,
    generate_span_id,
    generate_trace_id,
    parse_traceparent,
    resolve_trace_id,
)

VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
VALID_SPAN_ID = "00f067aa0ba902b7"
VALID_HEADER = f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"


def test_generate_trace_id_is_w3c_format():
    for _ in range(20):
        trace_id = generate_trace_id()
        assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
        assert trace_id != "0" * 32


def test_generate_span_id_is_w3c_format():
    for _ in range(20):
        span_id = generate_span_id()
        assert re.fullmatch(r"[0-9a-f]{16}", span_id)
        assert span_id != "0" * 16


def test_parse_valid_traceparent():
    ctx = parse_traceparent(VALID_HEADER)
    assert ctx is not None
    assert ctx.trace_id == VALID_TRACE_ID
    assert ctx.span_id == VALID_SPAN_ID
    assert ctx.sampled is True


def test_parse_unsampled_traceparent():
    ctx = parse_traceparent(f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-00")
    assert ctx is not None
    assert ctx.sampled is False


def test_parse_uppercase_hex_is_rejected():
    # W3C requires lowercase hex
    assert parse_traceparent(f"00-{VALID_TRACE_ID.upper()}-{VALID_SPAN_ID}-01") is None


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        "00-abc-def-01",  # wrong lengths
        f"00-{'0' * 32}-{VALID_SPAN_ID}-01",  # all-zero trace id
        f"00-{VALID_TRACE_ID}-{'0' * 16}-01",  # all-zero span id
        f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}",  # missing flags
        f"ff-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01",  # forbidden version
        f"00-{'g' * 32}-{VALID_SPAN_ID}-01",  # non-hex
    ],
)
def test_parse_invalid_traceparent(header):
    assert parse_traceparent(header) is None


def test_format_traceparent_round_trip():
    header = format_traceparent(VALID_TRACE_ID, VALID_SPAN_ID, sampled=True)
    assert header == VALID_HEADER
    ctx = parse_traceparent(header)
    assert ctx is not None and ctx.trace_id == VALID_TRACE_ID


def test_resolve_trace_id_prefers_traceparent():
    trace_id = resolve_trace_id(VALID_HEADER, "legacy-id-123")
    assert trace_id == VALID_TRACE_ID


def test_resolve_trace_id_falls_back_to_legacy_header():
    trace_id = resolve_trace_id(None, "legacy-id-123")
    assert trace_id == "legacy-id-123"
    # malformed traceparent also falls through to legacy
    assert resolve_trace_id("garbage", "legacy-id-123") == "legacy-id-123"


def test_resolve_trace_id_generates_when_absent():
    trace_id = resolve_trace_id(None, None)
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)


# ----------------------------------------------- outbound injection (C25)


def test_inject_traceparent_from_scope():
    from logtide_sdk.scope import push_scope
    from logtide_sdk.tracecontext import inject_traceparent

    with push_scope() as scope:
        scope.set_trace_context(VALID_TRACE_ID, VALID_SPAN_ID)
        headers: dict[str, str] = {}
        inject_traceparent(headers)

    assert headers["traceparent"] == f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"


def test_inject_traceparent_generates_span_id_when_scope_has_none():
    from logtide_sdk.scope import push_scope
    from logtide_sdk.tracecontext import inject_traceparent

    with push_scope() as scope:
        scope.set_trace_context(VALID_TRACE_ID)
        headers: dict[str, str] = {}
        inject_traceparent(headers)

    parsed = parse_traceparent(headers["traceparent"])
    assert parsed is not None
    assert parsed.trace_id == VALID_TRACE_ID
    assert re.fullmatch(r"[0-9a-f]{16}", parsed.span_id)


def test_inject_traceparent_noop_without_trace_context():
    from logtide_sdk.scope import push_scope
    from logtide_sdk.tracecontext import inject_traceparent

    with push_scope():
        headers: dict[str, str] = {}
        inject_traceparent(headers)

    assert "traceparent" not in headers


def test_inject_traceparent_does_not_override_existing():
    from logtide_sdk.scope import push_scope
    from logtide_sdk.tracecontext import inject_traceparent

    with push_scope() as scope:
        scope.set_trace_context(VALID_TRACE_ID, VALID_SPAN_ID)
        headers = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}
        inject_traceparent(headers)

    assert headers["traceparent"].startswith("00-" + "a" * 32)


def test_inject_traceparent_prefers_active_span():
    from logtide_sdk.scope import push_scope
    from logtide_sdk.tracecontext import (
        inject_traceparent,
        register_active_context_provider,
    )

    try:
        register_active_context_provider(lambda: ("c" * 32, "d" * 16))
        with push_scope() as scope:
            scope.set_trace_context(VALID_TRACE_ID, VALID_SPAN_ID)
            headers: dict[str, str] = {}
            inject_traceparent(headers)
        assert headers["traceparent"] == "00-" + "c" * 32 + "-" + "d" * 16 + "-01"
    finally:
        register_active_context_provider(None)
