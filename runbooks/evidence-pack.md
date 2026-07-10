# Evidence Pack Runbook

Use this runbook before a customer demo, release review, restore drill review, or incident follow-up. The evidence pack gathers the repo's static readiness controls and points at the latest generated operational artifacts.

## Generate A Static Pack

Run:

    make evidence

The command writes JSON and Markdown reports under `results/evidence/`.

The static pack checks:

- local-first and provider-neutral customer overlays
- gateway and RAG API authentication
- vector RAG profile and Qdrant customer values
- coding-agent workspace controls
- tenant onboarding workflow
- regulated/offline tenant onboarding profile
- traceable sandbox controls
- shared sandbox budget backend
- model catalog and admission controls
- model lifecycle governance
- prompt secret detection
- validation toolchain profiles
- release gates, SLOs, and error-budget evidence
- quota and chargeback governance
- model artifact provenance governance
- egress governance for coding-agent workspaces
- data retention and privacy governance
- advanced chaos drills for RAG, vector store, vLLM, and GPU capacity
- NVIDIA and AMD vLLM profiles
- multi-replica and autoscaling settings
- observability, policy-as-code, supply-chain controls, restore drills, evaluation, and load-test evidence

## Include Live Kubernetes Readiness

After the local lab is synced, run:

    make evidence LIVE=1

Live mode also verifies that key namespaces exist, gateway/RAG/budget deployments have available replicas, the coding-agent workspace PVC is bound, and (when the agent-sandbox CRDs are installed) that the agent-sandbox controller has available replicas (when they are not installed, the pack records that workspaces run on namespace isolation only and `C-ISOLATE` is not claimed).

Note: on `WaitForFirstConsumer` storage classes (including the `kind` default), the workspace PVC stays `Pending` until a pod mounts it, so the PVC control fails on a workspace with no running workload. Start a workspace pod or sandbox before generating live evidence.

## Interpret Results

The Markdown report is the customer-facing summary. The JSON report is useful for automation and audit ingestion.

A failed static control means the repo no longer contains a required platform capability or documented evidence path. Fix the missing control before handoff.

A failed live control usually means the local lab is not fully synced or a workload is not ready. Inspect the rollout, pod events, image pulls, probes, quotas, and storage class before regenerating the pack.

## Recommended Handoff Sequence

Run the static gates and live smoke paths first:

    make toolchain-install
    make validate-full
    make toolchain-report TOOLCHAIN_PROFILE=strict
    make slo-report
    make quota-report
    make model-provenance-report
    make egress-report
    make retention-report
    make smoke RUNTIME_BACKEND=ollama
    make rag-smoke
    make agent-smoke
    make eval
    make restore-drill RUNTIME=local
    make api-contract
    make config-contract
    make image-scan
    make supply-chain-check
    make evidence LIVE=1
    make release-gate-strict
    make release-report-strict

Attach the generated Markdown report to the customer handoff notes and retain the JSON report with release or drill evidence.
