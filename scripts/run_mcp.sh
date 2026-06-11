#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BOOTSTRAP_OUTPUT=$("$ROOT_DIR/scripts/bootstrap.sh")
printf '%s\n' "$BOOTSTRAP_OUTPUT" >&2

CODEBASE_BIN=$(printf '%s\n' "$BOOTSTRAP_OUTPUT" | awk -F= '/^CODEBASE_MEMORY_MCP_BIN=/{print $2; exit}')
export CODEBASE_MEMORY_MCP_BIN=${CODEBASE_MEMORY_MCP_BIN:-$CODEBASE_BIN}
export IMPACT_AI_WORKSPACE_ROOT=${IMPACT_AI_WORKSPACE_ROOT:-"$ROOT_DIR/.impact-ai/repos"}
export IMPACT_AI_HISTORY_PATH=${IMPACT_AI_HISTORY_PATH:-"$ROOT_DIR/.impact-ai/history.json"}
export IMPACT_AI_MODEL_CONFIG_PATH=${IMPACT_AI_MODEL_CONFIG_PATH:-"$ROOT_DIR/.impact-ai/model_config.json"}
export IMPACT_AI_REVIEW_STANDARDS_PATH=${IMPACT_AI_REVIEW_STANDARDS_PATH:-"$ROOT_DIR/.impact-ai/review_standards.json"}
export IMPACT_AI_PROFILE_ROOT=${IMPACT_AI_PROFILE_ROOT:-"$ROOT_DIR/profiles"}

if python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info < (3, 7) else 1)
PY
then
  for wheel in "$ROOT_DIR"/vendor/python/*.whl; do
    [ -f "$wheel" ] || continue
    PYTHONPATH="$wheel${PYTHONPATH:+:$PYTHONPATH}"
  done
  export PYTHONPATH=${PYTHONPATH:-}
fi

cd "$ROOT_DIR"
exec python3 -m impact_ai.mcp_server
