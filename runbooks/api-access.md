# API Access Runbook

Use this runbook when configuring or rotating access to the inference gateway or RAG service.

## Authentication Model

Gateway and RAG business endpoints use API-key authentication when `auth.enabled` is true in Helm values. Health and metrics endpoints remain unauthenticated for Kubernetes probes and in-cluster scraping.

Clients may send either:

    X-API-Key: <key>

or:

    Authorization: Bearer <key>

The services store and compare only SHA-256 hashes from `API_KEY_SHA256S`. Plaintext keys should come from the customer's secret manager, CI secret store, or local operator shell.

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

## Troubleshooting

HTTP 401 with reason `invalid_or_missing_api_key` means the request did not include a recognized key. Verify the client header, the configured hash, and whether the deployment has reloaded the updated Secret.
