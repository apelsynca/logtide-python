# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.5] - 2026-06-11

### Changed

- **Permanent client errors (4xx except 408/429) are no longer retried** (sync and async): the batch is dropped after the first attempt instead of burning the full retry budget
- A `Retry-After` header on `429`/`503` responses now overrides the computed backoff delay

## [0.9.4] - 2026-06-11

### Added

- **Scope API** (`logtide_sdk.scope`, exported from the package root): per-request/per-task context built on `contextvars` — `push_scope()`, `get_current_scope()`, `set_tag`, `set_extra`, `set_user`, `set_session_id`, `add_breadcrumb`. Scope state (tags, user, breadcrumbs, session, trace context) is merged into every captured entry; entry-level values win
- **Breadcrumbs**: ring buffer (default 100, oldest evicted), attached as `metadata.breadcrumbs`
- **User context** (`User`) attached as `metadata.user`; `session_id` sent as a top-level field (`LogEntry.session_id`)
- **Per-request scope isolation in all middleware** (Flask, Django, Starlette/FastAPI): breadcrumbs/user/tags set inside a handler stay local to that request, and handler logs inherit the request's trace context

### Changed

- Trace-id resolution order in `log()` now follows the spec: explicit value → scope trace context → client context/auto-generation

## [0.9.3] - 2026-06-11

### Added

- DSN support: `ClientOptions(dsn="https://lp_key@host[/path]")` (plus `parse_dsn` / `DsnParseError` exported from the package root). Explicit `api_url` + `api_key` keep working; a malformed DSN raises at init time
- `ClientOptions.service`: configure the service once and call log methods with just the message — `client.info("user logged in")`, `client.error("boom", exc)`. The legacy `(service, message)` form keeps working
- Every entry now carries `metadata.sdk = {"name": "logtide-python", "version": ...}` (caller-provided `sdk` key wins)

### Fixed

- `logtide_sdk.__version__` was stale (0.9.1); version now has a single runtime source (`logtide_sdk._version`)

## [0.9.2] - 2026-06-11

### Added

- W3C Trace Context support (`logtide_sdk.tracecontext`): `parse_traceparent`, `format_traceparent`, `generate_trace_id`, `generate_span_id`, `resolve_trace_id` — all exported from the package root
- All log methods (sync and async) accept keyword-only `trace_id` and `span_id`, sent as top-level fields
- `LogEntry.span_id` field, serialized as top-level `span_id`

### Changed

- **Middleware (Flask, Django, Starlette/FastAPI) now resolve the inbound trace context per the W3C spec**: `traceparent` header first, legacy `X-Trace-ID` as deprecated fallback, otherwise a new W3C trace ID is generated. The resolved trace ID is sent as the top-level `trace_id` field instead of `metadata["trace_id"]`, so trace correlation in the platform UI now works for middleware logs
- Auto-generated trace IDs (`auto_trace_id`, `with_new_trace_id`) are now 32-char lowercase-hex W3C IDs instead of UUIDs

## [0.9.1] - 2026-05-29

### Fixed

- `LogTideFastAPIMiddleware` import failed because `middleware/fastapi.py` imported
  `LogTideStarletteMiddleware` from the wrong path (`logtide_sdk.starlette` instead of
  `logtide_sdk.middleware.starlette`) — the FastAPI middleware now imports correctly

### Added

- Import tests covering all four framework middlewares (`tests/middleware/test_imports.py`)

### Contributors

- [@apelsynca](https://github.com/apelsynca) — [#6](https://github.com/logtide-dev/logtide-python/pull/6)

## [0.9.0] - 2026-05-26

### Added

- `LogTideProcessor` — a [structlog](https://www.structlog.org/) processor for forwarding
  structlog events to LogTide (`from logtide_sdk.structlog import LogTideProcessor`). It maps
  log levels, extracts non-reserved event fields into metadata, and serializes exceptions from
  `exc_info`. `structlog` is **not** a hard dependency — it is only required if you use the processor
- Robust JSON encoder (`logtide_json_dumps`) used for both payload-size calculation and the actual
  request body. Metadata containing non-JSON-serializable values no longer raises at flush time —
  Pydantic models, dataclasses, attrs classes, sets, `datetime`/`date`/`time`, `Decimal`, `UUID`,
  `bytes`, IP addresses, `Path`, enums, generators, and `pandas`/`numpy` objects are all handled,
  with circular references and unknown types degrading gracefully to `repr()`

### Changed

- **BREAKING** Dropped support for Python 3.8 and 3.9 (both EOL). Minimum supported version is now
  **Python 3.10**
- Internal imports converted from relative to absolute throughout the package

### Contributors

- [@apelsynca](https://github.com/apelsynca) — [#5](https://github.com/logtide-dev/logtide-python/pull/5)

## [0.8.5] - 2026-04-06

### Fixed

- Use `Z` suffix for UTC timestamps instead of `+00:00` to match server schema requirements
- Normalize caller-supplied `+00:00` offsets to `Z` in `LogEntry.__post_init__`
- Omit `trace_id` from `to_dict()` when `None` — server rejects null values

### Contributors

- [@TomatoInOil](https://github.com/TomatoInOil) — [#4](https://github.com/logtide-dev/logtide-python/pull/4)

## [0.8.4] - 2026-03-21

### Added

- `AsyncLogTideClient`: full async client using `aiohttp` with the same API as the
  sync client — supports `async with`, `await client.start()`, and all logging,
  flush, query, and stream methods as coroutines (`pip install logtide-sdk[async]`)
- `LogTideHandler`: standard `logging.Handler` for drop-in integration with Python's
  built-in logging module — forwards records to LogTide with structured exception
  metadata when `exc_info=True` is used
- `PayloadLimitsOptions`: configurable safeguards against 413 errors — per-field size
  cap, total entry size cap, named field exclusion, and automatic base64 removal
- `LogTideStarletteMiddleware`: standalone Starlette ASGI middleware independent of
  FastAPI (`pip install logtide-sdk[starlette]`)
- `serialize_exception()` exported at top level for use in custom integrations
- `payload_limits` field on `ClientOptions`

### Changed

- **BREAKING** API paths updated to match v1 server contract:
  - `POST /api/logs` → `POST /api/v1/ingest`
  - `GET /api/logs` → `GET /api/v1/logs`
  - `GET /api/logs/trace/{id}` → `GET /api/v1/logs/trace/{id}`
  - `GET /api/logs/stats` → `GET /api/v1/logs/aggregated`
  - `GET /api/logs/stream` → `GET /api/v1/logs/stream`
- **BREAKING** Auth header changed from `Authorization: Bearer <key>` to `X-API-Key: <key>`
- **BREAKING** Error metadata key changed from `"error"` to `"exception"`; value is now a
  structured object with `type`, `message`, `language`, `stacktrace` (array of
  `{file, function, line}` frames), and `raw`
- **BREAKING** `stream()` no longer blocks — it runs in a background daemon thread and
  returns a `Callable[[], None]` stop function immediately
- **BREAKING** Buffer overflow no longer raises `BufferFullError`; logs are silently
  dropped and `logs_dropped` is incremented (`BufferFullError` class is kept for
  backwards-compatible catch blocks)
- `requests.Session` is now created once and reused across all HTTP calls for
  connection reuse and reduced TCP overhead
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` throughout;
  `LogEntry.time` now includes `+00:00` timezone suffix (ISO 8601 compliant)
- Middleware `__init__.py` now uses per-framework `try/except` guards — importing
  `logtide_sdk.middleware` no longer fails if only a subset of frameworks are installed

### Fixed

- Flask, Django, and FastAPI middleware `_log_error` methods were passing raw
  `Exception` objects into the metadata dict instead of serializing them — exceptions
  are now serialized via `serialize_exception()`
- `log()` triggered `flush()` while holding `_buffer_lock`, causing a potential
  deadlock under concurrent access — flush is now triggered outside the lock
- `__version__` in `__init__.py` was incorrectly set to `"0.1.0"` despite the
  package being at `0.1.2`

## [0.1.0] - 2026-01-13

### Added

- Initial release of LogTide Python SDK
- Automatic batching with configurable size and interval
- Retry logic with exponential backoff
- Circuit breaker pattern for fault tolerance
- Max buffer size with drop policy
- Query API for searching and filtering logs
- Live tail with Server-Sent Events (SSE)
- Trace ID context for distributed tracing
- Global metadata support
- Structured error serialization
- Internal metrics tracking
- Logging methods: debug, info, warn, error, critical
- Thread-safe operations
- Graceful shutdown with atexit hook
- Flask middleware for auto-logging HTTP requests
- Django middleware for auto-logging HTTP requests
- FastAPI middleware for auto-logging HTTP requests
- Full type hints support for Python 3.10+

[0.9.1]: https://github.com/logtide-dev/logtide-python/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/logtide-dev/logtide-python/compare/v0.8.5...v0.9.0
[0.8.5]: https://github.com/logtide-dev/logtide-python/compare/v0.8.4...v0.8.5
[0.8.4]: https://github.com/logtide-dev/logtide-python/compare/v0.1.0...v0.8.4
[0.1.0]: https://github.com/logtide-dev/logtide-python/releases/tag/v0.1.0
