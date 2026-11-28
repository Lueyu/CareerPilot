# F7 HTTP errors and cancellation boundary

## Goal and status

Return safe correlatable server faults in personal and registered apps, standardize error metadata, and preserve business, recovery, streaming and cancellation contracts. The author implements and verifies source. Independent review, frozen-image observation and dev integration remain parent-owned.

## Decision

Keep the exported FastAPI instance. Install RequestCorrelation then ErrorBoundary as the outer user middleware layers, inside FastAPI's built-in exception telemetry and ServerErrorMiddleware. RequestCorrelation creates one uuid4 hex transport ID per inbound HTTP scope, ignores caller ID headers, adds X-Request-ID, and counts error response-start attempts once. E3 reuses this scope ID. The client StudyChat NewMessage.request_id remains a distinct business idempotency ticket. Successful JSON, stored messages and business deduplication fields are never normalized.

ErrorBoundary owns a local enum phase BEFORE_START / STARTED / COMPLETED and disconnect state. Before start it consumes unexpected Exception, logs a fixed event with transport ID, phase, exception class and source location, then returns a fixed JSON500. The original exception never escapes to outer telemetry/server traceback logging. After start it emits no JSON, second start or final body. It raises a fixed StreamAborted outside the original except block so Uvicorn closes an incomplete response without the original exception context. After a completed response, a background failure is logged and counted without retroactively changing the delivered response. BaseException cancellation and shutdown propagate unchanged. Actual receive/send failure and ClientDisconnect propagate as a fresh fixed ClientDisconnect without original exception context; an unrelated application OSError remains a server fault.

Fallback response decoration reuses the same Starlette CORS options and LanguageMiddleware as the personal app. Registered fallback reuses PrivateResponses and LanguageMiddleware, including no-store and security headers. The registered app retains its existing host/origin policy and does not gain cross-origin access. This was an accepted correction from the parent's independent design synthesis. The initial sketch put the boundary inside policy middleware, leaving policy failures outside coverage. The accepted final arrangement protects policy code too.

## Error wire contract

For application HTTP errors, preserve status, applicable headers and the legacy detail shape. No-body statuses retain their empty body. Add code, message and request_id. Public AuthRoute keeps its string detail. FastAPI validation keeps an array with type, loc and msg. Custom-validator messages use fixed templates. Validation omits input/ctx, limits output to50 errors and16 location components, bounds strings, and uses fixed messages so arbitrary validator exceptions cannot disclose input. The top-level422 code is VALIDATION_ERROR. Unexpected500 detail includes a fixed message compatible with the existing frontend detail.message reader.

| HTTP status | Default code |
| --- | --- |
| 400 | BAD_REQUEST |
| 401 | UNAUTHORIZED |
| 403 | FORBIDDEN |
| 404 | NOT_FOUND |
| 405 | METHOD_NOT_ALLOWED |
| 409 | CONFLICT |
| 413 | PAYLOAD_TOO_LARGE |
| 422 | VALIDATION_ERROR |
| 429 | RATE_LIMITED |
| 500 | INTERNAL_ERROR |
| 502 | BAD_GATEWAY |
| 503 | SERVICE_UNAVAILABLE |
| 504 | GATEWAY_TIMEOUT |
| Other error status | HTTP_ERROR |

Existing business detail.code takes precedence for non422/non500 errors. Examples are API_RATE_LIMITED, API_QUOTA_UNAVAILABLE, RECOVERY_QUARANTINED, session_required and workspace_changed. These business codes are not dynamic counter labels. Intentional HTTPException detail remains the caller's contract; it must already be suitable for clients. This boundary does not sanitize arbitrary successful responses or errors already caught and serialized by domain services.

Native gateway and native service are two HTTP scopes with separate server IDs. public_native.forward reads at most64KiB of a known JSON error (including legacy JSON bodies without a Content-Type header) before NativeStream, preserves status/detail and applicable Retry-After/WWW-Authenticate, re-encodes with the gateway ID, and recalculates length. Malformed, oversized or non-JSON error bodies become a fixed502. No success JSON/SSE/file rewriting occurs. Shielded upstream close stays in finally on error adaptation and NativeStream cleanup.

## Observability and limits

GET /api/v1/ops/http-errors uses require_local_owner and the existing recovery and admission gates. It returns app-owned, thread-safe process memory counts, pid, startup timestamp and reset semantics. http_400 through http_599 count error response-start attempts, once each. unexpected_before_start, unexpected_started, unexpected_completed and unexpected_disconnected count unexpected events separately. A safe500 contributes to both distinct measures. Adding those values is not a valid count of failed requests. Successful200 streams that later abort contribute to unexpected_started, not http_500. Cancel/disconnect do not become unexpected500. No route, tenant, request ID, exception message or arbitrary business code is a counter key.

Counts reset on process restart, are separate across apps/workers/containers/native hops, are not durable deployment-wide metrics, and may be inaccessible during recovery quarantine. There is no new background worker, database table or provider/SMTP call. Lifecycle/startup faults, interpreter crashes, boundary defects, deliberate preexisting service fallbacks and worker failures outside HTTP remain outside this guarantee. This work does not provide distributed tracing or the later F8 fallback inventory.

## Alternatives and carried fixes

A bare FastAPI Exception handler is smaller but cannot cover middleware consistently. Starlette rethrows its original exception after returning the handler response, letting outer telemetry/Uvicorn log secrets. Arbitrary JSON rewriting would require stream buffering and risk changing successful business request IDs. Both were rejected.

RecoveryGuard and the actual registered origin/private response adapters now use pure ASGI. This removes BaseHTTPMiddleware's conversion of CancelledError into RuntimeError("No response returned."). Feedback's durable in_progress record remains untouched, preventing replay after ambiguous cancellation.

The original synchronous static TestClient had neither a provisioned recovery control nor a shared async loop. A fresh isolated PG db_session and same-loop AsyncClient now establish explicit known-installation state, preserve the401 assertion and verify real quarantine503 plus static availability. Production require_normal remains fail-closed.

## Evidence ownership

Parent pre-commit probing found that a business exception raised after observed http.disconnect escaped the original safe boundary. The corrected invocation state distinguishes actual receive/send OSError from an observed disconnect. It propagates a fresh fixed ClientDisconnect outside the original exception context. A business failure after disconnect records unexpected_disconnected without emitting500; actual transport failure remains uncounted as an unexpected fault. Two added regressions cover secret RuntimeError after disconnect and secret receive OSError. Original 190-test author evidence remains historical; the parent reruns the expanded suite before commit.

RUN/f7/design.md records the initial shapes and accepted changes. RUN/f7/evidence contains baseline failures and exact source test receipts. Raw source-manifest.json is frozen only after the final focused suite and changed-file ruff. Parent supplies the actual frozen-image probes and independent final review. Passing source tests or in-memory counts alone do not prove a full image or production deployment.
