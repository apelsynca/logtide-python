"""Standalone Starlette ASGI middleware for LogTide SDK."""

import time
from collections.abc import Callable

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp
except ImportError:
    raise ImportError(
        "Starlette is required for LogTideStarletteMiddleware. "
        "Install it with: pip install logtide-sdk[starlette]"
    )

from logtide_sdk.client import LogTideClient, serialize_exception
from logtide_sdk.tracecontext import resolve_trace_id


class LogTideStarletteMiddleware(BaseHTTPMiddleware):
    """
    Standalone Starlette ASGI middleware for automatic request/response logging.

    Works with any Starlette-based application (including FastAPI).
    Use LogTideFastAPIMiddleware if you prefer the FastAPI-flavoured import path.

    Example:
        from starlette.applications import Starlette
        from logtide_sdk.middleware import LogTideStarletteMiddleware

        app = Starlette()
        app.add_middleware(
            LogTideStarletteMiddleware,
            client=client,
            service_name='starlette-api',
        )
    """

    def __init__(
        self,
        app: ASGIApp,
        client: LogTideClient,
        service_name: str,
        log_requests: bool = True,
        log_responses: bool = True,
        log_errors: bool = True,
        include_headers: bool = False,
        skip_health_check: bool = True,
        skip_paths: list[str] | None = None,
    ) -> None:
        """
        Initialize Starlette middleware.

        Args:
            app: ASGI application
            client: LogTide client instance
            service_name: Service name attached to every log entry
            log_requests: Log each incoming request
            log_responses: Log each response (with status and duration)
            log_errors: Log unhandled exceptions
            include_headers: Include request/response headers in metadata
            skip_health_check: Skip /health, /healthz, /docs, /redoc, /openapi.json
            skip_paths: Additional exact paths to skip
        """
        super().__init__(app)
        self.client = client
        self.service_name = service_name
        self.log_requests = log_requests
        self.log_responses = log_responses
        self.log_errors = log_errors
        self.include_headers = include_headers
        self.skip_paths: list[str] = list(skip_paths or [])

        if skip_health_check:
            self.skip_paths.extend(["/health", "/healthz", "/docs", "/redoc", "/openapi.json"])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and response, logging each phase."""
        if self._should_skip(request.url.path):
            return await call_next(request)

        # Resolve the inbound trace context (kept local — not set on the shared
        # client to avoid race conditions across concurrent requests).
        # Order: W3C traceparent -> legacy X-Trace-ID -> generated (spec 005).
        trace_id: str = resolve_trace_id(
            request.headers.get("traceparent"),
            request.headers.get("x-trace-id"),
        )

        start_time = time.time()

        if self.log_requests:
            self._log_request(request, trace_id)

        try:
            response = await call_next(request)
        except Exception as e:
            if self.log_errors:
                duration_ms = (time.time() - start_time) * 1000
                self._log_error(request, e, duration_ms, trace_id)
            raise

        if self.log_responses:
            duration_ms = (time.time() - start_time) * 1000
            self._log_response(request, response, duration_ms, trace_id)

        return response

    def _should_skip(self, path: str) -> bool:
        return path in self.skip_paths

    def _log_request(self, request: Request, trace_id: str | None = None) -> None:
        metadata = {
            "method": request.method,
            "path": request.url.path,
            "ip": self._get_client_ip(request),
        }
        if self.include_headers:
            metadata["headers"] = dict(request.headers)

        self.client.info(
            self.service_name,
            f"{request.method} {request.url.path}",
            metadata,
            trace_id=trace_id,
        )

    def _log_response(
        self,
        request: Request,
        response: Response,
        duration_ms: float,
        trace_id: str | None = None,
    ) -> None:
        metadata = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
        }
        if self.include_headers:
            metadata["response_headers"] = dict(response.headers)

        message = (
            f"{request.method} {request.url.path} {response.status_code} ({duration_ms:.0f}ms)"
        )

        if response.status_code >= 500:
            self.client.error(self.service_name, message, metadata, trace_id=trace_id)
        elif response.status_code >= 400:
            self.client.warn(self.service_name, message, metadata, trace_id=trace_id)
        else:
            self.client.info(self.service_name, message, metadata, trace_id=trace_id)

    def _log_error(
        self, request: Request, error: Exception, duration_ms: float, trace_id: str | None = None
    ) -> None:
        metadata = {
            "method": request.method,
            "path": request.url.path,
            "duration_ms": round(duration_ms, 2),
            "exception": serialize_exception(error),
        }
        self.client.error(
            self.service_name,
            f"Request error: {request.method} {request.url.path} - {str(error)}",
            metadata,
            trace_id=trace_id,
        )

    def _get_client_ip(self, request: Request) -> str | None:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else None
