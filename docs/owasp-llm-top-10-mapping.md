# OWASP Top 10 for LLM Applications 2025 mapping

This page uses the [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) names and numbering. The older list used different numbers for several risks; references in this repository use the `:2025` suffix to avoid that ambiguity.

This is a control inventory, not an OWASP assessment or certification. “Partial” means the repository has a relevant mechanism but leaves material residual risk.

## Summary

| Risk | In-repository mechanisms | Coverage |
| --- | --- | --- |
| LLM01:2025 Prompt Injection | Input limits and secret patterns, restricted RAG/workspace access, default-deny egress, safety evals | Partial |
| LLM02:2025 Sensitive Information Disclosure | Audit redaction, optional input/output pattern checks, tenant-scoped retrieval | Partial |
| LLM03:2025 Supply Chain | Pinned dependencies/actions, SBOMs, scans, signatures, model catalog/provenance | Partial |
| LLM04:2025 Data and Model Poisoning | Model promotion/provenance records, reviewed RAG ingestion and collection versions | Partial |
| LLM05:2025 Improper Output Handling | Optional output guardrail; downstream output remains untrusted | Partial |
| LLM06:2025 Excessive Agency | Workspace RBAC, quotas, budgets, default-deny and catalog egress | Partial |
| LLM07:2025 System Prompt Leakage | No secrets in prompts, audit redaction, optional output pattern checks | Limited |
| LLM08:2025 Vector and Embedding Weaknesses | Tenant/owner filters, classification filters, governed embedding model and collection version | Partial |
| LLM09:2025 Misinformation | Grounded context, eval suites, release thresholds | Partial |
| LLM10:2025 Unbounded Consumption | Admission limits, rate limits, budgets, concurrency shedding, batch/file ceilings | Strong at the gateway boundary |

## LLM01:2025 Prompt Injection

The gateway bounds caller-controlled messages, tools, and payload sizes and can reject configured secret patterns. Agent namespaces restrict network and Kubernetes access. RAG documents are filtered and workspace egress is catalog-based.

None of these controls reliably tells instructions from data. Retrieved text, repository files, tool output, and user prompts can still influence the model. Keep tools least-privileged, require confirmation for consequential actions, and design the system so a compromised model response has limited authority.

Verification: gateway admission tests, `make agent-sandbox-smoke`, `make egress-check`, and the safety eval suite.

## LLM02:2025 Sensitive Information Disclosure

Gateway audit events omit raw prompt and completion text. Input secret detection is enabled in the shipped environment values. The optional output guardrail can flag, redact, or block configured patterns for non-streaming responses.

Pattern matching is incomplete, streaming content can be observed before end-of-stream detection, and other components may log raw data. The operator must review runtime/proxy logging, classify RAG content, bind tenant identity, set retention, and prevent secrets from entering prompts in the first place.

Verification: audit-redaction and output-guardrail tests, `make retention-check`.

## LLM03:2025 Supply Chain

First-party Python dependencies are hash-locked, base images and GitHub Actions are pinned, and release workflows produce scans, SBOMs, signatures, and provenance. Kyverno can enforce the project's signing identity for project images.

The project does not establish the integrity of upstream model training, customer model weights, customer images, the cluster, or private mirrors. Model provenance records must be replaced with the digest of the artifact actually served.

Verification: `make dependency-lock-check`, `make repo-security-scan`, `make image-scan`, and release verification.

## LLM04:2025 Data and Model Poisoning

The model catalog requires promotion and provenance records. RAG ingestion records source metadata, owner/classification fields, embedding model, and collection version.

A digest identifies an artifact; it does not prove that its training data or behavior is clean. Likewise, collection metadata does not validate the truth or safety of ingested documents. Operators need source approval, malware/content review, representative evals, and rollback to known model and collection versions.

Verification: `make model-check`, `make model-provenance-check`, and RAG evals.

## LLM05:2025 Improper Output Handling

The optional output guardrail inspects visible text and tool/function arguments before a non-streaming response is cached or returned. It is off in the base values.

Model output must still be treated as untrusted. Callers are responsible for escaping rendered text, validating structured data, constraining shell/SQL/code execution, checking URLs and file paths, and applying authorization at the action boundary. A model saying an action is allowed is not authorization.

Verification: gateway output-guardrail and tool-output tests.

## LLM06:2025 Excessive Agency

Agent workspaces have namespace RBAC, quota, restricted pods, short-lived projected credentials, per-sandbox gateway budgets, and default-deny egress with reviewed exceptions.

These controls limit blast radius but do not decide which tools or actions an agent should have. Approved egress can still be abused, and a viewer or job-management role may be too broad for a particular workflow. Keep actions narrow, separately authorized, observable, and reversible where possible.

Verification: `make agent-smoke`, `make agent-sandbox-smoke`, `make quota-check`, and `make egress-check`.

## LLM07:2025 System Prompt Leakage

System/developer prompts are included in the request sent to the runtime. The platform does not claim that a model can keep them secret. Do not put API keys, private policy logic, or other secrets in prompts.

Audit redaction prevents the gateway's audit event from storing raw prompts, and the optional output guardrail may catch known credential formats. Neither control prevents a model from paraphrasing or revealing instructions.

Verification: prompt-redaction tests and application-specific adversarial evals.

## LLM08:2025 Vector and Embedding Weaknesses

RAG records owner and classification metadata, can enforce tenant filters on lexical and Qdrant backends, and versions collections against an embedding configuration. Customer values can derive tenant identity from a RAG-verified JWT.

When JWT verification is disabled, the service trusts `X-Sandbox-ID`. Shared keys plus caller-supplied tenant headers are not a multi-tenant identity boundary. The operator also owns document-source approval, embedding-model changes, collection migration, index exposure, and poisoning detection.

Verification: RAG tenant-isolation tests, `make rag-eval-check`, and the Qdrant migration procedure.

## LLM09:2025 Misinformation

The RAG service returns source excerpts and grounded message objects. Eval suites can check expected terms, forbidden terms, latency, retrieval precision, and a repository-defined faithfulness score.

Those checks do not establish truth for a customer domain. The local lexical corpus and mock-runtime evals are test fixtures. Add domain cases, human review, source citations, abstention behavior, and acceptance thresholds for the actual model and corpus.

Verification: `make eval`, `make rag-eval`, and strict release gates with current evidence.

## LLM10:2025 Unbounded Consumption

The gateway caps request/body size, message/tool counts, completion limits, batch sizes, and file uploads. It also supports per-sandbox rate limits, estimated-token budgets, and per-process concurrency shedding. Shared Redis is used by the shipped local/customer budget profiles.

These controls cover traffic through the gateway. They do not cap cluster autoscaling cost by themselves, prevent expensive model configuration, or account for work performed outside the gateway. Set KEDA ceilings, Kubernetes quotas, object-store limits, and customer cost alerts as separate controls.

Verification: admission, budget, rate-limit, body-limit, file, batch, and load-shedding tests; `make quota-check`.
