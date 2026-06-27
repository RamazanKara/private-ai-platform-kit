# Benchmarks And Evals

The repository includes lightweight eval and load-test paths for release hygiene. They are not a substitute for customer workload benchmarks.

## Evals

Eval suites live under `evals/`.

```bash
make eval-local
make eval
SUITE=evals/coding-agent-suite.yaml make eval
```

`make eval-local` uses an ephemeral mock runtime for current release-gate evidence without a live cluster. `make eval` targets a live gateway. The default smoke suite proves that the selected gateway/model path can answer basic platform prompts within configured latency limits. The coding-agent suite exercises agent-oriented prompts. Customer teams should add cases for their languages, tools, retrieval corpus, safety policy, and expected failure modes.

## Load Tests

For an isolated gateway-path check without a cluster or real model runtime:

```bash
make loadtest-local
```

For a live gateway:

```bash
GATEWAY_URL=http://127.0.0.1:8080 make loadtest
```

The release gates check request count, error rate, p95 latency, and p99 latency. Tune thresholds in `slo/release-gates.yaml` only when the target environment and workload justify the change.

## What These Tests Prove

- Gateway admission, auth, trace headers, and runtime forwarding can handle repeat traffic.
- Release reports have machine-checkable metrics.
- Strict gates can reject stale or sample evidence.

## What They Do Not Prove

- Production model quality for a customer's domain.
- Peak GPU throughput under real concurrency.
- Long-context behavior for a customer corpus.
- Full resilience under node, storage, ingress, or secret-backend failures.
