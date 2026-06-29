# Governance

Private AI Platform Kit is maintained as an operational platform project. The project is maintained by fluentorbit (https://fluentorbit.de) as its steward. Maintainers optimize for secure, reproducible customer-owned Kubernetes handoff.

## Maintainer Authority

Maintainers listed in [MAINTAINERS.md](MAINTAINERS.md) can triage issues, review pull requests, cut releases, update dependencies, and decide whether a change fits the project scope.

## Change Requirements

- Changes to gateway, RAG, Helm charts, GitOps overlays, policies, or release controls require tests or validation updates.
- Public API changes must update `platform/api-contracts/`.
- Runtime configuration changes must update `platform/config-contracts/`.
- Customer-facing operational changes must update README, docs, or runbooks.
- Security-sensitive changes should include evidence from `make validate`, and when relevant `make validate-full`, `make image-scan`, or `make release-gate-strict`.

## Decision Process

Small compatible changes can merge after maintainer review and passing checks. Large changes should start with an issue that states the problem, affected users, compatibility impact, test plan, and rollback path.

## Release Process

Releases should include current validation evidence, changelog entries, image scan evidence, SBOMs, checksums, and signed immutable image digests.

## Continuity

fluentorbit (https://fluentorbit.de) is the appointing organization for maintainer succession and for coordinating security response if the current maintainer is unavailable.
