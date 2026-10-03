# Private AI Platform Kit

**Self-hosted LLM gateway, retrieval, and coding-agent workspaces for Kubernetes, with a
verifiable record of every model call and agent action.**

Models run on Ollama or vLLM inside your cluster. Agents run in hardened workspaces behind
default-deny egress. Every request passes one governance path in the gateway and leaves a
hash-chained receipt that an auditor can verify offline. The kit is built for platform and
security teams who need OpenAI- and Anthropic-compatible APIs on their own hardware and must
be able to show how those APIs were used.

![Recorded terminal run of make compose-smoke: governed requests, blocked prompts, an agent receipt, and the audit chain verified](assets/compose-demo.gif)

## Try it in five minutes

You need Docker with Compose. No Kubernetes, no GPU.

```bash
git clone https://github.com/RamazanKara/private-ai-platform-kit.git
cd private-ai-platform-kit
make compose-up && make compose-smoke
```

The second command is the walkthrough recorded above. The [quickstart](quickstart.md) explains
each step and the local Kubernetes lab that follows it.

## What you get

<div class="grid cards" markdown>

-   :material-shield-check-outline:{ .lg .middle } **One governance path**

    ---

    OpenAI chat, completions, embeddings, Files, Batch, and Responses, plus Anthropic Messages
    with streaming, all pass the same authentication, model allowlist, budget, and guardrail
    checks.

    [:octicons-arrow-right-24: Feature inventory](feature-inventory.md)

-   :material-link-lock:{ .lg .middle } **Receipts you can verify**

    ---

    Model calls, retrievals, and agent actions land on a SHA-256 hash chain that detects edited,
    reordered, and missing records. Prompts are fingerprinted, never stored in clear.

    [:octicons-arrow-right-24: Audit chain runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/audit-chain.md)

-   :material-robot-outline:{ .lg .middle } **Agents in a hardened box**

    ---

    Coding agents run in agent-sandbox workspaces: non-root, read-only root filesystem,
    short-lived credentials, and egress denied unless a reviewed catalog entry allows it.

    [:octicons-arrow-right-24: Agent-sandbox integration](agent-sandbox-integration.md)

-   :material-server-network:{ .lg .middle } **Models on your hardware**

    ---

    Ollama for laptops and CPU nodes, vLLM for NVIDIA and AMD GPUs, with failover, canary, and
    shadow routing. Tenant-isolated RAG on a lexical index or Qdrant.

    [:octicons-arrow-right-24: Model selection](model-selection.md)

-   :material-source-branch-check:{ .lg .middle } **Delivery an auditor can check**

    ---

    Helm and Argo CD, signed images with SBOMs and provenance, OpenSSF Scorecard, and evidence
    packs mapped to the OWASP LLM Top 10, NIST AI RMF, EU AI Act, and ISO/IEC 42001.

    [:octicons-arrow-right-24: Release verification](release-verification.md)

-   :material-scale-balance:{ .lg .middle } **Is it a fit?**

    ---

    How the kit compares with LiteLLM, Kong, KServe, and Open WebUI, when it is the wrong
    choice, and the questions to answer before a trial.

    [:octicons-arrow-right-24: Decision guide](decision-guide.md)

</div>

## Where to go next

| You want to | Read |
| --- | --- |
| Call the gateway from OpenAI, Anthropic, or agent frameworks | [Client examples](client-examples.md) |
| Understand how the pieces fit | [Architecture](architecture.md) |
| Review the security model | [Security overview](security-overview.md) and [threat model](threat-model.md) |
| Deploy to your own cluster | [Customer handoff example](customer-handoff-example.md) and [production readiness](production-readiness.md) |
| Operate it | [Runbooks](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/README.md) |
| Work on the code | [Developer workflow](development.md) and [repository map](repository-map.md) |

The current release is `v0.29.0`: ready for evaluation and platform engineering work, not a
managed service. A production deployment still needs your identity provider, secrets, ingress,
storage, observability, backups, and capacity planning, as listed in the
[production readiness matrix](production-readiness.md).
