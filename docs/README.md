# Private AI Platform Kit Documentation

Use this map when the README is too high-level and you need a specific setup, operations, or customer handoff document.

## Start Here

| Need | Document |
| --- | --- |
| Public overview | [README](../README.md) |
| GitHub Pages landing page | [index.html](index.html) |
| Local setup and validation flow | [Getting started](getting-started.md) |
| Production control matrix | [Production readiness](production-readiness.md) |
| Upstream reference links | [References](references.md) |

## Setup

| Need | Document |
| --- | --- |
| Customer-owned Kubernetes deployment | [Customer cluster README](../clusters/customer/README.md) |
| Validation prerequisites | [Validation toolchain](../runbooks/validation-toolchain.md) |
| GPU scheduling and capacity | [GPU capacity](../runbooks/gpu-capacity.md) |
| Policy troubleshooting | [Policy blocked deploy](../runbooks/policy-blocked-deploy.md) |

## Runtime And Agent Labs

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
| Model lifecycle and promotion | [Model governance](../runbooks/model-governance.md) |
| Model artifact provenance | [Model provenance](../runbooks/model-provenance.md) |
| Restore verification | [Restore drill](../runbooks/restore-drill.md) |
| Resilience exercises | [Chaos drills](../runbooks/chaos-drills.md) |
| Runtime incident response | [Inference runtime incident](../runbooks/incident-inference-runtime.md) |

## Demo Assets

| Asset | Path |
| --- | --- |
| Architecture diagram | [architecture.svg](assets/architecture.svg) |
| README animated demo | [private-ai-platform-kit-demo.gif](assets/private-ai-platform-kit-demo.gif) |
| README demo video | [private-ai-platform-kit-demo.mp4](assets/private-ai-platform-kit-demo.mp4) |
| Demo poster image | [private-ai-platform-kit-demo-poster.png](assets/private-ai-platform-kit-demo-poster.png) |
| Live demo command script | [demo-live.sh](../scripts/demo-live.sh) |
