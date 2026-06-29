#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required to bootstrap Argo CD."

# Pin the Argo CD install manifest to an immutable release tag rather than the
# floating `stable` ref so every bootstrap is reproducible, matching the
# digest-pinned posture of the rest of the platform. Keep this in step with
# ARGOCD_VERSION in scripts/install-validation-tools.sh and
# tools/validation-toolchain.yaml. Override with the env var when intentionally
# moving to a different release.
ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.3}"
ARGOCD_INSTALL_MANIFEST="${ARGOCD_INSTALL_MANIFEST:-https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml}"

cd "$ROOT"
log "bootstrapping Argo CD ${ARGOCD_VERSION} from ${ARGOCD_INSTALL_MANIFEST}"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd -f "$ARGOCD_INSTALL_MANIFEST"
kubectl -n argocd rollout status deploy/argocd-server --timeout=5m
if [[ "${ENVIRONMENT:-local}" == "customer" ]]; then
  kubectl apply -f gitops/argocd/root-app-customer.yaml
else
  kubectl apply -f gitops/argocd/root-app.yaml
fi
kubectl -n argocd get application private-ai-platform-kit-root
