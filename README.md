# Private AI Platform Kit

[![CI](https://github.com/RamazanKara/private-ai-platform-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/RamazanKara/private-ai-platform-kit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/RamazanKara/private-ai-platform-kit)](https://github.com/RamazanKara/private-ai-platform-kit/releases)
[![Docs](https://img.shields.io/badge/docs-latest-0b7285)](https://ramazankara.github.io/private-ai-platform-kit/)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/RamazanKara/private-ai-platform-kit/badge)](https://scorecard.dev/viewer/?uri=github.com/RamazanKara/private-ai-platform-kit)
[![License](https://img.shields.io/github/license/RamazanKara/private-ai-platform-kit)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21039342.svg)](https://doi.org/10.5281/zenodo.21039342)

**Self-hosted LLM gateway, retrieval, and coding-agent workspaces for Kubernetes, with a
verifiable record of every model call and agent action.**

When a team asks to run coding agents, the security review asks three questions: where does
the generated code run, what can it reach, and can you prove afterwards what it did? This kit
answers them with running code on infrastructure you control. Models run on Ollama or vLLM
inside your cluster. Agents run in hardened workspaces behind default-deny egress. Every
request passes one governance path in the gateway and leaves a hash-chained receipt that an
auditor can verify offline.

It is built for platform and security teams who need OpenAI- and Anthropic-compatible APIs on
their own hardware, and who answer to an auditor, a works council, or a regulator for how
those APIs are used.

<p align="center">
  <img src="docs/assets/compose-demo.gif" alt="Recorded terminal run of make compose-smoke: a governed chat completion, streaming, a blocked credential, a refused model, a missing key, an agent receipt, tenant-scoped retrieval, usage and cost, the audit chain verified, and an edited receipt detected" width="100%">
</p>

## Try it in five minutes

You need Docker with Compose. No Kubernetes, no GPU.

```bash
git clone https://github.com/RamazanKara/private-ai-platform-kit.git
cd private-ai-platform-kit
make compose-up      # gateway, Ollama with a 0.5B model, and the RAG service (about two minutes)
make compose-smoke   # the walkthrough recorded above
```

Nothing in the recording is staged. `compose-smoke` sends real requests and stops at the first
one that does not behave: a credential in a prompt is blocked before the model sees it, a model
outside the allowlist is refused, an agent's denied egress attempt lands on the same audit
chain as the model calls, and an edited receipt fails verification.

Then point any OpenAI client at the gateway:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="local-development-only")
reply = client.chat.completions.create(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Write a haiku about audit logs."}],
)
print(reply.choices[0].message.content)
```

The Anthropic SDK works the same way against `http://127.0.0.1:8080`. For a chat UI, add
`--profile ui` to the Compose command and open <http://127.0.0.1:3000>: Open WebUI runs offline
and talks only to the governed gateway. `make compose-down` removes everything.

## What you get

**One governance path for every API.** OpenAI chat, completions, embeddings, moderations,
Files, Batch, and Responses, plus Anthropic Messages with streaming, all pass the same checks:
API-key or JWT authentication bound to a sandbox, model allowlists, admission limits,
per-sandbox budgets settled against measured token usage, rate limits, prompt-secret blocking
or redaction, and an output guardrail. An agent cannot shop for the endpoint with weaker rules,
because there isn't one.

**Receipts an auditor can verify.** Each model call, retrieval, and
reported agent action becomes a redacted record on a SHA-256 hash chain; prompts are
fingerprinted, never stored in clear. `make audit-verify` detects edited, reordered, and
deleted records. Chains link across restarts, so a missing process lifetime shows up as well,
and head anchors catch truncation of the newest records.

**Coding agents in a box they cannot leave.** Workspaces are hardened
[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) pods: non-root,
read-only root filesystem, no ambient credentials, and short-lived audience-bound tokens.
Egress is denied by default, and every exception is a reviewed catalog entry with an expiry
date. Kyverno policies reject a workspace that drops any of this.

**Models on your hardware.** Ollama for laptops and CPU nodes, vLLM for NVIDIA and AMD GPUs,
from the same charts, with failover, canary, and shadow routing between them. The RAG service
isolates tenants by default and runs on a local lexical index or on Qdrant.

**Delivery an auditor can check.** Helm charts and Argo CD applications, cosign-signed images
with SBOMs and build provenance, an OpenSSF Scorecard, and generated evidence packs for SLOs,
quotas, retention, egress, and model provenance. Controls are mapped to the OWASP LLM Top 10,
the NIST AI RMF, the EU AI Act, and ISO/IEC 42001.

**Cheap enough to leave on.** In the [benchmark behind the accompanying paper](paper/PAPER.md),
the full governance path added about 0.3 ms at the median compared with the same gateway with
governance switched off.

## How it compares

This kit does not replace every gateway. Choose by the problem you have:

| If you mainly need | Look at |
| --- | --- |
| One API in front of many hosted model providers, with spend tracking | LiteLLM, Portkey |
| AI features for an API gateway you already operate | Kong AI Gateway, Envoy AI Gateway |
| Model serving and autoscaling on Kubernetes | KServe, KubeAI, the vLLM production stack |
| A chat interface for people | Open WebUI, which runs on top of this kit |
| Self-hosted models **and** coding agents on your own cluster, with every call and agent action on a record you can verify | **this kit** |

The [decision guide](docs/decision-guide.md) covers the tradeoffs, including when this kit is
the wrong choice.

## Run it on Kubernetes

**Local lab.** On Linux or WSL with Docker, Python 3.12+, and `curl`, one command installs
pinned `kind`, `kubectl`, and Helm into `.tools/bin` and builds the full platform: Argo CD,
Kyverno, the gateway, Ollama, RAG, and the agent workspaces.

```bash
make bootstrap          # or `make quickstart` if kind, kubectl, and Helm are installed
make agent-sandbox-demo # a real coding agent in a hardened workspace, with receipts
make local-down
```

**Customer-owned clusters.** Install the signed umbrella chart from GHCR, or render the Argo CD
overlay for a GitOps deployment with GPU profiles:

```bash
helm install private-ai oci://ghcr.io/ramazankara/private-ai-platform-kit/charts/platform \
  --version 0.29.0 --namespace ai-platform --create-namespace

make customer-overlay CUSTOMER_REPO_URL=https://github.com/<you>/<fork>.git \
  CUSTOMER_REVISION=v0.29.0 CUSTOMER_GPU_PROFILE=nvidia
```

The [quickstart](docs/quickstart.md) lists what each path installs and how long it takes, and
the [customer deployment guide](deploy/clusters/customer/README.md) covers what your cluster
must provide.

## Status

The current release is `v0.29.0`. It is ready for evaluation and platform engineering work and
is tested on every pull request: unit and contract tests, chart rendering, policy tests, a
`kind` cluster end to end, and the Compose walkthrough. It is not a managed service. A
production deployment on customer-owned clusters still needs your identity provider, secret
backend, ingress, storage, observability, backups, and capacity planning; the
[production readiness matrix](docs/production-readiness.md) lists each item and who owns it.

## Documentation

| Task | Start here |
| --- | --- |
| Try it, then run the local lab | [Quickstart](docs/quickstart.md) |
| Call it from OpenAI, Anthropic, or Python clients | [Client examples](docs/client-examples.md), [Python SDK](sdk/python/README.md) |
| Decide whether it fits | [Decision guide](docs/decision-guide.md), [Feature inventory](docs/feature-inventory.md) |
| Understand the components | [Architecture](docs/architecture.md) |
| Review security | [Security overview](docs/security-overview.md), [Threat model](docs/threat-model.md) |
| Deploy to your cluster | [Customer deployment](deploy/clusters/customer/README.md), [Model selection](docs/model-selection.md) |
| Operate it | [Runbooks](runbooks/README.md) |
| Verify a release | [Release verification](docs/release-verification.md) |
| Change the code | [Developer workflow](docs/development.md), [Repository map](docs/repository-map.md), [Contributing](CONTRIBUTING.md) |

The full documentation is published at <https://ramazankara.github.io/private-ai-platform-kit/>.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | [Inference gateway](src/inference-gateway/README.md) and [RAG service](src/rag-service/README.md) |
| `sdk/` | [Python client](sdk/python/README.md) |
| `deploy/` | Helm charts, cluster profiles, Argo CD, Kyverno policies, the Compose stack, and agent workspaces |
| `platform/` | API and configuration contracts, model catalog, evals, and SLO inputs |
| `tenants/` | Tenant onboarding specifications and examples |
| `runbooks/` | Operational procedures |
| `scripts/` | [Validation, setup, and evidence tooling](scripts/README.md) |
| `docs/` | Documentation site and architecture decision records |
| `paper/` | Research harness and recorded results |
| `chaos/`, `loadtest/`, `results/` | Resilience drills, load scenarios, and sample report shapes |

Files under `results/` named `sample-*` show report formats only; strict release checks require
freshly generated evidence.

## Contributing

Issues and pull requests are welcome. `make test` and `make quality` run without a cluster;
[CONTRIBUTING.md](CONTRIBUTING.md) covers the rest. Report vulnerabilities privately as
described in [SECURITY.md](SECURITY.md).

Licensed under Apache-2.0. Kubernetes is a registered trademark of The Linux Foundation; this
project is not affiliated with or endorsed by The Linux Foundation.
