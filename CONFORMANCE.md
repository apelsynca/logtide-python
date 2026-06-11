# Conformance

Scenario-by-scenario status of this SDK against the LogTide SDK contract.
Each scenario ID is stable across all official SDKs; "n/a" entries explain
why a scenario does not apply. TODO entries are tracked work.

| ID | Scenario | Status | Test reference |
|---|---|---|---|
| C01 | basic log: one POST to /api/v1/ingest with X-API-Key, {logs:[...]} body, RFC 3339 time, metadata.sdk | ✅ | `tests/test_v084.py` (paths/headers), `tests/test_spec_tier1.py` (sdk stamp) |
| C02 | batch by size: batchSize entries flush automatically, order preserved | ✅ | `tests/test_client.py` (batching) |
| C03 | batch by interval: entries delivered without explicit flush | partial | timer-based flush exists; no dedicated test |
| C04 | wire format strictness: SDK fields nested in metadata, only contract fields top-level | ✅ | `tests/test_trace_w3c.py` (top-level trace fields), `test_v084.py` |
| C05 | exception capture: structured metadata.exception with type/message/language/frames/cause | ✅ | `tests/test_client.py::test_error_serialization`, `test_v084.py` |
| C06 | exception chain cap: cause depth ≤ 10, no infinite loop on cycles | ✅ | `tests/test_v084.py` (chained exceptions) |
| C07 | retry on 5xx with growing backoff | ✅ | `tests/test_retry_policy.py` |
| C08 | no retry on permanent 4xx (400/401/403/413) | ✅ | `tests/test_retry_policy.py` (400/401 not retried) |
| C09 | Retry-After overrides computed backoff | ✅ | `tests/test_retry_policy.py::test_retry_after_overrides_backoff` |
| C10 | circuit breaker opens after threshold failures | ✅ | `tests/test_client.py` (circuit breaker) |
| C11 | circuit breaker half-open probe and recovery | ✅ | `logtide_sdk/circuit_breaker.py` half-open; covered in client tests |
| C12 | buffer cap: drops beyond maxBufferSize, counted, never throws | ✅ | `tests/test_client.py` (buffer full silent drop) |
| C13 | flush on close; capture after close is a silent no-op | partial | atexit + close() flush; add explicit post-close no-op test |
| C14 | DSN parsing incl. base path; invalid DSN fails at init | ✅ | `tests/test_spec_tier1.py` (DSN incl. base path, init errors) |
| C15 | inbound traceparent lands on entry trace_id | ✅ | `tests/middleware/test_trace_propagation.py` |
| C16 | no PII by default; API key never logged | ✅ | explicit-only user context (`tests/test_scope.py`) |
| C17 | serialisation robustness: circular/unserialisable values never throw | ✅ | `tests/test_client.py` (unjsonable payloads), `json_encoder` |
| C18 | timestamp fidelity: time reflects capture, not delivery | ✅ | time stamped at LogEntry creation (`models.py`) |
| C20 | scope isolation across concurrent requests | ✅ | `tests/middleware/test_trace_propagation.py` (isolated scopes), `tests/test_scope.py` (async tasks) |
| C21 | breadcrumb ring buffer eviction, oldest first | ✅ | `tests/test_scope.py` (ring buffer) |
| C22 | beforeSend can mutate or drop entries | ✅ | `tests/test_hooks.py` |
| C23 | sampling: rate 0 sends nothing (logs) / no-op spans (traces) | ✅ (logs) | `tests/test_hooks.py`; trace sampling pending span support |
| C24 | OTLP span export with service.name resource | TODO | span export: planned via OpenTelemetry exporter preset |
| C25 | outbound traceparent injection on instrumented HTTP clients | TODO | outbound traceparent helper not implemented |
| C26 | log/trace correlation: active span ids on entries | partial | scope trace context applied to entries; no span manager |
| C27 | middleware error capture rethrows after logging | ✅ | middleware re-raise after logging (`middleware/*.py`) |
| C28 | logging-bridge level mapping and scope context | ✅ | `tests/test_structlog.py`, `handler.py` stdlib mapping |
