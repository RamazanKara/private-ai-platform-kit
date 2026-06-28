# Quickstart

This path is for a first evaluator who wants to prove the local lab works before reading the full operations docs.

Expected time is 15-30 minutes after Docker images and the default Ollama model are cached. The first run can take longer because `kind`, platform images, and `qwen2.5:0.5b` may need to be pulled. Plan for several GB of free disk space.

## Prerequisites

Required for the local lab:

- Python 3
- Docker
- kind
- kubectl
- Helm
- Go
- Syft

The strict production-readiness path also uses kubeconform, Kyverno CLI, restore-drill, k6, Argo CD CLI, Cosign, and Trivy.

## Fast Path

Check the toolchain, run static validation, create the local cluster, sync the platform, then run gateway and RAG smoke tests:

```bash
make quickstart
```

If optional validation tools are missing and you want Codex-managed binaries under `.tools/bin`, run:

```bash
QUICKSTART_INSTALL_TOOLS=1 make quickstart
```

If Argo CD is not useful for a quick workstation check, render and apply the charts directly:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
```

## Expected Output

A successful run ends with:

```text
[private-ai-platform-kit] smoke test completed for ollama
[private-ai-platform-kit] RAG smoke completed for agent-lab
[private-ai-platform-kit] quickstart completed
```

The gateway smoke response should include an OpenAI-compatible `choices` array. The RAG smoke response should include `results`, `grounded_messages`, `query_sha256`, `X-Request-ID`, and `X-Sandbox-ID`.

## What Quickstart Does

`make quickstart` runs these steps:

1. `make toolchain-doctor TOOLCHAIN_PROFILE=local`
2. `make validate`, unless `QUICKSTART_SKIP_VALIDATE=1`
3. `make local-up`
4. `make bootstrap-argocd` and `make sync`, unless `QUICKSTART_DIRECT_APPLY=1`
5. `make smoke RUNTIME_BACKEND=ollama`
6. `make rag-smoke`, unless `QUICKSTART_SKIP_RAG=1`

The script leaves the cluster running so you can inspect it.

## Useful Follow-Ups

```bash
make sandbox-smoke
make tenant-smoke
make agent-smoke
make evidence LIVE=1
```

Clean up the local cluster:

```bash
make local-down
```

## Troubleshooting

Docker: confirm `docker info` works and Docker has enough CPU, memory, and disk for a kind cluster plus model/runtime images.

kind: run `kind get clusters`; if the cluster is broken, use `make local-down` and rerun `make local-up`.

kubectl: confirm `kubectl config current-context` points at `kind-private-ai-platform-kit` after `make local-up`.

Helm: run `helm version --short`; `make validate` lints and renders every chart before the cluster path.

Argo CD: if bootstrap or sync is blocked by a local CLI issue, retry with `QUICKSTART_DIRECT_APPLY=1 make quickstart`.

Model pull: the first Ollama pull of `qwen2.5:0.5b` can dominate runtime. Check `kubectl -n ollama logs statefulset/ollama` for progress.

API keys: local smoke scripts send `X-API-Key: local-development-only`. Customer overlays should source SHA-256 API-key hashes from External Secrets instead of committing plaintext keys.

Port-forwarding: smoke tests bind localhost ports such as `18080` and `18083`. Override `LOCAL_PORT` if a port is already in use.

Kind nodePort mapping: the local cluster maps gateway nodePort `30080` to host port `8080` by default. If another process already owns `8080`, run quickstart with an alternate port:

```bash
LOCAL_GATEWAY_HOST_PORT=18080 QUICKSTART_DIRECT_APPLY=1 make quickstart
```

Kind node image: Docker hosts using cgroup v1 are automatically given a compatible local node image. To force a specific image, set `LOCAL_KIND_NODE_IMAGE`:

```bash
LOCAL_KIND_NODE_IMAGE=kindest/node:v1.31.4 QUICKSTART_DIRECT_APPLY=1 make quickstart
```
