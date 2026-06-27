# Security Policy

This repository is for private AI platform operations and should be treated as security-sensitive infrastructure code.

## Supported Surface

Security fixes should cover:

- inference gateway and RAG service code
- Helm charts and Kubernetes manifests
- GitOps overlays and customer values
- validation, evidence, release, and supply-chain scripts
- model governance, egress, retention, quota, and SLO policy files

## Reporting

Do not open public issues containing secrets, exploit details, customer data, or private prompt content. Report privately to the repository owner and include:

- affected component or path
- reproduction steps
- impact and severity
- whether a credential, model artifact, customer document, or generated evidence file is exposed
- suggested mitigation when known

## Handling Rules

- Store only API-key hashes in configuration.
- Do not log raw prompts, completions, RAG queries, or retrieved private context by default.
- Keep prompt secret detection enabled for coding-agent and tenant workflows.
- Use reviewed egress catalog entries for agent network access.
- Install Python dependencies from hashed lockfiles in local tests and runtime images.
- Promote only images that pass high/critical vulnerability scans, have SBOM evidence, and are signed by digest.

The public threat model is maintained in [docs/threat-model.md](docs/threat-model.md).

## Validation

Before security-sensitive handoff or release review, run:

```bash
make validate-full
make repo-security-scan
make dependency-lock-check
make image-scan
make release-gate-strict
```

If strict release gates fail because current evidence is missing or stale, regenerate the evidence instead of lowering thresholds.
