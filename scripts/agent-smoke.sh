#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required for agent smoke validation."

ENVIRONMENT="${ENVIRONMENT:-local}"
AGENT_NAMESPACE="${AGENT_NAMESPACE:-ai-agents}"
SANDBOX_ID="${SANDBOX_ID:-agent-lab}"
REQUEST_ID="${REQUEST_ID:-agent-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"

validate_k8s_name "$AGENT_NAMESPACE" "AGENT_NAMESPACE"
validate_k8s_name "$SANDBOX_ID" "SANDBOX_ID"

ENVIRONMENT="$ENVIRONMENT" AGENT_NAMESPACE="$AGENT_NAMESPACE" "$ROOT/scripts/agent-lab-up.sh"

log "recreating agent platform smoke job in ${AGENT_NAMESPACE}"
kubectl -n "$AGENT_NAMESPACE" delete job agent-platform-smoke --ignore-not-found
kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: agent-platform-smoke
  namespace: ${AGENT_NAMESPACE}
  labels:
    app.kubernetes.io/name: agent-platform-smoke
    app.kubernetes.io/component: agent-validation
    app.kubernetes.io/part-of: ai-platform-ops-lab
    platform.ai/cost-center: research
    platform.ai/environment: ${ENVIRONMENT}
    platform.ai/owner: agent-platform
    platform.ai/sandbox-id: ${SANDBOX_ID}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 180
  template:
    metadata:
      labels:
        app.kubernetes.io/name: agent-platform-smoke
        app.kubernetes.io/component: agent-validation
        app.kubernetes.io/part-of: ai-platform-ops-lab
        platform.ai/cost-center: research
        platform.ai/environment: ${ENVIRONMENT}
        platform.ai/owner: agent-platform
        platform.ai/sandbox-id: ${SANDBOX_ID}
    spec:
      restartPolicy: Never
      serviceAccountName: agent-runner
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: curl
          image: curlimages/curl:8.11.1
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -ceu
            - |
              gateway_headers="\$(mktemp)"
              rag_headers="\$(mktemp)"
              rag_body="\$(mktemp)"
              printf '%s\n' "${REQUEST_ID}" > /workspace/agent-smoke.txt
              test -s /workspace/agent-smoke.txt
              curl -fsS \
                -D "\${gateway_headers}" \
                -H "X-Request-ID: ${REQUEST_ID}-gateway" \
                -H "X-Sandbox-ID: ${SANDBOX_ID}" \
                -H "traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" \
                http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080/healthz
              grep -qi "^x-request-id: ${REQUEST_ID}-gateway" "\${gateway_headers}"
              grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "\${gateway_headers}"
              curl -fsS \
                -D "\${rag_headers}" \
                -o "\${rag_body}" \
                -H "Content-Type: application/json" \
                -H "X-Request-ID: ${REQUEST_ID}-rag" \
                -H "X-Sandbox-ID: ${SANDBOX_ID}" \
                -H "X-API-Key: ${PLATFORM_API_KEY}" \
                -H "traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" \
                -d '{"query":"coding agents gateway trace headers","top_k":2}' \
                http://rag-service-rag-service.rag.svc.cluster.local:8080/v1/rag/query
              grep -qi "^x-request-id: ${REQUEST_ID}-rag" "\${rag_headers}"
              grep -qi "^x-sandbox-id: ${SANDBOX_ID}" "\${rag_headers}"
              grep -q '"grounded_messages"' "\${rag_body}"
              grep -q '"results"' "\${rag_body}"
              cat "\${rag_body}"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 20m
              memory: 32Mi
            limits:
              cpu: 100m
              memory: 128Mi
          volumeMounts:
            - name: workspace
              mountPath: /workspace
      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: agent-workspace
YAML

kubectl -n "$AGENT_NAMESPACE" wait --for=condition=complete job/agent-platform-smoke --timeout=180s
kubectl -n "$AGENT_NAMESPACE" logs job/agent-platform-smoke
log "agent platform smoke completed for ${SANDBOX_ID}"
