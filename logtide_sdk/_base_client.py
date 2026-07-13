from typing import Any

from logtide_sdk.json_encoder import logtide_json_dumps
from logtide_sdk.models import ClientOptions, LogEntry, PayloadLimitsOptions
from logtide_sdk.payload_limits import apply_payload_limits
from logtide_sdk.scope import get_current_scope
from logtide_sdk.serialization import serialize_exception
from logtide_sdk.tracecontext import active_trace_context, generate_trace_id


class BaseClient:
    """
    Base LogTide SDK Client. (more like helper to be DRY for now)
    """

    def __init__(self, options: ClientOptions) -> None:
        self.options = options
        self._payload_limits = options.payload_limits or PayloadLimitsOptions()

        self._closed = False

    def set_trace_id(self, trace_id: str | None) -> None:
        """Set trace ID for subsequent logs."""
        self._trace_id = trace_id

    def get_trace_id(self) -> str | None:
        """Return the current trace ID."""
        return self._trace_id

    def _get_headers(self) -> dict[str, str]:
        """Return HTTP headers for all API requests."""
        assert self.options.api_key, "Get headers somehow with unset API Key"

        return {
            "X-API-Key": self.options.api_key,
            "Content-Type": "application/json",
        }

    def _apply_payload_limits(self, entry: LogEntry) -> None:
        """Enforce payload limits on entry.metadata in-place."""
        if not entry.metadata:
            return

        lim = self._payload_limits
        entry.metadata = apply_payload_limits(entry.metadata, "root", lim)

        raw = logtide_json_dumps(entry)
        if len(raw.encode()) > lim.max_log_size:
            if self.options.debug:
                # TODO: replace all prints with logging
                print(f"[LogTide] Log entry too large ({len(raw)} bytes), truncating metadata")

            entry.metadata = {
                "_truncated": True,
                "_original_size": len(raw.encode()),
            }

    def _process_metadata_or_error(
        self, metadata_or_error: dict[str, Any] | Exception | None
    ) -> dict[str, Any]:
        """
        Normalise the metadata_or_error parameter used by error() and critical().
        Exceptions are serialized to a structured 'exception' key.
        """
        if metadata_or_error is None:
            return {}
        if isinstance(metadata_or_error, dict):
            return metadata_or_error
        return {"exception": serialize_exception(metadata_or_error)}

    def _prepared_to_log(self) -> bool:
        if self._closed or self.options.local_mode is True:
            return False
        if self.options.local_mode == "if_unset_api_key" and not self.options.api_key:
            return False
        return True

    def _pin_trace_id_to_entry(self, entry: LogEntry):
        self._set_trace_and_span_id_from_context(entry)

        # Merge the current scope (tags, user, breadcrumbs, session, trace ctx).
        # Runs before trace-id injection so the scope's trace context wins
        # over auto-generation.
        get_current_scope().apply_to_entry(entry)

        # Inject trace ID (last resort: generation or client instance variable)
        if entry.trace_id is None:
            if self.options.auto_trace_id:
                entry.trace_id = generate_trace_id()
            elif self._trace_id is not None:
                entry.trace_id = self._trace_id

    def _set_trace_and_span_id_from_context(self, entry: LogEntry) -> None:
        # Active-span trace context (resolution order per spec 005 §4:
        # explicit -> active span -> scope -> client context/generation).
        if entry.trace_id is not None:
            return

        active_trace, active_span = active_trace_context()
        if active_trace is None:
            return

        entry.trace_id = active_trace
        if entry.span_id is None:
            entry.span_id = active_span
