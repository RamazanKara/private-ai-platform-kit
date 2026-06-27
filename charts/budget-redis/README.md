# Budget Redis Chart

Local Redis-compatible store for shared sandbox budget accounting.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `fullnameOverride` | `budget-redis` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `redis` |
| `image.tag` | `8.0-alpine` |
| `networkPolicy.allowedIngressNamespaces` | `["inference"]` |
| `networkPolicy.enabled` | `true` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `0` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `local` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `shared-budget` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `250m` |
| `resources.limits.memory` | `256Mi` |
| `resources.requests.cpu` | `50m` |
| `resources.requests.memory` | `64Mi` |
| `service.port` | `6379` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
<!-- chart-docs:end -->
