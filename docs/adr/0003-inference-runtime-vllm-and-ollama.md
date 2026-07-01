# 0003. Inference runtimes: vLLM and Ollama

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform needs a model-serving runtime behind the gateway that satisfies two profiles with one
operating model: a fast laptop/CI lab that runs on CPU without a GPU, and a customer GPU cluster
serving coding-agent workloads on NVIDIA or AMD hardware. The gateway speaks the OpenAI HTTP API, so
whatever serves models must expose an OpenAI-compatible endpoint. The choice should not lock the kit
to a single accelerator vendor.

## Decision

Ship two runtimes selected per environment, both OpenAI-compatible, with the gateway routing to one
via `RUNTIME_BACKEND`.

- **Ollama** for the local-first profile.
  [`deploy/charts/ollama`](../../deploy/charts/ollama) (`appVersion` `0.24.0`) backs the laptop and
  CI smoke path. The default local model is `qwen2.5:0.5b` (fast, non-reasoning, keeps CPU smoke
  quick); the customer Ollama profile default is the larger `qwen3.5:0.8b` reasoning model.
- **vLLM** for GPU clusters.
  [`deploy/charts/vllm`](../../deploy/charts/vllm) (`appVersion` `0.22.0`) backs NVIDIA and AMD GPU
  nodes via the
  [`vllm-nvidia.yaml`](../../deploy/clusters/customer/values/vllm-nvidia.yaml) and
  [`vllm-amd.yaml`](../../deploy/clusters/customer/values/vllm-amd.yaml) value profiles. The default
  customer vLLM target is `Qwen/Qwen3-Coder-Next` for coding-agent workloads.
- The gateway selects the backend from `RUNTIME_BACKEND`, validated to `ollama` or `vllm` in
  [`src/inference-gateway/app/settings.py`](../../src/inference-gateway/app/settings.py). Both
  runtimes ship as separate Argo CD applications (`runtime-ollama`, `runtime-vllm`) in
  [`deploy/clusters/local/apps.yaml`](../../deploy/clusters/local/apps.yaml).

## Consequences

- A contributor reproduces the full request path on a laptop with no GPU (Ollama), and a customer
  serves the same OpenAI API on GPUs (vLLM), with the gateway, policies, governance, and evidence
  unchanged between them.
- GPU vendor neutrality is explicit: NVIDIA clusters expose `nvidia.com/gpu`, AMD clusters expose
  `amd.com/gpu`, GPU nodes are labelled `platform.ai/node-pool=gpu` and
  `platform.ai/gpu-vendor=<nvidia|amd>`, and there are committed value profiles for each.
- Two runtimes mean two charts, two pinned `appVersion`s, and two sets of capacity assumptions to
  track. Replica count, context length, tensor parallelism, and GPU requests are explicitly left for
  the operator to tune before production use.
- The gateway abstracts the choice for callers; it can also fail over and shadow/canary across
  resolved routes (see [0005](0005-openai-compatible-gateway.md)), but the per-environment default
  backend is a single value, not a blend.

## Alternatives considered

- **Hugging Face TGI.** A strong OpenAI-compatible GPU server. vLLM was chosen for the GPU profile
  for its throughput-oriented batching and broad model coverage, and the kit already documents vLLM
  GPU/ROCm references; TGI would be a viable substitute but adds no capability this kit lacks.
- **TensorRT-LLM.** Excellent NVIDIA-specific performance. Rejected as the default because it is
  NVIDIA-only and conflicts with the explicit AMD/NVIDIA neutrality goal; a customer who wants it can
  point a value profile at a TensorRT-LLM-backed OpenAI endpoint.
- **llama.cpp (server).** Lightweight and great for single-machine CPU/Metal use. Rejected for the
  local default in favor of Ollama, which provides simpler model pull/management and a clean
  OpenAI-compatible surface inside a chart; the decision-guide already names "a single-machine
  personal Ollama setup" as a poor fit for the kit itself, which is a different point from the
  runtime choice here.
- **One runtime for both profiles.** Rejected: no single runtime is simultaneously the lightest CPU
  laptop experience and the highest-throughput multi-vendor GPU server. Splitting by profile keeps
  each path optimal while the gateway hides the difference from callers.
