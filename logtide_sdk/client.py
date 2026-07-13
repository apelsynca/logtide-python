"""Main LogTide SDK client implementation."""

import atexit
import dataclasses
import json
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Lock, Thread, Timer
from typing import Any

import requests

from logtide_sdk._base_client import BaseClient
from logtide_sdk._retry import classify_failure
from logtide_sdk._version import SDK_NAME, VERSION
from logtide_sdk.circuit_breaker import CircuitBreaker
from logtide_sdk.enums import CircuitState, LogLevel
from logtide_sdk.exceptions import CircuitBreakerOpenError
from logtide_sdk.json_encoder import logtide_json_dumps
from logtide_sdk.models import (
    AggregatedStatsOptions,
    AggregatedStatsResponse,
    ClientMetrics,
    ClientOptions,
    LogEntry,
    LogsResponse,
    QueryOptions,
)
from logtide_sdk.tracecontext import generate_trace_id

# ---------------------------------------------------------------------------
# Module-level helpers (importable by async_client and middleware)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class LogTideClient(BaseClient):
    """
    LogTide SDK Client.

    Main client for sending structured logs to LogTide with automatic batching,
    retry logic, circuit breaker, connection reuse, and query capabilities.
    """

    def __init__(self, options: ClientOptions) -> None:
        """
        Initialize LogTide client.

        Args:
            options: Client configuration options
        """
        super().__init__(options=options)

        self._buffer: list[LogEntry] = []
        self._trace_id: str | None = None
        self._buffer_lock = Lock()
        self._metrics_lock = Lock()
        self._metrics = ClientMetrics()
        self._circuit_breaker = CircuitBreaker(
            threshold=options.circuit_breaker_threshold,
            reset_timeout_ms=options.circuit_breaker_reset_ms,
        )
        self._latency_window: list[float] = []
        self._flush_timer: Timer | None = None
        self._closed = False

        # Persistent HTTP session for connection reuse across requests
        self._session = requests.Session()

        # Register cleanup on interpreter exit
        atexit.register(self.close)

        # Start timer-based auto-flush
        if options.flush_interval > 0:
            self._schedule_flush()

        if self.options.debug:
            print(f"[LogTide] Client initialized: {options.api_url}")

    @contextmanager
    def with_trace_id(self, trace_id: str) -> Iterator[None]:
        """
        Context manager that sets a trace ID for the duration of the block,
        then restores the previous value.

        Args:
            trace_id: Trace ID to use within context

        Example:
            with client.with_trace_id('request-123'):
                client.info('api', 'Processing request')
        """
        old_trace_id = self._trace_id
        self._trace_id = trace_id
        try:
            yield
        finally:
            self._trace_id = old_trace_id

    @contextmanager
    def with_new_trace_id(self) -> Iterator[None]:
        """
        Context manager with an auto-generated UUID trace ID.

        Example:
            with client.with_new_trace_id():
                client.info('worker', 'Background job')
        """
        with self.with_trace_id(generate_trace_id()):
            yield

    # -----------------------------------------------------------------------
    # Logging methods
    # -----------------------------------------------------------------------

    def _resolve_call(
        self,
        service_or_message: str,
        message_or_payload: Any,
        payload: Any,
    ) -> tuple[str, str, Any]:
        """Support both call forms (spec 004 §3).

        Legacy: (service, message, payload). New: (message, payload) with the
        service taken from ClientOptions.service. The second positional
        argument disambiguates: a string means legacy, anything else means
        the new form (it is the payload).
        """
        if isinstance(message_or_payload, str):
            return service_or_message, message_or_payload, payload
        if self.options.service is None:
            raise ValueError(
                "No service configured: set ClientOptions.service to call "
                "log methods with just a message, or pass (service, message)"
            )
        resolved_payload = message_or_payload if message_or_payload is not None else payload
        return self.options.service, service_or_message, resolved_payload

    def log(self, entry: LogEntry) -> None:
        """
        Log a pre-built entry. Applies trace ID, global metadata, and
        payload limits before buffering. Silently drops when buffer is full.

        Args:
            entry: Log entry to send
        """
        if not self._prepared_to_log():
            return

        # Coerce None to {} so unpacking never raises TypeError
        if entry.metadata is None:
            entry.metadata = {}

        self._pin_trace_id_to_entry(entry)

        # Merge global metadata (entry metadata wins on collision)
        if self.options.global_metadata:
            entry.metadata = {**self.options.global_metadata, **entry.metadata}

        # Stamp SDK identity (spec 003 §3); caller-provided value wins
        if "sdk" not in entry.metadata:
            entry.metadata["sdk"] = {"name": SDK_NAME, "version": VERSION}

        # before_send hook: may mutate or drop the entry. A buggy hook must
        # never lose the entry or raise to the caller.
        if self.options.before_send is not None:
            try:
                result = self.options.before_send(entry)
            except Exception as hook_error:
                if self.options.debug:
                    print(f"[LogTide] before_send raised, keeping entry: {hook_error}")
            else:
                if result is None:
                    return
                entry = result

        # Sampling (applied after before_send, spec 005 §5)
        if self.options.sample_rate < 1.0 and random.random() > self.options.sample_rate:
            return

        self._apply_payload_limits(entry)

        # TODO: move than logic from there
        should_flush = False
        with self._buffer_lock:
            if len(self._buffer) >= self.options.max_buffer_size:
                if self.options.debug:
                    print(f"[LogTide] Buffer full, dropping log: {entry.message}")
                with self._metrics_lock:
                    self._metrics.logs_dropped += 1
                return

            self._buffer.append(entry)
            if len(self._buffer) >= self.options.batch_size:
                should_flush = True

        # Flush outside the lock to avoid a deadlock on re-entry
        if should_flush:
            self.flush()

    def debug(
        self,
        service_or_message: str,
        message: str | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log a DEBUG-level message.

        Call as ``debug(message)`` / ``debug(message, metadata)`` with the
        service from ClientOptions, or legacy ``debug(service, message)``.
        """
        service, resolved_message, resolved_metadata = self._resolve_call(
            service_or_message, message, metadata
        )
        self.log(
            LogEntry(
                service=service,
                level=LogLevel.DEBUG,
                message=resolved_message,
                metadata=resolved_metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    def info(
        self,
        service_or_message: str,
        message: str | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log an INFO-level message.

        Call as ``info(message)`` / ``info(message, metadata)`` with the
        service from ClientOptions, or legacy ``info(service, message)``.
        """
        service, resolved_message, resolved_metadata = self._resolve_call(
            service_or_message, message, metadata
        )
        self.log(
            LogEntry(
                service=service,
                level=LogLevel.INFO,
                message=resolved_message,
                metadata=resolved_metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    def warn(
        self,
        service_or_message: str,
        message: str | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log a WARN-level message.

        Call as ``warn(message)`` / ``warn(message, metadata)`` with the
        service from ClientOptions, or legacy ``warn(service, message)``.
        """
        service, resolved_message, resolved_metadata = self._resolve_call(
            service_or_message, message, metadata
        )
        self.log(
            LogEntry(
                service=service,
                level=LogLevel.WARN,
                message=resolved_message,
                metadata=resolved_metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    def error(
        self,
        service_or_message: str,
        message: str | dict[str, Any] | Exception | None = None,
        metadata_or_error: dict[str, Any] | Exception | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """
        Log an ERROR-level message.

        Args:
            service: Service name
            message: Log message
            metadata_or_error: Metadata dict or Exception (serialized automatically)
        """
        service, resolved_message, payload = self._resolve_call(
            service_or_message, message, metadata_or_error
        )
        metadata = self._process_metadata_or_error(payload)
        self.log(
            LogEntry(
                service=service,
                level=LogLevel.ERROR,
                message=resolved_message,
                metadata=metadata,
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    def critical(
        self,
        service_or_message: str,
        message: str | dict[str, Any] | Exception | None = None,
        metadata_or_error: dict[str, Any] | Exception | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """
        Log a CRITICAL-level message.

        Args:
            service: Service name
            message: Log message
            metadata_or_error: Metadata dict or Exception (serialized automatically)
        """
        service, resolved_message, payload = self._resolve_call(
            service_or_message, message, metadata_or_error
        )
        metadata = self._process_metadata_or_error(payload)
        self.log(
            LogEntry(
                service=service,
                level=LogLevel.CRITICAL,
                message=resolved_message,
                metadata=metadata,
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    # -----------------------------------------------------------------------
    # Flush & send
    # -----------------------------------------------------------------------

    def flush(self) -> None:
        """Flush all buffered logs to the LogTide API immediately."""
        with self._buffer_lock:
            if not self._buffer:
                return
            logs_to_send = self._buffer[:]
            self._buffer.clear()

        self._send_logs_with_retry(logs_to_send)

    # -----------------------------------------------------------------------
    # Query / read API
    # -----------------------------------------------------------------------

    def query(self, options: QueryOptions) -> LogsResponse:
        """
        Query logs with filters.

        Args:
            options: Query options (service, level, time range, full-text search)

        Returns:
            LogsResponse with matched logs and total count

        Raises:
            requests.RequestException: On API error
        """
        params: dict[str, Any] = {
            "limit": options.limit,
            "offset": options.offset,
        }
        if options.service:
            params["service"] = options.service
        if options.level:
            params["level"] = options.level.value
        if options.q:
            params["q"] = options.q
        if options.from_time:
            params["from"] = options.from_time.isoformat()
        if options.to_time:
            params["to"] = options.to_time.isoformat()

        response = self._session.get(
            f"{self.options.api_url}/api/v1/logs",
            headers=self._get_headers(),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return LogsResponse(logs=data.get("logs", []), total=data.get("total", 0))

    def get_by_trace_id(self, trace_id: str) -> list[dict[str, Any]]:
        """
        Get all logs belonging to a trace ID.

        Args:
            trace_id: Trace ID to look up

        Returns:
            List of log entry dicts
        """
        response = self._session.get(
            f"{self.options.api_url}/api/v1/logs/trace/{trace_id}",
            headers=self._get_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_aggregated_stats(self, options: AggregatedStatsOptions) -> AggregatedStatsResponse:
        """
        Get aggregated log statistics over a time range.

        Args:
            options: Time range, interval, and optional service filter

        Returns:
            AggregatedStatsResponse with timeseries, top services, and top errors
        """
        params: dict[str, Any] = {
            "from": options.from_time.isoformat(),
            "to": options.to_time.isoformat(),
            "interval": options.interval,
        }
        if options.service:
            params["service"] = options.service

        response = self._session.get(
            f"{self.options.api_url}/api/v1/logs/aggregated",
            headers=self._get_headers(),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return AggregatedStatsResponse(
            timeseries=data.get("timeseries", []),
            top_services=data.get("top_services", []),
            top_errors=data.get("top_errors", []),
        )

    def stream(
        self,
        on_log: Callable[[dict[str, Any]], None],
        on_error: Callable[[Exception], None] | None = None,
        filters: dict[str, str] | None = None,
    ) -> Callable[[], None]:
        """
        Stream logs in real-time via Server-Sent Events.

        Runs in a background daemon thread and returns immediately.

        Args:
            on_log: Callback invoked for each incoming log entry dict
            on_error: Optional callback for connection or parse errors
            filters: Optional SSE filters, e.g. {'service': 'api', 'level': 'error'}

        Returns:
            A stop callable — call it to terminate the stream.

        Example:
            stop = client.stream(on_log=handle_log, filters={'level': 'error'})
            # ... later:
            stop()
        """
        params: dict[str, str] = dict(filters or {})
        params["token"] = self.options.api_key
        url = f"{self.options.api_url}/api/v1/logs/stream"
        stop_event = Event()

        def _run() -> None:
            try:
                with self._session.get(
                    url,
                    params=params,
                    stream=True,
                    timeout=None,
                    headers=self._get_headers(),
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if stop_event.is_set():
                            break
                        if not line:
                            continue
                        line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                        if line_str.startswith("data: "):
                            try:
                                log_data = json.loads(line_str[6:])
                                on_log(log_data)
                            except Exception as e:
                                if on_error:
                                    on_error(e)
            except Exception as e:
                if not stop_event.is_set() and on_error:
                    on_error(e)

        t = Thread(target=_run, daemon=True)
        t.start()

        def stop() -> None:
            stop_event.set()

        return stop

    # -----------------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------------

    def get_metrics(self) -> ClientMetrics:
        """
        Return a snapshot of the current SDK metrics.

        Returns:
            ClientMetrics dataclass with counters and average latency
        """
        with self._metrics_lock:
            return dataclasses.replace(self._metrics)

    def reset_metrics(self) -> None:
        """Reset all SDK metrics to zero."""
        with self._metrics_lock:
            self._metrics = ClientMetrics()
            self._latency_window.clear()

    def get_circuit_breaker_state(self) -> CircuitState:
        """
        Return the current circuit breaker state.

        Returns:
            CircuitState enum value (CLOSED, OPEN, or HALF_OPEN)
        """
        return self._circuit_breaker.state

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """Flush remaining logs, cancel the timer, and close the HTTP session."""
        if self._closed:
            return

        self._closed = True

        if self._flush_timer:
            self._flush_timer.cancel()

        self.flush()
        self._session.close()

        if self.options.debug:
            print("[LogTide] Client closed")

    def __del__(self) -> None:
        """Destructor — ensures cleanup if close() was not called explicitly."""
        try:
            self.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _send_logs_with_retry(self, log_entries: list[LogEntry]) -> None:
        """Send a batch of logs with exponential backoff and circuit breaker."""
        attempt = 0
        delay = self.options.retry_delay_ms / 1000.0
        state_before = self._circuit_breaker.state

        while attempt <= self.options.max_retries:
            try:
                if self._circuit_breaker.state == CircuitState.OPEN:
                    if self.options.debug:
                        print("[LogTide] Circuit breaker open, skipping send")
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(log_entries)
                    raise CircuitBreakerOpenError("Circuit breaker is open")

                start_time = time.time()
                self._send_logs(log_entries)
                latency = (time.time() - start_time) * 1000

                self._circuit_breaker.record_success()
                self._update_latency(latency)

                with self._metrics_lock:
                    self._metrics.logs_sent += len(log_entries)

                if self.options.debug:
                    print(f"[LogTide] Sent {len(log_entries)} logs ({latency:.2f}ms)")

                return

            except CircuitBreakerOpenError:
                break

            except Exception as e:
                attempt += 1
                self._circuit_breaker.record_failure()

                retryable, retry_after = classify_failure(e)

                with self._metrics_lock:
                    self._metrics.errors += 1
                    if retryable and attempt <= self.options.max_retries:
                        self._metrics.retries += 1

                # Permanent client errors (4xx except 408/429) will not become
                # valid by retrying: drop the batch after the first attempt.
                if not retryable:
                    if self.options.debug:
                        print(f"[LogTide] Non-retryable error, dropping batch: {e}")
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(log_entries)
                    break

                if attempt > self.options.max_retries:
                    if self.options.debug:
                        print(f"[LogTide] Failed to send logs after {attempt} attempts: {e}")
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(log_entries)
                    break

                if self.options.debug:
                    print(f"[LogTide] Retry {attempt}/{self.options.max_retries} in {delay}s")

                # Abort retries if the client was closed while we were in-flight.
                # The session is gone — all remaining attempts would fail anyway.
                if self._closed:
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(log_entries)
                    break

                # A server-provided Retry-After overrides the computed backoff
                time.sleep(retry_after if retry_after is not None else delay)
                delay *= 2

        # Only count a trip when the circuit *transitions* to OPEN during this call,
        # not on every subsequent call while it's already open.
        if self._circuit_breaker.state == CircuitState.OPEN and state_before != CircuitState.OPEN:
            with self._metrics_lock:
                self._metrics.circuit_breaker_trips += 1

    def _send_logs(self, log_entries: list[LogEntry]) -> None:
        """POST a batch of serialized log entries to /api/v1/ingest."""
        json_string = logtide_json_dumps({"logs": [log.to_dict() for log in log_entries]})

        response = self._session.post(
            f"{self.options.api_url}/api/v1/ingest",
            headers=self._get_headers(),
            data=json_string,
            timeout=30,
        )
        response.raise_for_status()

    def _schedule_flush(self) -> None:
        """Schedule the next timer-based auto-flush."""
        if self._closed:
            return
        interval = self.options.flush_interval / 1000.0
        self._flush_timer = Timer(interval, self._auto_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _auto_flush(self) -> None:
        """Timer callback: flush then reschedule."""
        if not self._closed:
            self.flush()
            self._schedule_flush()

    # TODO: refactor update latency code repeat
    def _update_latency(self, latency: float) -> None:
        """Update the rolling average latency (100-sample window)."""
        with self._metrics_lock:
            self._latency_window.append(latency)
            if len(self._latency_window) > 100:
                self._latency_window.pop(0)
            if self._latency_window:
                self._metrics.avg_latency_ms = sum(self._latency_window) / len(self._latency_window)
