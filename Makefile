SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

RUNTIME_BACKEND ?= ollama
RUNTIME ?= local
CLUSTER_NAME ?= private-ai-platform-kit
LIVE ?= 0
TENANT_SPEC ?= tenants/onboarding/coding-agents.yaml
TENANT_OUTPUT ?= .out/tenants
TOOLCHAIN_PROFILE ?= validate
RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS ?= 24
CUSTOMER_REPO_URL ?= https://github.com/RamazanKara/private-ai-platform-kit.git
CUSTOMER_REVISION ?= v0.10.0
CUSTOMER_GPU_PROFILE ?= nvidia
TOOLCHAIN_BIN_DIR ?= $(CURDIR)/.tools/bin
PYTHONDONTWRITEBYTECODE ?= 1
PYTHON := src/inference-gateway/.venv/bin/python

export PATH := $(TOOLCHAIN_BIN_DIR):$(PATH)
export PYTHONDONTWRITEBYTECODE

.PHONY: help clean clean-all python-env quickstart local-up local-down bootstrap-argocd sync smoke rag-smoke sandbox-smoke tenant-up tenant-smoke tenant-onboard tenant-onboard-regulated tenant-onboard-gpu customer-overlay customer-overlay-check agent-lab-up agent-smoke chaos-drill eval eval-local loadtest loadtest-local benchmark-local docs-install docs-serve docs-build restore-drill backup-drill evidence release-gate release-gate-strict release-report release-report-strict slo-check slo-report quota-check quota-report egress-check egress-report retention-check retention-report model-check model-report model-provenance-check model-provenance-report model-provenance-verify image-scan supply-chain-check repo-security-scan dependency-lock-check repo-hygiene chart-docs chart-docs-update api-contract api-contract-update config-contract config-contract-update toolchain-install toolchain-doctor toolchain-report policy-test production-check validate validate-full test-gateway test-rag lint format format-check typecheck quality coverage dashboard-check dashboard-update paths paths-check

help:
	@printf '%s\n' \
		'Private AI Platform Kit targets' \
		'' \
		'Local platform:' \
		'  make quickstart            Guided local lab path with validation and smoke tests' \
		'  make local-up              Create the local kind cluster' \
		'  make bootstrap-argocd      Install/bootstrap Argo CD' \
		'  make sync                  Sync local or customer GitOps apps' \
		'  make smoke                 Run gateway smoke test' \
		'  make rag-smoke             Run RAG service smoke test' \
		'  make agent-smoke           Run coding-agent workspace smoke test' \
		'' \
		'Validation:' \
		'  make validate              Run the default repo validation gate' \
		'  make validate-full         Require the strict validation toolchain' \
		'  make quality               Run Ruff lint, format check, and mypy' \
		'  make lint                  Ruff lint services and scripts' \
		'  make format                Apply Ruff format and autofixes' \
		'  make typecheck             Run mypy on both services' \
		'  make coverage              Report test coverage with enforced floors' \
		'  make production-check      Run static production-readiness checks' \
		'  make image-scan            Build and Trivy-scan runtime images' \
		'  make supply-chain-check    Validate local image scan evidence' \
		'  make repo-security-scan    Fail on HIGH/CRITICAL repo security findings' \
		'  make dependency-lock-check Check hashed Python dependency locks' \
		'  make repo-hygiene          Check contributor docs, links, and layout' \
		'  make chart-docs            Check generated Helm chart value tables' \
		'  make api-contract          Check service OpenAPI contracts' \
		'  make config-contract       Check service runtime config contracts' \
		'  make loadtest-local        Run k6 against an ephemeral local gateway' \
		'' \
		'Evidence and governance:' \
		'  make evidence              Generate customer evidence pack' \
		'  make release-gate          Check release gates with sample fallback allowed' \
		'  make release-gate-strict   Check gates with current evidence required' \
		'  make eval-local            Run evals against an ephemeral local mock runtime' \
		'  make slo-report            Write SLO report evidence' \
		'  make quota-report          Write quota and chargeback evidence' \
		'  make model-provenance-report  Write model provenance evidence' \
		'' \
		'Customer handoff:' \
		'  make customer-overlay      Configure customer GitOps overlay' \
		'  make tenant-onboard        Generate tenant onboarding artifacts' \
		'  make tenant-onboard-regulated Generate regulated/offline tenant artifacts' \
		'  make tenant-onboard-gpu    Generate GPU coding-agent tenant artifacts'

clean:
	rm -rf .coverage htmlcov .pytest_cache
	rm -rf src/inference-gateway/.venv src/rag-service/.venv
	find . -path ./.git -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -path ./.git -prune -o -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -path ./.git -prune -o -type d -name .ruff_cache -prune -exec rm -rf {} +
	find results -type f ! -name 'sample-*' -delete
	find results -mindepth 1 -type d -empty -delete

clean-all: clean
	rm -rf .tools

python-env:
	./scripts/bootstrap-python.sh

quickstart:
	./scripts/quickstart.sh

local-up:
	./scripts/local-up.sh

local-down:
	./scripts/local-down.sh

bootstrap-argocd:
	./scripts/bootstrap-argocd.sh

sync:
	./scripts/sync.sh

smoke:
	RUNTIME_BACKEND="$(RUNTIME_BACKEND)" ./scripts/smoke.sh

rag-smoke:
	./scripts/rag-smoke.sh

sandbox-smoke:
	./scripts/sandbox-smoke.sh

tenant-up:
	./scripts/tenant-up.sh

tenant-smoke:
	./scripts/tenant-smoke.sh

tenant-onboard: python-env
	$(PYTHON) scripts/tenant-onboard.py --spec "$(TENANT_SPEC)" --output-dir "$(TENANT_OUTPUT)"

tenant-onboard-regulated: python-env
	$(PYTHON) scripts/tenant-onboard.py --spec tenants/onboarding/regulated-offline-coding-agents.yaml --output-dir "$(TENANT_OUTPUT)"

tenant-onboard-gpu: python-env
	$(PYTHON) scripts/tenant-onboard.py --spec tenants/onboarding/gpu-coding-agents.yaml --output-dir "$(TENANT_OUTPUT)"

customer-overlay: python-env
	$(PYTHON) scripts/configure-customer-overlay.py --repo-url "$(CUSTOMER_REPO_URL)" --target-revision "$(CUSTOMER_REVISION)" --gpu-profile "$(CUSTOMER_GPU_PROFILE)"

customer-overlay-check: python-env
	$(PYTHON) scripts/configure-customer-overlay.py --check

agent-lab-up:
	./scripts/agent-lab-up.sh

agent-smoke:
	./scripts/agent-smoke.sh

chaos-drill:
	./scripts/chaos-drill.sh

eval:
	./scripts/eval.sh

eval-local:
	./scripts/eval-local.sh

loadtest:
	./scripts/loadtest.sh

loadtest-local:
	./scripts/loadtest-local.sh

benchmark-local:
	./scripts/benchmark-ollama.sh

docs-install:
	python3 -m venv .venv-docs && .venv-docs/bin/pip install -r requirements-docs.txt

docs-serve:
	./scripts/docs-build.sh serve

docs-build:
	./scripts/docs-build.sh build

restore-drill:
	RUNTIME="$(RUNTIME)" ./scripts/restore-drill.sh

backup-drill:
	RUNTIME="$(RUNTIME)" ./scripts/restore-drill.sh --include-velero

evidence: python-env
	$(PYTHON) scripts/evidence-pack.py $(if $(filter 1 true yes,$(LIVE)),--live,)

release-gate: python-env
	$(PYTHON) scripts/release-gate.py --check

release-gate-strict: python-env
	$(PYTHON) scripts/release-gate.py --check --require-current-evidence --max-evidence-age-hours "$(RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS)"

release-report: python-env
	$(PYTHON) scripts/release-gate.py --check --report

release-report-strict: python-env
	$(PYTHON) scripts/release-gate.py --check --report --require-current-evidence --max-evidence-age-hours "$(RELEASE_GATE_MAX_EVIDENCE_AGE_HOURS)"

slo-check: python-env
	$(PYTHON) scripts/slo-report.py --check

slo-report: python-env
	$(PYTHON) scripts/slo-report.py --check --report

quota-check: python-env
	$(PYTHON) scripts/quota-check.py --check

quota-report: python-env
	$(PYTHON) scripts/quota-check.py --check --report

egress-check: python-env
	$(PYTHON) scripts/egress-governance.py --check

egress-report: python-env
	$(PYTHON) scripts/egress-governance.py --check --report

retention-check: python-env
	$(PYTHON) scripts/retention-check.py --check

retention-report: python-env
	$(PYTHON) scripts/retention-check.py --check --report

model-check: python-env
	$(PYTHON) scripts/model-catalog.py --check

model-report: python-env
	$(PYTHON) scripts/model-catalog.py --report

model-provenance-check: python-env
	$(PYTHON) scripts/model-provenance.py --check

model-provenance-report: python-env
	$(PYTHON) scripts/model-provenance.py --check --report

model-provenance-verify: python-env
	$(PYTHON) scripts/model-provenance.py --verify

image-scan:
	./scripts/image-scan.sh

supply-chain-check: python-env
	$(PYTHON) scripts/supply-chain-evidence.py --check

repo-security-scan:
	trivy fs \
		--scanners secret,misconfig \
		--severity HIGH,CRITICAL \
		--exit-code 1 \
		--timeout 10m \
		--skip-dirs .tools \
		--skip-dirs results \
		--skip-dirs .out/tenants \
		--skip-dirs deploy/policies/kyverno/tests/resources \
		--skip-dirs src/inference-gateway/.venv \
		--skip-dirs src/rag-service/.venv \
		.

dependency-lock-check:
	python3 scripts/repo-hygiene.py --check

repo-hygiene:
	python3 scripts/repo-hygiene.py --check

paths:
	python3 scripts/paths.py --dump

paths-check:
	python3 scripts/paths.py --check

chart-docs: python-env
	$(PYTHON) scripts/chart-docs.py --check

chart-docs-update: python-env
	$(PYTHON) scripts/chart-docs.py --write

api-contract: python-env
	$(PYTHON) scripts/api-contract.py --check

api-contract-update: python-env
	$(PYTHON) scripts/api-contract.py --write

config-contract: python-env
	$(PYTHON) scripts/config-contract.py --check

config-contract-update: python-env
	$(PYTHON) scripts/config-contract.py --write

toolchain-install:
	./scripts/install-validation-tools.sh

toolchain-doctor: python-env
	$(PYTHON) scripts/toolchain-doctor.py --profile "$(TOOLCHAIN_PROFILE)" --check

toolchain-report: python-env
	$(PYTHON) scripts/toolchain-doctor.py --profile "$(TOOLCHAIN_PROFILE)" --check --report

policy-test:
	./scripts/policy-test.sh

production-check:
	./scripts/test-gateway.sh
	./scripts/test-rag.sh
	$(PYTHON) scripts/production-check.py

validate:
	./scripts/validate.sh

validate-full: python-env
	$(PYTHON) scripts/toolchain-doctor.py --profile strict --check
	REQUIRE_FULL_TOOLCHAIN=1 ./scripts/validate.sh

test-gateway:
	./scripts/test-gateway.sh

test-rag:
	./scripts/test-rag.sh

lint:
	./scripts/quality.sh lint

format:
	./scripts/quality.sh format

format-check:
	./scripts/quality.sh format-check

typecheck:
	./scripts/quality.sh typecheck

quality:
	./scripts/quality.sh

coverage:
	./scripts/coverage.sh

dashboard-check: python-env
	$(PYTHON) scripts/dashboard-check.py --check

dashboard-update: python-env
	$(PYTHON) scripts/dashboard-check.py --write
