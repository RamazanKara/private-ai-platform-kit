# Incident Runbook: Restore Drill Failure

## What The Restore Drills Prove (read this first)

There are two distinct drills, and they prove different things. Do not conflate
them when reviewing evidence:

- **Restore-tooling smoke (Redis AOF fixture).** The default `make restore-drill`
  replays a *synthetic 2-key Redis AOF fixture* into a disposable container and
  runs data checks. This proves the restore *pipeline/tooling* works -- it can
  stand up a target, replay a backup, and validate it. It does **not** prove
  that any production data store is recoverable. Treat a green
  `restore-pass-rate` from this path as "the restore tooling runs," nothing more.
- **Real data-recovery drill (Qdrant).** `RUNTIME=local RESTORE_DRILL_QDRANT_DATA=1
  make restore-drill` runs an end-to-end recovery of real vectors: it seeds a
  known set of points into a throwaway probe collection, exports a Qdrant
  snapshot, deletes the collection, restores it from the snapshot, and asserts
  the recovered point count matches. This is the drill that actually proves
  stored vector data is recoverable. The spec lives at
  `chaos/drills/qdrant-data-restore.yaml`.

## Symptoms

`make restore-drill` exits non-zero, the restore-drill CronJob fails, or Prometheus fires `RestoreDrillFailed`.

## Inspect

    kubectl get jobs,pods -n restore-drill
    kubectl logs -n restore-drill job/<job-name>
    kubectl get pods -n restore-drill -l restore-drill/ephemeral=true

For the local Docker runtime (restore-tooling smoke):

    restore-drill run --config backup/restore-drill/drills/local-redis-aof.yaml --runtime docker --no-cleanup --format json

## Real Data-Recovery Drill (Qdrant)

Run against a live local cluster with a reachable Qdrant (port-forward or
in-cluster). It is guarded behind `RUNTIME=local` and `RESTORE_DRILL_QDRANT_DATA=1`
so it never runs by accident, and it only touches its own ephemeral probe
collection (never the production `customer-platform-knowledge` collection).

    # Make Qdrant reachable (example: port-forward the vector service)
    kubectl -n vector port-forward svc/qdrant-vector-store 6333:6333 &

    RUNTIME=local RESTORE_DRILL_QDRANT_DATA=1 make restore-drill

Procedure executed by the drill:

1. Seed N known vectors into a throwaway probe collection.
2. Export a Qdrant snapshot of that collection.
3. Delete the collection (simulate data loss).
4. Restore the collection from the snapshot.
5. Assert the recovered point count equals N and is non-trivially `> 0`.

Tunables: `RESTORE_DRILL_QDRANT_URL` (default `http://127.0.0.1:6333`),
`RESTORE_DRILL_QDRANT_COLLECTION`, `RESTORE_DRILL_QDRANT_DIMENSIONS`,
`RESTORE_DRILL_QDRANT_POINTS`. The report is written to
`results/restore-drill/qdrant-data-restore-<stamp>.json`. A failed assertion or
an unreachable Qdrant produces a `validation_passed: false` record -- results
are never faked.

## Likely Causes

The backup artifact is missing, object-storage credentials are wrong, the restore image lacks Redis/PostgreSQL/MySQL tools, or a validation check no longer matches the restored data. For the Qdrant drill: Qdrant is unreachable, snapshots are disabled, or the snapshot volume is not writable.

## Mitigation

Rerun with `--no-cleanup`, inspect the retained target pod or container, fix credentials or backup source paths, and only then update checks if the data contract intentionally changed.

## Evidence

Save the run JSON, compliance report, retained pod name, relevant logs, and backup artifact timestamp. For the Qdrant drill, save the `qdrant-data-restore-<stamp>.json` report showing seeded vs recovered point counts.
