"""Django middleware for LogTide SDK."""

import time
from collections.abc import Callable

try:
    from django.conf import settings
    from django.http import HttpRequest, HttpResponse
except ImportError:
    raise ImportError(
        "Django is required for LogTideDjangoMiddleware. "
        "Install it with: pip install logtide-sdk[django]"
    )

from logtide_sdk.client import LogTideClient
from logtide_sdk.scope import push_scope
from logtide_sdk.serialization import serialize_exception
from logtide_sdk.tracecontext import resolve_trace_id


class LogTideDjangoMiddleware:
    """
    Django middleware for automatic request/response logging.

    Usage:
        # settings.py
        MIDDLEWARE = [
            'logtide_sdk.middleware.LogTideDjangoMiddleware',
        ]

        # Create client and configure
        LOGTIDE_CLIENT = LogTideClient(ClientOptions(...))
        LOGTIDE_SERVICE_NAME = 'django-api'
        LOGTIDE_LOG_REQUESTS = True
        LOGTIDE_LOG_RESPONSES = True
        LOGTIDE_SKIP_PATHS = ['/admin/']
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """
        Initialize Django middleware.

        Args:
            get_response: Django get_response callable
        """
        self.get_response = get_response

        # Get configuration from settings
        self.client: LogTideClient = getattr(settings, "LOGTIDE_CLIENT", None)
        if not self.client:
            raise ValueError("LOGTIDE_CLIENT must be configured in Django settings")

        self.service_name: str = getattr(settings, "LOGTIDE_SERVICE_NAME", "django-app")
        self.log_requests: bool = getattr(settings, "LOGTIDE_LOG_REQUESTS", True)
        self.log_responses: bool = getattr(settings, "LOGTIDE_LOG_RESPONSES", True)
        self.log_errors: bool = getattr(settings, "LOGTIDE_LOG_ERRORS", True)
        self.include_headers: bool = getattr(settings, "LOGTIDE_INCLUDE_HEADERS", False)
        self.skip_health_check: bool = getattr(settings, "LOGTIDE_SKIP_HEALTH_CHECK", True)
        self.skip_paths: list = list(getattr(settings, "LOGTIDE_SKIP_PATHS", []))

        if self.skip_health_check:
            self.skip_paths.extend(["/health", "/health/", "/healthz", "/healthz/"])

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request."""
        # Check if should skip
        if self._should_skip(request.path):
            return self.get_response(request)

        # Resolve the inbound trace context (kept local — not set on the shared
        # client to avoid race conditions across concurrent requests).
        # Order: W3C traceparent -> legacy X-Trace-ID -> generated (spec 005).
        trace_id: str = resolve_trace_id(
            request.headers.get("traceparent"),
            request.headers.get("X-Trace-ID"),
        )

        # Per-request scope isolation: breadcrumbs/user/tags set inside the
        # view stay local to this request (spec 004 §6).
        with push_scope() as scope:
            scope.set_trace_context(trace_id)

            # Log request
            start_time = time.time()
            if self.log_requests:
                self._log_request(request, trace_id)

            # Process request
            try:
                response = self.get_response(request)
            except Exception as e:
                # Log error
                if self.log_errors:
                    duration_ms = (time.time() - start_time) * 1000
                    self._log_error(request, e, duration_ms, trace_id)
                raise

            # Log response
            if self.log_responses:
                duration_ms = (time.time() - start_time) * 1000
                self._log_response(request, response, duration_ms, trace_id)

            return response

    def _should_skip(self, path: str) -> bool:
        """Check if path should be skipped."""
        return path in self.skip_paths

    def _log_request(self, request: HttpRequest, trace_id: str | None = None) -> None:
        """Log incoming request."""
        metadata = {
            "method": request.method,
            "path": request.path,
            "ip": self._get_client_ip(request),
        }
        if self.include_headers:
            metadata["headers"] = dict(request.headers)

        self.client.info(
            self.service_name,
            f"{request.method} {request.path}",
            metadata,
            trace_id=trace_id,
        )

    def _log_response(
        self,
        request: HttpRequest,
        response: HttpResponse,
        duration_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Log response."""
        metadata = {
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
        }
        if self.include_headers:
            metadata["response_headers"] = dict(response.items())

        message = f"{request.method} {request.path} {response.status_code} ({duration_ms:.0f}ms)"

        if response.status_code >= 500:
            self.client.error(self.service_name, message, metadata, trace_id=trace_id)
        elif response.status_code >= 400:
            self.client.warn(self.service_name, message, metadata, trace_id=trace_id)
        else:
            self.client.info(self.service_name, message, metadata, trace_id=trace_id)

    def _log_error(
        self,
        request: HttpRequest,
        error: Exception,
        duration_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Log error."""
        metadata = {
            "method": request.method,
            "path": request.path,
            "duration_ms": round(duration_ms, 2),
            "exception": serialize_exception(error),
        }
        self.client.error(
            self.service_name,
            f"Request error: {request.method} {request.path} - {str(error)}",
            metadata,
            trace_id=trace_id,
        )

    def _get_client_ip(self, request: HttpRequest) -> str | None:
        """Get client IP address."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")
