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

echo "==> Packing .mcpb bundle..."
npx @anthropic-ai/mcpb pack "$BUILD_DIR" "$OUTPUT_DIR/bitcoin-gift-wallet.mcpb"

echo ""
echo "==> Done! Bundle created at:"
echo "    $OUTPUT_DIR/bitcoin-gift-wallet.mcpb"
echo ""
echo "    Install in Claude Desktop by double-clicking the file,"
echo "    or upload to GitHub Releases for distribution."
