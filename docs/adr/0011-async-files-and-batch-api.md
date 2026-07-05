# 0011. Asynchronous Files and Batch API

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

## Context

The gateway is OpenAI-compatible and already offers a **synchronous** fan-out at
`POST /v1/batch-inference`: it runs a bounded set of chat requests concurrently and returns
every result inline, within one request timeout. ADR 0005 and `docs/scope-and-non-goals.md`
listed the OpenAI **asynchronous** file-batch API (`/v1/files` + `/v1/batches`) as an explicit
non-goal, because it is a stateful subsystem rather than a request handler.

The requirement now is to support large, offline, bulk workloads that do not fit a single
request: submit thousands of requests as a JSONL file, have them processed asynchronously under
the same governance as live traffic, poll for status, and retrieve the results (and per-item
errors) later. That is the real OpenAI Batch API shape (`input_file_id`, `completion_window`,
`status: validating → in_progress → finalizing → completed`, `output_file_id`/`error_file_id`,
`request_counts`, cancellation).

Constraints that shape the design:

- **The gateway must stay stateless and horizontally scalable.** It carries no local disk or
  batch state today (no PVC, no StatefulSet); that property must survive.
- **Governance must be enforced exactly once, identically to live traffic.** Every batched item
  has to pass the model allowlist, admission caps, prompt-secret policy, per-tenant budget, the
  output guardrail, tenant isolation, and the tamper-evident audit chain (ADR 0006), with no
  second, drifting copy of that policy.
- **Durable, cancellable, horizontally safe.** A submitted batch must survive a worker restart,
  be cancellable mid-run, honor its completion window, and be safe to process with N workers.
- **Minimal heavyweight dependencies.** The repo keeps a lean, hash-pinned dependency set and
  hand-rolls small primitives (e.g. the audit chain) rather than pulling large SDKs.

## Decision

Add an OpenAI-compatible asynchronous **Files** and **Batches** API as a durable,
horizontally-safe subsystem. State is externalized so the gateway stays stateless.

**API surface (served by the gateway).**

- Files: `POST /v1/files` (multipart, `purpose=batch`), `GET /v1/files/{id}`,
  `GET /v1/files/{id}/content`, `DELETE /v1/files/{id}`, `GET /v1/files`.
- Batches: `POST /v1/batches` (`{input_file_id, endpoint, completion_window, metadata}`),
  `GET /v1/batches/{id}`, `POST /v1/batches/{id}/cancel`, `GET /v1/batches` (paginated).
- All of these are authenticated and tenant-bound exactly like the existing endpoints. The batch
  `endpoint` is restricted to the governed inference routes (`/v1/chat/completions`,
  `/v1/completions`, `/v1/embeddings`), matching OpenAI's allowed set.

**Storage.**

- **Object store (S3 / MinIO)** holds the JSONL blobs (input, output, and error files), keyed by
  tenant and file id. Blobs do not belong in Redis.
- **Redis** holds file metadata, batch job records (status, `request_counts`, file ids,
  timestamps, `completion_window`, `metadata`), and the **durable work queue** as a reliable
  Redis list pair: a `pending` list plus a `processing` list with a claim-time hash the reaper
  reads. Redis is already a platform dependency (`budget-redis`) and this uses only single atomic
  commands (`RPOPLPUSH`, `LREM`, `LPUSH`, `HSET`/`HDEL`), with no Lua or multi/exec to reason about.
  AOF persistence makes the queue durable.

**Processing (`batch-processor`, a new stateless Deployment).**

- Claims a batch with an atomic `RPOPLPUSH` from the `pending` list to the `processing` list, so
  each batch is owned by exactly one worker; a reaper re-queues batches whose claim has been idle
  past a threshold (crashed-worker recovery). All worker state lives in Redis and the object
  store, so the Deployment scales horizontally and restarts freely.
- For each batch: transition `validating → in_progress`; stream the input file line by line; for
  each line **replay the request against the gateway's own governed HTTP endpoint** (e.g.
  `POST /v1/chat/completions`) carrying the batch's tenant/sandbox identity and a service
  credential, at a bounded concurrency; collect `{custom_id, response|error}` into the output and
  error JSONL; update `request_counts` in Redis; on completion upload the output/error files, set
  `output_file_id`/`error_file_id`, and transition to `completed` (or `failed`).
- **Replaying through the gateway is the core decision**: governance stays single-sourced. The
  worker holds no policy. Allowlist, admission, prompt-secret handling, budget, guardrail, tenant
  isolation, and audit all execute in the gateway per item, identically to live traffic, so batch
  items produce the same audit receipts (extending the ADR 0006 chain) and obey the same budgets.
- **Cancellation** is a per-batch flag in Redis, checked between items; the worker finalizes
  partial output and sets `cancelled`. **Expiry**: a batch not finished within its
  `completion_window` is swept to `expired` by a reaper loop, which also reclaims orphaned work.
- **At-least-once** delivery is made safe by idempotent output writes (deterministic object keys
  per batch) and by removing the batch from the `processing` list only after the output/error
  files are durably written; a redelivered batch is a no-op because processing checks for a
  terminal state first.

**Object-store access.** A minimal in-tree S3 client (SigV4 request signing over the existing
`httpx` + `cryptography` HMAC, path-style addressing for MinIO) behind an `ObjectStore`
abstraction, with a filesystem/in-memory implementation for tests and local runs. This avoids a
heavyweight cloud SDK, matching the repo's hand-rolled-primitive philosophy (cf. ADR 0006).

**Tenancy.** The batch record binds its tenant at creation. The worker replays only within that
tenant's identity, and the gateway enforces tenant binding on every replayed request, so a batch
cannot cross tenants.

## Consequences

- New backing state is introduced (an object store and a durable Redis queue) plus one new
  worker Deployment. The **gateway itself stays stateless**; all batch state is external. Operators
  who do not enable the feature pay nothing (the endpoints and worker are gated off by default).
- Governance is single-sourced: because items are replayed through the gateway, per-item audit
  receipts extend the existing chain, admission and budget apply per item, and **partial completion
  is normal** (some items land in the output file, some in the error file with the standard error
  envelope). No batch-specific copy of the policy exists to drift.
- This **reverses the async-batch non-goal** in ADR 0005 and `docs/scope-and-non-goals.md`, which
  are updated when the subsystem lands. The synchronous `/v1/batch-inference` route is unchanged
  and remains the right tool for small inline batches.
- Operational cost: an object store (MinIO locally, external S3 for customers) and a durable Redis
  must be run, and the `batch-processor` Deployment sized. This is documented for both the local
  and customer overlays, with the feature off by default.
- The self-hosted context means OpenAI's 50% batch **cost discount** has no analogue; the
  `completion_window` is honored as an **expiry** bound, not a scheduling SLA. Streaming batch
  output is out of scope; the batch `endpoint` set is limited to chat/completions/embeddings.

**Phased rollout** (each phase independently shippable and gated by `make validate` + `make
coverage` + `make production-check`):

1. **Foundations**: settings, the `ObjectStore` abstraction + fake, Redis file/batch stores,
   config/api contracts scaffolding. No externally visible behavior.
2. **Files API**: `/v1/files` endpoints over the object store + Redis metadata, size/line caps.
3. **Batches API (state)**: `/v1/batches` create/get/cancel/list, job records, enqueue to the
   queue; batches reach `in_progress` but are not yet processed.
4. **`batch-processor` worker**: the Deployment consumes, replays through the gateway, writes
   output/error files, updates counts/status, cancellation, expiry/reaper, crash recovery.
5. **Governance & hardening**: per-item budget/audit correctness, tenant-isolation enforcement in
   replay, backpressure, at-least-once idempotency, error taxonomy, adversarial review.
6. **Deploy, SDK, docs, evidence**: MinIO chart (local) + customer S3 overlay, worker chart with
   HPA/PDB/NetworkPolicy, umbrella wiring, SDK methods, scope/architecture/README/runbook updates,
   release-gate and evidence-pack integration.

## Alternatives considered

- **In-gateway, in-process worker (no separate service).** Simplest to build: an `asyncio`
  background task in the gateway drains a queue. Rejected for the production goal because it makes
  the gateway stateful, couples long bulk runs to the serving pods (a big batch competes with live
  traffic), and loses horizontal safety and clean restart semantics. This was the lighter "MVP"
  tier that was explicitly not chosen.
- **Import the governance code into the worker (no HTTP replay).** Runs items in-process in the
  worker against the policy modules directly. Rejected: it duplicates the gateway's app wiring and
  risks the batch path and the live path drifting apart, the exact failure the single-sourced
  design prevents. Replaying through the gateway keeps one enforcement point.
- **PVC filesystem for blobs instead of an object store.** Simpler locally, but `ReadWriteOnce`
  ties files to one node and `ReadWriteMany` is storage-class-dependent and awkward across the
  gateway and worker. An object store is the natural multi-writer blob backend and what customers
  already operate; the filesystem backend is kept only as an `ObjectStore` implementation for tests
  and single-node local runs.
- **A heavyweight cloud SDK (`boto3`/`aioboto3`).** Pulls a large, transitive dependency tree into
  a repo that deliberately hash-pins a lean set. Rejected in favor of a small in-tree SigV4 client,
  consistent with how the repo hand-rolls primitives (ADR 0006). If the signing surface grows
  beyond object PUT/GET/DELETE/list, this can be revisited.
- **Postgres (or another RDBMS) for job state.** Robust and queryable, but adds a new stateful
  dependency when Redis, already present, covers small job records plus a durable list-based queue.
  Rejected to avoid new infrastructure; revisit if batch metadata grows relational query needs.
