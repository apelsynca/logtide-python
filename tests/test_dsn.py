import pytest

from logtide_sdk.dsn import DsnParseError, parse_dsn


def test_parse_dsn_basic() -> None:
    parts = parse_dsn("https://lp_abc123@logs.example.com")
    assert parts.api_url == "https://logs.example.com"
    assert parts.api_key == "lp_abc123"


def test_parse_dsn_preserves_base_path() -> None:
    parts = parse_dsn("https://lp_abc123@logs.example.com/logtide")
    assert parts.api_url == "https://logs.example.com/logtide"


def test_parse_dsn_http_and_port() -> None:
    parts = parse_dsn("http://lp_k@localhost:8080")
    assert parts.api_url == "http://localhost:8080"
    assert parts.api_key == "lp_k"


@pytest.mark.parametrize(
    "dsn",
    [
        "",
        "not-a-dsn",
        "ftp://lp_k@host",  # bad scheme
        "https://logs.example.com",  # no key
        "https://@logs.example.com",  # empty key
    ],
)
def test_parse_dsn_rejects_invalid(dsn) -> None:
    with pytest.raises(DsnParseError):
        parse_dsn(dsn)
