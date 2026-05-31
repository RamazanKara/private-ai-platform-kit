#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

require_cmd kubectl "kubectl is required for tenant smoke validation."

TENANT_ID="${TENANT_ID:-team-a-lab}"
TENANT_NAMESPACE="${TENANT_NAMESPACE:-ai-${TENANT_ID}}"
REQUEST_ID="${REQUEST_ID:-tenant-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
PLATFORM_API_KEY="${PLATFORM_API_KEY:-local-development-only}"

validate_k8s_name "$TENANT_ID" "TENANT_ID"
validate_k8s_name "$TENANT_NAMESPACE" "TENANT_NAMESPACE"

"$ROOT/scripts/tenant-up.sh"

log "recreating tenant smoke job in ${TENANT_NAMESPACE}"
kubectl -n "$TENANT_NAMESPACE" delete job tenant-trace-smoke --ignore-not-found
kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: tenant-trace-smoke
  namespace: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/name: tenant-trace-smoke
    app.kubernetes.io/component: tenant-validation
    app.kubernetes.io/part-of: private-ai-platform-kit
    platform.ai/cost-center: ${COST_CENTER:-research}
    platform.ai/environment: ${ENVIRONMENT:-local}
    platform.ai/owner: ${TENANT_OWNER:-${TENANT_ID%-lab}}
    platform.ai/sandbox-id: ${TENANT_ID}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 120
  template:
    metadata:
      labels:
        app.kubernetes.io/name: tenant-trace-smoke
        app.kubernetes.io/component: tenant-validation
        app.kubernetes.io/part-of: private-ai-platform-kit
        platform.ai/cost-center: ${COST_CENTER:-research}
        platform.ai/environment: ${ENVIRONMENT:-local}
        platform.ai/owner: ${TENANT_OWNER:-${TENANT_ID%-lab}}
        platform.ai/sandbox-id: ${TENANT_ID}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
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
              headers="\$(mktemp)"
              body="\$(mktemp)"
              curl -fsS \
                -D "\${headers}" \
                -o "\${body}" \
                -H "X-Request-ID: ${REQUEST_ID}" \
                -H "X-Sandbox-ID: ${TENANT_ID}" \
                -H "X-API-Key: ${PLATFORM_API_KEY}" \
                -H "traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" \
                http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080/healthz
              grep -qi "^x-request-id: ${REQUEST_ID}" "\${headers}"
              grep -qi "^x-sandbox-id: ${TENANT_ID}" "\${headers}"
              cat "\${body}"
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
YAML

kubectl -n "$TENANT_NAMESPACE" wait --for=condition=complete job/tenant-trace-smoke --timeout=180s
kubectl -n "$TENANT_NAMESPACE" logs job/tenant-trace-smoke
log "tenant smoke completed for ${TENANT_ID}"
