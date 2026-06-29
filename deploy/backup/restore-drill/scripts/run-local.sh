#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
restore-drill run --config deploy/backup/restore-drill/drills/local-redis-aof.yaml --runtime docker --format json

