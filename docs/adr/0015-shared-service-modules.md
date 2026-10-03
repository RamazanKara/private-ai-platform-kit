# 0015. Shared service modules are copied per image and kept identical

- Status: Accepted
- Date: 2026-10-04
- Deciders: Platform maintainer

## Context

The inference gateway and the RAG service need the same request body limit, the same tracing
setup, and the same JWKS cache and JWT signature verification. Each service builds its image
from its own directory (`context: src/<service>` in `.github/workflows/ci.yml` and in both
Dockerfiles), so neither can import code that lives outside that directory.

The repository had dealt with this by copying the modules, and the copies drifted. The JWKS
cache gained a fix in one service and not the other, the RAG copy compared API keys with an
early-exit `any()` where the gateway compared in constant time, and the audit helpers diverged
until the RAG service lost a persistence metric. Each drift was a correctness or security gap
found only by reading both files side by side.

## Decision

Shared code stays copied into each service, and the copies must be byte-identical:

- `app/body_limit.py`, `app/tracing.py`, and `app/jwks.py` exist in both services with the same
  bytes. `make repo-hygiene` (part of `make validate`) fails when any pair differs, naming the
  file, so a change to one copy cannot merge without the other.
- A shared module takes its configuration as an explicit value, not a service's `Settings`
  class. `app/jwks.py` defines `JwtConfig`; each service's `app/jwt_auth.py` maps its own
  settings onto it and adds what only that service needs (the gateway's required scopes, the RAG
  service's tenant claim). Service-specific code never lives in a shared file.
- To share another module, move its service-independent part into a file listed in
  `SHARED_SERVICE_MODULES` in `scripts/repo-hygiene.py`, and keep the service-specific
  remainder in the service.

## Consequences

- A fix to shared code is one edit applied to two files, and CI refuses a partial application.
- Images stay self-contained: each build context holds everything its image needs, and no
  packaging step or second build context is introduced.
- The audit chain helpers remain separate implementations that share the hash primitives and
  the verifier (`scripts/audit-verify.py`) rather than code. Their record shapes differ by design
  (`inference_request` and `rag_query`); unifying them is not worth the coupling.

## Alternatives considered

- **A shared Python package under `src/common/`, installed into both images.** The cleanest
  long-term layout. Rejected for now because it changes both Docker build contexts, the image
  CI jobs, the Dependabot directories, coverage, and the locks, for three small modules. If the
  shared surface grows past a handful of files, revisit this.
- **A git submodule or vendored wheel.** Adds a release step and version skew between the
  services for code that changes together. Rejected.
- **Accept the duplication and review it.** This is what produced the drift above. Rejected.
