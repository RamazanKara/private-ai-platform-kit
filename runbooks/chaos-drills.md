# Chaos Drills Runbook

Use this runbook to prove that platform components recover from safe Kubernetes restarts.

## Available Drills

The scripted drills are intentionally controlled and non-destructive:

- `gateway-rollout`: restarts the inference gateway Deployment and runs gateway smoke.
- `budget-redis-rollout`: restarts the shared budget Redis Deployment, checks `redis-cli ping`, and runs gateway smoke.
- `ollama-rollout`: restarts the Ollama StatefulSet and runs gateway smoke when the model is present.
- `rag-service-rollout`: restarts the RAG service Deployment and runs RAG smoke.
- `qdrant-vector-store-rollout`: restarts Qdrant and validates vector-backed RAG with `EXPECTED_RAG_BACKEND=qdrant`.
- `vllm-runtime-rollout`: restarts the production-style vLLM Deployment and runs gateway smoke through the vLLM backend.
- `gpu-capacity-preflight`: non-mutating customer-cluster preflight that checks GPU node labels and allocatable `nvidia.com/gpu` or `amd.com/gpu`.

Drill definitions live under `chaos/drills/` as `ChaosDrill` YAML documents so they can be reviewed like other operational controls.

## Run A Drill

Run the default gateway drill:

    make chaos-drill

Run the shared budget backend drill:

    DRILL=budget-redis-rollout make chaos-drill

Run the Ollama drill:

    DRILL=ollama-rollout make chaos-drill

Run the RAG and vector-store drills:

    DRILL=rag-service-rollout make chaos-drill
    DRILL=qdrant-vector-store-rollout make chaos-drill

Run customer-only vLLM and GPU capacity drills:

    DRILL=vllm-runtime-rollout make chaos-drill
    DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill

Constrain GPU preflight to a vendor or resource when needed:

    GPU_VENDOR=amd GPU_RESOURCE_NAME=amd.com/gpu DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill

Skip the post-drill smoke only when debugging rollout mechanics:

    RUN_SMOKE=0 DRILL=gateway-rollout make chaos-drill

## Expected Evidence

A successful drill shows:

- Kubernetes rollout restart was accepted.
- Rollout completed within the timeout.
- Post-drill smoke passed.
- For budget Redis, `redis-cli ping` returned `PONG`.
- For Qdrant, RAG health reported `retrieval_backend=qdrant`.
- For GPU capacity, at least one labeled GPU node exposed allocatable NVIDIA or AMD GPU resources.

Record failed drills as incidents if a component does not roll out or the smoke check fails after recovery.
