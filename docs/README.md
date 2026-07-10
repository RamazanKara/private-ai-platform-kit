# Private AI Platform Kit Documentation

Use this map when the README is too high-level and you need a specific setup, operations, or customer handoff document.

## Start Here

| Need | Document |
| --- | --- |
| Public overview | [README](../README.md) |
| Contributor workflow | [Contributing](../CONTRIBUTING.md) |
| Security policy | [Security](../SECURITY.md) |
| Governance | [Governance](../GOVERNANCE.md) |
| Maintainers | [Maintainers](../MAINTAINERS.md) |
| Roadmap | [Roadmap](../ROADMAP.md) |
| Implemented feature status and boundaries | [Feature inventory](feature-inventory.md) |
| Adopters | [Adopters](../ADOPTERS.md) |
| Documentation site (mkdocs-material) | [ramazankara.github.io/private-ai-platform-kit](https://ramazankara.github.io/private-ai-platform-kit/) |
| 15-30 minute local quickstart | [Quickstart](quickstart.md) |
| Local setup and validation flow | [Getting started](getting-started.md) |
| Project fit and alternatives | [Decision guide](decision-guide.md) |
| Common questions | [FAQ](faq.md) |
| Glossary of kit terms | [Glossary](glossary.md) |
| Production control matrix | [Production readiness](production-readiness.md) |
| Project proof and strict evidence | [Proof](proof.md) |
| Release artifact verification | [Release verification](release-verification.md) |
| Package distribution and discovery | [Distribution](distribution.md) |
| Pinned / tested version matrix | [Version matrix](version-matrix.md) |
| Security surface index | [Security overview](security-overview.md) |
| Threat model | [Threat model](threat-model.md) |
| Architecture profiles and diagrams | [Architecture](architecture.md) |
| Architecture decision records | [ADRs](adr/README.md) |
| Scope boundary and non-goals | [Scope and non-goals](scope-and-non-goals.md) |
| Capacity and sizing worksheet | [Capacity sizing](capacity-sizing.md) |
| Cost model and TCO | [Cost model](cost-model.md) |
| OWASP LLM Top 10 control mapping | [OWASP LLM Top 10](owasp-llm-top-10-mapping.md) |
| AI governance crosswalk (NIST/EU AI Act/ISO 42001) | [AI governance crosswalk](ai-governance-crosswalk.md) |
| Upstream reference links | [References](references.md) |

## Setup

| Need | Document |
| --- | --- |
| Customer-owned Kubernetes deployment | [Customer cluster README](../deploy/clusters/customer/README.md) |
| Customer handoff walkthrough | [Customer handoff example](customer-handoff-example.md) |
| Validation prerequisites | [Validation toolchain](../runbooks/validation-toolchain.md) |
| GPU scheduling and capacity | [GPU capacity](../runbooks/gpu-capacity.md) |
| Policy troubleshooting | [Policy blocked deploy](../runbooks/policy-blocked-deploy.md) |

## Runtime And Agent Labs

| Need | Document |
| --- | --- |
| API-key access model | [API access](../runbooks/api-access.md) |
| Client and SDK examples | [Client API examples](client-examples.md) |
| Benchmark and eval interpretation | [Benchmarks and evals](benchmarks-and-evals.md) |
| API contract snapshots | [API contracts](../platform/api-contracts/README.md) |
| Configuration contract snapshots | [Configuration contracts](../platform/config-contracts/README.md) |
| Traceable sandbox controls | [Traceability sandbox](../runbooks/traceability-sandbox.md) |
| Sandbox budget controls | [Budget controls](../runbooks/budget-controls.md) |
| Prompt and secret guardrails | [Guardrails](../runbooks/guardrails.md) |
| Agent workspace design | [Agent workspaces](../runbooks/agent-workspaces.md) |
| Tenant lab onboarding | [Tenant labs](../runbooks/tenant-labs.md) |
| Regulated offline tenant example | [Regulated offline tenant](regulated-offline-tenant-example.md) |
| GPU coding-agent tenant example | [GPU coding-agent tenant](gpu-coding-agent-tenant-example.md) |
| RAG service | [RAG service](../runbooks/rag-service.md) |
| Qdrant vector RAG profile | [Vector RAG](../runbooks/vector-rag.md) |
| Qdrant collection migration | [Qdrant migration](../runbooks/qdrant-migration.md) |

## Governance And Evidence

| Need | Document |
| --- | --- |
| Customer evidence packs | [Evidence pack](../runbooks/evidence-pack.md) |
| Release gates | [Release gates](../runbooks/release-gates.md) |
| SLO and error-budget review | [SLO and error budget](../runbooks/slo-error-budget.md) |
| Evaluation suites | [Evaluation harness](../runbooks/evaluation-harness.md) |
| Quota and chargeback | [Quota and chargeback](../runbooks/quota-chargeback.md) |
| Data retention and privacy | [Data retention](../runbooks/data-retention.md) |
| External egress approvals | [Egress governance](../runbooks/egress-governance.md) |
| OpenSSF Scorecard triage | [Scorecard triage](../runbooks/scorecard-triage.md) |
| GitHub repository security settings | [Repository settings](../runbooks/repository-settings.md) |
| Model lifecycle and promotion | [Model governance](../runbooks/model-governance.md) |
| Model artifact provenance | [Model provenance](../runbooks/model-provenance.md) |
| Model cards / datasheets | [Model cards](../platform/model-catalog/model-cards/README.md) |
| Production model drift monitoring | [Model drift monitoring](../runbooks/model-drift-monitoring.md) |
| Restore verification | [Restore drill](../runbooks/restore-drill.md) |
| Disaster recovery (RPO/RTO) | [Disaster recovery](../runbooks/disaster-recovery.md) |
| Failure-mode / degradation matrix | [Failure modes](../runbooks/failure-modes.md) |
| Runtime threat detection | [Runtime threat detection](../runbooks/runtime-threat-detection.md) |
| Resilience exercises | [Chaos drills](../runbooks/chaos-drills.md) |
| Runtime incident response | [Inference runtime incident](../runbooks/incident-inference-runtime.md) |

## Demo Assets

| Asset | Path |
| --- | --- |
| Architecture diagram | [architecture.svg](assets/architecture.svg) |
| README animated demo | [private-ai-platform-kit-demo.gif](assets/private-ai-platform-kit-demo.gif) |
| Live demo command script | [demo-live.sh](../scripts/demo-live.sh) |
