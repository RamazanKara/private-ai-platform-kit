#!/usr/bin/env bash
# Canonical repo directory names for shell consumers. Single source of truth:
# scripts/paths.py. Source this file, then reference directories as
# "$REPO_ROOT/$CHARTS_DIR", "$REPO_ROOT/$SERVICES_DIR", etc.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/_paths.sh"
#
# REPO_ROOT is set here; every declared directory is exported as NAME_DIR
# (config-contracts -> CONFIG_CONTRACTS_DIR) from paths.py --dump-sh.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "${REPO_ROOT}/scripts/paths.py" --dump-sh)"
