# External / Managed Stateful Stores Runbook

Use this runbook when moving the platform's bundled stateful stores off their single-node
reference footprints and onto external, managed, or highly-available (HA) services before a
production-style handoff.

## Why this exists

The kit's stateful stores ship as **single-node, reference footprints** so a `kind` laptop
lab and a fresh cluster come up with zero external dependencies. Each is a deliberate
dev/reference default, not a production topology:

| Store | Bundled footprint | What it holds | SPOF? |
| --- | --- | --- | --- |
| Budget / response-cache Redis | [`deploy/charts/budget-redis`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/budget-redis) — 1 replica, no persistence, `podDisruptionBudget.minAvailable: 0` | Shared per-sandbox budget counters and the optional exact-match response cache | Yes — a restart drops counters; an outage fails budgets closed (503) |
| Qdrant vector store | [`deploy/charts/qdrant-vector-store`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/qdrant-vector-store) — single-instance, **enforced** by [`values.schema.json`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/qdrant-vector-store/values.schema.json) (`replicaCount` max 1) on one RWO PVC | RAG dense vectors / hybrid retrieval corpus | Yes — a node drain briefly evicts retrieval |
| Loki logging | [`deploy/observability/applications.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/observability/applications.yaml) — `deploymentMode: SingleBinary`, `replication_factor: 1`, filesystem storage, 31-day retention | Pod stdout including the redacted gateway/RAG audit JSON | Yes — filesystem-backed, not replicated |

None of these should be relied on as the durable system of record in a regulated or
multi-tenant production environment. The sections below make each one a clean opt-in swap to
an external/HA service **without ripping out the bundled dev default** — you point config at
your managed endpoint and stop syncing the bundled Application.

For the control-by-control map that references this runbook, see the
[Production readiness matrix](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/production-readiness.md).

---

## 1. External / managed Redis (budget + response cache)

The gateway talks to Redis over a plain URL, so "bring your own HA Redis" is a config change,
not a code change. The same guidance covers a managed cloud Redis (ElastiCache, MemoryStore,
Azure Cache), a self-run **Redis Sentinel** failover pair, or a **Redis Cluster**.

### What talks to Redis

Two independent URLs, both in the gateway values
([`deploy/charts/inference-gateway/values.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/inference-gateway/values.yaml)):

- `budget.redisUrl` (env `SANDBOX_BUDGET_REDIS_URL`) — shared budget counters. Requires
  `budget.backend: redis`. This is the correctness-critical one under multi-replica scale-out.
- `responseCache.redisUrl` (env `RESPONSE_CACHE_REDIS_URL`) — the optional shared response
  cache. Requires `responseCache.backend: redis`. Cache loss is a performance event, not a
  correctness event (the gateway degrades to a miss and calls the runtime).

They may point at the same server on different logical databases (the defaults use `/0` and
`/1`) or at entirely separate services.

### Steps

1. **Provision** the managed/HA Redis and get its endpoint. For Sentinel, front the failover
   set with a stable Service/DNS name (or a Sentinel-aware proxy) so the URL does not change on
   failover. For a cloud managed Redis, use its primary endpoint.

2. **Store the AUTH secret** through your secret manager, never in values. The bundled
   `budget-redis` chart has an optional `auth.existingSecret`; for an external Redis you supply
   the password in the URL, sourced from a Kubernetes Secret via External Secrets
   ([`deploy/clusters/customer/external-secrets.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/external-secrets.yaml)).

3. **Point the gateway at it.** In your customer gateway overlay
   ([`deploy/clusters/customer/values/inference-gateway.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/values/inference-gateway.yaml)):

        budget:
          enabled: true
          backend: redis
          # rediss:// for TLS; :PASSWORD@ sourced from your secret manager.
          redisUrl: rediss://:PASSWORD@managed-redis.internal:6379/0
          redisTimeoutSeconds: "0.5"
        responseCache:
          enabled: true
          backend: redis
          redisUrl: rediss://:PASSWORD@managed-redis.internal:6379/1

4. **Stop deploying the bundled Redis.** Remove (or set to not-sync) the `budget-redis`
   Argo `Application` in
   [`deploy/clusters/customer/apps.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/apps.yaml)
   so the reference Redis is not deployed alongside your managed one. The local lab
   ([`deploy/clusters/local/apps.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/local/apps.yaml))
   keeps the bundled chart — the dev default is untouched.

5. **Open egress.** The gateway namespace runs under a default-deny NetworkPolicy. Add an
   egress allowance to the managed Redis endpoint/port. If Redis is off-cluster, this is an
   external CIDR egress and must go through the reviewed egress catalog
   ([`platform/network/egress-catalog.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/network/egress-catalog.yaml)).

6. **Validate.** Render and smoke:

        helm template validate-gateway deploy/charts/inference-gateway \
          --values deploy/clusters/customer/values/inference-gateway.yaml
        make smoke

   Confirm `SANDBOX_BUDGET_REDIS_URL` / `RESPONSE_CACHE_REDIS_URL` in the rendered Deployment
   point at the managed endpoint, then check `GET /v1/sandbox/budget` returns live counters.

### Fail policy during a Redis outage (deliberate tradeoff)

When the shared Redis is unreachable:

- **Budgets always fail CLOSED** — the gateway returns `503 budget_backend_unavailable` with
  `Retry-After`. This is not configurable and is not weakened by any setting: an outage of the
  spend-enforcement store must never silently admit unmetered traffic.
- **The rate limiter fails CLOSED by default** too (`503 rate_limit_backend_unavailable`), so a
  Redis outage throttles all traffic. Operators who prefer **availability over the throttle**
  during a Redis outage can opt into failing **OPEN**:

        rateLimit:
          enabled: true
          failOpen: true   # env RATE_LIMIT_FAIL_OPEN=true

  With `failOpen: true`, a rate-limit-backend outage admits the request with a logged warning
  and increments `inference_gateway_rate_limit_fail_open_total{sandbox}` so the degraded window
  is visible on the dashboard. This is a **deliberate availability-vs-enforcement tradeoff** —
  during the outage the per-sandbox burst throttle is not enforced. It changes the rate limiter
  only; budgets stay fail-closed. Leave it `false` (the default) when the throttle is a hard
  abuse control you would rather 503 than drop.

- **The response cache always degrades to a miss** (no error) — cache is an optimization.

See [Budget controls](budget-controls.md) for budget sizing and
[Failure modes](failure-modes.md) for the consolidated dependency degradation matrix.

---

## 2. External / HA Qdrant (vector RAG)

The bundled Qdrant chart is **single-instance by design and enforced** — `replicaCount` is
capped at 1 by its `values.schema.json`, because raising replicas on the shared RWO PVC would
corrupt data, not scale it. For production RAG at scale or with an availability target, use an
external managed Qdrant or a Qdrant cluster instead of raising the bundled replica count.

### Options

- **External managed Qdrant** (Qdrant Cloud or a separately-operated Qdrant cluster) — point
  the RAG service at it and stop deploying the bundled chart.
- **Self-run Qdrant cluster** — a multi-node deployment with sharding/replication, operated
  outside this chart (the bundled chart intentionally does not model clustering).

### Steps

1. **Provision** the managed/clustered Qdrant and get its endpoint and API key.

2. **Point the RAG service at it** in your customer RAG overlay
   ([`deploy/clusters/customer/values/rag-service.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/values/rag-service.yaml)):

        retrieval:
          backend: qdrant
          vectorStore:
            url: https://managed-qdrant.internal:6333
            collection: private-ai-platform-kit
            dimensions: 384

   Supply the Qdrant API key from your secret manager, not in values.

3. **Stop deploying the bundled Qdrant** — remove/stop-syncing the `qdrant-vector-store` Argo
   `Application` from your customer `apps.yaml` so only the managed instance serves retrieval.

4. **Open egress** from the `rag` namespace to the managed Qdrant endpoint (reviewed external
   CIDR egress if off-cluster).

5. **Size and migrate.** Match `dimensions` to your embedding model, size storage/replication
   to your corpus, and follow [Qdrant migration](qdrant-migration.md) for the collection
   migration dry-run, source-metadata stamping, `collectionVersion` bump, and rollback. Keep
   [per-tenant isolation](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/production-readiness.md)
   (`retrieval.tenantIsolation`) enabled for multi-tenant corpora.

6. **Validate** the render and a `make rag-smoke` against the external endpoint. See
   [Vector RAG](vector-rag.md) for the full profile walkthrough.

---

## 3. HA Loki (audit / log durability)

The bundled Loki is `SingleBinary` with `replication_factor: 1` and **filesystem** storage —
fine for a lab, but not a durable or replicated log store. The redacted, tamper-evident audit
receipts the gateway emits should not live **only** in this Loki; ship them onward to a SIEM /
object store for long-term hold (see [Audit chain & SIEM forwarding](audit-chain.md)).

### Production path

1. **Move Loki to a scalable mode with object storage.** In the `loki` Argo `Application`
   values ([`deploy/observability/applications.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/observability/applications.yaml)),
   switch `deploymentMode` off `SingleBinary` (e.g. `SimpleScalable` or the distributed mode),
   set `replication_factor` above 1, and configure an object-storage backend (S3/GCS/Azure Blob)
   instead of `filesystem`. Size `retention_period` to your evidence-retention obligation.

2. **If enabling multi-tenancy** (`auth_enabled: true`): the bundled Promtail pushes without an
   `X-Scope-OrgID` header, so you must also set `clients[].tenant_id` on the Promtail values AND
   add the same `X-Scope-OrgID` header on every read path (the Grafana Loki datasource and any
   `audit-anchor` Loki query). The in-file comment in `applications.yaml` documents this exact
   hardening path — follow it or every push 401s and the audit stream silently drops.

3. **Forward audit receipts onward.** Regardless of Loki topology, treat Loki as a queryable
   buffer, not the durable audit hold. The [Audit chain & SIEM forwarding](audit-chain.md)
   runbook covers exporting/anchoring the chain head and shipping receipts to a SIEM.

Loki is an operator-owned platform service the kit does not run for you (see
[Scope and non-goals](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/scope-and-non-goals.md));
the bundled footprint is a working reference, and the object-storage/replicated topology is
yours to size and operate.

---

## Rollback

Every step above is reversible by re-enabling the bundled Argo `Application` and pointing the
config URL back at the in-cluster Service (`budget-redis.budget.svc.cluster.local:6379`,
`qdrant-vector-store.vector.svc.cluster.local:6333`, `loki.monitoring.svc.cluster.local:3100`).
Because the bundled charts are never removed from the repo, rolling back to the reference
footprint for a demo or a debugging session is a one-line values change.

## Related runbooks

- [Budget controls](budget-controls.md) — budget sizing and enforcement.
- [Vector RAG](vector-rag.md) and [Qdrant migration](qdrant-migration.md) — RAG vector profile and migrations.
- [Audit chain & SIEM forwarding](audit-chain.md) — durable audit hold beyond Loki.
- [Failure modes & degradation](failure-modes.md) — consolidated dependency failure-mode matrix.
- [Disaster recovery](disaster-recovery.md) — single-cluster RPO/RTO and restore order.
