# 0008. Helm packaging and OCI distribution

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform's workloads — gateway, runtimes, RAG, vector store, budget Redis, agent workspaces —
need a packaging format that is parameterizable per environment (local versus customer, Ollama versus
vLLM, NVIDIA versus AMD), reconcilable by Argo CD, and distributable as verifiable, versioned
artifacts a customer can pull and check before installing into a production cluster. The artifacts
must fit the kit's supply-chain story (pinned digests, SBOMs, Cosign signatures) without standing up
a separate chart-hosting service.

## Decision

Package every first-party workload as a Helm chart and distribute the charts as Cosign-signed OCI
artifacts in the same registry as the images.

- Each workload is a Helm chart under [`deploy/charts/`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/charts) — inference-gateway,
  ollama, vllm, rag-service, qdrant-vector-store, budget-redis, agent-workspace — all `apiVersion:
  v2`, `version: 0.13.0`, with `kubeVersion: ">=1.25.0"`.
- Environment differences are value files, not chart forks: Argo CD applications reference per-cluster
  values such as `../../clusters/local/values/inference-gateway.yaml`
  ([`deploy/clusters/local/apps.yaml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/deploy/clusters/local/apps.yaml)), and the customer
  overlay supplies its own values including the GPU profiles.
- CI packages the charts and pushes them to `oci://ghcr.io/${IMAGE_REPO}/charts` on tagged and
  main-branch releases ([`.github/workflows/ci.yml`](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/.github/workflows/ci.yml)), then
  cosign-signs each OCI artifact by digest in the same workflow that signs the images.
- Verification is documented in [`docs/release-verification.md`](../release-verification.md):
  `helm pull oci://$IMAGE_REPO/charts/<chart> --version "${RELEASE#v}"`, then `cosign verify` against
  the keyless `ci.yml` tag identity. Chart OCI tags drop the leading `v` to match the chart `version`,
  while image tags keep it.

## Consequences

- One distribution mechanism covers images and charts: the same registry, the same keyless Cosign
  identity, the same verification command shape. A customer pulls a chart, verifies its signature,
  renders it with their values, and reviews before install.
- OCI distribution needs no separate chart repository or index to host and secure; it reuses GHCR,
  which already holds the signed images, SBOM, and provenance attestations.
- Helm's per-environment values keep local/customer and Ollama/vLLM/NVIDIA/AMD differences as data,
  which is what lets [0003](0003-inference-runtime-vllm-and-ollama.md) and
  [0007](0007-local-first-kind-then-customer-cluster.md) share charts across environments.
- The tag convention (charts strip the leading `v`, images keep it) is a sharp edge: the verification
  docs call it out explicitly because mixing the two breaks `helm pull`/`cosign verify`.
- Templating complexity is the cost of parameterization; it is bounded by the API and config contract
  snapshots (`platform/config-contracts`) that pin chart configuration surfaces.

## Alternatives considered

- **A classic Helm HTTP chart repository (`index.yaml` on a static host or ChartMuseum).** Works and
  is widely understood. Rejected as the default because it is a second artifact store to host, secure,
  and index, with its own signing approach (provenance files). OCI keeps charts in the registry that
  already stores and signs the images, unifying the supply-chain story.
- **Raw manifests / Kustomize instead of Helm.** Kustomize overlays could express some
  per-environment differences. Rejected because the kit's variability (model selection, GPU vendor,
  replica/parallelism tuning) is naturally values-driven, Argo CD already drives these as Helm
  sources, and Helm gives a single packaged, versioned, signable artifact to pull and verify.
- **Plain `git`-only delivery (no packaged artifact).** Argo CD can render charts directly from this
  repo, which is exactly the local path. Rejected as the customer distribution unit because a pulled,
  pinned, signed OCI artifact is verifiable out-of-band before it ever touches a cluster, which the
  release-verification flow depends on; in-repo rendering and OCI artifacts coexist rather than
  compete.
