# OIDC / JWKS Rotation Runbook

The inference gateway can validate OIDC bearer tokens beside API-key auth. Validation is
JWKS-driven: the gateway fetches signing keys from `JWT_JWKS_URL`, caches them for
`JWT_CACHE_SECONDS`, and verifies HS256, RS256, or ES256 signatures plus `exp`, `nbf`,
`iss`, `aud`, and required-scope claims. Because keys are fetched by `kid`, the gateway
follows standard JWKS key rotation without a redeploy.

Use this runbook to enable JWT auth and to drill issuer key rotation.

## Enable JWT Validation

Set the JWT block in the gateway chart values and apply through GitOps. Configuration maps to
`auth.jwt.*` in [deploy/charts/inference-gateway/values.yaml](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts/inference-gateway/values.yaml).

```yaml
auth:
  enabled: true            # API-key auth stays available alongside JWT
  jwt:
    enabled: true
    jwksUrl: "https://<issuer>/.well-known/jwks.json"
    issuer: "https://<issuer>/"
    audience: "private-ai-platform-kit"
    requiredScopes:
      - inference.invoke
    cacheSeconds: 300
```

### IdP-specific endpoints

| IdP | `issuer` | `jwksUrl` |
| --- | --- | --- |
| Keycloak | `https://<host>/realms/<realm>` | `https://<host>/realms/<realm>/protocol/openid-connect/certs` |
| Auth0 | `https://<tenant>.auth0.com/` | `https://<tenant>.auth0.com/.well-known/jwks.json` |
| Okta | `https://<org>.okta.com/oauth2/<authz-server>` | `https://<org>.okta.com/oauth2/<authz-server>/v1/keys` |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant>/v2.0` | `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` |

Resolve `issuer` and `jwksUrl` from the IdP discovery document at
`https://<issuer>/.well-known/openid-configuration` (the `issuer` and `jwks_uri` fields).

## Rotation Drill

Goal: prove the gateway accepts tokens signed with a newly rotated key without downtime, and
stops accepting retired keys.

1. **Publish the new key.** Add the new signing key to the IdP so the JWKS document serves both
   the current key (`kid-old`) and the new key (`kid-new`). Do not retire the old key yet.
2. **Wait for cache expiry.** The gateway refreshes its JWKS cache after at most
   `JWT_CACHE_SECONDS`. New gateway pods refresh on first authenticated request.
3. **Verify new-key acceptance.** Mint a token signed with `kid-new` and confirm a
   `POST /v1/chat/completions` call returns a non-401 status:
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -H "Authorization: Bearer $NEW_KID_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"ping"}]}' \
     "$GATEWAY_URL/v1/chat/completions"
   ```
4. **Confirm old-key tokens still validate** until they expire, so in-flight sessions are not
   broken during the overlap window.
5. **Retire the old key.** Once all `kid-old` tokens have expired (after their `exp`), remove
   `kid-old` from the IdP. After the next cache refresh the gateway rejects any token still
   presenting `kid-old` with `401` and reason `invalid_or_missing_api_key`.
6. **Record evidence.** Capture the HTTP codes from steps 3-5 and the
   `inference_gateway_auth_failures_total` metric delta for the drill window.

## Rollback

If new-key tokens are rejected after the cache window, re-add `kid-old` to the JWKS document
(reverting step 5) so existing tokens validate, then investigate the new key's `kid`, `alg`,
and `use` fields. Lowering `JWT_CACHE_SECONDS` shortens the propagation window for the next
attempt. Tightening `issuer`, `audience`, or `requiredScopes` only affects claim validation,
not key selection.

## Validation

Key selection, signature verification, claim validation, and rotation behavior are covered by
`src/inference-gateway/tests/test_jwt_auth.py`, including the rotated-key case where a
retired `kid` is rejected while the active `kid` validates.
