"""DSN parsing (spec 002 §3).

A DSN is a single connection string carrying endpoint and API key:

    https://lp_abc123@logs.example.com
    https://lp_abc123@logs.example.com/base-path   (reverse-proxied installs)
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class DsnParseError(ValueError):
    """Raised when a DSN string cannot be parsed."""


@dataclass(frozen=True)
class DsnParts:
    """Components extracted from a DSN."""

    api_url: str
    api_key: str


def parse_dsn(dsn: str) -> DsnParts:
    """Parse ``scheme://api_key@host[/path]`` into api_url + api_key.

    The path, when present, is a base-path prefix and is preserved in the
    resulting api_url. Raises :class:`DsnParseError` on malformed input —
    configuration errors must fail loudly at init time.
    """
    if not dsn or not isinstance(dsn, str):
        raise DsnParseError("DSN must be a non-empty string")

    parsed = urlparse(dsn)
    if parsed.scheme not in ("http", "https"):
        raise DsnParseError(f"DSN scheme must be http or https, got {parsed.scheme!r}")
    if not parsed.username:
        raise DsnParseError("DSN is missing the API key (expected scheme://key@host)")
    if not parsed.hostname:
        raise DsnParseError("DSN is missing the host")

    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/")

    return DsnParts(api_url=f"{parsed.scheme}://{host}{path}", api_key=parsed.username)
