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

section "AI Platform Ops Lab hello-world demo"
printf 'A tiny terminal cut for the README demo.\n'

run printf 'user: say hello world\n'
sleep 0.4
printf 'assistant: Hello world. Private AI is ready.\n'
sleep 0.4
printf 'trace: request-id=demo-hello sandbox=local-lab model=local-qwen\n'
