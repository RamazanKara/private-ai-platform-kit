# Qdrant Vector Store Chart

Optional local vector store profile for Private AI Platform Kit RAG.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `image.digest` | `sha256:45f8e3ddc2570a4d029877e1b5ec1045c19b3852b4e22a55c7f43b05aea0ca89` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `qdrant/qdrant` |
| `image.tag` | `v1.18.1` |
| `networkPolicy.allowedIngressNamespaces` | `["rag", "monitoring"]` |
| `networkPolicy.enabled` | `true` |
| `persistence.enabled` | `true` |
| `persistence.mountPath` | `/qdrant/storage` |
| `persistence.size` | `20Gi` |
| `persistence.storageClassName` | `""` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `1` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `customer` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `rag-vector` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `2` |
| `resources.limits.memory` | `4Gi` |
| `resources.requests.cpu` | `250m` |
| `resources.requests.memory` | `512Mi` |
| `service.grpcPort` | `6334` |
| `service.httpPort` | `6333` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `serviceMonitor.enabled` | `true` |
| `serviceMonitor.interval` | `30s` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install qdrant-vector-store deploy/charts/qdrant-vector-store` |
| Local kind lab | `helm install qdrant-vector-store deploy/charts/qdrant-vector-store -f deploy/clusters/local/values/qdrant-vector-store.yaml` |
| Customer cluster | `helm install qdrant-vector-store deploy/charts/qdrant-vector-store -f deploy/clusters/customer/values/qdrant-vector-store.yaml` |

In GitOps installs these value files are applied by the matching Argo CD Application in `deploy/clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
