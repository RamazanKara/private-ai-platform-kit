# Architecture and deployment profiles

The repository has a local profile, a customer-cluster profile, and tenant onboarding examples. They share the first-party services and Helm charts, but they do not install the same Argo CD application set and they do not provide the same security or availability guarantees.

Use the [feature inventory](feature-inventory.md) for endpoint and default status, and the [threat model](threat-model.md) for trust boundaries.

## Core request path

![Platform architecture](assets/architecture.svg)

The inference gateway is the entry point for model API traffic. A normal request passes through:

1. API-key or bearer-token authentication;
2. sandbox identity binding, when the key record or token supplies one;
3. model allowlist, admission, and input-secret checks;
4. rate and budget accounting;
5. routing to Ollama or vLLM;
6. the optional output guardrail;
7. metrics and a redacted audit event.

The exact HTTP surface is generated into [`platform/api-contracts/inference-gateway.openapi.json`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/api-contracts/inference-gateway.openapi.json). Not every route performs model inference, and optional controls only apply when enabled in the active values.

RAG is a separate service. It returns retrieved passages and grounded message objects; it does not automatically intercept gateway calls. The local profile uses the checked-in lexical corpus. The customer values select Qdrant and an embedding endpoint.

## Components

| Component | Namespace | Source | Notes |
| --- | --- | --- | --- |
| Inference gateway | `inference` | `src/inference-gateway`, `deploy/charts/inference-gateway` | First-party API and policy point |
| RAG service | `rag` | `src/rag-service`, `deploy/charts/rag-service` | Lexical or Qdrant retrieval |
| Budget Redis | `budget` | `deploy/charts/budget-redis` | Single-node reference store by default |
| Qdrant | `vector` | `deploy/charts/qdrant-vector-store` | Single-instance chart; customer values enable persistence |
| Ollama | `ollama` | `deploy/charts/ollama` | Local default and a customer chat route |
| vLLM | `vllm` | `deploy/charts/vllm` | Customer generation and embedding profiles |
| Agent workspace | tenant namespace | `deploy/charts/agent-workspace` | Creates `agents.x-k8s.io` sandbox resources and namespace controls |
| Agent-sandbox controller | `agent-sandbox-system` | `deploy/vendor/agent-sandbox` | Cluster-scoped prerequisite |
| Policies and catalog | cluster/inference | `deploy/policies`, `platform/model-catalog` | Admission policy and approved model records |

The data plane uses plaintext HTTP by default. NetworkPolicy restricts reachability but does not encrypt traffic. The customer must supply transport encryption where it is required.

## Local profile

![Local profile](assets/architecture-local.svg)

`make quickstart` creates a single-node `kind` cluster with Calico, builds the gateway and RAG images locally, installs the vendored agent-sandbox controller, and deploys the local application set.

The local profile uses:

- Ollama with `qwen2.5:0.5b` for gateway smoke tests;
- a public development API key;
- lexical RAG data from the repository;
- a bundled Redis and Qdrant footprint;
- local-path storage and single-node availability.

The default Argo CD path includes platform operators, observability, policies, cost controls, and backup examples. `QUICKSTART_DIRECT_APPLY=1` is intentionally smaller: it applies the core runtime charts directly and omits those add-ons.

The local path needs network access for downloads and image/model pulls. It keeps inference requests on the local cluster after those components are installed, but it is not an air-gapped installation procedure.

## Customer profile

![Customer profile](assets/architecture-customer.svg)

The customer profile assumes that the cluster, Argo CD, ingress, secret integration, observability, and backup systems already exist. Its Argo CD application list is deliberately smaller than the local list and does not install the local observability, cost-control, platform-operator, or Velero applications.

The checked-in customer values deploy both Ollama and vLLM routes. They also add a separate vLLM embedding service and configure RAG to use Qdrant. The NVIDIA example requests four GPUs per vLLM replica and keeps at least two replicas when KEDA is enabled. Those values describe a large reference configuration, not a minimum or recommendation.

Before deployment, the operator must at least:

- configure a reachable, immutable Git revision;
- install and configure the required operators, including External Secrets if those manifests are used;
- replace identity and secret placeholders;
- select storage classes and backup targets;
- choose model artifacts and verify their provenance;
- size GPU count, context length, replicas, and persistent volumes;
- connect metrics, logs, alerts, ingress, and transport encryption.

See [the customer deployment guide](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/README.md).

## `regulated-offline` tenant example

![Restricted-egress tenant](assets/architecture-regulated-offline.svg)

`tenants/onboarding/regulated-offline-coding-agents.yaml` is a tenant policy example. It renders a namespace with no external CIDR egress and allows only DNS plus the in-cluster gateway and RAG service.

The profile name does not make the cluster air-gapped. It does not control image pulls, model downloads, Argo CD, the gateway namespace, the identity provider, or other cluster services. An offline deployment also needs private registries and mirrors, preloaded model weights, internal Git and identity endpoints, and cluster-wide egress controls.

Use the [restricted-egress tenant walkthrough](regulated-offline-tenant-example.md) to render and inspect the manifests.

## Stateful and failure boundaries

The bundled Redis, Qdrant, and Loki configurations are development/reference footprints. Redis has no persistence in the bundled chart, Qdrant is a single instance, and the local observability stack is not an HA logging service. The [external stores runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/external-managed-stores.md) describes the handoff path.

The gateway audit chain is per process/replica. Export records and store chain-head anchors outside the gateway if the log is intended as tamper or rollback evidence.
