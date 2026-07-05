# 0013. Opt-in bundled read-only admin console

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

## Context

`docs/scope-and-non-goals.md` listed "No admin/usage console UI": the kit shipped the data
layer a console would build on (`/v1/usage`, `/v1/sandbox/budget`, `/v1/models`, Prometheus
metrics, the client SDK) but no UI. The requirement now is a lightweight read-only console so an
operator can see a sandbox's usage, budget, and approved models without wiring Grafana or
running `curl`.

Constraints shape the design. The repo deliberately has **no frontend build tooling** (no
npm/node) and values a lean, hash-pinned, dependency-light footprint. A browser single-page app
calling the gateway from a different origin would need CORS on the gateway. The console must not
weaken auth or drag in a heavy stack.

## Decision

Ship a **self-contained static console**: a single HTML file with inline CSS and vanilla
JavaScript, no framework, no build step, and no external/CDN resources (CSP-safe). The operator
enters a gateway API key and sandbox id in the page; the console fetches the existing read-only
endpoints and renders health, usage, budget, and the model allowlist. It performs **no
mutations**.

The gateway optionally serves it at `/console` via a Starlette static mount, **off by default**
(`ADMIN_CONSOLE_ENABLED`). Serving it **same-origin** with the API means the browser's fetches
are same-origin, so there is no CORS and no separate deployment. `/console` is unauthenticated (it is static
HTML; the API calls the page makes carry the operator's key). No new dependency is added
(Starlette ships `StaticFiles`), and the console lives inside the existing gateway image
(`app/console/`).

## Consequences

- Operators get a zero-dependency read-only console by flipping one flag; when off, nothing is
  served and the surface is unchanged. The console is just another authenticated caller of the
  governed endpoints, so it can see no more than the supplied API key allows.
- Bundling the UI into the API image slightly couples the two, accepted for a reference kit to
  avoid CORS and a second deployment. It stays read-only and tiny (one HTML file).
- The API key is entered client-side and held only in the browser session (`sessionStorage`); no
  secret is baked into the console.

## Alternatives considered

- **A React/Vue SPA with a build pipeline.** Richer, but pulls npm/node and a build step into a
  repo that deliberately has no JS tooling and hash-pins every dependency. Disproportionate for a
  read-only dashboard. Rejected.
- **A separate nginx-served static site (its own chart).** Cleaner separation, but introduces
  cross-origin calls (CORS on the gateway) or a shared-ingress path-routing setup, plus another
  chart to maintain. Rejected in favor of the same-origin gateway mount.
- **Server-rendered HTML from the gateway.** Would add templating to an API service and re-render
  on every view. Rejected: a static page calling the JSON APIs keeps the gateway API-only in
  spirit.
- **Keep it a non-goal (Grafana + curl).** Grafana still owns metrics dashboards; this console is
  a lightweight per-sandbox usage/budget/model view for operators who do not want to wire Grafana
  for that. Implemented as **opt-in** so the non-goal stance remains the default.
