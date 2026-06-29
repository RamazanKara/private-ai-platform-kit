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

## Reference Serving Benchmark

A real, reproducible serving measurement for the default local model. This is a hardware reference, not a guarantee — re-run it on your own machine.

`qwen2.5:0.5b` (494M parameters, Q4_K_M; Ollama registry model-layer digest `sha256:c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515`) on an **AMD Ryzen 7 5800X3D** (CPU only, no GPU), 20 runs after warmup, `num_predict=100`, `temperature=0`:

| metric | p50 | p95 | mean |
| --- | --- | --- | --- |
| end-to-end latency (s) | 0.53 | 0.56 | 0.49 |
| generation throughput (tokens/s) | 55.4 | 58.1 | 55.2 |

Reproduce on your own hardware:

```bash
make benchmark-local
# or tune: MODEL=qwen2.5:0.5b RUNS=20 NUM_PREDICT=100 scripts/benchmark-ollama.sh
```

`scripts/benchmark-ollama.sh` reuses an Ollama at `OLLAMA_URL` if reachable, otherwise starts a throwaway Ollama container, pulls the model, warms up, and reports the latency and throughput distribution. GPU/vLLM throughput is materially higher and concurrency-dependent; size the GPU tier with the vLLM/GPU Grafana dashboard and `runbooks/gpu-capacity.md` before production.

## What These Tests Prove

- Gateway admission, auth, trace headers, and runtime forwarding can handle repeat traffic.
- Release reports have machine-checkable metrics.
- Strict gates can reject stale or sample evidence.

## What They Do Not Prove

- Production model quality for a customer's domain.
- Peak GPU throughput under real concurrency.
- Long-context behavior for a customer corpus.
- Full resilience under node, storage, ingress, or secret-backend failures.
