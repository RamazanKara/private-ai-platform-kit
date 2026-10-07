# Quickstart

There are two ways to run the kit on one machine. Both use the same service images and the
same configuration contract as a production cluster.

| | Docker Compose | Local Kubernetes lab |
| --- | --- | --- |
| Needs | Docker with Compose | Linux or WSL, Docker, Python 3.12+, Bash, `curl` |
| Time to first request | About two minutes | 15 to 30 minutes on the first run, mostly downloads |
| Runs | Gateway, Ollama, RAG, optional Open WebUI | The full platform on `kind`: Argo CD, Kyverno, Calico, gateway, Ollama, RAG, Redis, agent workspaces, observability |
| Good for | Trying the API and the audit trail | Evaluating the Kubernetes deployment, policies, and agent sandboxes |

Both are development environments with a public demo API key, `local-development-only`.
Never reuse it outside them.

## Docker Compose

From the repository root:

```bash
make compose-up
```

This builds the gateway and RAG images, starts Ollama, pulls `qwen2.5:0.5b` (about 400 MB, cached
in a Docker volume), and waits until every service reports ready. Ports bind to `127.0.0.1`
only: the gateway on `8080` and the RAG service on `8090`.

Walk through the governed request path:

```bash
make compose-smoke
```

The walkthrough also exercises all five cloud adapters against the bundled local
`cloud-fake` service, including streaming, overload fallback, confidential-data
refusal, credential blocking, and per-provider accounting. The `demo-*` models and
their prices are synthetic fixtures; no provider account, real credential, or external
inference request is used. The ordinary Ollama completion still uses a real local model.

The script sends real requests and exits non-zero at the first one that misbehaves. A passing
run ends with:

```text
== 9. Export the audit log and verify its hash chain
chain 7f3c...: OK (21 record(s), head 6865...)
   ok  21 receipt lines verified (.out/compose/gateway-audit.jsonl)

== 10. Rewrite history: turn the blocked request's 400 into a 200 and verify again
   ok  edit detected: BROKEN at position 3 (record_hash_mismatch)

All checks passed. Open the read-only console at http://127.0.0.1:8080/console
```

Call the gateway from any OpenAI or Anthropic client with base URL `http://127.0.0.1:8080/v1`
(OpenAI) or `http://127.0.0.1:8080` (Anthropic) and API key `local-development-only`; see
[client examples](client-examples.md). The key is a record in
`deploy/compose/key-records.yaml` bound to sandbox `demo`, so requests run as that sandbox
whatever `X-Sandbox-ID` they send.

Add a chat UI:

```bash
docker compose -f deploy/compose/compose.yaml --profile ui up -d
```

Open WebUI then serves <http://127.0.0.1:3000>. It runs in offline mode and talks only to the
gateway, so its chats appear in the same audit trail.

Settings you can override in the environment: `PAK_MODEL` (any Ollama model tag),
`PAK_GATEWAY_PORT`, `PAK_RAG_PORT`, and `PAK_UI_PORT`.

Stop and delete the stack, including the model volume:

```bash
make compose-down
```

The Compose stack deliberately leaves out what needs Kubernetes: network policy, the hardened
agent workspaces, GitOps, Redis-backed shared state, and the observability stack. Use the lab
below for those.

## Local Kubernetes lab

The lab creates a single-node `kind` cluster, builds the two first-party images, deploys the
platform through Argo CD, and runs gateway and RAG smoke tests against `qwen2.5:0.5b` on Ollama.
It uses plaintext in-cluster HTTP, single-node data stores, and workstation storage.

### Requirements

The managed bootstrap supports Linux and WSL and requires:

- Docker with a working daemon;
- Python 3.12 or newer;
- Bash, `curl`, `tar`, `sha256sum`, and `install`;
- enough disk for the `kind` node, platform images, and the Ollama model.

The bootstrap downloads pinned copies of `kind`, `kubectl`, Helm, kubeconform, the Kyverno CLI,
k6, Syft, the Argo CD CLI, Cosign, and Trivy into `.tools/bin`.

The first run also downloads container base images, the Kubernetes node image, Calico and Argo
CD manifests, third-party charts and images, Python packages, and the Ollama model. It creates
Docker state, writes a `kind` context to your kubeconfig, and reserves host port `8080` for the
cluster unless overridden. Stop the Compose stack first (`make compose-down`) if it is running.

On macOS or a managed workstation, install `kind`, `kubectl`, and Helm separately and run
`make quickstart`; the repository's tool installer is Linux-only.

### Run it

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

The Argo CD path needs the configured Git repository to be reachable from the cluster. For a
reduced workstation check that applies the core charts directly, use:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
```

Direct apply skips the Argo CD application set, including the full observability, policy,
cost, and backup add-ons. It is useful for the gateway and RAG smoke path, not as a GitOps or
production-readiness test.

Other switches:

```bash
QUICKSTART_INSTALL_TOOLS=1 make quickstart  # install the pinned CLI set first
QUICKSTART_SKIP_VALIDATE=1 make quickstart  # skip static validation
QUICKSTART_SKIP_RAG=1 make quickstart       # skip the RAG smoke test
```

### Check the result

A complete default run ends with:

```text
[private-ai-platform-kit] smoke test completed for ollama
[private-ai-platform-kit] RAG smoke completed for agent-lab
[private-ai-platform-kit] quickstart completed
```

These lines confirm that the gateway reached Ollama and that the RAG service returned results.
They do not validate a customer identity provider, GPU runtime, production storage, backup, or
external observability system.

The lab gateway is a ClusterIP service behind a default-deny network policy. Reach it from
your workstation with a port-forward:

```bash
kubectl -n inference port-forward svc/inference-gateway-inference-gateway 18080:8080
curl -s http://127.0.0.1:18080/v1/models -H 'X-API-Key: local-development-only'
```

Then run the hardened agent workspace demo and any focused checks you need:

```bash
make agent-sandbox-demo
make status
make trace-smoke
make tenant-smoke
make agent-smoke
make agent-sandbox-smoke
make evidence LIVE=1
```

### Troubleshooting

Docker must be reachable with `docker info`. If cluster creation stopped partway through,
remove the cluster with `make local-down` before retrying.

The default node image is set in `scripts/local-up.sh` and
`deploy/clusters/local/kind-config.yaml`. Docker hosts using cgroup v1 automatically fall back
to `kindest/node:v1.31.4`. To select a node image explicitly:

```bash
LOCAL_KIND_NODE_IMAGE=kindest/node:v1.31.4 make quickstart
```

If host port `8080` is taken, reserve another one for the cluster:

```bash
LOCAL_GATEWAY_HOST_PORT=18081 make quickstart
```

The smoke scripts use temporary local port-forwards. Override `LOCAL_PORT` for an individual
smoke command if its default port is occupied.

For model-pull progress:

```bash
kubectl -n ollama logs statefulset/ollama
```

### Remove the lab

```bash
make local-down
```

This deletes the `kind` cluster. It does not remove downloaded tools, Docker images, or caches.
`make clean-all` removes repository-local tool environments and generated files; Docker cleanup
remains a Docker operation.

Continue with [Getting started](getting-started.md) for focused validation and
customer-deployment commands.
