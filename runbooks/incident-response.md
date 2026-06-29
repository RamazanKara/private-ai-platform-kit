# Incident Response Index

Start here during an incident. This index defines severity tiers and the
escalation path, then routes to the component runbook that handles the specific
failure. Keep the response practical: classify, page the right owner, follow the
linked runbook, and capture evidence as you go.

## Severity Tiers

| Severity | Definition | Examples | Response |
| --- | --- | --- | --- |
| **SEV1** | Platform-wide outage or confirmed data loss/exposure. Core business endpoints down, customer data lost or leaked. | Inference gateway has zero ready endpoints; Qdrant data lost with no good backup; confirmed prompt/secret exfiltration. | Page on-call immediately, open an incident channel, engage platform + security leads. All hands until mitigated. |
| **SEV2** | Major degradation, single critical component down, or error budget fast-burn. Service usable but impaired; no confirmed data loss. | One runtime backend absent; RAG service high error rate; budget Redis down (budgets fail open/closed); restore drill failing. | Page on-call. Mitigate within the hour; keep stakeholders updated. |
| **SEV3** | Minor or contained issue, slow-burn, or single-tenant impact. Workarounds exist. | One sandbox over budget; elevated p95 latency; a single Kyverno-blocked deploy; backup stale but last backup good. | Handle in business hours. Track to closure; no paging unless it escalates. |

When unsure, round up: treat a possible data-loss or data-exposure event as SEV1
until proven otherwise.

## Escalation Path

1. **On-call operator** acknowledges the alert, classifies severity, and starts
   the matching runbook below.
2. **Platform lead** is engaged for SEV1/SEV2, or when the operator cannot
   mitigate within the tier's response window.
3. **Security lead** is engaged for any suspected data exposure, prompt-injection
   exploitation, model-artifact tampering, or pipeline compromise (see
   `docs/threat-model.md`).
4. **Customer / incident manager** owns external communication and the incident
   record for SEV1/SEV2.

Alerts carry a `runbook_url` annotation pointing at the relevant runbook; the
backend-absent and restore alerts also name the runbook in their summary.

## Incident And Drill Runbooks

| Situation | Runbook |
| --- | --- |
| Inference gateway / runtime unavailable (502s, backend absent, no ready endpoints) | `runbooks/incident-inference-runtime.md` |
| RAG service errors or high latency | `runbooks/rag-service.md` |
| Argo CD sync blocked by Kyverno admission | `runbooks/policy-blocked-deploy.md` |
| Restore / backup failure or stale backup | `runbooks/restore-drill.md` |
| Controlled disruption and recovery drills | `runbooks/chaos-drills.md` |
| Sandbox budget rejections and admission limits | `runbooks/budget-controls.md` |
| Upgrade gone wrong / need to roll back | `runbooks/upgrade.md` |
| Vector store schema / collection migration | `runbooks/qdrant-migration.md` |

## Gap Sections

The following failures did not have a dedicated runbook. Each section is the
first-response procedure; escalate per the tiers above.

### Budget Redis Outage

**Severity.** Usually SEV2: the shared budget backend (`deploy/charts/budget-redis`,
`budget` namespace) is down, so the gateway cannot read or write per-sandbox
usage. Inference availability itself may be unaffected, but budget enforcement is
degraded.

**What to check.**

    kubectl -n budget get deploy,svc,networkpolicy,pods
    kubectl -n budget exec deploy/budget-redis -- redis-cli ping   # expect PONG
    kubectl -n inference logs deploy/inference-gateway-inference-gateway --tail=100 | grep -E 'budget|redis'

Confirm `SANDBOX_BUDGET_BACKEND=redis` and that `SANDBOX_BUDGET_REDIS_URL`
resolves the `budget` namespace service. Check the gateway's
`SANDBOX_BUDGET_REDIS_TIMEOUT_SECONDS` -- a slow Redis can manifest as timeouts.

**How to recover.**

1. Restart the backend and confirm it returns: `DRILL=budget-redis-rollout make chaos-drill`
   (rollout-restarts Redis, checks `redis-cli ping`, runs gateway smoke).
2. If the Deployment is unschedulable, inspect events/PVC and node capacity; the
   chart renders a PDB and NetworkPolicies (default-deny + gateway allow), so a
   broken NetworkPolicy can also sever the gateway-to-Redis path.
3. While Redis is down, decide budget posture deliberately: a managed/enterprise
   Redis can be swapped in by pointing `budget.redisUrl` at it (see
   `runbooks/budget-controls.md`). Do not silently disable budgets in production.
4. Validate with the budget endpoint once recovered:
   `curl -H 'X-Sandbox-ID: <id>' http://127.0.0.1:18082/v1/sandbox/budget`.

### Vector-Store (Qdrant) Data Loss

**Severity.** SEV1 if customer vectors in the production
`customer-platform-knowledge` collection are lost or corrupted; SEV2 if only RAG
retrieval is degraded while data is intact.

**What to check.**

    kubectl -n vector get pods,pvc,svc
    kubectl -n vector logs deploy/qdrant-vector-store --tail=100
    # collection presence and point counts (port-forward first):
    kubectl -n vector port-forward svc/qdrant-vector-store 6333:6333 &
    curl -s http://127.0.0.1:6333/collections
    curl -s http://127.0.0.1:6333/collections/<collection>

Confirm whether the PVC still holds data (empty PVC vs. corrupted collection vs.
unreachable pod) and check the RAG service health for `retrieval_backend=qdrant`.

**How to recover.**

1. If the pod is simply unhealthy, restart and revalidate:
   `DRILL=qdrant-vector-store-rollout make chaos-drill`.
2. If data is actually lost, restore from the most recent Qdrant snapshot /
   backup. The end-to-end recovery procedure (seed, snapshot, delete, restore,
   assert point count) is in `runbooks/restore-drill.md` under "Real
   Data-Recovery Drill (Qdrant)"; the production restore follows the same
   snapshot-restore path against the real collection. Backups are a customer
   prerequisite -- a metadata-only Velero backup restores an empty store, so the
   PVC/snapshot contents must have been captured (see
   `deploy/clusters/customer/README.md` handoff checklist).
3. After restore, re-run RAG smoke and confirm recovered point counts match
   expectations before declaring recovery.
4. If no good backup exists, this is a SEV1 data-loss event: engage the customer
   incident manager and rebuild the collection from the source-of-truth knowledge
   ingestion.

### Kyverno-Blocked Deploys

**Severity.** Typically SEV3 (a single blocked manifest), escalating to SEV2 if
it blocks a release-wide sync or a rollback during another incident.

**What to check.**

    kubectl get events --all-namespaces --field-selector reason=PolicyViolation
    kubectl -n argocd describe application <application>
    kubectl get clusterpolicy
    kyverno apply deploy/policies/kyverno/policies.yaml --resource <rendered-resource.yaml>

Common causes: missing required labels (including `platform.ai/sandbox-id`), a
`latest` image tag, missing CPU/memory requests/limits, running as root, an
egress CIDR that is too broad (`ai-platform-restrict-egress-cidrs`), or an image
that fails signature verification (`ai-platform-verify-project-images`, set to
`Enforce` with a keyless subject/issuer restricted to the approved CI identity).

**How to recover.**

1. For a policy/manifest mismatch, fix the manifest -- see
   `runbooks/policy-blocked-deploy.md`. Do not bypass policy; exceptions must be
   time-boxed, documented, and reviewed.
2. For an image-verification block on a fork, the keyless `subject`/`issuer` in
   `deploy/policies/kyverno/policies.yaml` and the image registry must point at your own
   identity (see `docs/threat-model.md` and `deploy/clusters/customer/README.md`). A
   rejected image signed by an unexpected identity is the policy working as
   intended -- treat an unexplained verification failure as a potential
   supply-chain event and escalate to the security lead.
3. For a broad-egress block, add the destination to
   `platform/network/egress-catalog.yaml` and reference it by `catalogRef` rather than
   widening the CIDR (`runbooks/egress-governance.md`).

## Evidence

For every incident, record: the firing alert and severity, timeline, the runbook
followed, mitigation steps (including any paused Argo CD automation per
`runbooks/upgrade.md`), affected deploy/sandbox/request IDs, and -- for data-loss or
security events -- the deploy/backup/restore artifacts and the security-lead handoff.
