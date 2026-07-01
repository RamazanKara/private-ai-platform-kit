# Failure Modes & Graceful Degradation Matrix

A consolidated, code-grounded view of how the platform behaves when a dependency degrades or fails.
Each row states the failure symptom, the gateway/RAG behavior that ships today (with the exact code
path or probe), the blast radius, the first operator action, and the runbook to open next.

This is a triage map, not a substitute for the per-incident runbooks. During an active incident start
from the [incident-response index](incident-response.md), then return here to confirm whether the
observed behavior matches the designed degradation for the failing dependency.

## How to read this matrix

- **Behavior today** is what the running code does, cited to the source file and line range so it can
  be verified, not assumed. Where the kit delegates resilience to the operator (HA replicas, managed
  backing services, alert routing), the row says so explicitly.
- **Status codes** are the gateway/RAG HTTP responses a caller sees. The gateway maps upstream runtime
  failures to `502`, capacity/availability conditions to `503`, and budget/admission rejections to
  `400`/`429`. The RAG service maps a vector-store outage to `503`.
- **Trace correlation**: every gateway and RAG response (including error and `503` paths) echoes
  `X-Request-ID`, `X-Sandbox-ID`, and `traceparent`, so a failed call can be correlated to its audit
  event and trace. See [Traceability sandbox](traceability-sandbox.md).

## Probes that drive degradation

Two readiness probes define most of the behavior below.

- **Gateway `/readyz`** (`src/inference-gateway/app/main.py:848-876`) loops over the backends named in
  the model-routing policy and calls `RuntimeClient.health` for each. It returns `503` /
  `status: not_ready` only when a configured runtime backend is unreachable. It does **not** probe the
  budget Redis or the JWKS issuer, so neither of those dependencies removes the gateway pod from
  rotation. `/healthz` (`main.py:834-846`) is a static liveness check and never inspects dependencies.
- **RAG `/readyz`** (`src/rag-service/app/main.py:325-349`) pings Qdrant (`QdrantRetriever.ping`,
  `retriever.py:279-287`) only when `RETRIEVAL_BACKEND=qdrant`; it returns `503` when the collection
  endpoint is unreachable. With the default lexical backend `/readyz` is always `ready` because there
  is no external store to reach.

Both `/healthz` and `/readyz` stay unauthenticated by design (`_auth_required`,
`main.py:318-323` gateway / `main.py:100-102` RAG) so the kubelet probe works even when API-key or JWT
auth is enabled.

## Dependency matrix

| Dependency | Failure symptom | Behavior today (code path) | Blast radius | First operator action | Runbook |
| --- | --- | --- | --- | --- | --- |
| vLLM / Ollama runtime (primary) | `POST /v1/chat/completions` returns `502`; `/healthz` still `200` but `/readyz` flips to `503` for that backend | Non-streaming path tries each route in the resolved chain and fails over to the next on a connection error, retryable `5xx`/`429`, or an open circuit (`main.py:1052-1075`, `_is_failover_worthy` `main.py:156-165`); `RuntimeClient` adds bounded retries with backoff+jitter and a per-backend circuit breaker (`runtime_client.py:94-183`); `/readyz` marks the backend `unavailable` (`main.py:860-876`). A client `4xx` (e.g. `400`/`404`) is never failed over. | All chat/embeddings traffic routed to that backend; sandboxes are unaffected if a healthy fallback route exists in the policy chain | Confirm which backend is down, switch `RUNTIME_BACKEND` / fix the failing route, sync Argo CD | [Inference runtime incident](incident-inference-runtime.md), [Upgrade & rollback](upgrade.md) |
| vLLM / Ollama runtime (streaming) | Stream fails before first byte, or mid-stream | Pre-first-byte failure fails over to the next route (`_open_stream_with_fallback`, `main.py:168-201`). Once a byte is sent the response is committed; a later upstream error emits a terminal SSE error event and records the call as `502` (`stream_body`, `main.py:1006-1047`). | Single in-flight streamed request; the SSE error event lets the caller fail fast rather than hang | Inspect gateway logs for the `request_id`; the audit event records `status_code` and `error` | [Inference runtime incident](incident-inference-runtime.md) |
| vLLM GPU node pool | vLLM pod `Pending` / waiting for GPU; `/readyz` reports the vLLM backend `unavailable` | Same runtime-failover and circuit behavior as above; the gateway cannot schedule a missing GPU, so this is a capacity, not a code, condition | All vLLM-routed traffic until GPU capacity returns or traffic is shifted to a CPU/Ollama route | Run the GPU capacity preflight, check node labels and allocatable GPU | [GPU capacity](gpu-capacity.md), [Inference runtime incident](incident-inference-runtime.md) |
| Qdrant vector store (RAG `qdrant` profile) | `POST /v1/rag/query` returns `503` `vector_store_unavailable`; RAG `/readyz` returns `503` | A query that cannot reach Qdrant raises `VectorStoreError` (`retriever.py:319-342`), mapped to `503` with reason `vector_store_unavailable` (`main.py:488-500`). Startup bootstrap is best-effort: a failure is recorded and surfaced via `/readyz`/`last_sync_status` rather than crashing the pod (`_lifespan`, `main.py:214-230`). The `rag-degradation-fault` chaos drill proves the RAG Deployment stays `Available` while Qdrant is at 0 replicas. | RAG retrieval for the `qdrant` profile only; chat completions that do not call RAG are unaffected | Check Qdrant pods and the RAG `/readyz` / `last_sync_status`; restore Qdrant, then re-run RAG smoke | [Vector RAG](vector-rag.md), [Qdrant migration](qdrant-migration.md), [Chaos drills](chaos-drills.md) |
| RAG service (lexical profile) | `/v1/rag/query` errors or pod down | With `RETRIEVAL_BACKEND=lexical` retrieval is in-process over documents loaded at startup (`LexicalRetriever`, `retriever.py:91-128`); there is no external store, so `/readyz` is always `ready`. Availability depends on Deployment replicas / PDB, not an external dependency. | Callers that depend on grounded context; chat completions still work without RAG | Restart/scale the RAG Deployment; confirm documents loaded via `/healthz` `documents` count | [RAG service](rag-service.md) |
| RAG service (whole service down) | Gateway/agent calls to the RAG service time out or connection-refuse | The RAG service is a separate Deployment; the gateway does not call it inline on the chat path, so a RAG outage does not fail chat completions. Callers that orchestrate RAG-then-chat must handle the RAG error themselves. | Any workflow that injects retrieved context before a completion | Scale/restart RAG; verify with RAG smoke | [RAG service](rag-service.md) |
| Budget Redis (shared, `redis` backend) | Chat/embeddings requests fail with an unhandled `500` when Redis is unreachable or the `0.5s` socket timeout fires | `RedisSandboxBudgetTracker.reserve` runs the atomic Lua reservation via `client.eval` (`budget.py:257-303`). A `redis` connection/timeout error is **not** in the chat handler's caught set (only `AdmissionPolicyError`, `httpx.*`, `ValueError` are caught, `main.py:1083-1142`), so it propagates as a FastAPI `500`. Budget is fail-closed: requests are rejected, not silently un-metered. `/readyz` does not probe Redis, so the gateway pod stays Ready. Default timeout `0.5s` (`settings.py:186`). | All sandboxes when `budget.backend=redis` and budgets are enabled; switch to `memory` backend removes the dependency (per-pod, non-shared) | Confirm Redis reachability (`redis-cli ping`), check the budget namespace NetworkPolicy/Service | [Budget controls](budget-controls.md), [Chaos drills](chaos-drills.md) |
| Budget Redis (`memory` backend) | n/a — no external dependency | `InMemorySandboxBudgetTracker` keeps usage in-process with a lock and sliding window (`budget.py:83-161`). Usage is per-pod and resets on restart; it does not enforce across replicas. | Cross-replica budget accuracy only | None for availability; choose `redis` when running multiple gateway replicas | [Budget controls](budget-controls.md) |
| Gateway (single replica down) | Requests to that pod fail until rescheduled | The gateway is stateless except for the per-process audit hash chain and in-memory caches/budgets. The audit chain is **per replica** (`main.py:37-40`, `_chain_audit_event` `main.py:556-570`), so each pod produces its own verifiable chain. Run multiple replicas with HPA, PDB, and topology spread (see README "What You Get"). | One pod's in-flight requests; the audit chain continuity for that pod resets at restart | Let the Deployment/HPA reschedule; verify replica count and PDB | [Upgrade & rollback](upgrade.md), [SLO & error budget](slo-error-budget.md) |
| Gateway (overload) | Requests return `503` `concurrency_limit` with `Retry-After: 1` | Bounded-concurrency load shedding rejects excess requests instead of queuing them behind the httpx pool (`_overloaded_response` `main.py:490-509`, check at `main.py:809-814`); governed by `MAX_CONCURRENT_REQUESTS`. Per-sandbox burst throttle returns `429` `rate_limited` with `Retry-After` (`_rate_limited_response` `main.py:512-532`). | Excess load only; in-budget steady-state traffic is admitted | Confirm load shedding/rate limiting is the cause via `inference_gateway_load_shed_total` / `inference_gateway_rate_limited_total`; scale out | [SLO & error budget](slo-error-budget.md), [Budget controls](budget-controls.md) |
| Argo CD | GitOps sync stops; manual `kubectl` edits drift; no new deploys | Argo CD is the deploy control plane, not on the request path. Running workloads keep serving. The root application sets `automated: { prune: true, selfHeal: true }` (`deploy/gitops/argocd/root-app.yaml:21-23`), so drift is reconciled once Argo CD recovers. | Deploy/reconcile capability and drift correction; live inference traffic is unaffected | Restore Argo CD; for an urgent change use the documented direct-apply path before Argo CD returns | [Upgrade & rollback](upgrade.md), [Policy-blocked deploy](policy-blocked-deploy.md) |
| JWKS issuer (OIDC / JWT auth) | Transient outage tolerated via last-known-good keys; a cold issuer (no cached keys) returns `503` `jwks_unavailable` with `Retry-After: 5` | `JwksCache.keys` serves last-known-good keys on a fetch failure and applies a short negative-cache backoff; it raises `JwksUnavailableError` only when no keys were ever cached (`jwt_auth.py:77-108`). The middleware maps that to `503` (not a `401`) (`_jwks_unavailable_response` `main.py:468-487`, dispatch `main.py:770-776`). API-key auth, when enabled, is independent of JWKS and still authenticates. A valid token is rejected as `401` (`_auth_failure_response`); an invalid/unsupported `alg` is `401` without any network call (`jwt_auth.py:130-134`). | JWT-authenticated callers during a cold-issuer outage; API-key callers unaffected | Confirm issuer/JWKS reachability and key set; rotate per the rotation runbook if keys changed | [OIDC / JWKS rotation](oidc-jwks-rotation.md), [API access](api-access.md) |
| OTLP / trace backend | Spans are not exported | Tracing is optional and configured at startup (`configure_tracing`); when no tracer is set the request path runs without it (`main.py:829-832` gateway, `main.py:298-301` RAG). An unreachable OTLP endpoint does not fail requests or readiness. | Trace visibility only; correlation IDs in responses and audit logs still work | Check the OTLP collector / `OTEL_EXPORTER_OTLP_ENDPOINT`; requests continue meanwhile | [Traceability sandbox](traceability-sandbox.md) |
| Prometheus / Loki / Promtail | Metrics not scraped or logs not shipped | Services still expose `/metrics` (gateway `main.py:878-885`, RAG `main.py:351-358`) and write redacted audit JSON to stdout regardless of whether anything scrapes/ships it. Promtail ships pod stdout (including the audit chain) to Loki; without a collector the audit trail is lost on pod restart (`deploy/observability/applications.yaml:81-83`). The kube-prometheus-stack default Alertmanager receiver is `null`, so alerts fire but reach no destination until a real receiver is wired per environment (`applications.yaml:22-38`). | Observability, alerting, and durable audit retention — not request serving | Restore the observability stack; **wire a real Alertmanager receiver** and confirm Promtail ships to Loki | [Evidence pack](evidence-pack.md), [SLO & error budget](slo-error-budget.md), [Data retention](data-retention.md) |
| Backup / restore tooling | Restore-drill CronJob fails; Velero backup/restore fails | Backup is off the request path. The restore-drill (Redis AOF) and Velero examples (`deploy/backup/`) validate recoverability; a failed drill signals a recovery-readiness gap, not a live outage. | Recovery confidence and evidence; no impact on serving traffic | Re-run the restore drill; investigate the AOF/Velero failure | [Restore drill](restore-drill.md) |

## Cross-cutting notes

- **Failover is route-aware, not blind retry.** Only connection errors, retryable `5xx`/`429`, and an
  open circuit fail over to the next route; a `4xx` client error is returned as-is because the next
  runtime would reject it identically (`_is_failover_worthy`, `main.py:156-165`).
- **Budget enforcement is fail-closed.** When the `redis` backend is unreachable, requests are
  rejected (`500`) rather than admitted un-metered. If availability must be prioritized over strict
  enforcement, the `memory` backend removes the external dependency at the cost of cross-replica
  accuracy. See [Budget controls](budget-controls.md).
- **Audit continuity is per replica.** The tamper-evident SHA-256 chain is per gateway process; a pod
  restart starts a fresh chain from genesis. Durable retention depends on shipping stdout to Loki
  (Promtail). Plan log retention accordingly — see [Data retention](data-retention.md).
- **Operator-owned resilience.** HA replica counts, PodDisruptionBudgets, topology spread, a managed
  Redis-compatible service, a reachable JWKS issuer, and a wired Alertmanager receiver are operator
  responsibilities; the kit ships sane defaults and examples but does not operate these for you. See
  [Support Boundaries](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/README.md#support-boundaries).

## Validating these behaviors

The [chaos drills](chaos-drills.md) exercise several rows here directly:

- `rag-degradation-fault` scales Qdrant to 0 and asserts the RAG service stays `Available`, then
  recovers — the Qdrant row above.
- `budget-redis-rollout` rollout-restarts the shared budget Redis and re-runs gateway smoke.
- `gateway-rollout`, `ollama-rollout`, `vllm-runtime-rollout`, `qdrant-vector-store-rollout`, and
  `rag-service-rollout` prove graceful restart and post-restart smoke for each component.
