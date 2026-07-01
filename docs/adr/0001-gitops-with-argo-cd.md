# 0001. GitOps delivery with Argo CD

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The kit must deliver the same platform — gateway, runtimes, RAG, vector store, budget Redis, agent
workspaces, policies, observability, backup — onto a local `kind` cluster and onto customer-owned
clusters, with no manual `kubectl apply` drift between them. Delivery has to be declarative so the
desired state is reviewable in Git, auditable for customer handoff, and reproducible by an operator
who has never seen the cluster.

The README states the model directly: the local lab runs fully on `kind`, and customer clusters
"keep the same repo structure and replace only the platform services they already operate." That
requires a continuous-reconciliation tool that reads manifests and Helm charts straight from this
repository.

## Decision

Use Argo CD with an app-of-apps layout.

- A single root `Application` per environment points at a cluster directory and includes only its
  app list: [`deploy/gitops/argocd/root-app.yaml`](../../deploy/gitops/argocd/root-app.yaml)
  targets `deploy/clusters/local` and includes `apps.yaml`;
  [`deploy/gitops/argocd/root-app-customer.yaml`](../../deploy/gitops/argocd/root-app-customer.yaml)
  targets `deploy/clusters/customer` and includes `{apps.yaml,appprojects.yaml}`.
- [`deploy/clusters/local/apps.yaml`](../../deploy/clusters/local/apps.yaml) declares the child
  `Application`s: model-catalog, traceable-sandbox, platform-operators (Kyverno, KEDA,
  External Secrets via their upstream Helm charts), observability, security-policies, the Ollama and
  vLLM runtimes, budget-redis, inference-gateway, qdrant-vector-store, rag-service, agent-workspace,
  OpenCost cost-controls, and the Velero/restore-drill backup apps.
- Every child app sets `syncPolicy.automated` with `prune: true` and `selfHeal: true`, so manual
  edits are reverted and removed manifests are pruned.
- The local root tracks `targetRevision: HEAD`; the customer root pins a tagged revision (for
  example `v0.11.0` in the committed example) so customers reconcile against a fixed, reviewed
  release rather than a moving branch.
- Bootstrapped through `make bootstrap-argocd` and reconciled with `make sync`, matching the
  README's local run path.

## Consequences

- One reconciliation engine drives both environments; the difference between local and customer is a
  cluster directory and a pinned revision, not a different deploy mechanism.
- Self-heal plus prune means the cluster converges to Git, which is what the customer-handoff and
  evidence story depends on: the repository is the auditable source of desired state.
- Argo CD is itself a dependency the operator must install and operate (it is bootstrapped, not
  bundled into the application charts). For a fast workstation check that skips it,
  `QUICKSTART_DIRECT_APPLY=1 make quickstart` does a direct Helm apply instead.
- The app-of-apps pattern adds one indirection layer (root app -> app list -> workloads), which is
  the cost of keeping the per-environment surface to a single root manifest.

## Alternatives considered

- **Flux.** A capable GitOps controller with a strong Helm and image-automation story. Argo CD was
  chosen for its app-of-apps ergonomics, its first-class `AppProject` tenancy boundary (used in the
  customer overlay's `appprojects.yaml`), and a UI that helps during customer handoff and demos.
  Either tool could reconcile these manifests; the decision is not a claim that Flux cannot.
- **Direct `helm`/`kubectl` apply, scripted in CI or Make.** Simplest to start, and the kit keeps
  this path for the `QUICKSTART_DIRECT_APPLY=1` workstation check. Rejected as the primary model
  because it does not continuously reconcile, prune removed resources, or self-heal manual drift,
  all of which the customer-handoff evidence story relies on.
- **Cloud-provider GitOps / Terraform-driven delivery.** Rejected as a default because it ties the
  platform to a specific provider. The decision-guide's explicit position is "provider-neutral
  GitOps and Helm surfaces instead of cloud-specific Terraform."
