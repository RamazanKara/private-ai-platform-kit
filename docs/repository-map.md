# Repository map

The repository keeps runtime code, deployment configuration, operational procedures,
and verification together. Start with the component you are changing, then follow
its contracts and validation commands.

## Directory responsibilities

| Directory | Responsibility | Start here |
| --- | --- | --- |
| `src/` | Independently packaged FastAPI services | [Gateway code map](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/inference-gateway/README.md), [RAG code map](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/src/rag-service/README.md) |
| `sdk/` | First-party clients, package metadata, and client tests | [SDK guide](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/sdk/README.md) |
| `deploy/` | Helm charts, cluster overlays, GitOps applications, policy, observability, and backup manifests | [Chart index](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/README.md), [customer overlay](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/README.md) |
| `platform/` | API/config contracts, governance inputs, model catalog, eval suites, SLOs, and toolchain definitions | [Feature inventory](feature-inventory.md), [production readiness](production-readiness.md) |
| `tenants/` | Tenant onboarding specifications, sandbox policies, and deployment examples | [Tenant operations](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/tenant-labs.md) |
| `scripts/` | Setup, validation, contract generation, and evidence commands | [Automation guide](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/README.md) |
| `runbooks/` | Operator procedures linked from alerts and release gates | [Runbook index](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/README.md) |
| `docs/` | Tutorials, how-to guides, reference pages, explanations, and ADRs | [Documentation home](index.md) |
| `chaos/` | Resilience drill definitions | [Chaos drills](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/chaos-drills.md) |
| `loadtest/` | k6 scenarios, a mock runtime, and report summarization | [Benchmarks and evals](benchmarks-and-evals.md) |
| `paper/` | Research text, experiments, conformance drivers, and recorded research results | [Research guide](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/paper/README.md) |
| `results/` | Tracked sample evidence and ignored current reports | [Evidence and validation](proof.md) |

The directory inventory is declared in `scripts/paths.py`. `make paths` prints it
and `make paths-check` checks for missing or undeclared top-level directories.
Directory moves still require updating paths in scripts, manifests, CI, and docs.

## Runtime boundaries

The gateway and RAG service are separate deployable applications with separate
Docker build contexts, dependency locks, tests, and Helm charts. Each contains a
top-level `app` package; their test and type-check processes must remain separate.

The gateway's `app/main.py` assembles middleware, stores, and route registration.
Route modules handle endpoint-specific behavior. Shared admission, budget
settlement, error handling, and receipt recording live in `app/governance.py`.
Changes to these controls need coverage for both successful and rejected calls,
including streaming when applicable.

The RAG service's `app/main.py` assembles authentication, retrieval, and HTTP routes.
Retrieval, embeddings, reranking, ingestion, and audit behavior have their own modules.
It returns documents, context, and grounded messages; clients submit generation
requests to the gateway separately.

Read the service code maps for module-level entry points and the
[architecture guide](architecture.md) for deployed request flows.
Do not import one service's `app` package from the other service or the SDK.
Similar service utilities are currently packaged separately; a shared library would
also need explicit Docker packaging, dependency, and test changes.

## Sources and generated files

| Edit this source | Refresh or check with | Result |
| --- | --- | --- |
| Service routes and request schemas | `make api-contract-update` / `make api-contract` | `platform/api-contracts/*.openapi.json` |
| Service settings, chart defaults, and environment templates | `make config-contract-update` / `make config-contract` | `platform/config-contracts/*.config.json` |
| Helm `values.yaml` | `make chart-docs-update` / `make chart-docs` | Generated values sections in chart READMEs |
| Dashboard JSON under `deploy/observability/dashboards/` | `make dashboard-update` / `make dashboard-check` | The dashboard ConfigMap |
| Documentation and root runbooks | `make docs-build` | Ignored `site/`; temporary `docs/runbooks/` mirror |
| Tenant onboarding specifications | `make tenant-onboard` | Ignored `.out/tenants/` by default |
| Service `requirements*.txt` and root tooling requirement inputs | Hash-aware dependency resolution, then `make dependency-lock-check` | Corresponding checked-in dependency locks |
| Live measurements and validation runs | The relevant report or evidence target | Ignored current reports under `results/` |

Runtime dependencies belong in each service's `requirements.txt`; test dependencies
extend them through `requirements-dev.txt`. Root quality, docs, coverage, SDK build,
and SDK test environments have separate requirement files. Keep the lock associated
with each environment aligned with its input.

Files named `sample-*` under `results/` describe report formats. They are not current
validation evidence. Research results under `paper/` are a separate recorded dataset;
follow the research guide when reproducing or updating them.

## Where to put a change

- Add API behavior to the relevant route module, with service tests and regenerated
  contracts when the interface changes.
- Add a reusable operator action under `scripts/`, expose its normal entry point in
  the Makefile, and document it in the relevant runbook.
- Add repository-tooling regression tests under `scripts/tests/`.
- Add deployment defaults to a chart and environment-specific settings to the
  appropriate cluster overlay.
- Add significant design decisions as an [ADR](adr/README.md), with links to the
  implementation and consequences for operators.
- Put temporary output under `.out/` or the existing ignored report paths.

Follow the [developer workflow](development.md) for setup, focused tests, and the
checks expected before review.
