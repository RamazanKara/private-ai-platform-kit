#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

TOOLCHAIN_BIN_DIR="${TOOLCHAIN_BIN_DIR:-$(repo_root)/.tools/bin}"
if [[ -d "$TOOLCHAIN_BIN_DIR" && ":$PATH:" != *":$TOOLCHAIN_BIN_DIR:"* ]]; then
  export PATH="$TOOLCHAIN_BIN_DIR:$PATH"
fi

log() {
  printf '[private-ai-platform-kit] %s\n' "$*"
}

die() {
  printf '[private-ai-platform-kit] ERROR: %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_cmd() {
  local name="$1"
  local hint="${2:-Install ${name} and retry.}"
  if ! has_cmd "$name"; then
    die "missing required tool '${name}'. ${hint}"
  fi
}

require_optional_or_full() {
  local name="$1"
  local hint="${2:-Install ${name} for full validation.}"
  if has_cmd "$name"; then
    return 0
  fi
  if [[ "${REQUIRE_FULL_TOOLCHAIN:-0}" == "1" ]]; then
    die "missing production validation tool '${name}'. ${hint}"
  fi
  log "skip ${name}: ${hint}"
  return 1
}

validate_k8s_name() {
  local value="$1"
  local label="${2:-value}"
  if [[ ! "$value" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ || "${#value}" -gt 63 ]]; then
    die "${label} must be a Kubernetes DNS label: lowercase letters, numbers, hyphens, max 63 chars"
  fi
}

# Create or refresh a service's .venv from its hashed dev lock (src/<service>/requirements-dev.lock).
# The venv is rebuilt from scratch only when the lock changes, so packages a lock drops do
# not linger, and repeated test/validate runs skip a redundant pip install.
ensure_service_venv() {
  local dir="$1"
  local lock="${dir}/requirements-dev.lock"
  local stamp="${dir}/.venv/.lock-sha256"
  local digest
  digest="$(sha256sum "$lock" | cut -d' ' -f1)"
  if [[ -x "${dir}/.venv/bin/python" && "$(cat "$stamp" 2>/dev/null)" == "$digest" ]]; then
    return 0
  fi
  log "installing $(basename "$dir") dependencies from requirements-dev.lock"
  python3 -m venv --clear "${dir}/.venv"
  "${dir}/.venv/bin/python" -m pip install --quiet --require-hashes -r "$lock"
  echo "$digest" >"$stamp"
}
