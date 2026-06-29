# References

This project is implemented from the platform behavior described in the README, production readiness matrix, runbooks, and these upstream projects:

- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server.html
- vLLM GPU installation and platform support: https://docs.vllm.ai/en/latest/getting_started/installation/gpu/
- AMD ROCm vLLM inference image: https://rocm.docs.amd.com/en/develop/how-to/rocm-for-ai/inference/benchmark-docker/vllm.html
- AMD GPU Device Plugin for Kubernetes: https://instinct.docs.amd.com/projects/k8s-device-plugin/en/latest/index.html
- Ollama OpenAI compatibility: https://docs.ollama.com/openai
- Ollama Qwen2.5 model library: https://ollama.com/library/qwen2.5
- Ollama Qwen3.5 model library: https://ollama.com/library/qwen3.5
- Qwen3 Coder Next model card: https://huggingface.co/Qwen/Qwen3-Coder-Next
- Qdrant collections API: https://api.qdrant.tech/api-reference/collections/create-collection
- Qdrant query points API: https://api.qdrant.tech/api-reference/search/query-points
- Argo CD app-of-apps and declarative setup: https://argo-cd.readthedocs.io/
- External Secrets Operator Kubernetes provider: https://external-secrets.io/latest/provider/kubernetes/
- Kubernetes NetworkPolicy: https://kubernetes.io/docs/concepts/services-networking/network-policies/
- Kubernetes ResourceQuota and LimitRange: https://kubernetes.io/docs/concepts/policy/resource-quotas/
- W3C Trace Context: https://www.w3.org/TR/trace-context/
- KEDA Prometheus scaler: https://keda.sh/docs/latest/scalers/prometheus/
- Kyverno image verification: https://kyverno.io/docs/
- Sigstore Cosign: https://docs.sigstore.dev/cosign/
- Syft SBOM generation: https://oss.anchore.com/syft/
- Velero backup and restore: https://velero.io/docs/
- OpenCost: https://www.opencost.io/docs/
- redis-py package release history: https://pypi.org/project/redis/
- restore-drill: https://github.com/RamazanKara/restore-drill
