# Ollama Chart

Local-first private LLM runtime.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `image.digest` | `sha256:a6149234667efc71d37766d61c1a16f24c33e4cd7a0bf4125c44a7e47e2419c4` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `ollama/ollama` |
| `image.tag` | `0.24.0` |
| `livenessProbe.enabled` | `true` |
| `livenessProbe.failureThreshold` | `6` |
| `livenessProbe.initialDelaySeconds` | `0` |
| `livenessProbe.periodSeconds` | `20` |
| `livenessProbe.timeoutSeconds` | `5` |
| `model.name` | `qwen3.5:0.8b` |
| `model.pullOnStart` | `false` |
| `persistence.enabled` | `true` |
| `persistence.size` | `20Gi` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `1` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `local` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `local-lab` |
| `readinessProbe.failureThreshold` | `3` |
| `readinessProbe.initialDelaySeconds` | `10` |
| `readinessProbe.periodSeconds` | `10` |
| `readinessProbe.timeoutSeconds` | `5` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `2` |
| `resources.limits.memory` | `4Gi` |
| `resources.requests.cpu` | `500m` |
| `resources.requests.memory` | `1Gi` |
| `service.port` | `11434` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `startupProbe.enabled` | `true` |
| `startupProbe.failureThreshold` | `30` |
| `startupProbe.initialDelaySeconds` | `5` |
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
| Minimal (chart defaults) | `helm install ollama deploy/charts/ollama` |
| Local kind lab | `helm install ollama deploy/charts/ollama -f deploy/clusters/local/values/ollama.yaml` |
| Customer cluster | `helm install ollama deploy/charts/ollama -f deploy/clusters/customer/values/ollama.yaml` |

In GitOps installs these value files are applied by the matching Argo CD Application in `deploy/clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
