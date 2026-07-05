# Your first private AI platform

This tutorial takes you from an empty machine to a **working private chat completion** served from your own Kubernetes cluster, with no cloud account, no external API, and nothing leaving your laptop.

By the end you will have:

- a local `kind` cluster running the platform's Helm charts,
- an OpenAI-compatible gateway in front of a local Ollama model (`qwen2.5:0.5b`),
- a retrieval-augmented (RAG) service answering a grounded query,
- and the same evidence and smoke checks the project uses on customer clusters.

Everything here runs with a single command. The goal is a guaranteed first success; the [how-to guides](getting-started.md) come next, once you have seen the platform work.

!!! note "Time and space"
    Plan for **15 to 30 minutes** once Docker images and the default model are cached. The *first* run is slower because `kind`, the platform images, and `qwen2.5:0.5b` are pulled fresh. Keep several GB of free disk available.

## Before you begin

Install these on your machine:

- Python 3.12+
- Docker (running, with enough CPU, memory, and disk for a small cluster)
- kind
- kubectl
- Helm
- Go
- Syft

That is everything the tutorial needs. The stricter production-readiness path additionally uses kubeconform, the Kyverno CLI, the Argo CD CLI, Cosign, Trivy, and k6, but you do not need them yet.

If you would rather have the project manage the optional validation CLIs for you under `.tools/bin`, you can let the quickstart install them in the next step.

## Step 1: Bring up the platform

From the repository root, run:

```bash
make quickstart
```

That one command checks your toolchain, runs static validation, creates the local cluster, syncs the platform through GitOps, then runs the gateway and RAG smoke tests. Leave it running; it is doing the work you would otherwise do by hand.

A few variants for common situations:

!!! tip "Let the project install validation tools"
    ```bash
    QUICKSTART_INSTALL_TOOLS=1 make quickstart
    ```
    Fetches the optional validation CLIs into `.tools/bin` first.

!!! tip "Skip Argo CD for a fast workstation check"
    ```bash
    QUICKSTART_DIRECT_APPLY=1 make quickstart
    ```
    Renders and applies the charts directly instead of bootstrapping GitOps. Useful if Argo CD is overkill for a quick look.

## Step 2: Confirm it worked

When the run finishes, the last lines should read:

```text
[private-ai-platform-kit] smoke test completed for ollama
[private-ai-platform-kit] RAG smoke completed for agent-lab
[private-ai-platform-kit] quickstart completed
```

That is your private platform answering real requests:

- The **gateway smoke** response includes an OpenAI-compatible `choices` array: a chat completion produced entirely on your machine.
- The **RAG smoke** response includes `results`, `grounded_messages`, `query_sha256`, `X-Request-ID`, and `X-Sandbox-ID`: a grounded answer with an auditable request trail.

If you see those three lines, you are done: you have a private AI platform running. The rest of this page helps you understand what you just built and where to go next.

## Step 3: Compare against the recorded run

The repository commits a real capture of a successful run as the canonical expected output, so you can confirm yours matches:

- [quickstart-success.txt](assets/quickstart-screenshots/README.md): gateway and RAG smoke output.
- [agent-smoke.txt](assets/quickstart-screenshots/README.md): coding-agent smoke output.

Captures for the follow-up steps live alongside them (Argo CD applications, the Grafana dashboard, and an evidence report) in [docs/assets/quickstart-screenshots/](assets/quickstart-screenshots/README.md).

## What just happened

`make quickstart` ran these steps in order:

1. `make toolchain-doctor TOOLCHAIN_PROFILE=local`: check the local toolchain.
2. `make validate`: static validation (skipped if `QUICKSTART_SKIP_VALIDATE=1`).
3. `make local-up`: create the local `kind` cluster.
4. `make agent-sandbox-install`: install the agent-sandbox controller, the workspace-runtime prerequisite.
5. `make bootstrap-argocd` and `make sync`: install Argo CD and sync the platform (with `QUICKSTART_DIRECT_APPLY=1` the charts are applied directly with Helm instead).
6. `make smoke RUNTIME_BACKEND=ollama`: a chat completion through the gateway.
7. `make rag-smoke`: a grounded RAG query (skipped if `QUICKSTART_SKIP_RAG=1`).

The cluster is left running so you can inspect it. This is the same flow (same charts, policies, and checks) that runs against customer clusters; only the runtime backend and GPU profile change.

## Keep exploring

With the cluster up, try the other smoke paths and generate evidence:

```bash
make trace-smoke          # traceable sandbox controls
make tenant-smoke         # a team tenant lab
make agent-smoke          # a locked-down coding-agent workspace
make agent-sandbox-smoke  # the hardened workspace-runtime contract
make evidence LIVE=1      # customer-style evidence pack against the live cluster
```

## Clean up

When you are finished, tear the cluster down:

```bash
make local-down
```

## Troubleshooting

**Docker**: confirm `docker info` works and Docker has enough CPU, memory, and disk for a kind cluster plus the model and runtime images.

**kind**: run `kind get clusters`; if the cluster is broken, `make local-down` then `make local-up`.

**kubectl**: after `make local-up`, confirm `kubectl config current-context` points at `kind-private-ai-platform-kit`.

**Helm**: run `helm version --short`; `make validate` lints and renders every chart before the cluster path.

**Argo CD**: if bootstrap or sync is blocked by a local CLI issue, rerun with `QUICKSTART_DIRECT_APPLY=1 make quickstart`.

**Model pull**: the first Ollama pull of `qwen2.5:0.5b` can dominate the runtime. Watch progress with `kubectl -n ollama logs statefulset/ollama`.

**API keys**: local smoke scripts send `X-API-Key: local-development-only`. Customer overlays should source SHA-256 API-key hashes from External Secrets instead of committing plaintext keys.

**Port conflicts**: smoke tests bind localhost ports such as `18080` and `18083`; override `LOCAL_PORT` if one is already in use. The cluster also maps gateway nodePort `30080` to host port `8080`; if another process owns `8080`, set `LOCAL_GATEWAY_HOST_PORT`:

```bash
LOCAL_GATEWAY_HOST_PORT=18080 QUICKSTART_DIRECT_APPLY=1 make quickstart
```

**Kind node image**: Docker hosts on cgroup v1 are automatically given a compatible node image. To force a specific one, set `LOCAL_KIND_NODE_IMAGE`:

```bash
LOCAL_KIND_NODE_IMAGE=kindest/node:v1.31.4 QUICKSTART_DIRECT_APPLY=1 make quickstart
```

## Where to next

- **Operate the lab**: the [how-to guides](getting-started.md) cover evals, load tests, governance checks, and the customer-cluster path.
- **Decide if it fits**: the [decision guide](decision-guide.md) explains who this is and is not for.
- **Understand the controls**: the [production-readiness matrix](production-readiness.md) maps every claim to where it is enforced.
