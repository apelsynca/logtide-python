"""Flask middleware for LogTide SDK."""

import time

try:
    import werkzeug.exceptions as _werkzeug_exc
    from flask import Flask, Response, g, make_response, request
except ImportError:
    raise ImportError(
        "Flask is required for LogTideFlaskMiddleware. "
        "Install it with: pip install logtide-sdk[flask]"
    )

from logtide_sdk.client import LogTideClient, serialize_exception
from logtide_sdk.scope import get_current_scope, push_scope
from logtide_sdk.tracecontext import resolve_trace_id


class LogTideFlaskMiddleware:
    """
    Flask middleware for automatic request/response logging.

    Example:
        app = Flask(__name__)
        client = LogTideClient(ClientOptions(...))
        LogTideFlaskMiddleware(
            app,
            client=client,
            service_name='flask-api'
        )
    """

    def __init__(
        self,
        app: Flask,
        client: LogTideClient,
        service_name: str,
        log_requests: bool = True,
        log_responses: bool = True,
        log_errors: bool = True,
        include_headers: bool = False,
        include_body: bool = False,
        skip_health_check: bool = True,
        skip_paths: list | None = None,
    ) -> None:
        """
        Initialize Flask middleware.

        Args:
            app: Flask application instance
            client: LogTide client
            service_name: Service name for logs
            log_requests: Log incoming requests
            log_responses: Log responses
            log_errors: Log errors
            include_headers: Include headers in metadata
            include_body: Include request/response body
            skip_health_check: Skip /health and /healthz
            skip_paths: List of paths to skip
        """
        self.client = client
        self.service_name = service_name
        self.log_requests = log_requests
        self.log_responses = log_responses
        self.log_errors = log_errors
        self.include_headers = include_headers
        self.include_body = include_body
        self.skip_paths: list = list(skip_paths or [])

        if skip_health_check:
            self.skip_paths.extend(["/health", "/healthz"])

        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)
        app.errorhandler(Exception)(self._error_handler)

    def _should_skip(self, path: str) -> bool:
        """Check if path should be skipped."""
        return path in self.skip_paths

    def _trace_id(self) -> str:
        """Resolve the request's trace ID once and cache it on g.

        Order: W3C traceparent -> legacy X-Trace-ID -> generated (spec 005).
        Kept request-local (g) — never set on the shared client — to avoid
        races across concurrent requests.
        """
        if not hasattr(g, "logtide_trace_id"):
            g.logtide_trace_id = resolve_trace_id(
                request.headers.get("traceparent"),
                request.headers.get("X-Trace-ID"),
            )
        return g.logtide_trace_id

    def _before_request(self) -> None:
        """Log incoming request."""
        if self._should_skip(request.path):
            return

        # Per-request scope isolation: breadcrumbs/user/tags set inside the
        # handler stay local to this request (spec 004 §6).
        scope_cm = push_scope()
        g._logtide_scope_cm = scope_cm
        scope = scope_cm.__enter__()
        scope.set_trace_context(self._trace_id())

        g.logtide_start_time = time.time()

        if not self.log_requests:
            return

        metadata = {
            "method": request.method,
            "path": request.path,
            "ip": request.remote_addr,
        }

        if self.include_headers:
            metadata["headers"] = dict(request.headers)

        if self.include_body and request.is_json:
            metadata["body"] = request.get_json(silent=True)

        self.client.info(
            self.service_name,
            f"{request.method} {request.path}",
            metadata,
            trace_id=self._trace_id(),
        )

    def _after_request(self, response: Response) -> Response:
        """Log response."""
        if self._should_skip(request.path):
            return response

        if not self.log_responses:
            return response

        duration_ms = 0
        if hasattr(g, "logtide_start_time"):
            duration_ms = (time.time() - g.logtide_start_time) * 1000

        metadata = {
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
        }

        if self.include_headers:
            metadata["response_headers"] = dict(response.headers)

        if (
            self.include_body
            and response.content_type
            and "application/json" in response.content_type
        ):
            try:
                import json as _json

                metadata["response_body"] = _json.loads(response.get_data(as_text=True))
            except Exception:
                pass

        # Use appropriate log level based on status code
        if response.status_code >= 500:
            self.client.error(
                self.service_name,
                f"{request.method} {request.path} {response.status_code} ({duration_ms:.0f}ms)",
                metadata,
                trace_id=self._trace_id(),
            )
        elif response.status_code >= 400:
            self.client.warn(
                self.service_name,
                f"{request.method} {request.path} {response.status_code} ({duration_ms:.0f}ms)",
                metadata,
                trace_id=self._trace_id(),
            )
        else:
            self.client.info(
                self.service_name,
                f"{request.method} {request.path} {response.status_code} ({duration_ms:.0f}ms)",
                metadata,
                trace_id=self._trace_id(),
            )

        return response

    def _teardown_request(self, exc: BaseException | None = None) -> None:
        """Restore the outer scope at the end of the request."""
        scope_cm = g.pop("_logtide_scope_cm", None)
        if scope_cm is not None:
            scope_cm.__exit__(None, None, None)

    def _error_handler(self, error: Exception) -> Response:
        """Log error and return a response.

        Returning a Response (instead of re-raising) ensures Flask still executes
        after_request handlers, so _after_request can log the 500 response too.
        """
        if isinstance(error, _werkzeug_exc.HTTPException):
            resp: Response = error.get_response()
        else:
            resp = make_response("Internal Server Error", 500)

        if not self.log_errors or self._should_skip(request.path):
            return resp

        duration_ms = 0
        if hasattr(g, "logtide_start_time"):
            duration_ms = (time.time() - g.logtide_start_time) * 1000

        self.client.error(
            self.service_name,
            f"Request error: {request.method} {request.path} - {str(error)}",
            {
                "method": request.method,
                "path": request.path,
                "duration_ms": round(duration_ms, 2),
                "exception": serialize_exception(error),
            },
            trace_id=self._trace_id(),
        )

        return resp
