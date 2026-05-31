# Sample Evidence Pack Summary

Generated: `2026-05-31T00:00:00Z`

Mode: `static`

Summary: 28 passed, 0 failed.

| Area | Status | Summary |
| --- | --- | --- |
| Local-first customer-owned Kubernetes | pass | The README keeps the core product local-first and portable to customer-owned clusters. |
| OpenAI-compatible gateway and API authentication | pass | Gateway business endpoints require API keys in local and customer values. |
| RAG service for coding-agent grounding | pass | The RAG service exposes approved context and grounded messages with the same API-key pattern. |
| Vector RAG profile | pass | The local lab keeps zero-dependency lexical retrieval while customer values enable a persistent Qdrant vector-store profile. |
| Coding-agent workspaces | pass | Agent workspaces include PVC-backed storage, namespace-scoped RBAC, quota, and approved egress. |
| Tenant onboarding workflow | pass | Tenant onboarding renders namespace controls and matching coding-agent workspace values from one reviewed spec. |
| Regulated offline tenant profile | pass | A regulated/offline onboarding profile renders coding-agent tenant controls with no external CIDR egress. |
| Model lifecycle governance | pass | Approved models require promotion requests, evidence references, runtime metadata, and approved-only gateway allowlists. |
| Model provenance governance | pass | Approved models require source, immutable reference, digest, license, risk, data classification, promotion, serving, and evidence metadata. |
| Prompt secret detection | pass | Gateway admission rejects obvious credential material before prompts reach Ollama or vLLM. |
| Validation toolchain | pass | Validation profiles define the core, local-lab, and strict customer-handoff toolchain with a pinned installer. |
| Release gates and SLO evidence | pass | Customer handoff gates check eval, load, restore, strict toolchain, SLO, governance, and evidence-pack thresholds. |
| SLO and error budget governance | pass | SLO objectives cover inference availability, latency, eval pass rate, restore verification, and coding-agent platform readiness. |
| Quota and chargeback governance | pass | Reviewed quota plans connect tenant ResourceQuota, gateway sandbox budgets, workspace sizing, and chargeback labels. |
| Egress governance for coding agents | pass | External coding-agent egress must reference approved catalog entries before NetworkPolicies allow it. |
| Data retention and privacy governance | pass | Retention policy covers redacted audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records. |
| Advanced chaos drills | pass | The chaos catalog covers RAG, vector-store, vLLM runtime, and GPU capacity preflight drills in addition to core rollouts. |
| Restore-drill integration | pass | Application-data restore verification uses the restore-drill project. |
| Evaluation, load, and incident evidence | pass | The lab stores smoke and coding-agent evaluation summaries alongside load-test, incident, and chaos evidence. |

Use `make evidence` to generate a fresh JSON and Markdown evidence pack. Use `make evidence LIVE=1` after syncing the local lab to include live Kubernetes readiness checks.
