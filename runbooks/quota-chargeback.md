# Quota And Chargeback Runbook

Use this runbook when onboarding a tenant, sizing a coding-agent workspace, or preparing customer chargeback/showback evidence.

## Policy

Reviewed quota plans live in `governance/quota-plans.yaml`.

Each plan connects:

- Kubernetes ResourceQuota expectations
- gateway sandbox budget ceilings
- workspace PVC sizing
- owner, cost-center, environment, and sandbox labels
- source manifests that enforce or document the plan

## Validate Current Plans

Run:

    make quota-check

Generate JSON and Markdown evidence:

    make quota-report

Reports are written under `results/quota/`.

## Before Tenant Onboarding

For each customer tenant:

- confirm `platform.ai/owner`, `platform.ai/cost-center`, `platform.ai/environment`, and `platform.ai/sandbox-id`
- confirm the tenant onboarding spec matches the reviewed quota plan
- confirm the gateway sandbox budget is at least as large as the tenant's reviewed budget
- confirm the workspace PVC size and max concurrent agent count match expected usage
- attach the quota report to the customer handoff or tenant onboarding ticket

## Chargeback

Use the required labels as the stable attribution keys for OpenCost, Prometheus, logs, evidence packs, and customer reporting. Customers can add their own labels, but should keep these stable across upgrades so cost history remains comparable.

## Adjusting Quotas

Tune `governance/quota-plans.yaml` first, then update tenant onboarding specs or gateway values. Do not raise live Kubernetes or gateway limits without a matching quota-plan update and refreshed `make quota-report` evidence.
