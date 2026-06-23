#!/usr/bin/env bash
#
# Build the self-hosted Tailwind stylesheet with the standalone CLI (no Node/npm).
# Downloads a pinned Tailwind v3 CLI on first run (cached under .tailwind/, gitignored),
# then compiles tailwind/input.css -> static/css/app.css (minified, purged).
#
# Used by scripts/update.sh before collectstatic. Safe to run by hand any time.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP"

TW_VERSION="v3.4.19"          # pinned; matches the class semantics of the old CDN
BIN_DIR="$APP/.tailwind"
BIN="$BIN_DIR/tailwindcss"

os="$(uname -s)"; arch="$(uname -m)"
case "$os" in
  Darwin) p_os="macos" ;;
  Linux)  p_os="linux" ;;
  *) echo "build_css: unsupported OS '$os'" >&2; exit 1 ;;
esac
case "$arch" in
  x86_64|amd64)  p_arch="x64" ;;
  arm64|aarch64) p_arch="arm64" ;;
  *) echo "build_css: unsupported arch '$arch'" >&2; exit 1 ;;
esac
asset="tailwindcss-${p_os}-${p_arch}"

mkdir -p "$BIN_DIR"
if [ ! -x "$BIN" ] || [ "$(cat "$BIN_DIR/VERSION" 2>/dev/null || true)" != "$TW_VERSION" ]; then
  url="https://github.com/tailwindlabs/tailwindcss/releases/download/${TW_VERSION}/${asset}"
  echo "build_css: downloading Tailwind CLI ${TW_VERSION} (${asset})..."
  curl -fsSL -o "$BIN" "$url" || { echo "build_css: download failed from $url" >&2; exit 1; }
  chmod +x "$BIN"
  echo "$TW_VERSION" > "$BIN_DIR/VERSION"
fi

mkdir -p "$APP/static/css"
"$BIN" --config "$APP/tailwind.config.js" \
       --input "$APP/tailwind/input.css" \
       --output "$APP/static/css/app.css" \
       --minify
echo "build_css: wrote static/css/app.css"
