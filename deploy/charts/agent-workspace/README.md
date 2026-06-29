# Agent Workspace Chart

Isolated namespace template for coding agents that use the Private AI Platform Kit gateway and RAG service.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `limitRange.defaultCpu` | `1` |
| `limitRange.defaultMemory` | `1Gi` |
| `limitRange.defaultRequestCpu` | `100m` |
| `limitRange.defaultRequestMemory` | `128Mi` |
| `namespace.create` | `true` |
| `namespace.name` | `ai-agents` |
| `networkPolicy.allowDns` | `true` |
| `networkPolicy.allowedEgressCidrs` | `[]` |
| `networkPolicy.enabled` | `true` |
| `networkPolicy.gateway.namespace` | `inference` |
| `networkPolicy.gateway.port` | `8080` |
| `networkPolicy.rag.namespace` | `rag` |
| `networkPolicy.rag.port` | `8080` |
| `platform.gatewayUrl` | `http://inference-gateway-inference-gateway.inference.svc.cluster.local:8080` |
| `platform.ragUrl` | `http://rag-service-rag-service.rag.svc.cluster.local:8080` |
| `platform.requiredHeaders` | `X-Request-ID, X-Sandbox-ID, X-API-Key, traceparent` |
| `rbac.allowJobManagement` | `true` |
| `rbac.create` | `true` |
| `rbac.viewerGroup` | `coding-agents` |
| `resourceQuota.configMaps` | `30` |
| `resourceQuota.limitsCpu` | `8` |
| `resourceQuota.limitsMemory` | `16Gi` |
| `resourceQuota.persistentVolumeClaims` | `4` |
| `resourceQuota.pods` | `20` |
| `resourceQuota.requestsCpu` | `4` |
| `resourceQuota.requestsMemory` | `8Gi` |
| `resourceQuota.secrets` | `10` |
| `sandbox.complianceProfile` | `standard` |
| `sandbox.costCenter` | `research` |
| `sandbox.dataClassification` | `internal` |
| `sandbox.environment` | `local` |
| `sandbox.evidenceRetentionDays` | `""` |
| `sandbox.externalEgressAllowed` | `true` |
| `sandbox.id` | `agent-lab` |
| `sandbox.owner` | `agent-platform` |
| `sandbox.requirePrivateRegistry` | `false` |
| `sandbox.tenant` | `coding-agents` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `agent-runner` |
| `workspace.mountPath` | `/workspace` |
| `workspace.pvc.accessModes` | `["ReadWriteOnce"]` |
| `workspace.pvc.enabled` | `true` |
| `workspace.pvc.name` | `agent-workspace` |
| `workspace.pvc.size` | `10Gi` |
| `workspace.pvc.storageClassName` | `""` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install agent-workspace deploy/charts/agent-workspace` |
| Local kind lab | `helm install agent-workspace deploy/charts/agent-workspace -f deploy/clusters/local/values/agent-workspace.yaml` |
| Customer cluster | `helm install agent-workspace deploy/charts/agent-workspace -f deploy/clusters/customer/values/agent-workspace.yaml` |

In GitOps installs these value files are applied by the matching Argo CD Application in `deploy/clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
