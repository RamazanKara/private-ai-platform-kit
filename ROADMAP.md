# Roadmap

This file lists unfinished project work. Released features belong in the [changelog](CHANGELOG.md), not in the roadmap. The order below is a priority order, not a release promise.

## 1. Keep the deployment paths reproducible

- Run the local end-to-end job against both the CI-pinned Kubernetes version and the default local node image.
- Add a customer-overlay render/conformance job that does not require a customer cluster.
- Test upgrades across the supported release boundary, including rollback of charts and configuration contracts.
- Keep chart floors, tested Kubernetes versions, bootstrap tools, and the version matrix aligned.

## 2. Close identity and tenant-boundary gaps

- Add tested configuration examples for common OIDC providers without shipping customer-specific credentials.
- Exercise JWKS rotation and loss of the last-known-good cache in an end-to-end environment.
- Make verified tenant binding the normal customer RAG path and document the header-trusted fallback as a single-tenant option.
- Add negative multi-tenant tests that cover gateway, RAG, object-store, response-store, and batch data together.

## 3. Improve runtime compatibility testing

- Expand streaming and cancellation tests against real Ollama and vLLM releases.
- Add fault-injection coverage for timeout, retry, circuit-breaker, canary, shadow, and fallback behavior.
- Decide whether Responses and Anthropic streaming belong in scope; do not imply support until the contracts and tests exist.
- Keep multi-node serving as an integration example unless the project adopts and tests a specific operator.

## 4. Make stateful operations less manual

- Add a tested Qdrant collection migration dry run and rollback path.
- Add end-to-end examples for external Redis and Qdrant without embedding a vendor-specific managed-service configuration.
- Exercise backup and restore with data-bearing PVCs, not only metadata and report generation.
- Document response-store and batch-object-store migration and retention behavior.

## 5. Reduce maintenance cost

- Remove duplicated narrative documentation when a contract, values file, or runbook already answers the question.
- Generate version tables from pinned configuration where practical.
- Ratchet type checking and coverage only when the checks stay useful and maintainable.
- Keep sample evidence small and clearly separate from current release evidence.

## Not planned

The project does not plan to provision cloud infrastructure, operate customer clusters, become a hosted service, build a general training platform, or reproduce every OpenAI and Anthropic endpoint. Those are scope boundaries, not backlog items. See [Scope and non-goals](docs/scope-and-non-goals.md).

Customer work such as choosing an IdP, sizing GPUs, setting retention, operating backups, and staffing on-call also does not belong on the project roadmap. The repository can provide integration points and checks, but the customer owns those decisions.
