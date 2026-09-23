# Release Verification

Use this checklist before trusting a public release in a customer-owned cluster.

Set the release and repository once:

```bash
export RELEASE=v0.29.0
export REPOSITORY=RamazanKara/private-ai-platform-kit
export IMAGE_REPO=ghcr.io/ramazankara/private-ai-platform-kit
export RELEASE_IDENTITY="https://github.com/$REPOSITORY/.github/workflows/ci.yml@refs/tags/$RELEASE"
```

## Helm OCI Charts

Tag builds publish each chart to `oci://$IMAGE_REPO/charts`.

```bash
helm pull "oci://$IMAGE_REPO/charts/inference-gateway" --version "${RELEASE#v}"
helm pull "oci://$IMAGE_REPO/charts/rag-service" --version "${RELEASE#v}"
helm pull "oci://$IMAGE_REPO/charts/agent-workspace" --version "${RELEASE#v}"
```

Render the downloaded chart before installing:

```bash
helm template verify-inference "inference-gateway-${RELEASE#v}.tgz" \
  --values deploy/clusters/customer/values/inference-gateway.yaml >/tmp/inference.yaml
```

## Image Signatures

Release images are signed by digest with Cosign in GitHub Actions.

```bash
cosign verify "$IMAGE_REPO/inference-gateway:$RELEASE" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/rag-service:$RELEASE" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Release images are also multi-arch (`linux/amd64` and `linux/arm64`); the signature covers the manifest list, so the same `cosign verify` works on Apple Silicon and arm64 (Graviton/Ampere) clusters.

## Chart Signatures

Helm chart OCI artifacts are cosign-signed by digest in the same release workflow as the images.

```bash
cosign verify "$IMAGE_REPO/charts/inference-gateway:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/charts/rag-service:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify "$IMAGE_REPO/charts/agent-workspace:${RELEASE#v}" \
  --certificate-identity "$RELEASE_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Chart OCI tags drop the leading `v` (`${RELEASE#v}`) to match the chart `version`, while runtime image tags keep it.

## Provenance And SBOM Attestations

The main-branch image build publishes SLSA provenance and SPDX SBOM attestations.
The tag workflow promotes those same image digests and signs them for the release;
it does not rebuild them. Verify build attestations against `refs/heads/main` and
the release's exact source commit, using the authenticated GitHub CLI.

Run from a checkout containing the release tag:

```bash
SOURCE_REVISION="$(git rev-parse "${RELEASE}^{commit}")"
for service in inference-gateway rag-service; do
  gh attestation verify "oci://$IMAGE_REPO/$service:$RELEASE" \
    --repo "$REPOSITORY" \
    --signer-workflow "$REPOSITORY/.github/workflows/ci.yml" \
    --source-ref refs/heads/main \
    --source-digest "$SOURCE_REVISION"

  gh attestation verify "oci://$IMAGE_REPO/$service:$RELEASE" \
    --repo "$REPOSITORY" \
    --signer-workflow "$REPOSITORY/.github/workflows/ci.yml" \
    --source-ref refs/heads/main \
    --source-digest "$SOURCE_REVISION" \
    --predicate-type https://spdx.dev/Document/v2.3
done
```

See [GitHub artifact attestation verification](https://cli.github.com/manual/gh_attestation_verify)
for authentication and offline bundle options.

## SBOM And Scan Checksums

Download all release assets into a new directory. GitHub flattens asset paths, so
restore the chart and SDK directories recorded in the checksum manifests:

```bash
mkdir -p "release-evidence/$RELEASE/chart-packages" "release-evidence/$RELEASE/sdk-dist"
gh release download "$RELEASE" --repo "$REPOSITORY" --dir "release-evidence/$RELEASE"
(
  cd "release-evidence/$RELEASE"
  mv ./*.tgz chart-release-manifest.json chart-release-manifest.sigstore.json chart-packages/
  mv ./*.whl ./*.tar.gz sdk-dist/
  cosign verify-blob supply-chain-checksums.txt \
    --bundle supply-chain-checksums.sigstore.json \
    --certificate-identity "$RELEASE_IDENTITY" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  sha256sum --check supply-chain-checksums.txt
  sha256sum --check sdk-checksums.txt
)
```

Review the SBOMs and SARIF files before promotion. The Trivy release gate fails on
HIGH or CRITICAL image vulnerabilities. The signed checksum manifest covers the
chart packages and release image evidence; SDK checksums are published separately.

## Strict Evidence

Strict release evidence must be generated from current artifacts, not sample evidence:

```bash
make validate-full
make image-scan
make supply-chain-check
make loadtest-local
make evidence
make release-gate-strict
```

For a live customer-style validation path, run the local cluster checks and generate live evidence:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
make trace-smoke
make tenant-smoke
make agent-smoke
make evidence LIVE=1
```

Record the command output, generated evidence paths under `results/`, image digests, chart versions, and GitHub Actions run URL in the release notes.
