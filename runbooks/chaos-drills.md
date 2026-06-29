# Chaos Drills Runbook

Use this runbook to prove how platform components behave under controlled
disruption. Be precise about what each drill proves:

- **Rollout/recovery drills** (`*-rollout`) restart a workload with `kubectl
  rollout restart` and assert it comes back healthy. They prove graceful
  restart and post-restart smoke. They are *not* fault injection -- nothing is
  taken away or broken, the workload is simply rolled.
- **Capacity preflight** (`gpu-capacity-preflight`) is a non-mutating check of
  GPU node labels and allocatable capacity.
- **Fault-injection drills** (`rag-degradation-fault`) actively remove a
  dependency from a running service and assert it degrades gracefully, then
  assert recovery. This is the only drill here that injects a real fault.

## Available Drills

Rollout/recovery drills (controlled restarts, non-destructive):

- `gateway-rollout`: rollout-restarts the inference gateway Deployment and runs gateway smoke.
- `budget-redis-rollout`: rollout-restarts the shared budget Redis Deployment, checks `redis-cli ping`, and runs gateway smoke.
- `ollama-rollout`: rollout-restarts the Ollama StatefulSet and runs gateway smoke when the model is present.
- `rag-service-rollout`: rollout-restarts the RAG service Deployment and runs RAG smoke.
- `qdrant-vector-store-rollout`: rollout-restarts Qdrant and validates vector-backed RAG with `EXPECTED_RAG_BACKEND=qdrant`.
- `vllm-runtime-rollout`: rollout-restarts the production-style vLLM Deployment and runs gateway smoke through the vLLM backend.

Capacity preflight (non-mutating):

- `gpu-capacity-preflight`: customer-cluster preflight that checks GPU node labels and allocatable `nvidia.com/gpu` or `amd.com/gpu`.

Fault injection (removes a live dependency):

- `rag-degradation-fault`: scales Qdrant to 0 replicas under the running RAG
  service, asserts RAG stays `Available` (graceful degradation, PDB holds),
  then restores Qdrant and asserts recovery via RAG smoke. The original Qdrant
  replica count is restored on exit even if an assertion fails.

Drill definitions live under `chaos/drills/` as `ChaosDrill` YAML documents so they can be reviewed like other operational controls.

## Run A Drill

Run the default gateway rollout/recovery drill:

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

Run the true fault-injection drill (removes Qdrant under load):

    DRILL=rag-degradation-fault make chaos-drill

Constrain GPU preflight to a vendor or resource when needed:

    GPU_VENDOR=amd GPU_RESOURCE_NAME=amd.com/gpu DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill

Skip the post-drill smoke only when debugging rollout mechanics:

    RUN_SMOKE=0 DRILL=gateway-rollout make chaos-drill

## Expected Evidence

A successful rollout/recovery drill shows:

- Kubernetes rollout restart was accepted.
- Rollout completed within the timeout.
- Post-drill smoke passed.
- For budget Redis, `redis-cli ping` returned `PONG`.
- For Qdrant, RAG health reported `retrieval_backend=qdrant`.
- For GPU capacity, at least one labeled GPU node exposed allocatable NVIDIA or AMD GPU resources.

A successful `rag-degradation-fault` fault-injection drill shows:

- Qdrant was scaled to 0 (the fault was actually injected).
- The RAG Deployment stayed `Available` while Qdrant was down (no hard crash; PDB/error-budget held).
- Qdrant was restored to its original replica count.
- Post-fault RAG smoke passed against the qdrant backend.

Record failed drills as incidents if a component does not roll out, does not
stay available under the injected fault, or the smoke check fails after recovery.
