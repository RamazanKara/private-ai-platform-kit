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
- Decide whether Responses streaming belongs in scope. Anthropic streaming was decided in favor and shipped in v0.28.0, because the Anthropic SDKs stream by default and the endpoint could not otherwise serve an interactive agent. Responses has no equivalent forcing case yet, so it stays non-streaming until one exists.
- Run `make sdk-conformance` on a schedule and track vendor SDK releases, so a client-side change that breaks compatibility is caught rather than reported by a user.
- Keep multi-node serving as an integration example unless the project adopts and tests a specific operator.

## 4. Close the receipt-coverage gap

v0.28.0 made agent actions verifiable; it did not make them complete. Integrity is the
project's to guarantee, coverage is the operator's, and the difference should be documented
rather than blurred.

- Ship a reference producer that turns Falco or Tetragon alerts and CNI denied-flow logs into `POST /v1/receipts` calls, so the demo's blocked exfiltration files its own receipt instead of only being logged.
- Add a coverage report to the evidence pack that states which action types a deployment actually produces, so an evidence pack cannot imply coverage nobody wired up.
- Decide whether the agent-sandbox demo should ship receipt-emitting tool hooks, or whether that belongs to the agent rather than the kit.

## 5. Make stateful operations less manual

- Add a tested Qdrant collection migration dry run and rollback path.
- Add end-to-end examples for external Redis and Qdrant without embedding a vendor-specific managed-service configuration.
- Exercise backup and restore with data-bearing PVCs, not only metadata and report generation.
- Document response-store and batch-object-store migration and retention behavior.

## 6. Reduce maintenance cost

- Remove duplicated narrative documentation when a contract, values file, or runbook already answers the question.
- Generate version tables from pinned configuration where practical.
- Ratchet type checking and coverage only when the checks stay useful and maintainable.
- Keep sample evidence small and clearly separate from current release evidence.
- Decide between a shared package for the code both services copy (`body_limit`, `tracing`, and most of `jwt_auth` and `audit`) and the current per-image copies, which a hygiene check now keeps identical.

## 7. Hardening follow-ups from the v0.30 review

Found while reviewing the gateway and RAG code; each needs a design decision or a change too large for a patch release.

- Give batch queue claims an owner token, so a worker can tell its own claim from one another worker re-claimed after a reap.
- Stream batch input and results instead of holding a whole file in memory, and checkpoint progress so a crashed batch does not replay items it already charged.
- Replay batch items with the submitter's identity rather than one unbound worker key.
- Allow only `data:` image URLs by default and add a host allowlist for remote images, which the runtime would otherwise fetch from inside the cluster.
- Forward an explicit per-endpoint allowlist of runtime parameters instead of passing unknown request fields through.
- Serve `/metrics` on a separate port so an Ingress for the API cannot expose per-sandbox series.
- Translate Responses API tool-call items (`function_call`, `function_call_output`) so multi-turn tool use works on `/v1/responses`.

## Not planned

The project does not plan to provision cloud infrastructure, operate customer clusters, become a hosted service, build a general training platform, or reproduce every OpenAI and Anthropic endpoint. Those are scope boundaries, not backlog items. See [Scope and non-goals](docs/scope-and-non-goals.md).

Customer work such as choosing an IdP, sizing GPUs, setting retention, operating backups, and staffing on-call also does not belong on the project roadmap. The repository can provide integration points and checks, but the customer owns those decisions.
