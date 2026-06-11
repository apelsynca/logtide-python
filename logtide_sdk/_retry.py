"""Failure classification for the transport retry loops (spec 002 §6).

Retryable: network errors, 408, 429, 5xx. Permanent client errors (other
4xx) must be dropped after the first attempt. A Retry-After header
(delta-seconds form) overrides the computed backoff delay.
"""

from __future__ import annotations

from typing import Any

__all__ = ["classify_failure"]


def _status_and_headers(exc: Exception) -> tuple[int | None, Any]:
    # requests.HTTPError carries .response with .status_code/.headers
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            return status, getattr(response, "headers", None)
    # aiohttp.ClientResponseError carries .status/.headers directly
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status, getattr(exc, "headers", None)
    return None, None


def classify_failure(exc: Exception) -> tuple[bool, float | None]:
    """Return ``(retryable, retry_after_seconds)`` for a send failure."""
    status, headers = _status_and_headers(exc)

    if status is None:
        return True, None  # network/timeout errors: retry with backoff

    retryable = status in (408, 429) or status >= 500

    retry_after: float | None = None
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except AttributeError:
            raw = None
        if raw is not None:
            try:
                seconds = float(raw)
                if seconds >= 0:
                    retry_after = seconds
            except (TypeError, ValueError):
                pass

    return retryable, retry_after
