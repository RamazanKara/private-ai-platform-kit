# Platform Chart

Umbrella chart that installs the full Private AI Platform Kit stack (inference gateway, RAG service, Ollama/vLLM runtimes, Qdrant vector store, budget Redis) as one release. Each component is toggled with its <name>.enabled flag. GitOps (Argo CD) remains the recommended path for multi-namespace installs; this chart is for a single-command dev/demo bring-up.

<!-- chart-docs:start -->
## Values

| Value | Default |
| --- | --- |
| `budget-redis.enabled` | `true` |
| `inference-gateway.enabled` | `true` |
| `ollama.enabled` | `true` |
| `qdrant-vector-store.enabled` | `false` |
| `rag-service.enabled` | `true` |
| `vllm.enabled` | `false` |
<!-- chart-docs:end -->
