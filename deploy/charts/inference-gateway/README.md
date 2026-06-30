# Inference Gateway Chart

The inference gateway chart deploys the OpenAI-compatible gateway, admission controls, sandbox budgets, metrics, audit logging, auth configuration, and runtime egress policy.

## Important Values

| Value | Purpose |
| --- | --- |
| `runtime.backend` | Selects `ollama` or `vllm`. |
| `runtime.allowedModels` | Approved model IDs admitted by the gateway. |
| `admission.allowStreaming` | Allows streaming pass-through when callers set `stream: true`. |
| `budget.backend` | Uses `memory` for single-pod labs or `redis` for shared multi-replica counters. |
| `auth.enabled` | Requires API-key or bearer-token authentication for business endpoints. |
| `auth.existingSecret` | Sources comma-separated SHA-256 API-key hashes from a Kubernetes Secret. |
| `networkPolicy.runtimeEgress` | Allows gateway egress only to approved runtime and budget namespaces. |

## Profiles

- Minimal: `deploy/charts/inference-gateway/values.yaml`
- Local: `deploy/clusters/local/values/inference-gateway.yaml`
- Customer: `deploy/clusters/customer/values/inference-gateway.yaml`

Run `make config-contract` after changing settings, env vars, Helm values, or chart defaults.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `admission.allowStreaming` | `false` |
| `admission.maxCompletionTokens` | `1024` |
| `admission.maxMessages` | `16` |
| `admission.maxPromptChars` | `8192` |
| `auth.apiKeyHashes` | `[]` |
| `auth.apiKeyHeader` | `X-API-Key` |
| `auth.enabled` | `false` |
| `auth.existingSecret.key` | `api-key-sha256s` |
| `auth.existingSecret.name` | `""` |
| `auth.jwt.audience` | `""` |
| `auth.jwt.cacheSeconds` | `300` |
| `auth.jwt.enabled` | `false` |
| `auth.jwt.issuer` | `""` |
| `auth.jwt.jwksUrl` | `""` |
| `auth.jwt.requiredScopes` | `[]` |
| `budget.backend` | `memory` |
| `budget.enabled` | `true` |
| `budget.estimatedCharsPerToken` | `4` |
| `budget.estimatedTokenLimit` | `750000` |
| `budget.keyPrefix` | `private-ai-platform-kit:sandbox-budget` |
| `budget.promptCharLimit` | `2000000` |
| `budget.redisTimeoutSeconds` | `0.5` |
| `budget.redisUrl` | `redis://budget-redis.budget.svc.cluster.local:6379/0` |
| `budget.requestLimit` | `1000` |
| `budget.windowSeconds` | `86400` |
| `guardrails.promptSecretDetection.enabled` | `true` |
| `guardrails.promptSecretDetection.patterns` | `["private_key", "github_token", "slack_token", "bearer_token", "generic_api_key_assignment"]` |
| `image.pullPolicy` | `IfNotPresent` |
| `image.repository` | `ghcr.io/ramazankara/private-ai-platform-kit/inference-gateway` |
| `image.tag` | `v0.11.0` |
| `keda.enabled` | `true` |
| `keda.maxReplicaCount` | `5` |
| `keda.minReplicaCount` | `1` |
| `keda.prometheusServerAddress` | `http://kube-prometheus-stack-prometheus.monitoring:9090` |
| `keda.threshold` | `10` |
| `networkPolicy.allowDns` | `true` |
| `networkPolicy.allowedIngressNamespaceLabels` | `[{"platform.ai/traceable-sandbox": "true"}]` |
| `networkPolicy.allowedIngressNamespaces` | `["ai-agents", "ai-sandbox"]` |
| `networkPolicy.enabled` | `true` |
| `networkPolicy.runtimeEgress` | `[{"namespace": "ollama", "port": 11434}, {"namespace": "vllm", "port": 8000}, {"namespace": "budget", "port": 6379}]` |
| `observability.tracing.enabled` | `false` |
| `observability.tracing.otlpEndpoint` | `""` |
| `observability.tracing.serviceName` | `inference-gateway` |
| `podDisruptionBudget.enabled` | `true` |
| `podDisruptionBudget.minAvailable` | `1` |
| `podLabels.platform.ai/cost-center` | `platform` |
| `podLabels.platform.ai/environment` | `local` |
| `podLabels.platform.ai/owner` | `platform-team` |
| `podLabels.platform.ai/sandbox-id` | `local-lab` |
| `replicaCount` | `1` |
| `resources.limits.cpu` | `500m` |
| `resources.limits.memory` | `512Mi` |
| `resources.requests.cpu` | `100m` |
| `resources.requests.memory` | `128Mi` |
| `routing.policy.enabled` | `false` |
| `routing.policy.models` | `[]` |
| `routing.policyPath` | `""` |
| `runtime.allowedModels` | `["qwen3.5:0.8b"]` |
| `runtime.backend` | `ollama` |
| `runtime.circuitFailureThreshold` | `0` |
| `runtime.circuitResetSeconds` | `30` |
| `runtime.maxRetries` | `0` |
| `runtime.modelId` | `qwen3.5:0.8b` |
| `runtime.ollamaBaseUrl` | `http://ollama.ollama.svc.cluster.local:11434` |
| `runtime.requestTimeoutSeconds` | `120` |
| `runtime.retryBackoffSeconds` | `0.1` |
| `runtime.vllmBaseUrl` | `http://vllm.vllm.svc.cluster.local:8000` |
| `sandboxPolicy.policy.enabled` | `false` |
| `sandboxPolicy.policy.policies` | `[]` |
| `sandboxPolicy.policyPath` | `""` |
| `service.port` | `8080` |
| `serviceAccount.automountServiceAccountToken` | `false` |
| `serviceAccount.create` | `true` |
| `serviceAccount.name` | `""` |
| `serviceMonitor.enabled` | `true` |
| `serviceMonitor.interval` | `30s` |
| `tests.enabled` | `true` |
| `tests.image.digest` | `sha256:9532d8c39891ca2ecde4d30d7710e01fb739c87a8b9299685c63704296b16028` |
| `tests.image.repository` | `busybox` |
| `tests.image.tag` | `1.37.0` |
| `topologySpread.enabled` | `true` |
| `topologySpread.maxSkew` | `1` |
| `topologySpread.topologyKey` | `kubernetes.io/hostname` |
| `topologySpread.whenUnsatisfiable` | `ScheduleAnyway` |
| `traceability.auditLogEnabled` | `true` |
| `traceability.defaultSandboxId` | `local-lab` |
<!-- chart-docs:end -->
## Install profiles

| Profile | Command |
| --- | --- |
| Minimal (chart defaults) | `helm install inference-gateway deploy/charts/inference-gateway` |
| Local kind lab | `helm install inference-gateway deploy/charts/inference-gateway -f deploy/clusters/local/values/inference-gateway.yaml` |
| Customer cluster | `helm install inference-gateway deploy/charts/inference-gateway -f deploy/clusters/customer/values/inference-gateway.yaml` |

In GitOps installs these value files are applied by the matching Argo CD Application in `deploy/clusters/<env>/apps.yaml`; the commands above are for direct `helm` workstation checks.
