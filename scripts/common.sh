#!/usr/bin/env bash
set -euo pipefail

repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

log() {
  printf '[ai-platform-ops-lab] %s\n' "$*"
}

die() {
  printf '[ai-platform-ops-lab] ERROR: %s\n' "$*" >&2
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
