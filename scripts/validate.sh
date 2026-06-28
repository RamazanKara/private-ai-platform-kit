#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

require_cmd python3 "Python 3 is required for gateway tests and YAML checks."
require_cmd helm "Helm is required to lint and render local charts."

log "bootstrapping Python validation environment"
./scripts/bootstrap-python.sh

log "checking validation toolchain manifest"
services/inference-gateway/.venv/bin/python scripts/toolchain-doctor.py --profile validate --check

log "running inference gateway tests"
./scripts/test-gateway.sh

log "running RAG service tests"
./scripts/test-rag.sh

log "running Python lint, format, and type checks"
./scripts/quality.sh

log "linting and rendering local Helm charts"
rendered_manifests=()
for chart in charts/agent-workspace charts/budget-redis charts/inference-gateway charts/ollama charts/qdrant-vector-store charts/rag-service charts/vllm; do
  helm lint "$chart"
  rendered="/tmp/$(basename "$chart")-rendered.yaml"
  helm template "validate-$(basename "$chart")" "$chart" >"$rendered"
  rendered_manifests+=("$rendered")
  for environment in local customer; do
    values="clusters/${environment}/values/$(basename "$chart").yaml"
    if [[ -f "$values" ]]; then
      rendered="/tmp/${environment}-$(basename "$chart")-rendered.yaml"
      helm template "validate-${environment}-$(basename "$chart")" "$chart" --values "$values" >"$rendered"
      rendered_manifests+=("$rendered")
    fi
    if [[ "$(basename "$chart")" == "vllm" ]]; then
      for profile in "clusters/${environment}/values/vllm-"*.yaml; do
        if [[ -f "$profile" ]]; then
          rendered="/tmp/${environment}-$(basename "$profile" .yaml)-rendered.yaml"
          helm template "validate-${environment}-$(basename "$profile" .yaml)" "$chart" --values "$profile" >"$rendered"
          rendered_manifests+=("$rendered")
        fi
      done
    fi
  done
done

log "checking YAML syntax with Python"
services/inference-gateway/.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

errors = []
for path in list(Path(".").rglob("*.yaml")) + list(Path(".").rglob("*.yml")):
    if ".venv" in path.parts or "templates" in path.parts:
        continue
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
        if not docs and path.stat().st_size:
            errors.append(f"{path}: no YAML documents parsed")
    except Exception as exc:
        errors.append(f"{path}: {exc}")
if errors:
    raise SystemExit("\n".join(errors))
print("yaml ok")
PY

log "checking repository hygiene"
python3 scripts/repo-hygiene.py --check

log "checking generated chart docs"
services/inference-gateway/.venv/bin/python scripts/chart-docs.py --check

log "checking API contracts"
services/inference-gateway/.venv/bin/python scripts/api-contract.py --check

log "checking configuration contracts"
services/inference-gateway/.venv/bin/python scripts/config-contract.py --check

log "checking production readiness controls"
services/inference-gateway/.venv/bin/python scripts/production-check.py

log "checking evidence pack inputs"
services/inference-gateway/.venv/bin/python scripts/evidence-pack.py --check

log "checking egress governance"
services/inference-gateway/.venv/bin/python scripts/egress-governance.py --check

log "checking data retention governance"
services/inference-gateway/.venv/bin/python scripts/retention-check.py --check

log "checking SLO and error budget governance"
services/inference-gateway/.venv/bin/python scripts/slo-report.py --check

log "checking quota and chargeback governance"
services/inference-gateway/.venv/bin/python scripts/quota-check.py --check

log "checking release gates"
services/inference-gateway/.venv/bin/python scripts/release-gate.py --check

log "checking tenant onboarding spec"
services/inference-gateway/.venv/bin/python scripts/tenant-onboard.py --check
services/inference-gateway/.venv/bin/python scripts/tenant-onboard.py --check --spec tenants/onboarding/regulated-offline-coding-agents.yaml
services/inference-gateway/.venv/bin/python scripts/tenant-onboard.py --check --spec tenants/onboarding/gpu-coding-agents.yaml

log "checking customer overlay configuration"
services/inference-gateway/.venv/bin/python scripts/configure-customer-overlay.py --check

log "checking model catalog governance"
services/inference-gateway/.venv/bin/python scripts/model-catalog.py --check

log "checking model provenance governance"
services/inference-gateway/.venv/bin/python scripts/model-provenance.py --check

log "checking eval suite syntax"
services/inference-gateway/.venv/bin/python scripts/eval-suite.py --suite evals/smoke-suite.yaml --check-config
services/inference-gateway/.venv/bin/python scripts/eval-suite.py --suite evals/coding-agent-suite.yaml --check-config

if require_optional_or_full kubeconform "kubeconform is needed for Kubernetes schema validation."; then
  kubeconform -summary -ignore-missing-schemas "${rendered_manifests[@]}"
  mapfile -d '' manifest_files < <(
    find \
      clusters \
      gitops \
      backup/restore-drill/k8s \
      backup/velero \
      observability \
      policies/kyverno/policies.yaml \
      policies/kyverno/tests/resources \
      sandbox \
      model-catalog/k8s \
      tenants/examples \
      -name '*.yaml' \
      ! -path 'clusters/*/values/*' \
      ! -name 'values*.yaml' \
      ! -name 'Chart.yaml' \
      -print0
  )
  if [[ "${#manifest_files[@]}" -gt 0 ]]; then
    kubeconform -summary -ignore-missing-schemas "${manifest_files[@]}"
  fi
fi

if require_optional_or_full kyverno "Kyverno CLI is needed for policy tests."; then
  kyverno test policies/kyverno/tests
fi

if require_optional_or_full restore-drill "restore-drill validates drill config syntax."; then
  restore-drill validate --config backup/restore-drill/drills/local-redis-aof.yaml
fi

if require_optional_or_full k6 "k6 is needed for load-test syntax validation."; then
  k6 inspect tests/load/chat-completions.js >/dev/null
fi

if require_optional_or_full syft "Syft is needed for SBOM smoke validation."; then
  syft dir:services/inference-gateway -o spdx-json >/tmp/inference-gateway.sbom.json
  syft dir:services/rag-service -o spdx-json >/tmp/rag-service.sbom.json
fi

if require_optional_or_full argocd "Argo CD CLI is needed for GitOps client validation."; then
  argocd version --client >/dev/null
fi

if require_optional_or_full cosign "Cosign is needed for image signature validation workflows."; then
  cosign version >/dev/null
fi

if require_optional_or_full trivy "Trivy is needed for filesystem secret and config scanning."; then
  trivy_output="/tmp/private-ai-platform-kit-trivy-fs.txt"
  if ! trivy fs \
    --scanners secret,misconfig \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    --timeout 10m \
    --skip-dirs .tools \
    --skip-dirs results \
    --skip-dirs tenants/generated \
    --skip-dirs policies/kyverno/tests/resources \
    --skip-dirs services/inference-gateway/.venv \
    --skip-dirs services/rag-service/.venv \
    . >"$trivy_output"; then
    cat "$trivy_output"
    exit 1
  fi
fi

log "validation completed"
