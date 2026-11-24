#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$MCP_DIR/../.." && pwd)"

export BLENDER_MCP_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="${MCP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m blender.lifecycle_manager stop "$@"
