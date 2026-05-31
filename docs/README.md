# AI Platform Ops Lab Documentation

Use this map to move from product overview to implementation detail, operational runbooks, and customer handoff evidence.

## Start Here

| Need | Document |
| --- | --- |
| Product overview and quick start | [README](../README.md) |
| Production control matrix | [Production readiness](production-readiness.md) |
| Upstream reference links | [References](references.md) |

## Local Lab And Customer Cluster Setup

| Need | Document |
| --- | --- |
| Customer-owned Kubernetes assumptions | [Customer cluster README](../clusters/customer/README.md) |
| Validation prerequisites | [Validation toolchain](../runbooks/validation-toolchain.md) |
| GPU scheduling and capacity | [GPU capacity](../runbooks/gpu-capacity.md) |
| Policy troubleshooting | [Policy blocked deploy](../runbooks/policy-blocked-deploy.md) |

## Private AI Runtime And Coding Agents

| Need | Document |
| --- | --- |
| API-key access model | [API access](../runbooks/api-access.md) |
| Traceable sandbox controls | [Traceability sandbox](../runbooks/traceability-sandbox.md) |
| Sandbox budget controls | [Budget controls](../runbooks/budget-controls.md) |
| Prompt and secret guardrails | [Guardrails](../runbooks/guardrails.md) |
| Agent workspace design | [Agent workspaces](../runbooks/agent-workspaces.md) |
| Tenant lab onboarding | [Tenant labs](../runbooks/tenant-labs.md) |
| RAG service | [RAG service](../runbooks/rag-service.md) |
| Qdrant vector RAG profile | [Vector RAG](../runbooks/vector-rag.md) |

## Governance And Handoff Evidence

| Need | Document |
| --- | --- |
| Customer evidence packs | [Evidence pack](../runbooks/evidence-pack.md) |
| Release gates | [Release gates](../runbooks/release-gates.md) |
| SLO and error-budget review | [SLO and error budget](../runbooks/slo-error-budget.md) |
| Evaluation suites | [Evaluation harness](../runbooks/evaluation-harness.md) |
| Quota and chargeback | [Quota and chargeback](../runbooks/quota-chargeback.md) |
| Data retention and privacy | [Data retention](../runbooks/data-retention.md) |
| External egress approvals | [Egress governance](../runbooks/egress-governance.md) |

## Model And Supply-Chain Governance

| Need | Document |
| --- | --- |
| Model lifecycle and promotion | [Model governance](../runbooks/model-governance.md) |
| Model artifact provenance | [Model provenance](../runbooks/model-provenance.md) |
| Restore verification | [Restore drill](../runbooks/restore-drill.md) |
| Resilience exercises | [Chaos drills](../runbooks/chaos-drills.md) |
| Runtime incident response | [Inference runtime incident](../runbooks/incident-inference-runtime.md) |

## Demo Assets

| Asset | Path |
| --- | --- |
| Architecture diagram | [architecture.svg](assets/architecture.svg) |
| README demo video | [ai-platform-ops-lab-demo.mp4](assets/ai-platform-ops-lab-demo.mp4) |
| Demo poster image | [ai-platform-ops-lab-demo-poster.png](assets/ai-platform-ops-lab-demo-poster.png) |
| Live demo command script | [demo-live.sh](../scripts/demo-live.sh) |
