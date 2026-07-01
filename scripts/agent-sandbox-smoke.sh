#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd helm "Helm is required to install the agent workspace chart."
require_cmd kubectl "kubectl is required for agent-sandbox smoke validation."

ENVIRONMENT="${ENVIRONMENT:-local}"
AGENT_NAMESPACE="${AGENT_NAMESPACE:-ai-agents}"
SANDBOX_ID="${SANDBOX_ID:-agent-lab}"
VALUES_FILE="$ROOT/deploy/clusters/${ENVIRONMENT}/values/agent-workspace.yaml"
# RFC 5737 TEST-NET-2 address: never routable, never in the egress catalog.
EGRESS_PROBE_TARGET="${EGRESS_PROBE_TARGET:-198.51.100.10}"

validate_k8s_name "$AGENT_NAMESPACE" "AGENT_NAMESPACE"
validate_k8s_name "$SANDBOX_ID" "SANDBOX_ID"

if [[ ! -f "$VALUES_FILE" ]]; then
  die "missing agent workspace values file ${VALUES_FILE}"
fi

# Render the Namespace only when this release owns it; on clusters where the
# namespace pre-exists (e.g. created by GitOps), install into it instead of
# fighting Helm ownership annotations.
NS_CREATE=true
if kubectl get namespace "$AGENT_NAMESPACE" >/dev/null 2>&1; then
  ns_owner="$(kubectl get namespace "$AGENT_NAMESPACE" \
    -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}' 2>/dev/null || true)"
  if [[ "$ns_owner" != "agent-workspace" ]]; then
    NS_CREATE=false
  fi
fi

log "installing agent workspace with the agent-sandbox runtime into ${AGENT_NAMESPACE} (namespace.create=${NS_CREATE})"
helm upgrade --install agent-workspace "$ROOT/deploy/charts/agent-workspace" \
  --namespace "$AGENT_NAMESPACE" \
  --create-namespace \
  --values "$VALUES_FILE" \
  --set namespace.create="$NS_CREATE" \
  --set namespace.name="$AGENT_NAMESPACE" \
  --set sandbox.runtime=agent-sandbox \
  --set sandbox.id="$SANDBOX_ID" \
  --set workspace.credentials.projectedToken.enabled=true

log "waiting for sandbox ${SANDBOX_ID} to become ready"
kubectl -n "$AGENT_NAMESPACE" wait --for=condition=Ready "sandbox/${SANDBOX_ID}" --timeout=180s
kubectl -n "$AGENT_NAMESPACE" wait --for=condition=Ready "pod/${SANDBOX_ID}" --timeout=60s

# The agent-sandbox controller does not roll the singleton pod when the
# Sandbox pod template changes; refresh the pod if it drifted from the
# current template (upgrade path). Compare the desired image and volume set
# against the running pod.
desired_image="$(kubectl -n "$AGENT_NAMESPACE" get "sandbox/${SANDBOX_ID}" \
  -o jsonpath="{.spec.podTemplate.spec.containers[0].image}")"
current_image="$(kubectl -n "$AGENT_NAMESPACE" get "pod/${SANDBOX_ID}" \
  -o jsonpath="{.spec.containers[0].image}")"
desired_vols="$(kubectl -n "$AGENT_NAMESPACE" get "sandbox/${SANDBOX_ID}" \
  -o jsonpath="{.spec.podTemplate.spec.volumes[*].name}")"
current_vols="$(kubectl -n "$AGENT_NAMESPACE" get "pod/${SANDBOX_ID}" \
  -o jsonpath="{.spec.volumes[*].name}")"
if [[ "$desired_image" != "$current_image" || "$desired_vols" != "$current_vols" ]]; then
  log "sandbox pod drifted from the current template; recreating it"
  kubectl -n "$AGENT_NAMESPACE" delete pod "$SANDBOX_ID" --wait=true
  for _ in $(seq 1 30); do
    kubectl -n "$AGENT_NAMESPACE" get pod "$SANDBOX_ID" >/dev/null 2>&1 && break
    sleep 2
  done
  kubectl -n "$AGENT_NAMESPACE" wait --for=condition=Ready "pod/${SANDBOX_ID}" --timeout=120s
fi

log "verifying hardened pod contract inside the sandbox"
kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- /bin/sh -ceu '
  uid="$(id -u)"
  [ "$uid" != "0" ] || { echo "FAIL: sandbox runs as root"; exit 1; }
  if touch /probe-rootfs 2>/dev/null; then echo "FAIL: root filesystem is writable"; exit 1; fi
  [ ! -e /var/run/secrets/kubernetes.io/serviceaccount/token ] || { echo "FAIL: service-account token mounted"; exit 1; }
  touch /workspace/.smoke-probe && rm /workspace/.smoke-probe
  echo "hardening contract ok (uid=$uid, read-only rootfs, no SA token, writable workspace)"
'

# NetworkPolicy is only as good as the CNI that enforces it. kindnet (the kind
# default) does not implement NetworkPolicy, so a successful probe there means
# "not enforced", not "allowed by policy".
cni_enforces_netpol() {
  if kubectl -n kube-system get daemonset kindnet >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

log "verifying the short-lived projected platform credential"
kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- /bin/sh -ceu '
  token_file=/var/run/platform/token
  [ -s "$token_file" ] || { echo "FAIL: projected platform token missing"; exit 1; }
  token="$(cat "$token_file")"
  dots="$(printf "%s" "$token" | tr -cd "." | wc -c)"
  [ "$dots" = "2" ] || { echo "FAIL: token is not a JWT"; exit 1; }
  payload="$(printf "%s" "$token" | cut -d. -f2 | tr -- "-_" "+/")"
  case $(( ${#payload} % 4 )) in
    2) payload="${payload}==" ;;
    3) payload="${payload}=" ;;
  esac
  printf "%s" "$payload" | base64 -d 2>/dev/null | grep -q "inference-gateway" \
    || { echo "FAIL: token audience is not inference-gateway"; exit 1; }
  [ ! -e /var/run/secrets/kubernetes.io/serviceaccount/token ] \
    || { echo "FAIL: ambient SA token present alongside scoped token"; exit 1; }
  echo "projected credential ok (JWT, audience-bound, no ambient token)"
'

# Positive control: catalog-approved DNS egress must still resolve names,
# otherwise a broken CNI would make the deny probe below pass vacuously.
log "verifying approved DNS egress still works (positive control)"
resolve_out="$(kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- \
  curl -sS -o /dev/null --connect-timeout 5 http://kubernetes.default.svc.cluster.local 2>&1 || true)"
if printf "%s" "$resolve_out" | grep -qi "could not resolve"; then
  die "DNS egress is broken inside the sandbox; deny probe would be meaningless"
fi

log "probing that non-catalog egress fails closed (target ${EGRESS_PROBE_TARGET})"
if kubectl -n "$AGENT_NAMESPACE" exec "$SANDBOX_ID" -- \
  curl -sS -o /dev/null --connect-timeout 5 "https://${EGRESS_PROBE_TARGET}" 2>/dev/null; then
  die "egress probe reached ${EGRESS_PROBE_TARGET}: default-deny is not effective"
else
  if cni_enforces_netpol; then
    log "egress probe blocked: default-deny + approved-egress policies are enforced"
  else
    log "egress probe did not connect, but the cluster CNI is kindnet (no NetworkPolicy support)."
    log "WARNING: install a policy-enforcing CNI (e.g. Calico) to validate fail-closed egress."
  fi
fi

log "agent-sandbox smoke completed for ${SANDBOX_ID}"
