import re
from typing import Any

from logtide_sdk.models import PayloadLimitsOptions


def apply_payload_limits(value: Any, path: str, lim: PayloadLimitsOptions) -> Any:
    """Recursively apply payload limits and hide base64 to a metadata value."""

    if value is None:
        return

    field_name = path.split(".")[-1]
    if field_name in lim.exclude_fields:
        return "[EXCLUDED]"

    if isinstance(value, str):
        if len(value) >= 100 and _looks_like_base64(value):
            return "[BASE64 DATA REMOVED]"
        if len(value) > lim.max_field_size:
            return value[: lim.max_field_size] + lim.truncation_marker
        return value

    if isinstance(value, dict):
        return {k: apply_payload_limits(v, f"{path}.{k}", lim) for k, v in value.items()}

    if isinstance(value, list):
        return [apply_payload_limits(v, f"{path}[{i}]", lim) for i, v in enumerate(value)]

    return value


_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=]{100,}$")


def _looks_like_base64(s: str) -> bool:
    """Return True if the string looks like base64-encoded or data-URI data."""
    if s.startswith("data:"):
        return True
    return bool(_BASE64_RE.match(s.replace("\n", "").replace("\r", "")))
