# Release Gates Runbook

Use this runbook before a customer demo, release review, restore-drill review, or production-readiness handoff.

## Gate Definition

Release gates are declared in `slo/release-gates.yaml`. The default local customer-handoff gate checks:

- eval evidence has the required number of passing cases
- load evidence stays inside latency and error-rate limits
- restore-drill evidence passed validation
- strict toolchain evidence has no missing required tools
- SLO evidence has no failed objectives or config errors
- quota and chargeback evidence has no errors
- model provenance evidence covers approved models
- egress and retention governance evidence has no errors
- evidence-pack controls have no failures

## Generate Required Evidence

Run:

    make toolchain-install
    export PATH="$PWD/.tools/bin:$PATH"
    make validate-full
    make toolchain-report TOOLCHAIN_PROFILE=strict
    make slo-report
    make quota-report
    make model-provenance-report
    make egress-report
    make retention-report
    make eval
    make loadtest
    make restore-drill RUNTIME=local
    make evidence LIVE=1

## Check The Gate

Run the default gate for local development and configuration checks:

    make release-gate

Write JSON and Markdown release-gate evidence:

    make release-report

Reports are written under `results/release-gate/`.

For customer demos, release reviews, restore-drill reviews, and production-readiness handoff, run the strict gate after generating the required evidence:

    make release-gate-strict

Write a strict JSON and Markdown release-gate report:

    make release-report-strict

The strict gate fails when a required gate falls back to checked-in `sample-*` evidence or when selected evidence is older than `RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS` hours. The default freshness window is 24 hours:

    make release-gate-strict RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS=48

## Interpreting Failures

A failed release gate means the handoff evidence is incomplete or below the defined threshold. Do not promote the lab to a customer handoff until the failed gate has been rerun and the report passes.

If the strict gate reports sample evidence, rerun the matching evidence command from the previous section. Sample artifacts prove report shape only; they do not prove the current build is ready for a customer handoff.

Tune thresholds only through reviewed changes to `slo/release-gates.yaml`; do not edit generated evidence to make a gate pass.
