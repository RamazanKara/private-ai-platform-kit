#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-restore-drill}"
mkdir -p .out/results/restore-drill
POD="$(kubectl -n "$NAMESPACE" get pods --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')"
kubectl -n "$NAMESPACE" cp "$POD:/reports" ".out/results/restore-drill/${POD}-reports"
