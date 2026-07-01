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
| `cache.persistence.accessMode` | `ReadWriteOnce` |
| `cache.persistence.enabled` | `false` |
| `cache.persistence.size` | `200Gi` |
| `cache.persistence.storageClassName` | `""` |
| `externalSecrets.enabled` | `false` |
| `externalSecrets.huggingFaceTokenKey` | `token` |
| `externalSecrets.huggingFaceTokenSecretName` | `hf-token` |
| `extraArgs` | `["--tensor-parallel-size", "4", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"]` |
| `extraEnv` | `[]` |
| `image.digest` | `sha256:0fec7ec5f3e6bc168e54899935fb0557da908a4832a1dbc88e2debcf2f889416` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `vllm/vllm-openai` |
| `image.tag` | `v0.22.0` |
| `keda.cooldownPeriod` | `300` |
| `keda.enabled` | `false` |
| `keda.maxReplicaCount` | `4` |
| `keda.minReplicaCount` | `1` |
| `keda.prometheusServerAddress` | `http://kube-prometheus-stack-prometheus.monitoring:9090` |
| `keda.query` | `avg(vllm:num_requests_waiting)` |
| `keda.threshold` | `5` |
| `livenessProbe.enabled` | `true` |
| `livenessProbe.failureThreshold` | `6` |
| `livenessProbe.initialDelaySeconds` | `0` |
| `livenessProbe.periodSeconds` | `20` |
| `livenessProbe.timeoutSeconds` | `5` |
| `model.dtype` | `auto` |
| `model.maxModelLen` | `262144` |
| `model.name` | `Qwen/Qwen3-Coder-Next` |
| `model.revision` | `""` |
| `namespace.create` | `true` |
| `namespace.name` | `""` |
| `networkPolicy.allowModelPullEgress` | `true` |
| `networkPolicy.allowedIngressNamespaces` | `["inference", "monitoring"]` |
| `networkPolicy.enabled` | `true` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `1` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `local` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `local-lab` |
| `podSecurityContext.fsGroup` | `1000` |
| `podSecurityContext.runAsGroup` | `1000` |
| `podSecurityContext.runAsUser` | `1000` |
| `readinessProbe.failureThreshold` | `3` |
| `readinessProbe.initialDelaySeconds` | `30` |
| `readinessProbe.periodSeconds` | `15` |
| `readinessProbe.timeoutSeconds` | `5` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `4` |
| `resources.limits.memory` | `64Gi` |
| `resources.requests.cpu` | `2` |
| `resources.requests.memory` | `32Gi` |
| `server.enablePrefixCaching` | `true` |
| `server.gpuMemoryUtilization` | `0.90` |
| `server.guidedDecodingBackend` | `""` |
| `server.kvCacheDtype` | `auto` |
| `server.quantization` | `""` |
| `server.speculative.config` | `""` |
| `server.speculative.enabled` | `false` |
| `service.port` | `8000` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `serviceMonitor.enabled` | `true` |
| `serviceMonitor.interval` | `30s` |
| `startupProbe.enabled` | `true` |
| `startupProbe.failureThreshold` | `60` |
| `startupProbe.initialDelaySeconds` | `15` |
| `startupProbe.periodSeconds` | `10` |
| `startupProbe.timeoutSeconds` | `5` |
| `topologySpread.enabled` | `true` |
| `topologySpread.maxSkew` | `1` |
| `topologySpread.topologyKey` | `kubernetes.io/hostname` |
| `topologySpread.whenUnsatisfiable` | `ScheduleAnyway` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install vllm deploy/charts/vllm` |
| Local kind lab | `helm install vllm deploy/charts/vllm -f deploy/clusters/local/values/vllm.yaml` |
| Customer cluster | `helm install vllm deploy/charts/vllm -f deploy/clusters/customer/values/vllm.yaml` |

GPU variants: `deploy/clusters/customer/values/vllm-nvidia.yaml` and `deploy/clusters/customer/values/vllm-amd.yaml`.

In GitOps installs these value files are applied by the matching Argo CD Application in `deploy/clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
