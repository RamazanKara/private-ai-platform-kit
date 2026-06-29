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

## Changing Retention

Tune `retentionDays` and classifications only through reviewed changes to `platform/governance/data-retention.yaml`. If a customer requires longer retention or stricter classification, update the policy first and regenerate `make retention-report`.
