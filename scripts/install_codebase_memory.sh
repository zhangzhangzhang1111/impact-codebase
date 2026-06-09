#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=${CODEBASE_MEMORY_VERSION:-latest}
INSTALL_DIR=${CODEBASE_MEMORY_INSTALL_DIR:-"$ROOT_DIR/vendor/codebase-memory-mcp"}

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

need_cmd curl
need_cmd chmod
need_cmd tar

PLATFORM=$(platform_id)
TARGET_DIR="$INSTALL_DIR/$PLATFORM"
TARGET_BIN="$TARGET_DIR/codebase-memory-mcp"
TARGET_ARCHIVE="$TARGET_DIR/codebase-memory-mcp.tar.gz"

case "$PLATFORM" in
  linux-amd64) ASSET="codebase-memory-mcp-linux-amd64.tar.gz" ;;
  linux-arm64) ASSET="codebase-memory-mcp-linux-arm64.tar.gz" ;;
  darwin-amd64) ASSET="codebase-memory-mcp-darwin-amd64.tar.gz" ;;
  darwin-arm64) ASSET="codebase-memory-mcp-darwin-arm64.tar.gz" ;;
  *) fail "unsupported platform: $PLATFORM" ;;
esac

if [ "$VERSION" = "latest" ]; then
  URL="https://github.com/DeusData/codebase-memory-mcp/releases/latest/download/$ASSET"
else
  URL="https://github.com/DeusData/codebase-memory-mcp/releases/download/$VERSION/$ASSET"
fi

mkdir -p "$TARGET_DIR"
printf 'Downloading %s\n' "$URL"
curl -fL "$URL" -o "$TARGET_ARCHIVE"
tar xzf "$TARGET_ARCHIVE" -C "$TARGET_DIR" codebase-memory-mcp
chmod +x "$TARGET_BIN"
"$TARGET_BIN" --version
printf 'Installed archive to %s\n' "$TARGET_ARCHIVE"
printf 'Extracted binary to %s\n' "$TARGET_BIN"
