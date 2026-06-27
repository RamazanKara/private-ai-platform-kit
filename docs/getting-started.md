# Getting Started

This guide runs the local lab first, then points to the customer-owned Kubernetes path. The local path is intentionally close to the customer path: the same charts, policies, runbooks, and validation checks are used in both places.

For a shorter first evaluation path, start with [Quickstart](quickstart.md):

```bash
make quickstart
```

## Prerequisites

Local validation needs:

- Python 3
- Docker
- kind
- kubectl
- Helm
- Go
- Syft

Full validation also uses Argo CD CLI, Cosign, Trivy, k6, kubeconform, and the Kyverno CLI.

Check what is already installed:

```bash
make toolchain-doctor
```

Install the optional validation CLIs into `.tools/bin`:

```bash
make toolchain-install
```

Make targets and repo scripts automatically prepend `.tools/bin` when it exists.

Generate a toolchain evidence report:

```bash
make toolchain-report TOOLCHAIN_PROFILE=strict
```

## Static Validation

Run the default validation set:

```bash
make validate
```

Check the public service API snapshots when route or schema behavior changes:

```bash
make api-contract
```

Check that service settings, Helm environment variables, and chart defaults remain aligned:

```bash
make config-contract
```

Run the stricter check that fails when optional production tools are missing:

```bash
make validate-full
```

Run the static production-readiness gates without a live cluster:

```bash
make production-check
```

## Local Cluster

Create a local cluster and bootstrap GitOps:

```bash
make local-up
make bootstrap-argocd
make sync
```

Run a smoke test through the gateway:

```bash
make smoke RUNTIME_BACKEND=ollama
```

The default local runtime is Ollama with `qwen3:0.6b`. The smoke scripts send `PLATFORM_API_KEY`, defaulting to the local demo key `local-development-only`.

## Platform Checks

Run the traceable sandbox proof:

```bash
make sandbox-smoke
```

Validate RAG and coding-agent workspace access:

```bash
make rag-smoke
make agent-smoke
```

Create and validate a team tenant lab:

```bash
make tenant-smoke
```

Generate customer tenant onboarding artifacts:

```bash
make tenant-onboard
```

Generate the regulated/offline tenant profile with no external CIDR egress:

```bash
make tenant-onboard-regulated
```

## Restore And Resilience

Run the local restore drill:

```bash
make restore-drill RUNTIME=local
```

Run restore-drill plus the Velero disposable namespace scenario:

```bash
make backup-drill
```

Run safe recovery and dependency drills:

```bash
make chaos-drill
DRILL=rag-service-rollout make chaos-drill
DRILL=qdrant-vector-store-rollout make chaos-drill
DRILL=vllm-runtime-rollout make chaos-drill
DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill
```

Restore evidence is written under `results/restore-drill/`.

## Evals, Load Tests, And Evidence

Run repeatable prompt checks:

```bash
make eval
SUITE=evals/coding-agent-suite.yaml make eval
```

Run k6 load tests against an ephemeral local gateway and mock runtime:

```bash
make loadtest-local
```

When a live local or customer gateway is already running, use `GATEWAY_URL=http://127.0.0.1:8080 make loadtest`.

Generate a customer-facing evidence pack:

```bash
make evidence
```

After the local lab is synced, include live Kubernetes readiness checks:

```bash
make evidence LIVE=1
```

Check handoff gates against eval, load, restore, toolchain, SLO, governance, and evidence-pack thresholds:

```bash
make release-gate
make release-gate-strict
make release-report-strict
```

## Governance Checks

```bash
make slo-check
make quota-check
make egress-check
make retention-check
make model-check
make model-provenance-check
```

The matching report targets write JSON and Markdown evidence under `results/`.

## Customer-Owned Kubernetes

For an existing Kubernetes cluster, install Argo CD, configure the customer overlay, and sync the applications from `clusters/customer/`.

```bash
make customer-overlay \
  CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
  CUSTOMER_REVISION=v0.4.2 \
  CUSTOMER_GPU_PROFILE=nvidia
```

Use `CUSTOMER_GPU_PROFILE=amd` for AMD ROCm clusters.

Customer clusters should provide ingress, storage classes, secret backends, logging, and optional GPU nodes. The stack expects GPU nodes to expose standard Kubernetes device resources:

- NVIDIA: `nvidia.com/gpu`
- AMD: `amd.com/gpu`

Label GPU nodes with:

```bash
kubectl label node <node> platform.ai/node-pool=gpu platform.ai/gpu-vendor=<nvidia|amd>
```

Then choose the matching vLLM profile:

```bash
clusters/customer/values/vllm-nvidia.yaml
clusters/customer/values/vllm-amd.yaml
```

Customer RAG values switch retrieval to Qdrant and deploy `charts/qdrant-vector-store` with persistent storage. Size Qdrant storage, vector dimensions, and ingestion to the customer's embedding model and approved knowledge pipeline.

For regulated or offline teams, start from `tenants/onboarding/regulated-offline-coding-agents.yaml`.

The full customer deployment checklist is in `clusters/customer/README.md`.

## Clean Up

Stop the local cluster:

```bash
make local-down
```
