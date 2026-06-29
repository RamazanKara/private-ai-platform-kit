#!/usr/bin/env bash
# Reproducible local serving micro-benchmark for an Ollama model.
#
# Reports end-to-end latency (p50/p95/mean) and generation throughput (tokens/s)
# for a fixed prompt. Uses an existing Ollama at $OLLAMA_URL if reachable, otherwise
# starts a throwaway Ollama container. Results are a reference for the given hardware,
# not a guarantee; re-run on your own machine before relying on the numbers.
#
#   MODEL=qwen2.5:0.5b RUNS=20 NUM_PREDICT=100 scripts/benchmark-ollama.sh
set -euo pipefail

MODEL="${MODEL:-qwen2.5:0.5b}"
RUNS="${RUNS:-20}"
NUM_PREDICT="${NUM_PREDICT:-100}"
PORT="${PORT:-11436}"
URL="${OLLAMA_URL:-http://127.0.0.1:${PORT}}"
OWN_CONTAINER=0

cleanup() { [ "$OWN_CONTAINER" = 1 ] && docker rm -f ollama-benchmark >/dev/null 2>&1 || true; }
trap cleanup EXIT

if ! curl -fsS "${URL}/api/tags" >/dev/null 2>&1; then
  command -v docker >/dev/null 2>&1 || { echo "need docker, or a running Ollama at ${URL} (set OLLAMA_URL)"; exit 1; }
  docker rm -f ollama-benchmark >/dev/null 2>&1 || true
  docker run -d --name ollama-benchmark -p "${PORT}:11434" ollama/ollama >/dev/null
  OWN_CONTAINER=1
  for _ in $(seq 1 30); do curl -fsS "${URL}/api/tags" >/dev/null 2>&1 && break; sleep 1; done
fi

if [ "$OWN_CONTAINER" = 1 ]; then
  docker exec ollama-benchmark ollama pull "$MODEL" >/dev/null
else
  curl -fsS "${URL}/api/pull" -d "{\"name\":\"${MODEL}\"}" >/dev/null
fi

python3 - "$URL" "$MODEL" "$RUNS" "$NUM_PREDICT" <<'PY'
import json, statistics, subprocess, sys, time, urllib.request
url, model, runs, npredict = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

def gen():
    body = json.dumps({"model": model, "prompt": "Write one concise sentence about Kubernetes.",
                       "stream": False, "options": {"num_predict": npredict, "temperature": 0}}).encode()
    start = time.time()
    request = urllib.request.Request(url + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read())
    eval_seconds = data.get("eval_duration", 1) / 1e9
    return time.time() - start, (data.get("eval_count", 0) / eval_seconds if eval_seconds > 0 else 0.0)

for _ in range(2):
    gen()  # warmup
latencies, throughputs = [], []
for _ in range(runs):
    wall, tps = gen()
    latencies.append(wall)
    throughputs.append(tps)

def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

cpu = subprocess.run(["bash", "-lc", "lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1"],
                     capture_output=True, text=True).stdout.strip() or "unknown CPU"
print()
print(f"Model {model}  |  runs={runs}  num_predict={npredict}  temperature=0  |  {cpu}")
print()
print("| metric | p50 | p95 | mean |")
print("| --- | --- | --- | --- |")
print(f"| end-to-end latency (s) | {statistics.median(latencies):.2f} | {pct(latencies, 0.95):.2f} | {statistics.mean(latencies):.2f} |")
print(f"| generation throughput (tokens/s) | {statistics.median(throughputs):.1f} | {pct(throughputs, 0.95):.1f} | {statistics.mean(throughputs):.1f} |")
PY
