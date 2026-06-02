#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

BIN_DIR="${TOOLCHAIN_BIN_DIR:-$ROOT/.tools/bin}"
FORCE=0
DRY_RUN=0

KUBECONFORM_VERSION="${KUBECONFORM_VERSION:-v0.7.0}"
KYVERNO_VERSION="${KYVERNO_VERSION:-v1.18.1}"
RESTORE_DRILL_VERSION="${RESTORE_DRILL_VERSION:-v1.0.1}"
K6_VERSION="${K6_VERSION:-v2.0.0}"
SYFT_VERSION="${SYFT_VERSION:-v1.44.0}"
ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.3}"
COSIGN_VERSION="${COSIGN_VERSION:-v3.0.6}"
TRIVY_VERSION="${TRIVY_VERSION:-v0.70.0}"

INSTALL_TOOLS="${INSTALL_TOOLS:-kubeconform kyverno restore-drill k6 syft argocd cosign trivy}"
CURL_ARGS=(-fsSL --retry 5 --retry-all-errors --retry-delay 2)

usage() {
  cat <<USAGE
Usage: scripts/install-validation-tools.sh [--bin-dir PATH] [--force] [--dry-run]

Installs strict validation tools into a local bin directory. Versions can be
overridden with KUBECONFORM_VERSION, KYVERNO_VERSION, RESTORE_DRILL_VERSION,
K6_VERSION, SYFT_VERSION, ARGOCD_VERSION, COSIGN_VERSION, and TRIVY_VERSION.

Set INSTALL_TOOLS to a space-separated subset when only specific tools are needed.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bin-dir)
      BIN_DIR="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_cmd curl "curl is required to download validation tool releases."
require_cmd python3 "Python 3 is required to parse GitHub release metadata."
require_cmd tar "tar is required to unpack validation tool archives."
require_cmd sha256sum "sha256sum is required to verify GitHub release asset digests."
require_cmd install "install is required to place validation tools in the bin directory."

normalize_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64' ;;
    aarch64|arm64) printf 'arm64' ;;
    *) die "unsupported architecture: $(uname -m)" ;;
  esac
}

assert_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    die "this installer currently supports Linux/WSL/CI. Use the runbook install hints for other operating systems."
  fi
}

has_tool() {
  local name="$1"
  [[ -x "$BIN_DIR/$name" ]] || has_cmd "$name"
}

should_install() {
  local name="$1"
  if [[ "$FORCE" == "1" ]]; then
    return 0
  fi
  ! has_tool "$name"
}

python_release_asset() {
  local json_path="$1"
  local asset_name="$2"
  python3 - "$json_path" "$asset_name" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
asset_name = sys.argv[2]
for asset in payload.get("assets", []):
    if asset.get("name") == asset_name:
        print(asset.get("url", ""))
        print(asset.get("browser_download_url", ""))
        print(asset.get("digest", ""))
        raise SystemExit(0)
raise SystemExit(f"asset {asset_name!r} not found")
PY
}

download_github_asset() {
  local owner_repo="$1"
  local tag="$2"
  local asset_name="$3"
  local output="$4"
  local release_json
  release_json="$(mktemp)"

  log "resolving ${owner_repo} ${tag} asset ${asset_name}"
  curl "${CURL_ARGS[@]}" "https://api.github.com/repos/${owner_repo}/releases/tags/${tag}" -o "$release_json"
  mapfile -t asset < <(python_release_asset "$release_json" "$asset_name")
  rm -f "$release_json"
  local api_url="${asset[0]:-}"
  local browser_url="${asset[1]:-}"
  local digest="${asset[2]:-}"
  [[ -n "$browser_url" ]] || die "could not resolve download URL for ${owner_repo} ${asset_name}"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry-run download ${browser_url}"
    return 0
  fi

  if [[ -n "${GITHUB_TOKEN:-}" && -n "$api_url" ]]; then
    if ! curl "${CURL_ARGS[@]}" \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "Accept: application/octet-stream" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$api_url" -o "$output"; then
      log "warning: GitHub API asset download failed for ${owner_repo} ${asset_name}; falling back to browser download URL"
      curl "${CURL_ARGS[@]}" "$browser_url" -o "$output"
    fi
  else
    curl "${CURL_ARGS[@]}" "$browser_url" -o "$output"
  fi
  if [[ "$digest" == sha256:* ]]; then
    local expected="${digest#sha256:}"
    local actual
    actual="$(sha256sum "$output" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || die "sha256 mismatch for ${asset_name}: expected ${expected}, got ${actual}"
  else
    log "warning: no release digest published for ${owner_repo} ${asset_name}"
  fi
}

install_tar_binary() {
  local owner_repo="$1"
  local tag="$2"
  local asset_name="$3"
  local binary="$4"
  local archive
  local unpack
  archive="$(mktemp)"
  unpack="$(mktemp -d)"

  download_github_asset "$owner_repo" "$tag" "$asset_name" "$archive"
  if [[ "$DRY_RUN" == "1" ]]; then
    rm -f "$archive"
    rm -rf "$unpack"
    return 0
  fi

  tar -xzf "$archive" -C "$unpack"
  local source_path
  source_path="$(find "$unpack" -type f -name "$binary" | head -1)"
  [[ -n "$source_path" ]] || die "binary ${binary} not found in ${asset_name}"
  install -m 0755 "$source_path" "$BIN_DIR/$binary"
  rm -f "$archive"
  rm -rf "$unpack"
  log "installed ${binary} to ${BIN_DIR}/${binary}"
}

install_direct_binary() {
  local owner_repo="$1"
  local tag="$2"
  local asset_name="$3"
  local binary="$4"
  local target="$BIN_DIR/$binary"
  download_github_asset "$owner_repo" "$tag" "$asset_name" "$target"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  chmod 0755 "$target"
  log "installed ${binary} to ${target}"
}

install_go_binary() {
  local module="$1"
  local version="$2"
  local binary="$3"
  require_cmd go "Go is required to install ${binary} from ${module}."
  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry-run go install ${module}@${version}"
    return 0
  fi
  GOBIN="$BIN_DIR" go install "${module}@${version}"
  [[ -x "$BIN_DIR/$binary" ]] || die "go install did not produce ${BIN_DIR}/${binary}"
  log "installed ${binary} to ${BIN_DIR}/${binary}"
}

install_tool() {
  local tool="$1"
  local arch
  arch="$(normalize_arch)"
  case "$tool" in
    kubeconform)
      should_install kubeconform || { log "skip kubeconform: already available"; return 0; }
      install_tar_binary yannh/kubeconform "$KUBECONFORM_VERSION" "kubeconform-linux-${arch}.tar.gz" kubeconform
      ;;
    kyverno)
      should_install kyverno || { log "skip kyverno: already available"; return 0; }
      local kyverno_arch="$arch"
      [[ "$arch" == "amd64" ]] && kyverno_arch="x86_64"
      install_tar_binary kyverno/kyverno "$KYVERNO_VERSION" "kyverno-cli_${KYVERNO_VERSION}_linux_${kyverno_arch}.tar.gz" kyverno
      ;;
    restore-drill)
      should_install restore-drill || { log "skip restore-drill: already available"; return 0; }
      install_go_binary github.com/RamazanKara/restore-drill/cmd/restore-drill "$RESTORE_DRILL_VERSION" restore-drill
      ;;
    k6)
      should_install k6 || { log "skip k6: already available"; return 0; }
      install_tar_binary grafana/k6 "$K6_VERSION" "k6-${K6_VERSION}-linux-${arch}.tar.gz" k6
      ;;
    syft)
      should_install syft || { log "skip syft: already available"; return 0; }
      local syft_version="${SYFT_VERSION#v}"
      install_tar_binary anchore/syft "$SYFT_VERSION" "syft_${syft_version}_linux_${arch}.tar.gz" syft
      ;;
    argocd)
      should_install argocd || { log "skip argocd: already available"; return 0; }
      install_direct_binary argoproj/argo-cd "$ARGOCD_VERSION" "argocd-linux-${arch}" argocd
      ;;
    cosign)
      should_install cosign || { log "skip cosign: already available"; return 0; }
      install_direct_binary sigstore/cosign "$COSIGN_VERSION" "cosign-linux-${arch}" cosign
      ;;
    trivy)
      should_install trivy || { log "skip trivy: already available"; return 0; }
      local trivy_version="${TRIVY_VERSION#v}"
      local trivy_arch="64bit"
      [[ "$arch" == "arm64" ]] && trivy_arch="ARM64"
      install_tar_binary aquasecurity/trivy "$TRIVY_VERSION" "trivy_${trivy_version}_Linux-${trivy_arch}.tar.gz" trivy
      ;;
    *)
      die "unsupported validation tool: ${tool}"
      ;;
  esac
}

assert_linux
if [[ "$DRY_RUN" == "0" ]]; then
  mkdir -p "$BIN_DIR"
fi

log "installing validation tools to ${BIN_DIR}"
for tool in $INSTALL_TOOLS; do
  install_tool "$tool"
done

if [[ "$DRY_RUN" == "0" ]]; then
  log "validation tools installed. Repo scripts automatically add this directory to PATH: ${BIN_DIR}"
fi
