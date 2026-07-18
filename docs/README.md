# Documentation

The published site is at [ramazankara.github.io/private-ai-platform-kit](https://ramazankara.github.io/private-ai-platform-kit/). This file is a smaller map for browsing the repository on GitHub.

## Setup and deployment

| Need | Document |
| --- | --- |
| First local run | [Local quickstart](quickstart.md) |
| Validation and common operator tasks | [Getting started](getting-started.md) |
| Customer cluster template | [Customer deployment](../deploy/clusters/customer/README.md) |
| Client and SDK examples | [Client examples](client-examples.md) |
| Helm charts | [Chart index](../deploy/charts/README.md) |

## Design and scope

| Need | Document |
| --- | --- |
| Components and request flow | [Architecture](architecture.md) |
| Implemented features and defaults | [Feature inventory](feature-inventory.md) |
| Project fit | [Decision guide](decision-guide.md) |
| Supported boundary | [Scope and non-goals](scope-and-non-goals.md) |
| Design decisions | [Architecture decision records](adr/README.md) |
| Versions tested and pinned | [Version matrix](version-matrix.md) |

## Production and security review

| Need | Document |
| --- | --- |
| Control and validation matrix | [Production readiness](production-readiness.md) |
| Security defaults and limitations | [Security overview](security-overview.md) |
| Threats and residual risk | [Threat model](threat-model.md) |
| OWASP LLM Top 10 for 2025 | [OWASP mapping](owasp-llm-top-10-mapping.md) |
| Governance framework crosswalk | [AI governance crosswalk](ai-governance-crosswalk.md) |
| Release artifacts | [Release verification](release-verification.md) |
| Current versus sample evidence | [Evidence and validation](proof.md) |

## Operations

The [runbook index](../runbooks/README.md) covers API access, upgrades, incidents, budgets, RAG, GPU capacity, backups, data stores, and release gates. Runbooks stay at the repository root because alert annotations link to those paths; the docs build mirrors them into the site.

Repository policies live in [Contributing](../CONTRIBUTING.md), [Security](../SECURITY.md), and [Governance](../GOVERNANCE.md).
