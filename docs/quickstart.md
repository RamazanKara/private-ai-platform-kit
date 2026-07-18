# Local quickstart

The quickstart creates a single-node `kind` cluster, builds the two first-party service images, deploys the local charts, and runs gateway and RAG smoke tests. The default model is `qwen2.5:0.5b` served by Ollama on the local machine.

This is a development environment. It uses a public demo API key, plaintext in-cluster HTTP, single-node data stores, and workstation storage.

## Requirements

The managed bootstrap supports Linux and WSL and requires:

- Docker with a working daemon;
- Python 3.12 or newer;
- Bash, `curl`, `tar`, `sha256sum`, and `install`;
- enough disk for the `kind` node, platform images, and Ollama model.

The bootstrap downloads pinned copies of `kind`, `kubectl`, Helm, kubeconform, the Kyverno CLI, k6, Syft, the Argo CD CLI, Cosign, and Trivy into `.tools/bin`.

The first run also downloads container base images, the Kubernetes node image, Calico and Argo CD manifests, third-party charts and images, Python packages, and the Ollama model. It creates Docker state, writes a `kind` context to your kubeconfig, and binds host port `8080` unless overridden. Do not use the quickstart on a workstation where those changes are unacceptable.

On macOS or a managed workstation, install `kind`, `kubectl`, and Helm separately and run `make quickstart`; the repository's tool installer is Linux-only.

## Run it

From the repository root:

```bash
make bootstrap
```

If the required cluster tools are already installed:

```bash
make quickstart
```

The command runs, in order:

1. the local toolchain check;
2. `make validate`;
3. `make local-up` to create the cluster and build the gateway and RAG images;
4. `make agent-sandbox-install` from the vendored manifests;
5. Argo CD bootstrap and sync;
6. the gateway smoke test against Ollama;
7. the RAG smoke test against the local lexical corpus.

The Argo CD path needs the configured Git repository to be reachable from the cluster. For a reduced workstation check that applies the core charts directly, use:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
```

Direct apply skips the Argo CD application set, including the full observability, policy, cost, and backup add-ons. It is useful for the gateway/RAG smoke path, not as a GitOps or production-readiness test.

Other switches:

```bash
QUICKSTART_INSTALL_TOOLS=1 make quickstart  # install the pinned CLI set first
QUICKSTART_SKIP_VALIDATE=1 make quickstart  # skip static validation
QUICKSTART_SKIP_RAG=1 make quickstart       # skip the RAG smoke test
```

## Check the result

A complete default run ends with:

```text
[private-ai-platform-kit] smoke test completed for ollama
[private-ai-platform-kit] RAG smoke completed for agent-lab
[private-ai-platform-kit] quickstart completed
```

These lines confirm that the local gateway reached Ollama and that the RAG service returned results. They do not validate a customer identity provider, GPU runtime, production storage, backup, or external observability system.

Inspect the cluster with:

```bash
make status
kubectl get pods -A
```

Then run any focused checks you need:

```bash
make trace-smoke
make tenant-smoke
make agent-smoke
make agent-sandbox-smoke
make evidence LIVE=1
```

## Troubleshooting

Docker must be reachable with `docker info`. If cluster creation stopped partway through, remove the cluster with `make local-down` before retrying.

The default node image is set in `scripts/local-up.sh` and `deploy/clusters/local/kind-config.yaml`. Docker hosts using cgroup v1 automatically fall back to `kindest/node:v1.31.4`. To select a node image explicitly:

```bash
LOCAL_KIND_NODE_IMAGE=kindest/node:v1.31.4 make quickstart
```

The cluster maps gateway node port `30080` to host port `8080`. Choose another host port if it is in use:

```bash
LOCAL_GATEWAY_HOST_PORT=18080 make quickstart
```

The smoke scripts also use temporary local port-forwards. Override `LOCAL_PORT` for an individual smoke command if its default port is occupied.

For model-pull progress:

```bash
kubectl -n ollama logs statefulset/ollama
```

The local smoke key is `local-development-only`. It is deliberately public and must not be reused outside the local profile.

## Remove the lab

```bash
make local-down
```

This deletes the `kind` cluster. It does not remove downloaded tools, Docker images, or caches. `make clean-all` removes repository-local tool environments and generated files; Docker cleanup remains a Docker operation.

Continue with [Getting started](getting-started.md) for focused validation and customer-deployment commands.
