#!/bin/bash
# Build .mcpb bundle for Claude Desktop distribution.
# Usage: cd mcp && ./build.sh
#
# Output: dist/bitcoin-gift-wallet.mcpb

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$SCRIPT_DIR/dist/bundle"
OUTPUT_DIR="$SCRIPT_DIR/dist"

# Read version from manifest.json (single source of truth)
VERSION=$(node -e "console.log(require('$SCRIPT_DIR/manifest.json').version)")
echo "==> Building bitcoin-gift-wallet v${VERSION}..."

# Sync version to package.json
node -e "
const fs = require('fs');
const pkg = JSON.parse(fs.readFileSync('$SCRIPT_DIR/package.json', 'utf8'));
pkg.version = '$VERSION';
fs.writeFileSync('$SCRIPT_DIR/package.json', JSON.stringify(pkg, null, 2) + '\n');
"

echo "==> Cleaning previous build..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server" "$BUILD_DIR/js" "$BUILD_DIR/assets"

echo "==> Copying manifest and package files..."
cp "$SCRIPT_DIR/manifest.json" "$BUILD_DIR/"
cp "$SCRIPT_DIR/package.json" "$BUILD_DIR/"
cp "$SCRIPT_DIR/package-lock.json" "$BUILD_DIR/" 2>/dev/null || true

echo "==> Copying server entry point..."
cp "$SCRIPT_DIR/mcp_server.js" "$BUILD_DIR/server/index.js"

echo "==> Copying JS modules..."
cp "$PROJECT_ROOT/js/bitcoin_crypto.js" "$BUILD_DIR/js/"
cp "$PROJECT_ROOT/js/qr_generator.js" "$BUILD_DIR/js/"
cp "$PROJECT_ROOT/js/bill_generator.js" "$BUILD_DIR/js/"

echo "==> Copying assets..."
cp "$PROJECT_ROOT/assets/bill_template.png" "$BUILD_DIR/assets/"

echo "==> Installing production dependencies..."
cd "$BUILD_DIR"
npm install --production --ignore-scripts=false 2>&1 | tail -3

echo "==> Installing cross-platform native binaries..."
# npm skips optional platform-specific packages that don't match the build host.
# Download and extract each platform's tarball directly so the .mcpb bundle
# works on macOS (ARM & Intel), Windows (x64), and Linux (x64 & ARM64).
CANVAS_VERSION=$(node -e "console.log(require('./node_modules/@napi-rs/canvas/package.json').version)")
PLATFORMS=(
  "canvas-darwin-arm64"
  "canvas-darwin-x64"
  "canvas-win32-x64-msvc"
  "canvas-linux-x64-gnu"
  "canvas-linux-arm64-gnu"
)
for PLAT in "${PLATFORMS[@]}"; do
  PKG="@napi-rs/$PLAT"
  TARGET_DIR="node_modules/@napi-rs/$PLAT"
  if [ -d "$TARGET_DIR" ]; then
    echo "    $PKG already installed"
    continue
  fi
  echo "    Downloading $PKG@$CANVAS_VERSION..."
  mkdir -p "$TARGET_DIR"
  npm pack "$PKG@$CANVAS_VERSION" --pack-destination /tmp 2>/dev/null
  tar -xzf "/tmp/napi-rs-$PLAT-$CANVAS_VERSION.tgz" -C "$TARGET_DIR" --strip-components=1
  rm -f "/tmp/napi-rs-$PLAT-$CANVAS_VERSION.tgz"
done

echo "==> Packing .mcpb bundle..."
MCPB_FILE="bitcoin-gift-wallet-v${VERSION}.mcpb"
npx @anthropic-ai/mcpb pack "$BUILD_DIR" "$OUTPUT_DIR/$MCPB_FILE"

echo ""
echo "==> Done! Bundle created at:"
echo "    $OUTPUT_DIR/$MCPB_FILE"
echo ""
echo "    Install in Claude Desktop by double-clicking the file,"
echo "    or upload to GitHub Releases for distribution."
