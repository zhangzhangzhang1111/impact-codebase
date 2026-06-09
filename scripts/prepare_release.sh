#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=${1:-$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'local')}
RELEASE_ROOT="$ROOT_DIR/release"
STAGE_DIR="$RELEASE_ROOT/impact-codebase-$VERSION"
DIST_DIR="$ROOT_DIR/dist"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
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

copy_path() {
  src=$1
  dest=$2
  if [ -d "$src" ]; then
    mkdir -p "$dest"
    (cd "$src" && tar cf - .) | (cd "$dest" && tar xf -)
  elif [ -f "$src" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
  fi
}

PLATFORM=$(platform_id)
mkdir -p "$DIST_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

copy_path "$ROOT_DIR/impact_ai" "$STAGE_DIR/impact_ai"
copy_path "$ROOT_DIR/tests" "$STAGE_DIR/tests"
copy_path "$ROOT_DIR/scripts" "$STAGE_DIR/scripts"
copy_path "$ROOT_DIR/.codebase-memory" "$STAGE_DIR/.codebase-memory"
copy_path "$ROOT_DIR/README.md" "$STAGE_DIR/README.md"
copy_path "$ROOT_DIR/pyproject.toml" "$STAGE_DIR/pyproject.toml"
copy_path "$ROOT_DIR/requirements.txt" "$STAGE_DIR/requirements.txt"
copy_path "$ROOT_DIR/.gitignore" "$STAGE_DIR/.gitignore"

if [ -n "${CODEBASE_MEMORY_MCP_BIN:-}" ]; then
  CODEBASE_BIN=$CODEBASE_MEMORY_MCP_BIN
elif command -v codebase-memory-mcp >/dev/null 2>&1; then
  CODEBASE_BIN=$(command -v codebase-memory-mcp)
else
  CODEBASE_BIN=""
fi

if [ -n "$CODEBASE_BIN" ] && [ -x "$CODEBASE_BIN" ]; then
  mkdir -p "$STAGE_DIR/vendor/codebase-memory-mcp/$PLATFORM"
  cp "$CODEBASE_BIN" "$STAGE_DIR/vendor/codebase-memory-mcp/$PLATFORM/codebase-memory-mcp"
  chmod +x "$STAGE_DIR/vendor/codebase-memory-mcp/$PLATFORM/codebase-memory-mcp"
fi

find "$STAGE_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE_DIR" -name '*.pyc' -type f -delete
chmod +x "$STAGE_DIR"/scripts/*.sh

ARCHIVE="$DIST_DIR/impact-codebase-$VERSION-$PLATFORM.tar.gz"
(cd "$RELEASE_ROOT" && tar czf "$ARCHIVE" "impact-codebase-$VERSION")
printf 'Created %s\n' "$ARCHIVE"
