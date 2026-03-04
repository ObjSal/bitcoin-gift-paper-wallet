#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed"
    exit 1
fi

# Check dependencies
missing=0
python3 -c "from PIL import Image" 2>/dev/null || { echo "Missing: Pillow"; missing=1; }

if [ $missing -eq 1 ]; then
    echo ""
    echo "Install missing dependencies with:"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Check template image
if [ ! -f "assets/bill_template.png" ]; then
    echo "ERROR: assets/bill_template.png not found in $(pwd)"
    echo "Make sure to run this from the bitcoin-gift-wallet directory."
    exit 1
fi

# Parse arguments
PORT="8080"
REGTEST=""
for arg in "$@"; do
    if [ "$arg" = "--regtest" ]; then
        REGTEST="--regtest"
    elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        PORT="$arg"
    fi
done

# If regtest, check Bitcoin Core is installed
if [ -n "$REGTEST" ]; then
    for bin in bitcoind bitcoin-cli; do
        if ! command -v "$bin" &>/dev/null; then
            echo "ERROR: $bin not found in PATH"
            echo "Install Bitcoin Core: brew install bitcoin (macOS)"
            exit 1
        fi
    done
    echo "Starting Bitcoin Gift Wallet (REGTEST) on http://localhost:$PORT"
else
    echo "Starting Bitcoin Gift Wallet on http://localhost:$PORT"
fi

python3 server/server.py "$PORT" $REGTEST
