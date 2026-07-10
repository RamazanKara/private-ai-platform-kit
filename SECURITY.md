# Security Policy

This repository is for private AI platform operations and should be treated as security-sensitive infrastructure code.

## Supported Surface

The latest tagged release and `main` receive security fixes. Pre-1.0 older minor lines are not
maintained; operators should upgrade to the latest release. A coordinated fix may be backported
when a customer cannot upgrade immediately, but that is an explicit exception rather than a
standing support promise.

Security fixes should cover:

- inference gateway and RAG service code
- Helm charts and Kubernetes manifests
- GitOps overlays and customer values
- validation, evidence, release, and supply-chain scripts
- model governance, egress, retention, quota, and SLO policy files

## Reporting

Do not open public issues containing secrets, exploit details, customer data, or private prompt content. Report privately through one of these channels:

- **Primary:** GitHub Private Vulnerability Reporting. Open the repository's **Security** tab and choose **"Report a vulnerability"**.
  <!-- Maintainer: if "Report a vulnerability" is not available, enable Private Vulnerability Reporting in repo Settings -> Security & analysis. -->
- **Alternate:** email [security@fluentorbit.de](mailto:security@fluentorbit.de).

Include:

- affected component or path
- reproduction steps
- impact and severity
- whether a credential, model artifact, customer document, or generated evidence file is exposed
- suggested mitigation when known

We acknowledge reports within 2 business days and aim to provide a triage update within 7 days.

## Coordinated Disclosure

Please do not publicly disclose a vulnerability until a fix is released. We follow a default 90-day disclosure window and will coordinate timing with you.

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
