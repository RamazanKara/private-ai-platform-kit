SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

RUNTIME_BACKEND ?= ollama
RUNTIME ?= local
CLUSTER_NAME ?= private-ai-platform-kit
LIVE ?= 0
TENANT_SPEC ?= tenants/onboarding/coding-agents.yaml
TENANT_OUTPUT ?= tenants/generated
TOOLCHAIN_PROFILE ?= validate
PYTHON := services/inference-gateway/.venv/bin/python

.PHONY: python-env local-up local-down bootstrap-argocd sync smoke rag-smoke sandbox-smoke tenant-up tenant-smoke tenant-onboard tenant-onboard-regulated agent-lab-up agent-smoke chaos-drill eval loadtest restore-drill backup-drill evidence release-gate release-report slo-check slo-report quota-check quota-report egress-check egress-report retention-check retention-report model-check model-report model-provenance-check model-provenance-report toolchain-install toolchain-doctor toolchain-report policy-test production-check validate validate-full test-gateway test-rag

python-env:
	./scripts/bootstrap-python.sh

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

agent-lab-up:
	./scripts/agent-lab-up.sh

agent-smoke:
	./scripts/agent-smoke.sh

chaos-drill:
	./scripts/chaos-drill.sh

eval:
	./scripts/eval.sh

loadtest:
	./scripts/loadtest.sh

restore-drill:
	RUNTIME="$(RUNTIME)" ./scripts/restore-drill.sh

backup-drill:
	RUNTIME="$(RUNTIME)" ./scripts/restore-drill.sh --include-velero

evidence: python-env
	$(PYTHON) scripts/evidence-pack.py $(if $(filter 1 true yes,$(LIVE)),--live,)

release-gate: python-env
	$(PYTHON) scripts/release-gate.py --check

release-report: python-env
	$(PYTHON) scripts/release-gate.py --check --report

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
