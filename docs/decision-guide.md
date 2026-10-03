# Decision guide

This project is a reasonable fit when the team already owns Kubernetes and wants the gateway, retrieval, workspace, policy, and validation pieces kept in one repository.

## Good fit

Consider it when all of these are true:

- model traffic must run through a customer-controlled Kubernetes environment;
- a platform team can own Helm, Argo CD, networking, storage, secrets, and on-call work;
- Ollama is useful for the local path and vLLM matches the intended GPU runtime;
- coding-agent workspaces need namespace, RBAC, quota, and egress controls;
- API/config contracts and repeatable validation reports are useful handoff artifacts;
- the team is willing to adapt the reference values rather than deploy them unchanged.

## Poor fit

Use a smaller or managed solution when the main need is:

- one model on one machine;
- a hosted API with managed identity, billing, and support;
- cloud infrastructure provisioning;
- distributed training;
- broad provider routing or full upstream API compatibility;
- a production platform without a Kubernetes operations team.

If you only need model serving, deploy the chosen runtime directly. If you only need an API gateway, use a gateway product. This repository is useful when the integration and operating model are the point.

## Compared with other tools

Most adjacent projects solve one layer of this stack well. Pick by the problem you mainly have:

| If you mainly need | Look at | How it relates to this kit |
| --- | --- | --- |
| One API in front of many hosted model providers, with spend tracking | LiteLLM, Portkey | Broader provider coverage. This kit governs self-hosted runtimes and agent workspaces, and records each call on a verifiable hash chain. |
| AI features for an API gateway you already operate | Kong AI Gateway, Envoy AI Gateway | Keep your edge gateway; place this kit's gateway behind it when you need sandbox budgets, agent receipts, and the audit chain. |
| Model serving and autoscaling on Kubernetes | KServe, KubeAI, the vLLM production stack | Serving layers. This kit uses vLLM and Ollama directly and adds the governance, retrieval, and workspace layers around them. |
| A chat interface for people | Open WebUI | Complementary: the Compose stack's `ui` profile runs Open WebUI against the governed gateway. |
| Coding agents that execute generated code | agent-sandbox on its own | This kit runs agents in hardened agent-sandbox workspaces and adds egress catalogs, policy, and receipts. |

The kit is the right choice when you need several of these layers together, on your own
cluster, with one record that shows what every model call and agent action did.

## Questions to answer before a trial

1. Which models and exact artifact revisions will be served?
2. Does the target cluster have enough GPU memory, storage, and a NetworkPolicy-capable CNI?
3. Who supplies ingress, identity, secrets, TLS, observability, backups, and incident response?
4. How will tenant identity be bound to `X-Sandbox-ID` at both the gateway and RAG service?
5. Which egress destinations are required by agents, image pulls, model downloads, Git, and package mirrors?
6. Which current eval, load, restore, and security evidence is required before handoff?

The [capacity worksheet](capacity-sizing.md), [customer deployment guide](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/README.md), and [production readiness matrix](production-readiness.md) cover those decisions.

## Maturity

The local path is an executable lab. The customer path is a template. The bundled Redis, Qdrant, and Loki footprints are not HA services, transport encryption is off by default, and the checked-in customer values contain integration placeholders. Treat a production deployment as its own engineering and acceptance project.
