# Model Quality & Drift Monitoring Runbook

Release gates evaluate a model *before* it is promoted. This runbook covers the complementary
concern: detecting when a model already in production **drifts** — behaves worse than when it was
approved — because of a runtime upgrade, a config change, a prompt/template change, or an upstream
model artifact swap.

## Signals

Two layers, cheap to expensive:

1. **Live proxy metrics (Prometheus).** The `governance.rules` group in
   [`ai-platform-alerts.yaml`](../deploy/observability/alerts/ai-platform-alerts.yaml) alerts on:
   - `ModelOutputGuardrailSpike` — a sustained rise in output-guardrail redactions/blocks
     (`inference_gateway_output_guardrail_total`), which can mean the model started leaking
     credentials/PII it previously did not.
   - `InferenceAdmissionRejectionSpike` — elevated admission rejections, often a prompt/template
     or policy regression.
   These are *proxies*, not quality scores: they catch some regressions early but do not measure
   answer quality.

2. **Scheduled evaluation (authoritative).** Run the scored suites on a cadence against the live
   model and compare aggregate metrics to the approved baseline:
   - `SUITE=platform/evals/coding-agent-suite.yaml make eval` — functional coding-agent quality.
   - `make rag-eval` — retrieval hit rate, context precision, and faithfulness.
   - `SUITE=platform/evals/safety-suite.yaml make eval` — jailbreak/injection resistance.
   Keep each run's JSON under `results/evals/` and diff the aggregate scores against the values
   recorded in the model's promotion request. A drop past the suite thresholds is drift.

## Cadence

Wire the scheduled evals into a CronJob (or the existing CI `scheduled-proof` job) so the
comparison runs at least daily and after every runtime or model change. Treat a threshold breach
like a failed release gate: hold traffic on the affected model (remove it from
`runtime.allowedModels`), open an incident per [incident-response.md](incident-response.md), and
re-run promotion review before restoring it.

## Respond

1. Confirm the drift with a re-run (rule out a transient runtime blip).
2. Identify the change window (runtime image, model revision, chart values, prompt template).
3. Roll back the offending change or pin the model revision
   (`platform/governance/model-provenance.yaml` immutableRef) to the last-good artifact.
4. Record the incident and the corrected baseline in the model's promotion request.
