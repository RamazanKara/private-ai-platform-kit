# Governance

Private AI Platform Kit is maintained as an operational platform project. The project is maintained by fluentorbit (https://fluentorbit.de) as its steward. Maintainers optimize for secure, reproducible customer-owned Kubernetes handoff.

## Maintainer Authority

Maintainers listed in [MAINTAINERS.md](MAINTAINERS.md) can triage issues, review pull requests, cut releases, update dependencies, and decide whether a change fits the project scope.

Contributors become reviewers after a sustained record of accurate reviews or merged changes in
an area. Reviewers become maintainers through a public nomination issue, at least seven calendar
days for community feedback, and unanimous approval from the active maintainers. When only one
maintainer is active, the steward must also approve the nomination. The same process, with a stated
reason and transition plan, applies to removal; inactivity for six months may move a maintainer to
emeritus status. Emeritus maintainers retain attribution but no merge or release authority.

## Change Requirements

- Changes to gateway, RAG, Helm charts, GitOps overlays, policies, or release controls require tests or validation updates.
- Public API changes must update `platform/api-contracts/`.
- Runtime configuration changes must update `platform/config-contracts/`.
- Customer-facing operational changes must update README, docs, or runbooks.
- Security-sensitive changes should include evidence from `make validate`, and when relevant `make validate-full`, `make image-scan`, or `make release-gate-strict`.

## Decision Process

Small compatible changes can merge after maintainer review and passing checks. Large changes should start with an issue that states the problem, affected users, compatibility impact, test plan, and rollback path.

Architecture, governance, security-boundary, and compatibility decisions use lazy consensus: a
proposal remains open for at least seven days, objections must include a concrete risk or
alternative, and the decision is recorded in an ADR or issue. Security incidents may use an
expedited private decision; the rationale is published after coordinated disclosure. A maintainer
does not approve their own security-sensitive or release-control change when another active
maintainer is available. If consensus fails, the steward makes and records the final decision.

## Release Process

Releases should include current validation evidence, changelog entries, image scan evidence, SBOMs, checksums, and signed immutable image digests.

The target cadence is one reviewed maintenance release per month when releasable changes exist,
with quarterly roadmap and dependency-support reviews. Empty calendar releases are not created.
Critical security fixes ship as soon as coordinated disclosure permits. Release tags are immutable;
a bad release is superseded by a new patch version rather than retagged. The release checklist and
distribution channels are documented in [Distribution](docs/distribution.md).

## Support and Compatibility

The latest release and `main` receive fixes. Before 1.0, only the latest minor line is actively
supported; release notes must call out breaking changes and migrations. Published images, charts,
SDK artifacts, and documentation use the same version. Community support is best effort through
issues; security reports follow [SECURITY.md](SECURITY.md).

## Continuity

fluentorbit (https://fluentorbit.de) is the appointing organization for maintainer succession and for coordinating security response if the current maintainer is unavailable.
