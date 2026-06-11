"""Async LogTide SDK client using aiohttp."""

import asyncio
import dataclasses
import json
import time
from collections.abc import Callable
from threading import Lock as ThreadingLock
from typing import Any

try:
    import aiohttp
except ImportError:
    raise ImportError(
        "aiohttp is required for AsyncLogTideClient. "
        "Install it with: pip install logtide-sdk[async]"
    )

from logtide_sdk.circuit_breaker import CircuitBreaker
from logtide_sdk.client import _process_value, serialize_exception
from logtide_sdk.enums import CircuitState, LogLevel
from logtide_sdk.exceptions import CircuitBreakerOpenError
from logtide_sdk.json_encoder import logtide_json_dumps
from logtide_sdk.tracecontext import generate_trace_id
from logtide_sdk.models import (
    AggregatedStatsOptions,
    AggregatedStatsResponse,
    ClientMetrics,
    ClientOptions,
    LogEntry,
    LogsResponse,
    PayloadLimitsOptions,
    QueryOptions,
)


class AsyncLogTideClient:
    """
    Async LogTide SDK Client.

    Async equivalent of LogTideClient using aiohttp. Designed for use in
    asyncio-based applications. Best used as an async context manager.

    Example:
        async with AsyncLogTideClient(ClientOptions(...)) as client:
            await client.info('my-service', 'Hello from async!')

    Or with manual lifecycle management:
        client = AsyncLogTideClient(options)
        await client.start()   # begin background flush loop
        try:
            await client.info('my-service', 'message')
        finally:
            await client.close()
    """

    def __init__(self, options: ClientOptions) -> None:
        """
        Initialize async LogTide client.

        Args:
            options: Client configuration options (same as LogTideClient)
        """
        self.options = options
        self._buffer: list[LogEntry] = []
        self._trace_id: str | None = None
        self._buffer_lock: asyncio.Lock | None = None  # created lazily in first async call
        self._metrics_lock = ThreadingLock()
        self._metrics = ClientMetrics()
        self._circuit_breaker = CircuitBreaker(
            threshold=options.circuit_breaker_threshold,
            reset_timeout_ms=options.circuit_breaker_reset_ms,
        )
        self._latency_window: list[float] = []
        self._payload_limits = options.payload_limits or PayloadLimitsOptions()
        self._session: aiohttp.ClientSession | None = None
        self._flush_task: Any | None = None  # asyncio.Task[None]
        self._closed = False

        if self.options.debug:
            print(f"[LogTide] Async client initialized: {options.api_url}")

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the background flush loop. Called automatically by __aenter__.
        Only needed when not using the async context manager.
        """
        # Eagerly create the session so concurrent callers don't race on first use.
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        if self.options.flush_interval > 0 and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def __aenter__(self) -> "AsyncLogTideClient":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Cancel the flush loop, flush remaining logs, and close the HTTP session."""
        if self._closed:
            return

        # Set _closed immediately so new log() calls are rejected from this point.
        # We then drain the buffer directly, bypassing the _closed guard in flush().
        self._closed = True

        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        await self._drain()

        if self._session is not None and not self._session.closed:
            await self._session.close()

        if self.options.debug:
            print("[LogTide] Async client closed")

    # -----------------------------------------------------------------------
    # Trace ID helpers
    # -----------------------------------------------------------------------

    def set_trace_id(self, trace_id: str | None) -> None:
        """Set trace ID for subsequent logs."""
        self._trace_id = trace_id

    def get_trace_id(self) -> str | None:
        """Return the current trace ID."""
        return self._trace_id

    # -----------------------------------------------------------------------
    # Logging methods
    # -----------------------------------------------------------------------

    async def log(self, entry: LogEntry) -> None:
        """
        Buffer a log entry. Silently drops when buffer is full.

        Args:
            entry: Pre-built log entry
        """
        if self._closed:
            return

        if entry.trace_id is None:
            if self.options.auto_trace_id:
                entry.trace_id = generate_trace_id()
            elif self._trace_id is not None:
                entry.trace_id = self._trace_id

        if entry.metadata is None:
            entry.metadata = {}

        if self.options.global_metadata:
            entry.metadata = {**self.options.global_metadata, **entry.metadata}

        self._apply_payload_limits(entry)

        should_flush = False
        if self._buffer_lock is None:
            self._buffer_lock = asyncio.Lock()
        async with self._buffer_lock:
            if len(self._buffer) >= self.options.max_buffer_size:
                if self.options.debug:
                    print(f"[LogTide] Buffer full, dropping log: {entry.message}")
                with self._metrics_lock:
                    self._metrics.logs_dropped += 1
                return
            self._buffer.append(entry)
            if len(self._buffer) >= self.options.batch_size:
                should_flush = True

        if should_flush:
            await self.flush()

    async def debug(
        self,
        service: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log a DEBUG-level message."""
        await self.log(
            LogEntry(
                service=service,
                level=LogLevel.DEBUG,
                message=message,
                metadata=metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    async def info(
        self,
        service: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log an INFO-level message."""
        await self.log(
            LogEntry(
                service=service,
                level=LogLevel.INFO,
                message=message,
                metadata=metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    async def warn(
        self,
        service: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log a WARN-level message."""
        await self.log(
            LogEntry(
                service=service,
                level=LogLevel.WARN,
                message=message,
                metadata=metadata or {},
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    async def error(
        self,
        service: str,
        message: str,
        metadata_or_error: dict[str, Any] | Exception | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log an ERROR-level message. Accepts an Exception for automatic serialization."""
        metadata = self._process_metadata_or_error(metadata_or_error)
        await self.log(
            LogEntry(
                service=service,
                level=LogLevel.ERROR,
                message=message,
                metadata=metadata,
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    async def critical(
        self,
        service: str,
        message: str,
        metadata_or_error: dict[str, Any] | Exception | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Log a CRITICAL-level message. Accepts an Exception for automatic serialization."""
        metadata = self._process_metadata_or_error(metadata_or_error)
        await self.log(
            LogEntry(
                service=service,
                level=LogLevel.CRITICAL,
                message=message,
                metadata=metadata,
                trace_id=trace_id,
                span_id=span_id,
            )
        )

    # -----------------------------------------------------------------------
    # Flush & send
    # -----------------------------------------------------------------------

    async def flush(self) -> None:
        """Flush all buffered logs to the LogTide API. No-op after close()."""
        if self._closed:
            return
        await self._drain()

    async def _drain(self) -> None:
        """Drain the buffer unconditionally (used internally, including during close)."""
        if self._buffer_lock is None:
            self._buffer_lock = asyncio.Lock()
        async with self._buffer_lock:
            if not self._buffer:
                return
            logs_to_send = self._buffer[:]
            self._buffer.clear()

        await self._send_logs_with_retry(logs_to_send)

    # -----------------------------------------------------------------------
    # Query / read API
    # -----------------------------------------------------------------------

    async def query(self, options: QueryOptions) -> LogsResponse:
        """Query logs with optional filters."""
        params: dict[str, Any] = {"limit": options.limit, "offset": options.offset}
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

        async with self._get_session().get(
            f"{self.options.api_url}/api/v1/logs",
            headers=self._get_headers(),
            params=params,
        ) as response:
            response.raise_for_status()
            data = await response.json()
            return LogsResponse(logs=data.get("logs", []), total=data.get("total", 0))

    async def get_by_trace_id(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all log entries for a given trace ID."""
        async with self._get_session().get(
            f"{self.options.api_url}/api/v1/logs/trace/{trace_id}",
            headers=self._get_headers(),
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def get_aggregated_stats(
        self, options: AggregatedStatsOptions
    ) -> AggregatedStatsResponse:
        """Return aggregated statistics over a time range."""
        params: dict[str, Any] = {
            "from": options.from_time.isoformat(),
            "to": options.to_time.isoformat(),
            "interval": options.interval,
        }
        if options.service:
            params["service"] = options.service

        async with self._get_session().get(
            f"{self.options.api_url}/api/v1/logs/aggregated",
            headers=self._get_headers(),
            params=params,
        ) as response:
            response.raise_for_status()
            data = await response.json()
            return AggregatedStatsResponse(
                timeseries=data.get("timeseries", []),
                top_services=data.get("top_services", []),
                top_errors=data.get("top_errors", []),
            )

    async def stream(
        self,
        on_log: Callable[[dict[str, Any]], None],
        on_error: Callable[[Exception], None] | None = None,
        filters: dict[str, str] | None = None,
    ) -> None:
        """
        Stream logs in real-time via SSE. This coroutine runs until cancelled.

        Wrap with asyncio.create_task() to run concurrently.

        Example:
            task = asyncio.create_task(client.stream(on_log=handle_log))
            # ... later:
            task.cancel()
        """
        params: dict[str, str] = dict(filters or {})
        params["token"] = self.options.api_key

        async with self._get_session().get(
            f"{self.options.api_url}/api/v1/logs/stream",
            headers=self._get_headers(),
            params=params,
        ) as response:
            response.raise_for_status()
            async for line_bytes in response.content:
                line = line_bytes.decode("utf-8").strip()
                if line.startswith("data: "):
                    try:
                        on_log(json.loads(line[6:]))
                    except Exception as e:
                        if on_error:
                            on_error(e)

    # -----------------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------------

    def get_metrics(self) -> ClientMetrics:
        """Return a snapshot of current SDK metrics."""
        with self._metrics_lock:
            return dataclasses.replace(self._metrics)

    def reset_metrics(self) -> None:
        """Reset all metrics to zero."""
        with self._metrics_lock:
            self._metrics = ClientMetrics()
            self._latency_window.clear()

    def get_circuit_breaker_state(self) -> CircuitState:
        """Return the current circuit breaker state."""
        return self._circuit_breaker.state

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        """Return (or lazily create) the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _get_headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.options.api_key,
            "Content-Type": "application/json",
        }

    async def _flush_loop(self) -> None:
        """Background coroutine: flush on a fixed interval until closed."""
        interval = self.options.flush_interval / 1000.0
        while not self._closed:
            await asyncio.sleep(interval)
            if not self._closed:
                await self.flush()

    async def _send_logs_with_retry(self, logs: list[LogEntry]) -> None:
        """Send a batch with exponential backoff and circuit breaker."""
        attempt = 0
        delay = self.options.retry_delay_ms / 1000.0
        state_before = self._circuit_breaker.state

        while attempt <= self.options.max_retries:
            try:
                if self._circuit_breaker.state == CircuitState.OPEN:
                    if self.options.debug:
                        print("[LogTide] Circuit breaker open, skipping send")
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(logs)
                    raise CircuitBreakerOpenError("Circuit breaker is open")

                start_time = time.time()
                await self._send_logs(logs)
                latency = (time.time() - start_time) * 1000

                self._circuit_breaker.record_success()
                self._update_latency(latency)

                with self._metrics_lock:
                    self._metrics.logs_sent += len(logs)

                if self.options.debug:
                    print(f"[LogTide] Sent {len(logs)} logs ({latency:.2f}ms)")

                return

            except CircuitBreakerOpenError:
                break

            except Exception as e:
                attempt += 1
                self._circuit_breaker.record_failure()

                with self._metrics_lock:
                    self._metrics.errors += 1
                    if attempt <= self.options.max_retries:
                        self._metrics.retries += 1

                if attempt > self.options.max_retries:
                    if self.options.debug:
                        print(f"[LogTide] Failed to send logs after {attempt} attempts: {e}")
                    with self._metrics_lock:
                        self._metrics.logs_dropped += len(logs)
                    break

                if self.options.debug:
                    print(f"[LogTide] Retry {attempt}/{self.options.max_retries} in {delay}s")

                await asyncio.sleep(delay)
                delay *= 2

        if self._circuit_breaker.state == CircuitState.OPEN and state_before != CircuitState.OPEN:
            with self._metrics_lock:
                self._metrics.circuit_breaker_trips += 1

    async def _send_logs(self, logs: list[LogEntry]) -> None:
        json_string = logtide_json_dumps({"logs": [log.to_dict() for log in logs]})

        async with self._get_session().post(
            f"{self.options.api_url}/api/v1/ingest",
            headers=self._get_headers(),
            data=json_string,
        ) as response:
            response.raise_for_status()

    def _process_metadata_or_error(
        self, metadata_or_error: dict[str, Any] | Exception | None
    ) -> dict[str, Any]:
        if metadata_or_error is None:
            return {}
        if isinstance(metadata_or_error, dict):
            return metadata_or_error
        return {"exception": serialize_exception(metadata_or_error)}

    # NOTE: this is twice. (both in async and regular clients, maybe need base class)
    def _apply_payload_limits(self, entry: LogEntry) -> None:
        """Enforce payload limits on entry.metadata in-place."""
        if not entry.metadata:
            return
        lim = self._payload_limits
        entry.metadata = _process_value(entry.metadata, "root", lim)

        raw = logtide_json_dumps(entry)
        if len(raw.encode()) > lim.max_log_size:
            if self.options.debug:
                print(f"[LogTide] Log entry too large ({len(raw)} bytes), truncating metadata")
            entry.metadata = {"_truncated": True, "_original_size": len(raw.encode())}

    def _update_latency(self, latency: float) -> None:
        with self._metrics_lock:
            self._latency_window.append(latency)
            if len(self._latency_window) > 100:
                self._latency_window.pop(0)
            if self._latency_window:
                self._metrics.avg_latency_ms = sum(self._latency_window) / len(self._latency_window)
