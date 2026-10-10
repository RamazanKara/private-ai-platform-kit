# Security overview

This page summarizes the security-relevant defaults in release `v0.29.0`. The [threat model](threat-model.md) has the detailed trust boundaries and risks. The [production readiness matrix](production-readiness.md) lists validation commands.

## Defaults that matter

- The local profile uses the public key `local-development-only`. It is for the local lab only.
- In-cluster application traffic is HTTP, with NetworkPolicy controlling reachability. The opt-in mTLS overlay adds encryption.
- The output guardrail and stored Responses state are off in the base chart.
- The local RAG profile disables tenant isolation because it uses one shared demo corpus.
- Without a tenant-bound key record or verified JWT claim, `X-Sandbox-ID` is caller-supplied.
- The agent-sandbox pod template is restricted. Select an isolation `RuntimeClass` such as gVisor or Kata in the values to add a separate kernel boundary.
- The bundled Redis, Qdrant, and Loki footprints are single-node reference services.
- Sample reports under `results/` show report shape; generate current security evidence per release.

Review and adapt the customer values before deploying them.

## Gateway controls

The gateway supports API-key hashes and JWT/JWKS verification. Key records can add scopes, expiry, sandbox binding, and budget overrides. JWT configuration can bind a verified tenant claim to the sandbox. A contradictory `X-Sandbox-ID` is rejected when a binding exists.

Before forwarding an inference request, the gateway can enforce:

- allowed model IDs and routing policy;
- message, prompt, tool, completion, and batch size limits;
- per-sandbox rate and estimated-token budgets;
- input credential-pattern detection;
- a per-process concurrency limit and load shedding.

The optional output guardrail can flag, redact, or block configured credential, PII, and denied-content patterns. Redact and block apply to non-streaming responses; streaming responses are flagged at end of stream.

These are deterministic application checks. Combine them with caller-side output handling and least-privilege tools to address prompt injection.

## Tenant and RAG identity

The customer RAG values enable owner-based tenant filtering, backed by a trusted tenant identity.

The RAG service can verify its own JWT and derive the tenant from a claim. When JWT verification is off, it trusts `X-Sandbox-ID`; a caller with the shared key can assert another sandbox ID. Put direct RAG access behind a trusted identity-stamping path or enable RAG-side JWT verification for multi-tenant use.

Tenant NetworkPolicies default to deny and add explicit DNS, gateway, RAG, and reviewed external CIDR rules. The cluster CNI enforces them; the local cluster uses Calico by default for NetworkPolicy enforcement.

## Workspace isolation

Agent workspaces use the vendored `kubernetes-sigs/agent-sandbox` controller and a restricted pod template: non-root user, read-only root filesystem, dropped capabilities, no ambient service-account token, resource limits, namespace RBAC, and default-deny egress.

The projected platform token is short-lived and audience-bound; handle it as a credential. Approved egress destinations are reachable from the workspace, so keep the egress catalog narrow and treat files, retrieved text, model output, and tool arguments as untrusted.

## Audit records

The gateway audit event stores request metadata and hashes rather than raw prompt or completion text. Records are linked into a per-process hash chain.

The chain detects edits and reordering in an exported sequence, and each replica keeps its own chain. For deletion resistance, log-stream durability, and end-to-end completeness, export logs, retain the `chain_id`, and commit chain-head anchors to a separate trusted system. Use `make audit-verify` and follow the [audit-chain runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/audit-chain.md).

Gateway redaction covers gateway logs; also review runtime, ingress, proxy, RAG, object-store, and application logs.

## Supply chain

Release workflows build the two first-party images, create SBOM and vulnerability-scan artifacts, sign image/chart digests with Cosign, and publish provenance. GitHub Actions are pinned by commit.

Verify the integrity and license of customer model weights, customer base images, external Helm charts, private mirrors, and the target cluster as part of the deployment. Forks that publish their own images update the Kyverno image reference and signing identity so admission accepts them.

## Operator production checklist

- connect gateway and RAG auth to the customer identity boundary;
- source secrets from the customer secret system;
- enable transport encryption where required;
- select and test a kernel-isolation runtime for higher-risk agent workspaces;
- replace bundled stateful services with an appropriate availability and backup design;
- configure log export, retention, chain-head anchoring, alerts, and incident response;
- validate model artifacts, RAG ingestion, tenant binding, and egress rules;
- generate current eval, load, restore, policy, and supply-chain evidence.

## Related documents

- [Threat model](threat-model.md)
- [OWASP Top 10 for LLM Applications 2025 mapping](owasp-llm-top-10-mapping.md)
- [AI governance crosswalk](ai-governance-crosswalk.md)
- [Security policy](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/SECURITY.md)
- [External stores](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/external-managed-stores.md)
- [Guardrails](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/guardrails.md)
