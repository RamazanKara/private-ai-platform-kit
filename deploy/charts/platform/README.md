# Platform Chart

Umbrella chart that installs the full Private AI Platform Kit stack (inference gateway, RAG service, Ollama/vLLM runtimes, Qdrant vector store, budget Redis) as one release. Each component is toggled with its <name>.enabled flag. GitOps (Argo CD) remains the recommended path for multi-namespace installs; this chart is for a single-command dev/demo bring-up.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `budget-redis.enabled` | `true` |
| `budget-redis.fullnameOverride` | `budget-redis` |
| `budget-redis.namespace.create` | `false` |
| `budget-redis.networkPolicy.enabled` | `false` |
| `inference-gateway.budget.backend` | `redis` |
| `inference-gateway.budget.redisUrl` | `redis://budget-redis:6379/0` |
| `inference-gateway.enabled` | `true` |
| `inference-gateway.fullnameOverride` | `inference-gateway` |
| `inference-gateway.keda.enabled` | `false` |
| `inference-gateway.namespace.create` | `false` |
| `inference-gateway.networkPolicy.enabled` | `false` |
| `inference-gateway.responseCache.redisUrl` | `redis://budget-redis:6379/1` |
| `inference-gateway.runtime.ollamaBaseUrl` | `http://ollama:11434` |
| `inference-gateway.runtime.vllmBaseUrl` | `http://vllm:8000` |
| `inference-gateway.serviceMonitor.enabled` | `false` |
| `ollama.enabled` | `true` |
| `ollama.fullnameOverride` | `ollama` |
| `ollama.namespace.create` | `false` |
| `ollama.networkPolicy.enabled` | `false` |
| `qdrant-vector-store.enabled` | `false` |
| `qdrant-vector-store.fullnameOverride` | `qdrant-vector-store` |
| `qdrant-vector-store.namespace.create` | `false` |
| `qdrant-vector-store.networkPolicy.enabled` | `false` |
| `qdrant-vector-store.serviceMonitor.enabled` | `false` |
| `rag-service.enabled` | `true` |
| `rag-service.fullnameOverride` | `rag-service` |
| `rag-service.namespace.create` | `false` |
| `rag-service.networkPolicy.enabled` | `false` |
| `rag-service.serviceMonitor.enabled` | `false` |
| `vllm.enabled` | `false` |
| `vllm.fullnameOverride` | `vllm` |
| `vllm.namespace.create` | `false` |
| `vllm.networkPolicy.enabled` | `false` |
| `vllm.serviceMonitor.enabled` | `false` |
<!-- chart-docs:end -->
