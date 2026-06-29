# API Access Runbook

Use this runbook when configuring or rotating access to the inference gateway or RAG service.

For security boundaries and threat modeling, see [Threat model](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/threat-model.md).

## Authentication Model

Gateway and RAG business endpoints use API-key authentication when `auth.enabled` is true in Helm values. Health and metrics endpoints remain unauthenticated for Kubernetes probes and in-cluster scraping.

Clients may send either:

    X-API-Key: <key>

or:

    Authorization: Bearer <key>

The services store and compare only SHA-256 hashes from `API_KEY_SHA256S`. Plaintext keys should come from the customer's secret manager, CI secret store, or local operator shell.

The inference gateway also supports optional JWT bearer validation beside API-key hashes:

    auth:
      jwt:
        enabled: true
        jwksUrl: https://idp.example/.well-known/jwks.json
        issuer: https://idp.example
        audience: private-ai-platform-kit
        requiredScopes:
          - chat:write

The gateway validates HS256 `oct`, RS256 `RSA`, and ES256 P-256 `EC` JWKS keys, including `exp`, optional `nbf`, issuer, audience, and required scopes from `scope` or `scp` claims. Prefer RS256 or ES256 for enterprise OIDC providers and keep API-key hashes available for break-glass automation.

## Local Lab

The local demo key is:

    local-development-only

Local values store only its SHA-256 digest. Smoke scripts send the key through `PLATFORM_API_KEY`, defaulting to the local demo key:

    PLATFORM_API_KEY=local-development-only make smoke
    PLATFORM_API_KEY=local-development-only make rag-smoke
    PLATFORM_API_KEY=local-development-only make agent-smoke

## Customer Clusters

Customer values reference External Secrets-backed Kubernetes Secrets:

- `inference/inference-gateway-secrets`, key `api-key-sha256s`
- `rag/rag-service-secrets`, key `api-key-sha256s`

Populate the upstream secret property `api-key-sha256s` with one or more comma-separated SHA-256 hashes.

Generate a hash:

    printf '%s' "$PLATFORM_API_KEY" | sha256sum | awk '{print $1}'

Rotate by adding the new hash, rolling out clients, then removing the old hash.

For JWT signing-key rotation, publish both old and new signing keys in JWKS, wait at least `auth.jwt.cacheSeconds` plus the maximum token lifetime, then remove the old key. During IdP outages or emergency rotation, keep at least one API-key hash active for operational break-glass access.

## Gateway Policy Files

Mount `ModelRoutingPolicy` to route approved model IDs to Ollama or vLLM:

    apiVersion: platform.ai/v1alpha1
    kind: ModelRoutingPolicy
    spec:
      models:
        - id: qwen-coder
          backend: vllm
          aliases: [coder]
        - id: qwen-local
          backend: ollama

Mount `SandboxPolicySet` to narrow per-sandbox limits:

    apiVersion: platform.ai/v1alpha1
    kind: SandboxPolicySet
    spec:
      policies:
        - sandboxId: regulated-offline-lab
          allowedModels: [qwen-local]
          maxPromptChars: 4096
          maxCompletionTokens: 512
          allowStreaming: false
          budgets:
            requestLimit: 100

The gateway exposes `GET /v1/models` for approved models and `GET /readyz` for runtime-aware readiness. `/readyz` omits backend URLs and secrets.

## Troubleshooting

HTTP 401 with reason `invalid_or_missing_api_key` means the request did not include a recognized key. Verify the client header, the configured hash, and whether the deployment has reloaded the updated Secret.
