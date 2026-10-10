# OWASP Top 10 for LLM Applications 2025 mapping

This page uses the [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) names and numbering. The older list used different numbers for several risks; references in this repository use the `:2025` suffix to avoid that ambiguity.

This page inventories the platform's controls for each risk and the operator controls that complete them. Use it as input to an OWASP assessment of a specific deployment.

## Summary

| Risk | In-repository mechanisms |
| --- | --- |
| LLM01:2025 Prompt Injection | Input limits and secret patterns, restricted RAG/workspace access, default-deny egress, safety evals |
| LLM02:2025 Sensitive Information Disclosure | Audit redaction, optional input/output pattern checks, tenant-scoped retrieval |
| LLM03:2025 Supply Chain | Pinned dependencies/actions, SBOMs, scans, signatures, model catalog/provenance |
| LLM04:2025 Data and Model Poisoning | Model promotion/provenance records, reviewed RAG ingestion and collection versions |
| LLM05:2025 Improper Output Handling | Optional output guardrail; caller-side output validation |
| LLM06:2025 Excessive Agency | Workspace RBAC, quotas, budgets, default-deny and catalog egress |
| LLM07:2025 System Prompt Leakage | No secrets in prompts, audit redaction, optional output pattern checks |
| LLM08:2025 Vector and Embedding Weaknesses | Tenant/owner filters, classification filters, governed embedding model and collection version |
| LLM09:2025 Misinformation | Grounded context, eval suites, release thresholds |
| LLM10:2025 Unbounded Consumption | Admission limits, rate limits, budgets, concurrency shedding, batch/file ceilings |

## LLM01:2025 Prompt Injection

The gateway bounds caller-controlled messages, tools, and payload sizes and can reject configured secret patterns. Agent namespaces restrict network and Kubernetes access. RAG documents are filtered and workspace egress is catalog-based.

Retrieved text, repository files, tool output, and user prompts all reach the model as input, so these controls focus on limiting what an influenced response can reach. Keep tools least-privileged, require confirmation for consequential actions, and design the system so a compromised model response has limited authority.

Verification: gateway admission tests, `make agent-sandbox-smoke`, `make egress-check`, and the safety eval suite.

## LLM02:2025 Sensitive Information Disclosure

Gateway audit events omit raw prompt and completion text. Input secret detection is enabled in the shipped environment values. The optional output guardrail can flag, redact, or block configured patterns for non-streaming responses.

Pattern checks catch configured formats, and streaming content is checked at end of stream. Complete the picture by reviewing runtime/proxy logging, classifying RAG content, binding tenant identity, setting retention, and keeping secrets out of prompts at the source.

Verification: audit-redaction and output-guardrail tests, `make retention-check`.

## LLM03:2025 Supply Chain

First-party Python dependencies are hash-locked, base images and GitHub Actions are pinned, and release workflows produce scans, SBOMs, signatures, and provenance. Kyverno can enforce the project's signing identity for project images.

The operator verifies upstream model training sources, customer model weights, customer images, the cluster, and private mirrors. Replace model provenance records with the digest of the artifact actually served.

Verification: `make dependency-lock-check`, `make repo-security-scan`, `make image-scan`, and release verification.

## LLM04:2025 Data and Model Poisoning

The model catalog requires promotion and provenance records. RAG ingestion records source metadata, owner/classification fields, embedding model, and collection version.

A digest identifies an artifact, and collection metadata records where ingested documents came from. Judging training data, model behavior, and document safety calls for source approval, malware/content review, representative evals, and rollback to known model and collection versions, all supported by these records.

Verification: `make model-check`, `make model-provenance-check`, and RAG evals.

## LLM05:2025 Improper Output Handling

The optional output guardrail inspects visible text and tool/function arguments before a non-streaming response is cached or returned. It is off in the base values.

Treat model output as untrusted input. Callers escape rendered text, validate structured data, constrain shell/SQL/code execution, check URLs and file paths, and apply authorization at the action boundary, independent of what the model says is allowed.

Verification: gateway output-guardrail and tool-output tests.

## LLM06:2025 Excessive Agency

Agent workspaces have namespace RBAC, quota, restricted pods, short-lived projected credentials, per-sandbox gateway budgets, and default-deny egress with reviewed exceptions.

These controls limit blast radius; the operator decides which tools and actions each agent gets. Review approved egress and role scope (for example viewer or job-management) per workflow, and keep actions narrow, separately authorized, observable, and reversible where possible.

Verification: `make agent-smoke`, `make agent-sandbox-smoke`, `make quota-check`, and `make egress-check`.

## LLM07:2025 System Prompt Leakage

System/developer prompts are included in the request sent to the runtime, so treat them as visible to the model's output. Keep API keys, private policy logic, and other secrets out of prompts.

Audit redaction keeps raw prompts out of the gateway's audit event, and the optional output guardrail catches known credential formats in responses.

Verification: prompt-redaction tests and application-specific adversarial evals.

## LLM08:2025 Vector and Embedding Weaknesses

RAG records owner and classification metadata, can enforce tenant filters on lexical and Qdrant backends, and versions collections against an embedding configuration. Customer values can derive tenant identity from a RAG-verified JWT.

With JWT verification disabled, the service trusts `X-Sandbox-ID`; enable verified tenant identity for multi-tenant deployments that share keys. The operator also owns document-source approval, embedding-model changes, collection migration, index exposure, and poisoning detection.

Verification: RAG tenant-isolation tests, `make rag-eval-check`, and the Qdrant migration procedure.

## LLM09:2025 Misinformation

The RAG service returns source excerpts and grounded message objects. Eval suites can check expected terms, forbidden terms, latency, retrieval precision, and a repository-defined faithfulness score.

The local lexical corpus and mock-runtime evals are test fixtures. For a customer domain, add domain cases, human review, source citations, abstention behavior, and acceptance thresholds for the actual model and corpus.

Verification: `make eval`, `make rag-eval`, and strict release gates with current evidence.

## LLM10:2025 Unbounded Consumption

The gateway caps request/body size, message/tool counts, completion limits, batch sizes, and file uploads. It also supports per-sandbox rate limits, estimated-token budgets, and per-process concurrency shedding. Shared Redis is used by the shipped local/customer budget profiles.

These controls cover traffic through the gateway. Pair them with KEDA ceilings, Kubernetes quotas, object-store limits, model configuration review, and customer cost alerts for autoscaling cost and work outside the gateway.

Verification: admission, budget, rate-limit, body-limit, file, batch, and load-shedding tests; `make quota-check`.
