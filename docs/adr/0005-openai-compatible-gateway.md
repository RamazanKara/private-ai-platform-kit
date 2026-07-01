# 0005. A thin self-built OpenAI-compatible gateway

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

Every request to the platform must pass through a single control point that enforces authentication,
a model allowlist, sandbox budgets, redacted and tamper-evident auditing, Prometheus metrics, and
runtime routing — while presenting the OpenAI HTTP API so existing clients work unchanged. The kit
also wants progressive delivery (canary and shadow) and runtime failover across resolved routes, all
expressed against the platform's own governance objects (the model catalog, sandbox budgets in
Redis, the audit chain). The question is whether to adopt an existing AI gateway or build a thin one.

## Decision

Build a small, purpose-fit gateway in `src/inference-gateway/app` rather than adopt a general-purpose
AI gateway.

- It serves the OpenAI-compatible surface under `/v1`: chat completions, embeddings, moderations,
  batches, models, and usage (see the route handlers and OpenAPI tags in
  [`src/inference-gateway/app/main.py`](../../src/inference-gateway/app/main.py)). Embeddings are
  routed through the gateway specifically so they get the same auth, allowlist, budget, and audit
  controls as chat.
- Authentication is API-key (SHA-256 digest compared with `hmac.compare_digest`) or JWT verified
  against JWKS ([`jwt_auth.py`](../../src/inference-gateway/app/jwt_auth.py)); the audit principal is
  non-reversible (a key-id digest prefix or summarized JWT claims).
- Progressive delivery and resilience are first-class: weighted canary routing
  (`inference_gateway_canary_routed_total`), fire-and-forget shadow mirroring
  (`inference_gateway_shadow_requests_total`), and runtime failover across a resolved route chain
  (`RUNTIME_FALLBACKS`), all in `main.py`.
- It emits Prometheus metrics, redacted audit events with a tamper-evident hash chain
  (see [0006](0006-tamper-evident-audit-hash-chain.md)), and propagates `X-Request-ID`,
  `X-Sandbox-ID`, and W3C `traceparent` without logging raw prompt text (per the README's
  "How It Works").
- Backend routing is by `RUNTIME_BACKEND` to Ollama or vLLM
  (see [0003](0003-inference-runtime-vllm-and-ollama.md)).

## Consequences

- The gateway is governance-shaped: routing decisions are made against the kit's own model catalog,
  sandbox budgets, and audit chain, instead of being adapted onto a third party's configuration
  model. New controls (a budget type, an audit field, a route policy) are code in one service.
- It is small enough for one maintainer to own, pinned-base containerized, and covered by the API and
  config contract snapshots (`platform/api-contracts`, `platform/config-contracts`).
- The kit owns the maintenance of this code: provider/model routing breadth, polished per-key spend
  dashboards, and rate-limit UX are not what this gateway optimizes for. The decision-guide is
  explicit that LiteLLM does far broader provider routing and richer per-key spend tracking, and that
  the kit's gateway is "not a full Kubernetes operating model" substitute for those proxies — it is
  the control point inside one.
- Because it is self-built, it is exactly as featureful as the repo shows; there is no upstream to
  inherit new provider integrations from.

## Alternatives considered

- **LiteLLM proxy.** Broader provider/model routing and richer out-of-the-box per-key spend tracking
  and rate limiting, as the decision-guide acknowledges. Rejected as the platform's control point
  because the kit needs routing and admission bound to its own governance objects (model catalog,
  Redis-backed sandbox budgets, the tamper-evident audit chain), and a thin self-built gateway makes
  those the native data model rather than an adaptation layer. A customer who wants LiteLLM's breadth
  can place it alongside or behind this gateway.
- **Kong AI Gateway (or another API-gateway AI plugin).** Strong general API-gateway features (auth,
  rate limiting, plugins). Rejected as the default because it centers on generic API-gateway concerns
  and would still require building the model-catalog allowlist, sandbox budgets, canary/shadow, and
  the audit hash chain on top; the kit chose to write those directly.
- **No gateway (clients call runtimes directly).** Rejected outright: it removes the single control
  point and makes auth, allowlists, budgets, and auditing impossible to enforce uniformly — the exact
  controls the platform exists to provide.
