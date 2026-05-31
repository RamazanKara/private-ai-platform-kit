#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kubectl "kubectl is required to bootstrap Argo CD."

cd "$ROOT"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=5m
if [[ "${ENVIRONMENT:-local}" == "customer" ]]; then
  kubectl apply -f gitops/argocd/root-app-customer.yaml
else
  kubectl apply -f gitops/argocd/root-app.yaml
fi
kubectl -n argocd get application private-ai-platform-kit-root
