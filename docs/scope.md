# Scope

This page describes what release `v0.29.0` covers and how it fits alongside the infrastructure and tools you already run. For per-feature defaults, use the [feature inventory](feature-inventory.md).

## What the repository provides

The repository contains and tests:

- the inference gateway and RAG service under `src/`;
- Helm charts for those services, Ollama, vLLM, Redis, Qdrant, and agent workspaces;
- local and customer Argo CD application manifests;
- Kubernetes policy, tenant templates, model catalog records, eval definitions, and SLO inputs;
- API and configuration contracts;
- validation, evidence, release, and supply-chain scripts;
- operational runbooks and customer handoff documentation.

The gateway implements these protocol families:

- OpenAI-style chat completions, legacy completions, embeddings, moderations, models, Files, Batch, and Responses;
- the project-specific synchronous `/v1/batch-inference`, usage, and sandbox-budget endpoints;
- an Anthropic Messages translation endpoint, with streaming.

The generated [OpenAPI contract](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/api-contracts/inference-gateway.openapi.json) is the route-level reference.

## Protocol coverage

- Chat completions and Anthropic Messages stream responses. Legacy completions and Responses return complete responses.
- Responses supports the synchronous request shape, function tools with multi-turn `function_call` / `function_call_output` items, and `input_image` parts. Optional stored state supports `store`, `previous_response_id`, retrieve, delete, and input-items routes.
- The asynchronous Batch implementation accepts chat completions, completions, and embeddings. `completion_window` is treated as an expiry bound.
- Translated Messages and Responses payloads carry the supported text and tool fields.

## How it fits with your infrastructure

The platform deploys onto a Kubernetes cluster you already operate and plugs into the services around it. The operator provides and owns:

- the Kubernetes cluster and its upgrades;
- networks, load balancers, GPU nodes, and cloud databases;
- the identity provider and secret manager;
- production ingress, certificate authority, logging service, backup destination, and incident team;
- the choice, hosting, licensing, and validation of model weights;
- data classification and the decision whether a use case is regulated;
- sizing of replicas, GPU memory, context windows, storage, retention, and SLOs for each workload;
- day-to-day operation of the platform.

The customer values are starting points: replace their placeholders and tune the GPU defaults and stateful service topology for your workload.

Pair the platform with purpose-built systems for cloud provisioning, distributed training, fine-tuning, audio, image generation, multi-node serving orchestration, and billing. The read-only `/console` gives operators a view over health, models, usage, and budget data; configuration changes flow through GitOps.

## Security and compliance

The repository ships controls, policies, and crosswalks that support an assessment. NetworkPolicy governs which workloads can talk to each other, and the opt-in [mTLS overlay](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/mtls/README.md) encrypts traffic between them. Configure a gVisor, Kata, or equivalent `RuntimeClass` to give agent sandboxes a separate kernel boundary. Anchor hash-chain heads outside the process (`make audit-anchor`) for durable, rollback-resistant audit logs. Generate current evidence for each release or deployment with `make evidence`.

Compliance with a law, standard, or internal policy is established per deployment from its use case, configuration, operations, and current evidence. See the [security overview](security-overview.md), [threat model](threat-model.md), and [production readiness matrix](production-readiness.md).
