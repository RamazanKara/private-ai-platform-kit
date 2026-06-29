# Runbooks

Operational procedures, incident playbooks, and governance runbooks for Private AI Platform Kit. Start with the [incident-response index](incident-response.md) during an incident, or [upgrade](upgrade.md) for the most common day-2 change.

## Setup & day-2 operations

| Runbook | Purpose |
| --- | --- |
| [Validation toolchain](validation-toolchain.md) | Install and verify the local/strict validation CLIs |
| [Upgrade & rollback](upgrade.md) | Promote a new release tag, test before promote, and roll back |
| [GPU capacity](gpu-capacity.md) | GPU scheduling, sizing estimates, and tensor-parallel tuning |
| [Policy-blocked deploy](policy-blocked-deploy.md) | Diagnose and resolve Kyverno-blocked deployments |
| [Agent workspaces](agent-workspaces.md) | Design and operate locked-down coding-agent workspaces |
| [Tenant labs](tenant-labs.md) | Onboard a tenant lab with quotas, egress, and isolation |
| [API access](api-access.md) | API-key access model and rotation |

## Runtime, RAG & guardrails

| Runbook | Purpose |
| --- | --- |
| [RAG service](rag-service.md) | Operate the RAG retrieval service |
| [Vector RAG](vector-rag.md) | The Qdrant vector-store retrieval profile |
| [Qdrant migration](qdrant-migration.md) | Collection migration with dry-run and rollback |
| [Guardrails](guardrails.md) | Prompt and secret-detection guardrails |
| [Traceability sandbox](traceability-sandbox.md) | Sandbox trace contract and request correlation |
| [Budget controls](budget-controls.md) | Redis-backed sandbox spend/abuse budgets |
| [Evaluation harness](evaluation-harness.md) | Run and interpret eval suites |

## Governance & evidence

| Runbook | Purpose |
| --- | --- |
| [Model governance](model-governance.md) | Model lifecycle, promotion requests, and approval |
| [Model provenance](model-provenance.md) | Artifact provenance and reproducible digest verification |
| [Evidence pack](evidence-pack.md) | Customer-facing evidence pack generation |
| [Release gates](release-gates.md) | Release-gate thresholds and strict evidence |
| [SLO & error budget](slo-error-budget.md) | SLO objectives and error-budget review |
| [Quota & chargeback](quota-chargeback.md) | Quota plans and chargeback labelling |
| [Data retention](data-retention.md) | Retention and privacy controls |
| [Egress governance](egress-governance.md) | Approved-egress catalog and enforcement |
| [Scorecard triage](scorecard-triage.md) | OpenSSF Scorecard finding triage |

## Incidents & resilience

| Runbook | Purpose |
| --- | --- |
| [Incident response](incident-response.md) | Severity tiers, escalation, and the incident index |
| [Inference runtime incident](incident-inference-runtime.md) | Gateway / vLLM / Ollama runtime outages |
| [Chaos drills](chaos-drills.md) | Rollout/recovery and fault-injection drills |
| [Restore drill](restore-drill.md) | Backup restore-tooling smoke and real data-recovery drill |
| [OIDC / JWKS rotation](oidc-jwks-rotation.md) | Identity-provider key rotation |

For the documentation map (setup, customer handoff, contracts), see [docs/README.md](../docs/README.md).
