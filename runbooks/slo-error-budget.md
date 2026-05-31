# SLO And Error Budget Runbook

Use this runbook to review whether the local lab or a customer-owned cluster is healthy enough for demos, release reviews, or coding-agent onboarding.

## SLO Definition

SLO objectives live in `slo/objectives.yaml`.

The default local customer-handoff profile covers:

- inference gateway request error rate
- inference gateway p95 and p99 latency
- smoke evaluation pass rate
- restore-drill pass rate
- coding-agent platform readiness controls from the evidence pack

Prometheus alert references are validated against `observability/alerts/ai-platform-alerts.yaml`.

## Generate Evidence

Run the evidence-producing commands first:

    make loadtest
    make eval
    make restore-drill RUNTIME=local
    make evidence LIVE=1

For static reviews, committed sample evidence is enough to validate the SLO machinery:

    make slo-check

Generate JSON and Markdown SLO evidence:

    make slo-report

Reports are written under `results/slo/`.

## Interpreting Failures

Treat a failed SLO objective as a customer-readiness blocker for that profile.

- Error-rate failures usually mean the gateway, runtime, budget backend, or API-key clients are unhealthy.
- Latency failures usually mean runtime capacity, model size, GPU placement, or queueing policy needs tuning.
- Eval failures usually mean the selected model or prompt path is not ready for the customer's task mix.
- Restore failures mean backup evidence is not trustworthy enough for handoff.
- Evidence-control failures mean a required coding-agent platform control is missing from the handoff pack.

Tune targets only through reviewed changes to `slo/objectives.yaml`. Do not edit generated evidence to make an SLO pass.

## Customer Adaptation

For a real customer cluster, set targets to the contract they expect. Keep the objective IDs stable so dashboards, release gates, and evidence automation can compare reports across releases.
