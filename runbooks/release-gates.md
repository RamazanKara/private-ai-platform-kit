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

Run:

    make release-gate

Write JSON and Markdown release-gate evidence:

    make release-report

Reports are written under `results/release-gate/`.

## Interpreting Failures

A failed release gate means the handoff evidence is incomplete or below the defined threshold. Do not promote the lab to a customer handoff until the failed gate has been rerun and the report passes.

Tune thresholds only through reviewed changes to `slo/release-gates.yaml`; do not edit generated evidence to make a gate pass.
