# 0012. Server-side response state for the Responses API

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

## Context

The gateway ships `POST /v1/responses` (the OpenAI Responses API) as a **stateless** subset:
requests are translated to and from the internal chat shape and run through the same governance
path as chat, but a request asking for server-side state (`store: true` or
`previous_response_id`) is rejected with `stateful_not_supported`. Clients that want a
conversation to persist server-side must resend the whole history each turn.

The requirement now is to support that stateful surface: persist a response so it can be
retrieved and chained, and expose `GET`/`DELETE`/`input_items` on stored responses.

This collides with a deliberate privacy posture. The tamper-evident audit chain (ADR 0006)
**never** stores raw prompt or completion text, only counts, roles, and SHA-256 fingerprints.
Honoring `store: true` means the gateway now persists **raw conversation content** (the input
and output text) server-side, which is the opposite of that default. The decision must be
explicit, opt-in, and bounded, not a silent behavior change.

## Decision

Add an **opt-in** server-side response store, off by default (`RESPONSES_STORE_ENABLED`).

- **Disabled (default):** unchanged. `store` / `previous_response_id` are rejected with
  `stateful_not_supported`, so no raw content is persisted unless an operator turns it on.
- **Enabled:**
  - `store: true` persists the response object, the turn's input items, and the running
    conversation, keyed by `<tenant>/<response_id>`, with a retention TTL.
  - `previous_response_id` loads the prior response (tenant-scoped; `404` if absent) and
    **prepends its conversation** so the stateless runtime is shown the full history; chaining
    is reconstructed by the gateway, not delegated to runtime memory.
  - New endpoints: `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, and
    `GET /v1/responses/{id}/input_items`, all tenant-scoped.
- **Storage:** a `ResponseStore` protocol with a `MemoryResponseStore` (local/tests) and a
  `RedisResponseStore` (shared, TTL-bounded, on the existing budget Redis). Records are
  tenant-scoped, so a tenant can neither read nor delete another tenant's responses.
- The content store is **separate from the audit chain**. The audit chain stays redacted
  (fingerprints only); stored responses are the caller's own conversation content, retained
  only for the TTL and deletable on demand.

## Consequences

- The gateway can persist raw conversation content when an operator opts in, a deliberate
  departure from the redacted-only default, bounded by off-by-default operation, per-tenant isolation, a
  retention TTL, an explicit `DELETE`, and a store that is a distinct backend from the audit
  log, budget, and cache. Operators handling regulated data keep it off (the default) or set a
  short TTL.
- Governance is unchanged: every stateful request still runs the full chat governance path
  (allowlist, admission, prompt-secret policy, budget, output guardrail, audit) on the
  reconstructed conversation, so chaining cannot bypass any control.
- Streaming and background (`background: true`) response objects remain out of scope
  (documented); this is the stateful-but-synchronous subset.
- Operational cost: a Redis keyspace for stored responses (or per-replica memory locally). Off
  by default, so operators who do not enable it pay nothing.

## Alternatives considered

- **Keep it stateless (reject as before).** Simplest and privacy-safest, but leaves the
  Responses API incomplete and forces every client to resend the full history each turn.
  Rejected now that agents want server-side state; the trade-off is instead made explicit and
  opt-in.
- **Store raw content in, or alongside, the audit chain.** Would violate the audit chain's
  redaction guarantee (ADR 0006) and conflate an integrity control with a content store.
  Rejected: the response store is a separate, deletable, TTL-bounded backend.
- **Persist responses to the object store (like batch blobs).** Response objects are small
  structured records with a natural TTL, which Redis fits better than blob storage; the object
  store (ADR 0011) is for large JSONL batch files. Rejected for this record type.
- **Background (async) responses via the batch-processor pattern.** Deferred: the synchronous
  stateful subset covers the common agent use (store + chain). Background responses can be a
  later ADR if the demand appears.
