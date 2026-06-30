# Data Retention Runbook

Use this runbook when reviewing customer handoff evidence, audit-log handling, RAG knowledge, restore reports, or coding-agent workspace data.

## Policy

The retention policy lives in `platform/governance/data-retention.yaml`.

Default policy:

- Gateway and RAG audit logs keep hashes, lengths, IDs, timing, status, usage, and result IDs. They must not store raw prompts, completions, or RAG queries.
- Generated evidence is retained for release and audit review, with sample files committed and generated run files ignored.
- RAG knowledge and vector-store collections require review before customer use.
- Coding-agent workspace PVC data should be purged on tenant offboarding.
- Model governance evidence has longer retention because it supports model lifecycle audit.

## Validate Retention

Run:

    make retention-check

Generate JSON and Markdown retention evidence:

    make retention-report

Reports are written under `results/retention/`.

## Customer Handoff

Before handoff, confirm:

- audit logs do not contain raw prompt, completion, or query text
- generated evidence is retained according to customer policy
- RAG knowledge and vector-store collections have been approved for the environment
- agent workspace PVCs have an offboarding and purge process
- model governance reports are retained with model approval evidence

## Erasing a RAG Source (Right-to-Erasure)

Ingestion is upsert-only, so removing a source from the manifest leaves its vectors in
Qdrant. To purge a source's vectors (right-to-erasure or source decommission), delete by
`source_id`:

```bash
# Purge across all collection versions:
python scripts/rag-ingest.py --delete --source-id <source-id> \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION"

# Or scope the delete to one collection version:
python scripts/rag-ingest.py --delete --source-id <source-id> \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION" --collection-version v2
```

This issues a filtered Qdrant `points/delete` on the `source_id` payload field written at
ingest time. To re-index a source after a content change, run `--delete --source-id <id>`
followed by `--write`. Record the deletion in the retention evidence for the environment.

## Age-Based Retention Purge

Ingested chunks carry an `ingestedAtEpoch` timestamp, so the `retentionDays` policy can be
enforced by purging points older than the retention window (run on a schedule, e.g. a CronJob):

```bash
python scripts/rag-ingest.py --purge --older-than-days 180 \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION"
```

This deletes every point whose `ingestedAtEpoch` is older than the cutoff (optionally scoped
to a `--collection-version`). Set `--older-than-days` to the `retentionDays` value from
`platform/governance/data-retention.yaml` for the relevant retention class, and retain the
purge summary as retention evidence.

## Changing Retention

Tune `retentionDays` and classifications only through reviewed changes to `platform/governance/data-retention.yaml`. If a customer requires longer retention or stricter classification, update the policy first and regenerate `make retention-report`.
