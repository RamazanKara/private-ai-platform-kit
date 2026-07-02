#!/usr/bin/env bash
set -euo pipefail

# End-to-end demo of the hardened agent-sandbox workspace path (ADR 0009):
# controller install -> hardened workspace -> fail-closed egress -> evidence.
# When the full platform (inference gateway) is running, the demo also drives
# a real request from inside the sandbox through the governed model path.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required for the agent-sandbox demo."
require_cmd helm "Helm is required for the agent-sandbox demo."

ENVIRONMENT="${ENVIRONMENT:-local}"
# The demo provisions its own workspace instance so it never fights a
# GitOps-managed agent-workspace release for resource ownership.
AGENT_NAMESPACE="${AGENT_NAMESPACE:-ai-agents-demo}"
SANDBOX_ID="${SANDBOX_ID:-agent-demo}"
EVIDENCE_DIR="${EVIDENCE_DIR:-results/evidence}"

log "step 1/4: installing the agent-sandbox controller (vendored, checksummed)"
"$ROOT/scripts/agent-sandbox-install.sh"

log "step 2/4: hardened workspace + fail-closed egress probe"
VALUES_FILE="$ROOT/deploy/clusters/${ENVIRONMENT}/values/agent-workspace.yaml"
[[ -f "$VALUES_FILE" ]] || die "missing agent workspace values file ${VALUES_FILE}"
# The demo provisions its own release in its own namespace so it never fights
# the GitOps-managed instance for ownership.
helm upgrade --install agent-workspace "$ROOT/deploy/charts/agent-workspace" \
  --namespace "$AGENT_NAMESPACE" \
  --create-namespace \
  --values "$VALUES_FILE" \
  --set namespace.name="$AGENT_NAMESPACE" \
  --set sandbox.id="$SANDBOX_ID" >/dev/null
AGENT_NAMESPACE="$AGENT_NAMESPACE" SANDBOX_ID="$SANDBOX_ID" \
  "$ROOT/scripts/agent-sandbox-smoke.sh"

log "step 3/4: governed model path and agent-action receipts"
GATEWAY_URL="http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"
DEMO_MODEL="${DEMO_MODEL:-qwen2.5:0.5b}"
# Pinned tag: the platform's own block-latest-tags policy (rightly) denies
# tag-less images at admission.
AGENT_IMAGE="${AGENT_IMAGE:-paulgauthier/aider:v0.86.2}"
REAL_AGENT="${REAL_AGENT:-1}"
if kubectl -n inference get deployment inference-gateway-inference-gateway >/dev/null 2>&1; then
  REQUEST_ID="agent-sandbox-demo-$(date -u +%Y%m%dT%H%M%SZ)"

  log "allowed action: chat completion through the governed path"
  kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- \
    curl -fsS -o /dev/null \
    -H "Content-Type: application/json" \
    -H "X-Request-ID: ${REQUEST_ID}-allowed" \
    -H "X-Sandbox-ID: ${SANDBOX_ID}" \
    -H "X-API-Key: ${PLATFORM_API_KEY}" \
    -d "{\"model\":\"${DEMO_MODEL}\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello from the governed sandbox.\"}]}" \
    "${GATEWAY_URL}/v1/chat/completions"

  log "denied action: model outside the approved allowlist"
  denied_code="$(kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- \
    curl -sS -o /dev/null -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "X-Request-ID: ${REQUEST_ID}-denied" \
    -H "X-Sandbox-ID: ${SANDBOX_ID}" \
    -H "X-API-Key: ${PLATFORM_API_KEY}" \
    -d "{\"model\":\"not-on-the-allowlist\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" \
    "${GATEWAY_URL}/v1/chat/completions")"
  [[ "$denied_code" == "400" ]] || die "expected the disallowed model to be rejected with 400, got ${denied_code}"

  if [[ "$REAL_AGENT" == "1" ]]; then
    log "real coding agent: switching the workspace to ${AGENT_IMAGE}"
    CLUSTER_NAME="$(kubectl config current-context | sed "s/^kind-//")"
    if command -v kind >/dev/null 2>&1 && docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1; then
      kind load docker-image "$AGENT_IMAGE" --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
    fi
    helm upgrade agent-workspace "$ROOT/deploy/charts/agent-workspace" \
      --namespace "$AGENT_NAMESPACE" \
      --reuse-values \
      --set workspace.container.image="$AGENT_IMAGE" \
      --set "workspace.container.command={/bin/sh,-c,sleep 7200}" \
      --set workspace.container.resources.requests.cpu=250m \
      --set workspace.container.resources.requests.memory=512Mi \
      --set workspace.container.resources.limits.cpu=1 \
      --set workspace.container.resources.limits.memory=2Gi >/dev/null
    kubectl -n "$AGENT_NAMESPACE" delete pod "$SANDBOX_ID" --wait=true
    for _ in $(seq 1 60); do
      kubectl -n "$AGENT_NAMESPACE" get pod "$SANDBOX_ID" >/dev/null 2>&1 && break
      sleep 2
    done
    kubectl -n "$AGENT_NAMESPACE" wait --for=condition=Ready "pod/${SANDBOX_ID}" --timeout=300s

    log "running aider inside the hardened sandbox against the governed gateway"
    kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- /bin/sh -c "
      cd /workspace && rm -f hello.py && \
      printf -- '- name: openai/${DEMO_MODEL}\n  extra_params:\n    extra_headers:\n      X-Sandbox-ID: ${SANDBOX_ID}\n' \
        > /workspace/.aider.model.settings.yml && \
      env HOME=/workspace XDG_CACHE_HOME=/tmp \
        OPENAI_API_BASE=${GATEWAY_URL}/v1 \
        OPENAI_API_KEY=${PLATFORM_API_KEY} \
        AIDER_STREAM=false AIDER_PRETTY=false \
        AIDER_CHECK_UPDATE=false AIDER_ANALYTICS=false \
        AIDER_SHOW_MODEL_WARNINGS=false AIDER_SHOW_RELEASE_NOTES=false \
      /venv/bin/aider --model openai/${DEMO_MODEL} --no-git --yes-always \
        --message 'Create hello.py that prints: Hello from the governed sandbox' \
        hello.py
    " || log "aider exited non-zero; checking whether the task still landed"
    if kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- test -s /workspace/hello.py; then
      log "the agent created /workspace/hello.py:"
      kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- cat /workspace/hello.py | sed "s/^/  /"
    else
      log "WARNING: the agent run did not produce hello.py (small local models are"
      log "unreliable coders). Its model calls are still on the receipt chain."
    fi
  fi

  log "agent-action receipts recorded by the gateway (redacted, hash-chained):"
  kubectl -n inference logs deployment/inference-gateway-inference-gateway --tail=400 2>/dev/null \
    | grep "\"event\": \"inference_request\"" \
    | grep "\"sandbox_id\": \"${SANDBOX_ID}\"" \
    | tail -3 | cut -c1-220 | sed "s/^/  /" || true
else
  log "inference gateway is not deployed on this cluster; skipping the model-path step."
  log "run the full local lab (make quickstart) first to demo the governed model path."
fi

log "step 4/4: generating the evidence pack (live checks included)"
evidence_status=0
make -C "$ROOT" evidence LIVE=1 >/dev/null 2>&1 || evidence_status=$?
latest_md="$(ls -t "$ROOT/$EVIDENCE_DIR"/evidence-*.md 2>/dev/null | head -1 || true)"
if [[ -n "$latest_md" ]]; then
  log "evidence pack written: ${latest_md#"$ROOT/"}"
  grep -E "Agent-sandbox workspace runtime|Live agent-sandbox controller" "$latest_md" | sed "s/^/  /" || true
fi
if [[ "$evidence_status" -ne 0 && -n "$latest_md" ]]; then
  log "evidence pack records failing controls:"
  grep "| fail |" "$latest_md" | cut -d"|" -f2 | sed "s/^/   -/" || true
  log "(platform controls fail by design on sandbox-only clusters; on the full lab, inspect the pack)"
fi

log "agent-sandbox demo complete: isolated workspace, fail-closed egress, evidence on record"
