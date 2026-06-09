#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MIN_PYTHON="3.12"

info() {
  printf '%s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

platform_id() {
  os=$(uname -s)
  arch=$(uname -m)
  case "$os:$arch" in
    Darwin:arm64) printf 'darwin-arm64' ;;
    Darwin:x86_64) printf 'darwin-amd64' ;;
    Linux:x86_64) printf 'linux-amd64' ;;
    Linux:aarch64|Linux:arm64) printf 'linux-arm64' ;;
    *) fail "unsupported platform: $os $arch" ;;
  esac
}

need_cmd python3
need_cmd git
need_cmd tar

python3 - "$MIN_PYTHON" <<'PY'
import sys

minimum = tuple(int(part) for part in sys.argv[1].split("."))
current = sys.version_info[:2]
if current < minimum:
    raise SystemExit(
        f"Python {sys.argv[1]}+ is required, found {sys.version.split()[0]}"
    )
PY

PLATFORM=$(platform_id)
BUNDLED_BIN="$ROOT_DIR/vendor/codebase-memory-mcp/$PLATFORM/codebase-memory-mcp"
BUNDLED_ARCHIVE="$ROOT_DIR/vendor/codebase-memory-mcp/$PLATFORM/codebase-memory-mcp.tar.gz"
EXTRACTED_BIN="$ROOT_DIR/.impact-ai/bin/codebase-memory-mcp/$PLATFORM/codebase-memory-mcp"

extract_bundled_binary() {
  archive=$1
  target=$2
  target_dir=$(dirname "$target")
  mkdir -p "$target_dir"
  tar xzf "$archive" -C "$target_dir" codebase-memory-mcp
  chmod +x "$target"
}

if [ -n "${CODEBASE_MEMORY_MCP_BIN:-}" ]; then
  CODEBASE_BIN=$CODEBASE_MEMORY_MCP_BIN
elif [ -x "$BUNDLED_BIN" ]; then
  CODEBASE_BIN=$BUNDLED_BIN
elif [ -f "$BUNDLED_ARCHIVE" ]; then
  if [ ! -x "$EXTRACTED_BIN" ] || [ "$BUNDLED_ARCHIVE" -nt "$EXTRACTED_BIN" ]; then
    extract_bundled_binary "$BUNDLED_ARCHIVE" "$EXTRACTED_BIN"
  fi
  CODEBASE_BIN=$EXTRACTED_BIN
elif command -v codebase-memory-mcp >/dev/null 2>&1; then
  CODEBASE_BIN=$(command -v codebase-memory-mcp)
else
  fail "codebase-memory-mcp not found. Run scripts/install_codebase_memory.sh or place an archive at $BUNDLED_ARCHIVE"
fi

mkdir -p "$ROOT_DIR/.impact-ai/repos" "$ROOT_DIR/.impact-ai/codebase-memory-cache" "$ROOT_DIR/profiles"

info "ROOT_DIR=$ROOT_DIR"
info "PYTHON=$(python3 --version 2>&1)"
info "GIT=$(git --version)"
info "PLATFORM=$PLATFORM"
info "CODEBASE_MEMORY_MCP_BIN=$CODEBASE_BIN"
