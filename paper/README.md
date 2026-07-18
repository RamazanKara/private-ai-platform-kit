# Paper reproducibility artifact

Harness and tools that produce the paper's measurable claims. All of it drives the
runnable `private-ai-platform-kit` gateway as an external process.

The recorded results belong to the `v1.1.0-paper` tag. Use that tag when reproducing the
published numbers; the current release may have different code and performance.

## cost-of-compliance/

- `mock_runtime.py` raw asyncio OpenAI-compatible backend with a fixed think-time
  (`--delay-ms`); sets `TCP_NODELAY`/`TCP_QUICKACK` to avoid loopback artifacts.
- `bench.py` closed-loop load generator; emits a latency/throughput JSON.
- `run_experiment.py` orchestrates the configurations (baseline, ungoverned gateway,
  per-control toggles, fully governed), sweeps concurrency and backend think-time,
  and writes `results/raw-results.json`.
- `microbench.py` times the gateway's real governance functions with no network in
  the path; writes `results/micro-results.json`.
- `analyze.py` aggregates per-cell statistics and the scalar numbers cited in the
  paper; writes `results/aggregate.json` and `results/paper-numbers.json`.
- `figures.py` renders the paper figures (needs matplotlib).

## evidence-model/

- `audit_chain.py` builds a tamper-evident hash chain over gateway audit events,
  provides the auditor query (by request id, by time window), and demonstrates
  detection of a mutated record on both synthetic and real gateway logs.

## conformance/

- `conformance.py` starts the fully governed gateway, attempts a fixed set of
  forbidden actions, asserts each is blocked with the expected reason, and confirms a
  legitimate request still succeeds. Writes `results/conformance-evidence.json`.

See [PAPER.md](PAPER.md) for the claim-to-command mapping.
