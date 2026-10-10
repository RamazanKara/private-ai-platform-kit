# FAQ

## Is this production-ready?

It is a reference implementation and customer template, not a managed product. The customer values still need identity, secrets, ingress, transport encryption, storage, observability, backup, model selection, capacity tests, and current release evidence. See [Production readiness](production-readiness.md).

## What does the project install?

The local Argo CD profile installs the platform services and several lab add-ons. The customer profile installs a smaller application set and expects the operator to provide platform operators, observability, ingress, and backup integration. [Architecture](architecture.md) lists the difference.

## Does the quickstart work offline?

No. The first run downloads tools, manifests, images, charts, Python packages, and an Ollama model. Inference uses the local Ollama pod after setup. An offline deployment needs internal mirrors and preloaded artifacts.

## Do I need a GPU?

Not for the local path. The local smoke test uses `qwen2.5:0.5b` with Ollama on CPU. The checked-in customer vLLM profiles expect GPU resources and must be resized for the target model and nodes.

## Is in-cluster traffic encrypted?

The data plane uses HTTP by default, and NetworkPolicy restricts which pods can connect. Enable the opt-in [mTLS overlay](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/customer/mtls/README.md) to encrypt pod-to-pod traffic. See [Security overview](security-overview.md).

## Is the agent workspace a separate kernel sandbox?

Yes, when the cluster supplies an isolation runtime such as gVisor or Kata and `sandbox.runtimeClassName` selects it. On the node's standard container runtime, the workspace uses a restricted container/pod security boundary.

## How is tenant identity enforced?

A sandbox-bound key record or verified JWT tenant claim binds the gateway request to a tenant. RAG can verify its own JWT as well. Use one of these bindings for multi-tenant deployments: without them, `X-Sandbox-ID` is trusted caller input under a shared key.

## Which OpenAI and Anthropic routes does the gateway implement?

The routes in the checked-in [OpenAPI contract](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/platform/api-contracts/inference-gateway.openapi.json). Chat completions and Anthropic Messages stream; legacy completions and Responses return complete responses. See [Scope](scope.md).

## What do the checked-in evidence files prove?

Files named `sample-*` show report shape and gate behavior, and the non-strict gate uses them. For the state of the current checkout, a release, or a customer cluster, generate fresh reports and use `make release-gate-strict` for a handoff.

## Does the audit chain prevent log tampering?

It makes edits and reordering detectable in an exported chain. Pair it with external log retention and a trusted chain-head anchor for durability and rollback detection. Each gateway process/replica has its own chain.

## What does the `regulated-offline` profile guarantee?

It renders a tenant namespace with no external CIDR egress. Cluster-wide air-gapping, private registries, model mirrors, internal identity, and cluster-wide egress policy are configured by the operator. See the [restricted-egress example](regulated-offline-tenant-example.md).

## How do I upgrade or roll back?

Change the immutable `CUSTOMER_REVISION`, review the rendered changes, and let Argo CD reconcile. Roll back by returning to the prior tag. Follow the [upgrade runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/upgrade.md); for stateful schema or collection changes, plan a matching data rollback.

## Where should I report a security issue?

Use the private process in [SECURITY.md](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/SECURITY.md). Do not put secrets, customer data, private prompts, or exploit details in a public issue.
