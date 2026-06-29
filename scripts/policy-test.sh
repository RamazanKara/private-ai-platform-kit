#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
require_cmd kyverno "Install the Kyverno CLI to run policy tests."

cd "$ROOT"
kyverno test deploy/policies/kyverno/tests

