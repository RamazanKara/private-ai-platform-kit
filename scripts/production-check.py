from __future__ import annotations

import subprocess
import sys

from production_checks.charts import (
    check_agent_workspace_render,
    check_budget_redis_render,
    check_gateway_render,
    check_ollama_render,
    check_qdrant_render,
    check_rag_render,
    check_vllm_render,
)
from production_checks.common import ROOT, render_chart
from production_checks.governance import (
    check_egress_governance,
    check_model_provenance_governance,
    check_quota_governance,
    check_retention_governance,
    check_slo_governance,
    check_values_and_docs,
)
from production_checks.platform import (
    check_chaos_drills,
    check_evals,
    check_evidence_pack,
    check_gitops_revisions,
    check_model_catalog,
    check_model_governance,
    check_platform_namespace_psa,
    check_sandbox,
    check_static_workload_security,
    check_tenant_labs,
    check_tenant_onboarding,
    check_validation_toolchain,
)
from production_checks.release import check_oss_governance, check_release_gates, check_release_packaging


def main() -> int:
    errors: list[str] = []
    try:
        check_agent_workspace_render("agent-workspace-defaults", render_chart("agent-workspace"), errors)
        for environment in ("local", "customer"):
            check_agent_workspace_render(
                f"{environment}-agent-workspace",
                render_chart("agent-workspace", ROOT / f"deploy/clusters/{environment}/values/agent-workspace.yaml"),
                errors,
            )
        check_budget_redis_render(render_chart("budget-redis"), errors)
        check_ollama_render("ollama-defaults", render_chart("ollama"), errors)
        check_ollama_render(
            "local-ollama",
            render_chart("ollama", ROOT / "deploy/clusters/local/values/ollama.yaml"),
            errors,
        )
        check_qdrant_render("qdrant-defaults", render_chart("qdrant-vector-store"), True, errors)
        check_qdrant_render(
            "local-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "deploy/clusters/local/values/qdrant-vector-store.yaml"),
            False,
            errors,
        )
        check_qdrant_render(
            "customer-qdrant-vector-store",
            render_chart("qdrant-vector-store", ROOT / "deploy/clusters/customer/values/qdrant-vector-store.yaml"),
            True,
            errors,
        )
        check_gateway_render("chart-defaults", render_chart("inference-gateway"), errors)
        for environment in ("local", "customer"):
            check_gateway_render(
                environment,
                render_chart(
                    "inference-gateway", ROOT / f"deploy/clusters/{environment}/values/inference-gateway.yaml"
                ),
                errors,
            )
        check_rag_render("chart-defaults", render_chart("rag-service"), False, errors)
        check_rag_render(
            "local-rag-service",
            render_chart("rag-service", ROOT / "deploy/clusters/local/values/rag-service.yaml"),
            False,
            errors,
        )
        check_rag_render(
            "customer-rag-service",
            render_chart("rag-service", ROOT / "deploy/clusters/customer/values/rag-service.yaml"),
            True,
            errors,
        )
        # The default profile is rendered too: it is a complete, applyable overlay,
        # so its autoscaler must be gated like the vendor profiles (no CPU HPA).
        check_vllm_render(
            "customer-vllm-default",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-embeddings",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm-embeddings.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-nvidia",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm-nvidia.yaml"),
            "nvidia.com/gpu",
            errors,
        )
        check_vllm_render(
            "customer-vllm-amd",
            render_chart("vllm", ROOT / "deploy/clusters/customer/values/vllm-amd.yaml"),
            "amd.com/gpu",
            errors,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        errors.append(f"failed to render production charts: {exc}")

    check_sandbox(errors)
    check_platform_namespace_psa(errors)
    check_model_catalog(errors)
    check_model_governance(errors)
    check_evals(errors)
    check_tenant_labs(errors)
    check_tenant_onboarding(errors)
    check_chaos_drills(errors)
    check_static_workload_security(errors)
    check_gitops_revisions(errors)
    check_evidence_pack(errors)
    check_validation_toolchain(errors)
    check_release_gates(errors)
    check_release_packaging(errors)
    check_oss_governance(errors)
    check_slo_governance(errors)
    check_quota_governance(errors)
    check_model_provenance_governance(errors)
    check_egress_governance(errors)
    check_retention_governance(errors)
    check_values_and_docs(errors)

    if errors:
        print("production readiness check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("production readiness controls ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
