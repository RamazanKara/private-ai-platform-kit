# Budget Redis Chart

Local Redis-compatible store for shared sandbox budget accounting.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `fullnameOverride` | `budget-redis` |
| `image.digest` | `sha256:5f61955be8ab2ccee9372b84ae4d4da2e2b156f87281e3f218544055e7ee04d4` |
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
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install budget-redis charts/budget-redis` |

This chart ships only the minimal profile; tune values inline for your environment.

In GitOps installs these value files are applied by the matching Argo CD Application in `clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
