# Threat Model

This threat model covers the local lab and customer-owned Kubernetes deployment pattern.

## Assets

- User prompts, completions, RAG queries, and retrieved private context.
- API keys, secret hashes, model-pull credentials, and customer secret-backend references.
- Model artifacts, provenance records, eval results, and release evidence.
- Agent workspace PVC data, tenant manifests, and approved egress policy.
- Gateway audit logs, metrics, and trace identifiers.

## Trust Boundaries

- External callers to gateway and RAG service business endpoints.
- In-cluster platform services and runtime namespaces.
- Tenant and coding-agent namespaces.
- Customer secret manager and External Secrets.
- Model registries, package mirrors, Git hosts, and other approved egress destinations.

## Primary Risks

- Raw prompt or retrieved customer context leaks into logs or evidence.
- Unapproved model IDs bypass the model catalog and gateway allowlist.
- A tenant or agent workspace reaches unapproved network destinations.
- Runtime images or dependencies are promoted with high or critical vulnerabilities.
- Sample evidence is mistaken for current production proof.
- Budget exhaustion is treated as a client error instead of capacity/rate exhaustion.

## Current Controls

- API-key hash authentication for business endpoints.
- Prompt and query audit redaction with length and SHA-256 fingerprints.
- Model allowlists, admission limits, prompt secret detection, and sandbox budgets.
- Default-deny NetworkPolicies and catalog-backed external egress.
- Pinned runtime images, hashed Python lockfiles, SBOMs, Trivy scans, and Cosign signing.
- Strict release gates that require current evidence.

## Required Customer Hardening

- Wire API-key hashes or OIDC/JWT validation to the enterprise identity boundary, with RS256 or ES256 preferred for customer IdPs.
- Replace source-reference model digests with customer model-store digests.
- Run strict gates with current evidence before production handoff.
- Review RAG document ingestion, retention class, and vector collection access.
- Align SLO, quota, budget, backup, and incident-response settings to customer policy.
