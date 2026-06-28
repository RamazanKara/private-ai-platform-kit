# Ollama Chart

Local-first private LLM runtime.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `ollama/ollama` |
| `image.tag` | `0.24.0` |
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
| `replicaCount` | `1` |
| `resources.limits.cpu` | `2` |
| `resources.limits.memory` | `4Gi` |
| `resources.requests.cpu` | `500m` |
| `resources.requests.memory` | `1Gi` |
| `service.port` | `11434` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `topologySpread.enabled` | `true` |
| `topologySpread.maxSkew` | `1` |
| `topologySpread.topologyKey` | `kubernetes.io/hostname` |
| `topologySpread.whenUnsatisfiable` | `ScheduleAnyway` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install ollama charts/ollama` |
| Local kind lab | `helm install ollama charts/ollama -f clusters/local/values/ollama.yaml` |
| Customer cluster | `helm install ollama charts/ollama -f clusters/customer/values/ollama.yaml` |

In GitOps installs these value files are applied by the matching Argo CD Application in `clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
