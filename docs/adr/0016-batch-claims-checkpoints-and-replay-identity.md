# 0016. Batch claims have owners, progress is checkpointed, items replay as the submitter

- Status: Accepted
- Date: 2026-10-04
- Deciders: Platform maintainer
- Refines: [0011](0011-async-files-and-batch-api.md) (its decisions stand; this records how three of
  its guarantees are now met)

## Context

ADR 0011 promised that a batch is durable, safe to process with N workers, and replayed only
within its tenant's identity. A review of the shipped worker (`src/inference-gateway/app/batch_worker.py`)
found each promise weaker than stated:

- **N workers.** A claim was keyed only by batch. A worker that stalled past the reclaim interval
  kept processing, and later acknowledged, a batch the reaper had already handed to another
  replica: two workers running the same items, and the second one's claim deleted by the first.
- **Durability.** The worker read the whole input file into memory, held every result until the
  end, and a redelivered batch replayed from line one. A 100 MB file meant hundreds of MB of
  resident memory, and a crash near the end charged every item twice.
- **Tenant identity.** The worker replayed with one API key and a per-item `X-Sandbox-ID`. That
  key had to be unbound to serve every tenant, which made it a key able to act as any tenant at
  any time, and the receipts named the worker rather than the person who submitted the batch.

## Decision

- **Owner tokens.** `BatchStore.claim()` returns a `Claim` carrying a random token, stored with
  the claim time (`<token>|<time>` in Redis). `heartbeat(claim)` and `ack(claim)` act only when the
  token still holds the claim; the reaper reads the time after the separator. The worker
  heartbeats between chunks and stops without finalizing when the token no longer holds. Claims
  written before this change parse as token-less, age out, and are re-queued normally.
- **Streamed, checkpointed processing.** `ObjectStore.open_lines()` streams input from every
  backend. Results are written as parts of `BATCH_WORKER_PART_LINES` items under
  `<tenant>/parts/<batch>/`, and after each part the batch record stores `processed_lines` and the
  part counts. A resumed batch skips the processed lines, so a crash replays at most one part.
  Parts are written before the record moves forward, so the record never points at a missing
  part; parts are combined through a spooled temporary file at finalize and then deleted.
- **Replay bound to a running batch.** Batches record their submitter (from the audit principal).
  An API-key record with the `batch_replay` scope may assert a tenant only when `X-Batch-ID`
  names that tenant's batch and the batch is `in_progress`; the request is then treated as bound
  to that sandbox, and the receipt records `batch_id` and `on_behalf_of`. Any other use of the key
  is `403 batch_replay_not_authorized`. A worker key without the scope behaves as before.

## Consequences

- Two workers can no longer finish the same batch, and a stalled worker cannot interfere with
  the replica that took over.
- Worker memory is bounded by one part plus one chunk, independent of file size. The worker pod
  needs a writable `/tmp` sized for one assembled result file; the chart mounts one.
- A leaked worker key is limited to tenants with running batches and leaves a receipt per item
  naming the batch and its submitter. Operators should issue the worker key as a scoped record.
- At-least-once delivery still applies inside a part: a crash between a part's last item and its
  checkpoint replays that part's items.

## Alternatives considered

- **Per-batch credentials minted for the submitter.** The gateway would mint a short-lived token
  for each batch and the worker would present it. This gives true delegation, but requires the
  gateway to issue and store tokens, and API-key submitters have no identity provider to
  delegate from. Rejected in favor of binding the worker to the running batch.
- **Per-item checkpoints.** Exact resume with no replay, at the cost of a store write per item.
  Rejected: parts bound the replay at a small, configurable cost.
- **Redis streams with consumer groups instead of lists.** Built-in ownership and pending
  entries, but a different data model to migrate. Rejected while the list-based queue, with
  tokens, meets the same guarantees.
