#!/usr/bin/env bash
set -uo pipefail

# Unscripted driver for the real-run README recording (scripts/demo-real.tape):
# every command below executes live against the demo workspace provisioned by
# `make agent-sandbox-demo`. No echoed fakes.

NS="${AGENT_NAMESPACE:-ai-agents-demo}"
SB="${SANDBOX_ID:-agent-demo}"
GW="http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080"
KEY="${PLATFORM_API_KEY:-local-development-only}"
MODEL="${DEMO_MODEL:-qwen2.5:0.5b}"

echo "# the workspace: a hardened agent-sandbox pod (standard runtime)"
kubectl -n "$NS" get sandbox,pod
sleep 1

echo
echo "# exfiltration attempt from inside the sandbox (non-catalog destination)"
kubectl -n "$NS" exec "$SB" -- python3 -c '
import urllib.request
try:
    urllib.request.urlopen("https://198.51.100.10", timeout=5)
    print("REACHED (should never happen)")
except Exception as exc:
    print("BLOCKED by default-deny egress:", type(exc).__name__)'
sleep 1

echo
echo "# a real coding agent (aider) working through the governed gateway"
kubectl -n "$NS" exec "$SB" -- sh -c "
  cd /workspace && rm -f hello.py && \
  printf -- '- name: openai/${MODEL}\n  extra_params:\n    extra_headers:\n      X-Sandbox-ID: ${SB}\n' > .aider.model.settings.yml && \
  env HOME=/workspace XDG_CACHE_HOME=/tmp \
    OPENAI_API_BASE=${GW}/v1 OPENAI_API_KEY=${KEY} \
    AIDER_STREAM=false AIDER_PRETTY=false AIDER_CHECK_UPDATE=false \
    AIDER_ANALYTICS=false AIDER_SHOW_MODEL_WARNINGS=false AIDER_SHOW_RELEASE_NOTES=false \
  /venv/bin/aider --model openai/${MODEL} --no-git --yes-always \
    --message 'Create hello.py that prints: Hello from the governed sandbox' hello.py" || true
sleep 1

echo
echo "# its actions are receipts on the tamper-evident audit chain"
kubectl -n inference logs deployment/inference-gateway-inference-gateway --tail=400 \
  | grep '"event": "inference_request"' | tail -2 | cut -c1-190
echo
echo "# isolated workspace - fail-closed egress - receipts on record"
