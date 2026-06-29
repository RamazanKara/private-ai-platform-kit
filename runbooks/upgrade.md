# Upgrade And Rollback Runbook

Use this runbook for the most common day-2 operation: promoting a new platform
version, or rolling back to a previous one. Every Argo CD Application in this
repo runs `syncPolicy.automated` with `prune: true` and `selfHeal: true`, so
once a new revision is in Git, Argo CD applies it without a manual sync and
reverts any out-of-band change. That makes the promotion lever simple but
unforgiving: the thing you change in Git is the thing that ships.

## What Moves When You Upgrade

A release bumps several pinned references together. `make production-check`
enforces that they all match the latest `CHANGELOG.md` version:

- the `inference-gateway` and `rag-service` chart image tags (`deploy/charts/*/values.yaml`),
- every chart `version` and the service `appVersion`,
- `SERVICE_VERSION` in the gateway and RAG service code and the OpenAPI `info.version`,
- the `CUSTOMER_REVISION=<tag>` examples in `README.md`, `docs/getting-started.md`, and `deploy/clusters/customer/README.md`.

CI builds and signs the images at that tag (see the build/release controls in
`docs/threat-model.md`). For an operator promoting a published release you do
not rebuild images -- you point GitOps at the tag whose images already exist.

## The Promotion Lever: CUSTOMER_REVISION

The customer overlay deploys from an immutable Git tag, not a branch. The root
Application `deploy/gitops/argocd/root-app-customer.yaml` is pinned (currently
`targetRevision: v0.9.0`) and every child Application in `deploy/clusters/customer/apps.yaml`
runs under the `private-ai-platform` AppProject (`deploy/clusters/customer/appprojects.yaml`),
which locks `sourceRepos` to the approved repo. **Moving `CUSTOMER_REVISION` to a
new tag is the canonical promotion.** `make customer-overlay-check` rejects
`HEAD` or a branch, so every deployed state is reproducible and revertible.

Promote by re-running the overlay configurator with the new tag and committing:

    make customer-overlay \
      CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
      CUSTOMER_REVISION=<new-tag> \
      CUSTOMER_GPU_PROFILE=<nvidia|amd|default>

    make customer-overlay-check

This rewrites `deploy/gitops/argocd/root-app-customer.yaml` and the child Applications
in `deploy/clusters/customer/apps.yaml` to the new tag. Commit to the customer repo;
Argo CD picks it up on the next poll. See `deploy/clusters/customer/README.md` for the
overlay mechanics.

## Order Of Operations

Upgrade in dependency order so a half-applied release never serves traffic
against a mismatched store:

1. **Read the changelog** for the target tag. Note any data-store, schema, or
   collection-version changes (Qdrant `collectionVersion`, vector dimensions).
2. **Test the tag before promoting** (next section) in a lab or staging cluster.
3. **Promote stateful and platform dependencies first** if the release changes
   them: vector store (Qdrant) and budget Redis, then the RAG service, then the
   inference gateway and runtimes. Argo CD sync waves and health gating handle
   ordering within a sync, but stage risky data migrations explicitly.
4. **Move `CUSTOMER_REVISION` to the new tag** and commit. Argo CD syncs
   automatically; watch application health.
5. **Smoke test** the gateway and RAG paths:

       GATEWAY_URL=http://127.0.0.1:8080 make eval
       GATEWAY_URL=http://127.0.0.1:8080 make loadtest

   and confirm `make release-gate-strict` passes against current evidence.

## Test A Tag Before You Promote

Never let production be the first cluster to run a tag. Deploy the candidate tag
to a lab or staging cluster (or the local kind lab) and run the same gates:

    # Point a non-production overlay/cluster at the candidate tag, then:
    make eval
    make loadtest
    make release-gate-strict

Run the relevant chaos and restore checks for anything the release touches:
`runbooks/chaos-drills.md` for rollout/recovery and the RAG fault-injection
drill, and `runbooks/restore-drill.md` (especially the real Qdrant
data-recovery drill) before any release that changes the vector store. Only
promote a tag whose evidence passes.

## Watch The Upgrade Land

    kubectl -n argocd get applications
    argocd app list
    argocd app get private-ai-platform-kit-root

Healthy + Synced across all applications means the new tag is live. If an
Application is Degraded or OutOfSync, follow the symptom-specific runbook:
`runbooks/incident-inference-runtime.md` for gateway/runtime,
`runbooks/rag-service.md` for RAG, `runbooks/policy-blocked-deploy.md` if Kyverno
admission rejected the sync.

## Rollback

Because the revision is a tag and the AppProject locks the source, rollback is
"point Git back at the last known-good tag." Prefer this to ad-hoc kubectl edits,
which selfHeal will revert anyway.

### Preferred: revert to the previous tag (GitOps-native)

    make customer-overlay \
      CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
      CUSTOMER_REVISION=<previous-good-tag> \
      CUSTOMER_GPU_PROFILE=<nvidia|amd|default>
    make customer-overlay-check
    # commit; Argo CD syncs back to the previous tag automatically

This is the right rollback for a bad release: it is reproducible, leaves an audit
trail in Git, and keeps automation in charge.

### Fast: argocd app rollback

For an urgent single-application revert to a previously synced revision without a
Git change yet, use Argo CD's deployment history:

    argocd app history private-ai-platform-kit-root
    argocd app rollback private-ai-platform-kit-root <history-id>

This is a stopgap. The Git revision still points at the bad tag, so reconcile Git
(revert `CUSTOMER_REVISION`) afterward or selfHeal/the next sync will roll forward
again.

## Pausing selfHeal And Prune For A Manual Rollback

A manual recovery -- restoring data, editing a live resource, draining a node --
fights `selfHeal` and `prune`, which will revert your change or delete resources
that are not yet in Git. Pause automation on the affected Application for the
duration, then re-enable it.

Disable automated sync while you work:

    argocd app set <application> --sync-policy none

Re-enable automation (with prune and selfHeal) when the manual step is done and
Git matches the intended state:

    argocd app set <application> --sync-policy automated --auto-prune --self-heal

You can also pause by editing the Application's `spec.syncPolicy.automated` in
Git, but for a time-boxed manual rollback the `argocd app set` form is faster and
self-documenting in the audit log. Always re-enable automation before closing the
incident -- a permanently manual Application drifts silently.

## Evidence

Record the from/to tags, the `argocd app history` entry used (if any), the
commit that moved `CUSTOMER_REVISION`, smoke and gate results
(`make eval`, `make loadtest`, `make release-gate-strict`), and -- if automation
was paused -- when it was disabled and re-enabled.
