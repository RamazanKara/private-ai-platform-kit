# Helm Charts

The charts are designed to be rendered by the local and customer GitOps overlays, but each chart can also be inspected directly with Helm.

```bash
helm lint charts/inference-gateway
helm template private-ai charts/inference-gateway --values clusters/local/values/inference-gateway.yaml
```

## Profiles

| Profile | Values | Purpose |
| --- | --- | --- |
| Minimal | `charts/*/values.yaml` | Chart defaults for validation and local development. |
| Local | `clusters/local/values/*.yaml` | kind-based lab with Ollama and local service profiles. |
| Customer | `clusters/customer/values/*.yaml` | Provider-neutral customer-owned Kubernetes profile with vLLM and optional GPU values. |

CI packages charts as OCI artifacts for tagged and main-branch image releases.
