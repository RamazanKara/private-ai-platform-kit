# Version and Compatibility Matrix

This page records the versions this kit is **pinned to and tested against** for the `v0.14.0`
release. It is a compatibility reference, not a support SLA: the tables list what the kit ships
and what CI exercises, so you can reproduce a known-good state and reason about drift. Newer or
older versions may work but are outside what the release was validated on.

Every version below is extracted from the repository at the tag: chart `Chart.yaml` /
`values.yaml`, the Argo CD `Application` manifests, the CI workflow, and the validation toolchain
descriptor. Where the kit deliberately delegates a version to the operator (the customer's
Kubernetes distribution, ingress, storage, secrets, and GPU stack), that is called out explicitly.

## Kit release

| Item | Version | Source |
| --- | --- | --- |
| Private AI Platform Kit | `v0.14.0` | [README.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/README.md) |
| Helm chart version (all first-party charts) | `0.14.0` | `deploy/charts/*/Chart.yaml` |
| `kubeVersion` constraint (all charts) | `>=1.25.0` | `deploy/charts/*/Chart.yaml` |

Maturity is a reference implementation and customer lab; a production handoff still requires current
strict evidence, customer identity/secrets integration, capacity sizing, and backup validation. See
[Production readiness](production-readiness.md).

## First-party kit components

The two application services (inference gateway, RAG service) are built from source in this repo;
their `appVersion` tracks the kit release and their images are published to GHCR and digest-pinned by
release CI. The runtime and datastore charts wrap upstream images and pin both a documentation tag
and an immutable manifest-list digest in `values.yaml`.

| Component | Chart `appVersion` | Image | Tag | Notes |
| --- | --- | --- | --- | --- |
| Inference gateway | `0.14.0` | `ghcr.io/ramazankara/private-ai-platform-kit/inference-gateway` | `v0.14.0` | First-party; release CI pins the published digest. |
| RAG service | `0.14.0` | `ghcr.io/ramazankara/private-ai-platform-kit/rag-service` | `v0.14.0` | First-party; release CI pins the published digest. |
| Ollama runtime | `0.24.0` | `ollama/ollama` | `0.24.0` | Default local-first LLM runtime; digest-pinned. |
| vLLM runtime | `0.22.0` | `vllm/vllm-openai` | `v0.22.0` | GPU/production-style OpenAI-compatible runtime; digest-pinned. |
| Qdrant vector store | `1.18.1` | `qdrant/qdrant` | `v1.18.1` | Optional vector-RAG profile; single-instance; digest-pinned. |
| Budget Redis | `8.0` | `redis` | `8.0-alpine` | Shared sandbox budget accounting store; digest-pinned. |
| Agent workspace | `0.14.0` | (namespace/RBAC template — no image) | — | Tenant namespace scaffold; no workload image of its own. |
| Platform (umbrella) | `0.14.0` | (aggregates the charts above) | — | Single-command dev/demo bring-up; GitOps remains recommended for multi-namespace installs. |

The first-party service container images are built on `python:3.14-alpine`
(`src/inference-gateway/Dockerfile`, `src/rag-service/Dockerfile`), digest-pinned in the Dockerfiles.

## Platform add-ons (Argo CD Applications)

These are third-party operators and observability components installed as Argo CD `Application`
resources. Versions are the chart `targetRevision` pinned in the manifests. The observability stack
is defined once in `deploy/observability/applications.yaml` and referenced by both cluster overlays;
the operator and cost/backup add-ons are pinned per overlay in `deploy/clusters/*/apps.yaml`.

| Add-on | Chart | `targetRevision` | Repository | Defined in |
| --- | --- | --- | --- | --- |
| kube-prometheus-stack | `kube-prometheus-stack` | `66.2.1` | prometheus-community | `deploy/observability/applications.yaml` |
| Grafana Tempo | `tempo` | `1.23.3` | grafana | `deploy/observability/applications.yaml` |
| Grafana Loki | `loki` | `6.24.0` | grafana | `deploy/observability/applications.yaml` |
| Promtail | `promtail` | `6.16.6` | grafana | `deploy/observability/applications.yaml` |
| Prometheus Pushgateway | `prometheus-pushgateway` | `2.15.0` | prometheus-community | `deploy/observability/applications.yaml` |
| Kyverno | `kyverno` | `3.2.7` | kyverno | `deploy/clusters/local/apps.yaml`, `deploy/clusters/customer/apps.yaml` |
| KEDA | `keda` | `2.16.1` | kedacore | `deploy/clusters/local/apps.yaml`, `deploy/clusters/customer/apps.yaml` |
| External Secrets Operator | `external-secrets` | `0.11.0` | external-secrets | `deploy/clusters/local/apps.yaml`, `deploy/clusters/customer/apps.yaml` |
| OpenCost | `opencost` | `1.41.0` | opencost | `deploy/clusters/local/apps.yaml` |

Notes:

- Kyverno, KEDA, and External Secrets are grouped into a single `platform-operators` Application
  (multi-source) in both the local and customer overlays.
- OpenCost is pinned in the **local** overlay only (`cost-controls` Application); the customer overlay
  leaves cost tooling to the operator's chargeback stack.
- The kyverno `3.2.7` value is the **Helm chart** version. The Kyverno **CLI** used for
  policy-as-code tests is pinned separately in the toolchain (see below).
- Velero-based backup (`backup-velero`) and the restore-drill workload are shipped from in-repo
  manifests rather than a pinned third-party chart.
- The agent-sandbox workspace runtime is installed from vendored, checksummed release manifests
  (`deploy/vendor/agent-sandbox/`) via `make agent-sandbox-install` rather than a chart — there is
  no upstream Helm chart. See ADR 0009 and the row below.

## Runtime, Kubernetes, and toolchain

| Item | Version | Where it is pinned / tested | Notes |
| --- | --- | --- | --- |
| Kubernetes (chart floor) | `>=1.25.0` | `kubeVersion` in every `Chart.yaml` | Minimum the charts declare compatible. |
| Kubernetes (tested node image) | `kindest/node:v1.31.4` | `.github/workflows/ci.yml` `local-e2e` (`LOCAL_KIND_NODE_IMAGE`) | The single Kubernetes version the end-to-end job actually spins up. Customer clusters bring their own conformant Kubernetes. |
| Python (CI + images) | `3.14` | `.github/workflows/ci.yml`, `src/*/Dockerfile` | CI runs on 3.14; service images are `python:3.14-alpine`. |
| Python (documented local minimum) | `3.12+` | [quickstart.md](quickstart.md), [getting-started.md](getting-started.md) | Minimum for running local validation tooling. The validation-toolchain install hint recommends 3.14 or newer. |
| Python (SDK) | `>=3.11` | [sdk/python/pyproject.toml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/sdk/python/pyproject.toml) | `requires-python` for the client SDK package. |
| Helm | v3 | [validation-toolchain.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/tools/validation-toolchain.yaml); `azure/setup-helm` in CI | CI uses the action default; the toolchain requires Helm 3. |
| Go | `1.26` | `.github/workflows/ci.yml`; toolchain install hint | Builds Go-based validation utilities (kubeconform, Kyverno CLI, restore-drill). |
| agent-sandbox controller | `v0.5.0` | `deploy/vendor/agent-sandbox/` (SHA-256 in the vendor README) | Standard coding-agent workspace runtime (ADR 0010, platform prerequisite); CRDs `agents.x-k8s.io/v1beta1` + `extensions.agents.x-k8s.io/v1beta1`. `v1beta1` API — re-verify spec fields on upgrade. |
| Calico (optional local CNI) | `v3.29.1` | `CALICO_VERSION` in `scripts/local-up.sh` | Opt-in NetworkPolicy-enforcing CNI for the local lab (`LOCAL_CNI=calico`); default remains kindnet (non-enforcing). |

## Validation toolchain (pinned tool versions)

These are the tool versions the validation and evidence pipeline installs and runs
(`platform/tools/validation-toolchain.yaml`, defaults installed by
[scripts/install-validation-tools.sh](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/scripts/install-validation-tools.sh)).
Each is overridable via the listed environment variable.

| Tool | Default version | Override env | Purpose |
| --- | --- | --- | --- |
| kubeconform | `v0.7.0` | `KUBECONFORM_VERSION` | Kubernetes YAML schema validation. |
| Kyverno CLI | `v1.18.1` | `KYVERNO_VERSION` | Policy-as-code tests (labels, pod security, resources, image tags, signatures). |
| restore-drill | `v1.0.1` | `RESTORE_DRILL_VERSION` | Backup/restore-drill config validation and restore evidence. |
| k6 | `v2.0.0` | `K6_VERSION` | Gateway load-test scenarios. |
| Syft | `v1.44.0` | `SYFT_VERSION` | Filesystem and image SBOM generation. |
| Argo CD CLI | `v3.4.3` | `ARGOCD_VERSION` | GitOps client compatibility checks. |
| Cosign | `v3.0.6` | `COSIGN_VERSION` | Image / Helm OCI artifact signature verification. |
| Trivy | `v0.70.0` | `TRIVY_VERSION` | Filesystem, secret, config, and image vulnerability scanning. |

## What the kit does not pin

Consistent with the kit boundary, the following are the operator's responsibility and are
intentionally not pinned here:

- The customer's Kubernetes distribution and its exact minor version (any conformant cluster at or
  above the chart floor).
- Ingress controllers, `StorageClass` / CSI drivers, and load balancers.
- Secret backends and identity providers (the External Secrets *operator* is pinned; the backing
  secret store is not).
- GPU drivers, device plugins, and accelerator runtimes for NVIDIA / AMD nodes.
- Served model weights and revisions (governed via
  [model provenance](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/governance/model-provenance.yaml)
  and the model catalog, not this matrix).

See [Scope and non-goals](scope-and-non-goals.md) for the full boundary and
[Release verification](release-verification.md) for how to verify the published artifacts against
these pins.
