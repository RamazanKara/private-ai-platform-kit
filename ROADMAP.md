# Roadmap

This file lists planned project work in priority order. Released features are recorded in the [changelog](CHANGELOG.md).

## 1. Keep the deployment paths reproducible

- Run the local end-to-end job against both the CI-pinned Kubernetes version and the default local node image.
- Add a customer-overlay render/conformance job that runs without a customer cluster.
- Test upgrades across the supported release boundary, including rollback of charts and configuration contracts.
- Keep chart floors, tested Kubernetes versions, bootstrap tools, and the version matrix aligned.

## 2. Strengthen identity and tenant boundaries

- Add tested configuration examples for common OIDC providers without shipping customer-specific credentials.
- Exercise JWKS rotation and last-known-good cache recovery in an end-to-end environment.
- Make verified tenant binding the normal customer RAG path and document the header-trusted fallback as a single-tenant option.
- Offer per-batch delegated credentials as an alternative to the `batch_replay` worker scope for deployments whose identity provider supports token exchange.
- Add cross-tenant isolation tests that cover gateway, RAG, object-store, response-store, and batch data together.

## 3. Broaden runtime compatibility testing

- Expand streaming and cancellation tests against real Ollama and vLLM releases.
- Add fault-injection coverage for timeout, retry, circuit-breaker, canary, shadow, and fallback behavior.
- Evaluate Responses streaming. Anthropic streaming shipped in v0.28.0 because the Anthropic SDKs stream by default and interactive agents rely on it; Responses streaming follows the same path when a comparable client need appears.
- Run `make sdk-conformance` on a schedule and track vendor SDK releases, so client-side compatibility changes are caught early.
- Keep multi-node serving as an integration example, with room to adopt a specific operator.

## 4. Extend receipt coverage

v0.28.0 made agent actions verifiable. The next step widens which actions produce receipts
automatically. The kit guarantees receipt integrity; the operator chooses which producers to wire up.

- Ship a reference producer that turns Falco or Tetragon alerts and CNI denied-flow logs into `POST /v1/receipts` calls, so the demo's blocked exfiltration files its own receipt alongside the log entry.
- Add a coverage report to the evidence pack that states which action types a deployment produces.
- Decide whether the agent-sandbox demo should ship receipt-emitting tool hooks, or whether that belongs to the agent rather than the kit.

## 5. Automate stateful operations

- Add a tested Qdrant collection migration dry run and rollback path.
- Add vendor-neutral end-to-end examples for external Redis and Qdrant.
- Extend backup and restore drills to data-bearing PVCs.
- Document response-store and batch-object-store migration and retention behavior.

## 6. Keep maintenance lean

- Consolidate narrative documentation where a contract, values file, or runbook already answers the question.
- Generate version tables from pinned configuration where practical.
- Ratchet type checking and coverage as the checks prove their value.
- Keep sample evidence small and clearly separate from current release evidence.
- Revisit a shared `src/common` package if the modules both services copy grow beyond the three ADR 0015 keeps identical.

## Scope

[Scope](docs/scope.md) describes what the platform covers and how it fits alongside cloud provisioning, training, and hosted services. Customer decisions such as choosing an IdP, sizing GPUs, setting retention, operating backups, and staffing on-call stay with the customer; the repository provides the integration points and checks for them.
