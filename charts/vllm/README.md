# Vllm Chart

GPU-backed OpenAI-compatible LLM runtime.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `accelerator.count` | `4` |
| `accelerator.enabled` | `true` |
| `accelerator.nodeSelector.platform.ai/gpu-vendor` | `nvidia` |
| `accelerator.nodeSelector.platform.ai/node-pool` | `gpu` |
| `accelerator.resourceName` | `nvidia.com/gpu` |
| `accelerator.runtimeClassName` | `""` |
| `accelerator.tolerations` | `[{"effect": "NoSchedule", "key": "nvidia.com/gpu", "operator": "Exists"}]` |
| `accelerator.vendor` | `nvidia` |
| `autoscaling.enabled` | `false` |
| `autoscaling.maxReplicas` | `4` |
| `autoscaling.minReplicas` | `1` |
| `autoscaling.targetCPUUtilizationPercentage` | `70` |
| `cache.mountPath` | `/models` |
| `externalSecrets.enabled` | `false` |
| `externalSecrets.huggingFaceTokenKey` | `token` |
| `externalSecrets.huggingFaceTokenSecretName` | `hf-token` |
| `extraArgs` | `["--tensor-parallel-size", "4", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"]` |
| `extraEnv` | `[]` |
| `image.digest` | `sha256:0fec7ec5f3e6bc168e54899935fb0557da908a4832a1dbc88e2debcf2f889416` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `vllm/vllm-openai` |
| `image.tag` | `v0.22.0` |
| `model.dtype` | `auto` |
| `model.maxModelLen` | `262144` |
| `model.name` | `Qwen/Qwen3-Coder-Next` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `1` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `local` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `local-lab` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `4` |
| `resources.limits.memory` | `64Gi` |
| `resources.requests.cpu` | `2` |
| `resources.requests.memory` | `32Gi` |
| `service.port` | `8000` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `serviceMonitor.enabled` | `true` |
| `serviceMonitor.interval` | `30s` |
| `topologySpread.enabled` | `true` |
| `topologySpread.maxSkew` | `1` |
| `topologySpread.topologyKey` | `kubernetes.io/hostname` |
| `topologySpread.whenUnsatisfiable` | `ScheduleAnyway` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install vllm charts/vllm` |
| Local kind lab | `helm install vllm charts/vllm -f clusters/local/values/vllm.yaml` |
| Customer cluster | `helm install vllm charts/vllm -f clusters/customer/values/vllm.yaml` |

GPU variants: `clusters/customer/values/vllm-nvidia.yaml` and `clusters/customer/values/vllm-amd.yaml`.

In GitOps installs these value files are applied by the matching Argo CD Application in `clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
