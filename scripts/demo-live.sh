#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

section() {
  printf '\n==> %s\n' "$1"
}

run() {
  printf '\n$ %s\n' "$*"
  "$@"
}

section "Private AI Platform Kit hello-world demo"
printf 'A tiny terminal cut for the README demo.\n'

run printf 'user: say hello world\n'
sleep 0.4
printf 'assistant: Hello world from Private AI Platform Kit.\n'
sleep 0.4
printf 'trace: project=private-ai-platform-kit sandbox=local-lab model=local-qwen\n'
