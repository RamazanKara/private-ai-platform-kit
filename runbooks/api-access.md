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

### Per-tenant sandbox binding

Set `auth.jwt.tenantClaim` to bind the sandbox id to a verified JWT claim. When set, the gateway takes the sandbox from that claim instead of trusting the client `X-Sandbox-ID` header: a request whose header contradicts the claim is rejected with `403` and reason `sandbox_identity_mismatch`, and a request missing the header adopts the bound sandbox. This also scopes the read-only `GET /v1/usage` and `GET /v1/sandbox/budget` endpoints - a bound caller can only ever read its own tenant's usage and budget, never another tenant's by setting a different header. The customer overlay ships this on as a template (`tenantClaim: sandbox_id`); the operator completes the placeholder issuer/JWKS/audience with their real IdP.

Without a binding (`tenantClaim` empty, the base-chart default), the gateway is header-trusted: any valid key or token may assert any sandbox via `X-Sandbox-ID`. This is the documented insecure default for the local lab and single-tenant deployments.

## API-Key Records (per-key scopes, expiry, sandbox binding, budget)

Beside the flat `API_KEY_SHA256S` allowlist, the gateway can load an optional **API-key records** file (`API_KEY_RECORDS_PATH`, a JSON or YAML document) that attaches per-key attributes. A flat hash remains an unbound, unexpiring, unscoped key; a record adds any of:

- `sandbox` - binds the key to one sandbox id, enforced exactly like the JWT `tenantClaim` (a mismatched `X-Sandbox-ID` is `403`; a missing one adopts the binding);
- `scopes` - recorded on the audit principal for attribution;
- `expires_at` - epoch seconds or ISO-8601; a presented-but-expired key is rejected with `401` and reason `api_key_expired`;
- `budget` - per-key overrides of the sandbox request / prompt-char / estimated-token budgets, applied to that request (and reflected in `GET /v1/usage`). Each field follows the platform convention that **`0` means unlimited**, not "deny": to tighten a key set a small positive limit, never `0`. A key must not appear in both `apiKeyHashes` and a record - the record always wins (binding, expiry, and scopes), but list it in one place to keep the intent clear.

The file is matched by SHA-256 (constant-time), so it never stores plaintext keys. A **malformed records file fails the gateway closed at startup** (the pod does not start) rather than silently disabling auth. No records file configured means today's flat-hash behavior is unchanged.

Example `key-records.json`:

    {
      "records": [
        {
          "name": "team-a-agent",
          "sha256": "<sha256 hex of the issued key>",
          "sandbox": "team-a",
          "scopes": ["chat:write"],
          "expires_at": "2027-01-01T00:00:00Z",
          "budget": { "requestLimit": 5000, "estimatedTokenLimit": 2000000 }
        },
        {
          "name": "break-glass",
          "sha256": "<sha256 hex of an unbound key>"
        }
      ]
    }

Because the file maps key hashes to tenant bindings, mount it from a Secret rather than committing it to values. Set `auth.keyRecords.existingSecret.name` (and `key`, default `key-records.json`) and the chart mounts it read-only at `auth.keyRecords.mountPath` (default `/etc/private-ai-platform-kit/auth`) and points `API_KEY_RECORDS_PATH` at it:

    auth:
      enabled: true
      keyRecords:
        existingSecret:
          name: inference-gateway-key-records
          key: key-records.json

Generate a record's hash the same way as a flat hash:

    printf '%s' "$PLATFORM_API_KEY" | sha256sum | awk '{print $1}'

Rotate a records-based key by adding the new record (new hash), rolling out clients, then removing the old record - or set an `expires_at` for a hard cutover.

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

## Human SSO for Operator Dashboards

Two distinct auth surfaces exist in this platform; do not conflate them:

- **Machine auth on the data plane** - the inference gateway and RAG service authenticate *workloads* (agents, apps, CI) with the API keys, API-key records, and JWTs described above. This is what gates `POST /v1/chat/completions` and friends.
- **Human SSO on the control plane** - the *operator dashboards* (Grafana, Argo CD) authenticate *people* via your OIDC identity provider. This is unrelated to the gateway's machine auth and never grants access to tenant inference traffic.

The kit does **not** run an identity provider. The snippets below are operator templates that wire Grafana and Argo CD to an IdP you already operate (Keycloak, Auth0, Okta, Microsoft Entra ID, Google Workspace, etc.). Resolve `issuer`, auth, token, and userinfo/JWKS URLs from the IdP discovery document at `https://<issuer>/.well-known/openid-configuration`, and source every client secret from your secret manager - never commit it.

### Grafana OIDC

Grafana ships as part of the `kube-prometheus-stack` Application in [deploy/observability/applications.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/observability/applications.yaml). Add OIDC under the chart's `grafana.grafana.ini` and map an IdP group to the Grafana admin role. Template - replace the placeholders:

    grafana:
      # Source GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET from a Secret via grafana.envFromSecret;
      # do not inline the client secret here.
      grafana.ini:
        server:
          root_url: https://grafana.example.com/
        auth.generic_oauth:
          enabled: true
          name: Corporate SSO
          client_id: grafana
          scopes: "openid email profile groups"
          auth_url: https://idp.example.com/authorize
          token_url: https://idp.example.com/oauth/token
          api_url: https://idp.example.com/userinfo
          # Grant admin to members of the platform-admins IdP group; everyone else is a Viewer.
          role_attribute_path: "contains(groups[*], 'platform-admins') && 'Admin' || 'Viewer'"
          allow_assign_grafana_admin: true

### Argo CD SSO (OIDC via Dex or direct)

Argo CD authenticates operators through its `argocd-cm`/`argocd-rbac-cm` ConfigMaps. Either point Argo CD directly at your IdP (`oidc.config`) or front it with the bundled Dex connector. Template - replace the placeholders and store `clientSecret` in the `argocd-secret` Secret (referenced as `$oidc.clientSecret`):

    # argocd-cm
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: argocd-cm
      namespace: argocd
    data:
      url: https://argocd.example.com
      oidc.config: |
        name: Corporate SSO
        issuer: https://idp.example.com/
        clientID: argocd
        clientSecret: $oidc.clientSecret
        requestedScopes: ["openid", "profile", "email", "groups"]
    ---
    # argocd-rbac-cm: map an IdP group to the built-in admin role.
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: argocd-rbac-cm
      namespace: argocd
    data:
      policy.default: role:readonly
      policy.csv: |
        g, platform-admins, role:admin

Human SSO for these dashboards is an operator responsibility outside the kit's data-plane security boundary; see [Security overview](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/docs/security-overview.md) for where that boundary sits.

## Troubleshooting

HTTP 401 with reason `invalid_or_missing_api_key` means the request did not include a recognized key. Verify the client header, the configured hash, and whether the deployment has reloaded the updated Secret.

HTTP 401 with reason `api_key_expired` means the presented key matched an API-key record whose `expires_at` has passed. Issue a fresh key (add a new record) and roll out the client; the expired record can then be removed.

HTTP 403 with reason `sandbox_identity_mismatch` means a sandbox-bound principal (JWT `tenantClaim` or an API-key record with a `sandbox`) sent an `X-Sandbox-ID` that does not match its binding. The caller may only act as - and read the usage/budget of - its bound sandbox; drop the contradicting header or use the correct sandbox.
