# ₿itcoin Gift Paper Wallet

A self-contained Bitcoin paper wallet generator that creates gift-ready Bitcoin bills as printable images. Generate a wallet, print the bill, load it with Bitcoin, and give it as a gift.

**Zero external dependencies.** All cryptography — secp256k1, SHA-256, RIPEMD-160, bech32/bech32m, ECDSA, Schnorr — is hand-rolled in pure JavaScript and runs entirely in your browser. No npm, no CDN, no third-party libraries. Works offline.

> **Live site:** Deploy to GitHub Pages or run locally — no server required for mainnet and testnet4.

## What It Does

1. **Generate** a Bitcoin paper wallet (SegWit or Taproot) with a printable bill image
2. **Sweep** funds from the paper wallet to any Bitcoin address
3. **Recover** funds using an optional backup key (Taproot script-path spend)

Each bill includes the public address (as text + QR code) on the front and the private key (as text + QR code) on the back, overlaid on a bill template.

## Who It's For

- **Gift givers** who want to give Bitcoin as a physical, tangible present
- **Bitcoin educators** teaching people about keys, addresses, and self-custody
- **Developers** who want a reference implementation of Bitcoin cryptography in pure JS/Python
- **Claude users** who want to generate wallets directly from Claude Desktop or Claude Code via MCP
- **Anyone** who needs a simple, auditable paper wallet generator with no dependencies

## Quick Start

### Option 1: GitHub Pages (easiest)

Push this repo to GitHub and enable GitHub Pages. Visit `https://ObjSal.github.io/bitcoin-gift-paper-wallet/`. Works immediately — mainnet and testnet4 supported.

### Option 2: Claude Desktop / Claude Code (via MCP)

Download `bitcoin-gift-wallet.mcpb` from the [latest GitHub release](https://github.com/ObjSal/bitcoin-gift-wallet/releases) and double-click to install. Then ask Claude:

- *"Generate a bitcoin paper wallet"*
- *"Create a taproot wallet with a backup key"*
- *"Make me 3 segwit gift wallets for holiday gifts"*

See the [MCP Server](#mcp-server-claude-desktop--claude-code) section for manual setup and more examples.

### Option 3: Run Locally (no dependencies)

```bash
# Clone the repo
git clone https://github.com/ObjSal/bitcoin-gift-paper-wallet.git
cd bitcoin-gift-wallet

# Serve with any static file server
python3 -m http.server 8080

# Open http://localhost:8080 in your browser
```

That's it. All crypto, QR generation, and bill rendering happen client-side in JavaScript.

### Option 4: Run with Python Server (for regtest testing)

```bash
# Install dependencies
pip install -r requirements.txt

# Start in standard mode (mainnet/testnet4 via mempool.space)
python3 server/server.py 8080

# Or start in regtest mode (requires Bitcoin Core: brew install bitcoin)
python3 server/server.py 8080 --regtest
```

Regtest mode starts a local `bitcoind`, creates a wallet, mines 101 blocks, and enables the faucet page. Transactions confirm instantly (1 block mined after each broadcast).

## How It Works

### Architecture

```
GitHub Pages (static):     All JS crypto → mempool.space API
Local Python server:       All JS crypto → regtest via server API (bitcoin-cli bridge)
```

All pages auto-detect the Python server on load via a `/api/health` probe. If no server is found, the regtest option and faucet link are hidden.

### Cryptography

Every cryptographic operation runs in your browser:

- **Key generation**: `crypto.getRandomValues()` (OS CSPRNG) with rejection sampling for valid secp256k1 range
- **Address derivation**: Pure JS secp256k1 point multiplication, bech32/bech32m encoding (BIP173/BIP350)
- **Signing**: ECDSA with RFC 6979 deterministic nonces (SegWit), Schnorr with BIP340 nonce derivation (Taproot)
- **Transaction construction**: Full SegWit v0 and Taproot v1 transaction building, sighash computation (BIP143/BIP341)

The Python implementations (`server/bitcoin_crypto.py`, `server/qr_generator.py`) serve as reference code and are cross-validated against the JavaScript with 10 fixed test vectors.

### Address Types

| Type | Prefix | Description |
|------|--------|-------------|
| **SegWit** (P2WPKH) | `bc1q...` | Standard native SegWit. Widely supported by all wallets. |
| **Taproot** (P2TR) | `bc1p...` | Modern Taproot. Supports optional backup key for recovery. |

### Taproot Backup Key

When generating a Taproot address, you can optionally enable a **backup recovery key**. This creates a script tree with a single leaf containing `<backup_pubkey> OP_CHECKSIG`, allowing the giver to recover funds if the recipient loses the bill.

- **Bill shows**: Tweaked private key (for key-path spending via sweep page)
- **Giver keeps**: Backup key + internal pubkey (for script-path spending via recover page)

### Networks

| Network | SegWit | Taproot | UTXO Source |
|---------|--------|---------|-------------|
| Mainnet | `bc1q...` | `bc1p...` | mempool.space API |
| Testnet4 | `tb1q...` | `tb1p...` | mempool.space/testnet4 API |
| Regtest | `bcrt1q...` | `bcrt1p...` | Local bitcoin-cli (requires server) |

## Testing

The project has comprehensive test coverage across JavaScript, Python, and end-to-end browser tests:

```bash
# JavaScript crypto tests (120 tests, runs in browser)
open tests/test_bitcoin_crypto.html

# Python crypto unit tests (54 tests)
python3 tests/test_bitcoin.py

# Regtest on-chain spending tests (9 tests, requires Bitcoin Core)
python3 tests/test_regtest_spending.py

# End-to-end API tests (8 tests, requires Bitcoin Core)
python3 tests/test_e2e_api.py

# Playwright browser UI test (requires Bitcoin Core + Playwright)
pip install playwright && playwright install chromium
python3 tests/test_ui_playwright.py

# Testnet4 browser UI test (requires pre-funded testnet4 address)
python3 tests/test_ui_playwright_testnet4.py --wif "cXXX..." --address "tb1q..."

# Node.js MCP server tests (16 tests, requires npm install in mcp/)
node tests/test_mcp_server.js

# Python MCP server tests (16 tests, requires mcp + Pillow pip packages)
python3 tests/test_mcp_server.py

# MCP E2E tests (5 tests, requires Bitcoin Core + npm install in mcp/)
node tests/test_mcp_e2e.js
```

**Current state: 120/120 JS + 54/54 Python + 9/9 regtest + 8/8 E2E API + 1/1 Playwright + 16/16 Node.js MCP + 16/16 Python MCP + 5/5 MCP E2E = all tests passing.**

## Security

A full security assessment is available in [`docs/security_assessment.md`](docs/security_assessment.md). Key highlights:

**Strengths:**
- Zero external dependencies eliminates supply chain risk
- CSPRNG entropy via `crypto.getRandomValues()` (same OS kernel source as Bitcoin Core)
- RFC 6979 deterministic ECDSA nonces prevent catastrophic nonce reuse
- BIP340 Schnorr nonce derivation with auxiliary randomness
- All signing happens client-side in the browser — private keys never leave your machine
- Cross-validated against Python reference implementation with 10 fixed test vectors

**Recommendations for maximum security:**
- Generate wallets on an **offline/air-gapped computer**
- Use an **incognito/private browsing window** (disables most extensions)
- Print using a **direct USB-connected printer** (not a network printer)
- Verify the source code before use — it's fully auditable

## MCP Server (Claude Desktop / Claude Code)

Generate paper wallets directly from Claude using [MCP](https://modelcontextprotocol.io/) — no browser needed. Bill images render **inline in Claude Desktop conversations** via MCP Apps. Two implementations are provided (Node.js and Python), both reusing the project's existing crypto modules.

### Option 1: Download the .mcpb bundle (easiest)

Download `bitcoin-gift-wallet.mcpb` from the [latest GitHub release](https://github.com/ObjSal/bitcoin-gift-wallet/releases) and double-click to install in Claude Desktop. No extra software required — Claude Desktop includes its own Node.js runtime.

> **Note:** The .mcpb bundle includes pre-compiled native binaries for macOS (ARM & Intel), Windows (x64), and Linux (x64 & ARM64). It has been tested on macOS — if you encounter issues on Windows or Linux, please [open an issue](https://github.com/ObjSal/bitcoin-gift-wallet/issues) so we can fix it.

### Option 2: Node.js manual setup (from source)

```bash
cd mcp && npm install
```

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bitcoin-gift-wallet": {
      "command": "node",
      "args": ["/path/to/bitcoin-gift-wallet/mcp/mcp_server.js"]
    }
  }
}
```

### Option 3: Python manual setup (from source)

```bash
pip install mcp Pillow
```

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "bitcoin-gift-wallet": {
      "command": "python3",
      "args": ["/path/to/bitcoin-gift-wallet/mcp/mcp_server.py"]
    }
  }
}
```

Both options expose the same tools. Restart Claude Desktop after editing the config, then ask Claude: *"Generate a taproot paper wallet"*.

See [`docs/mcp_setup.md`](docs/mcp_setup.md) for Claude Code configuration and more details.

### MCP Tools

| Tool | Description |
|------|-------------|
| `generate_segwit_wallet` | Generate a SegWit (bc1q...) wallet + bill image |
| `generate_taproot_wallet` | Generate a Taproot (bc1p...) wallet, with optional backup key |
| `check_balance` | Check the Bitcoin balance of any address |
| `check_all_balances` | Check balances of all generated wallets at once |
| `sweep_wallet` | Sweep all funds from a paper wallet to a destination address (with optional tip) |
| `recover_wallet` | Recover funds using the backup key (Taproot script-path spend, with optional tip) |
| `open_wallet_app` | Open the web app (generator, sweep, or recover page) |
| `list_generated_wallets` | List previously generated bill images |
| `open_wallet_bill` | Open a specific bill by filename |

### Testing on Regtest

You can use the MCP tools with a local regtest network for testing. This lets you generate wallets, fund them with test Bitcoin, and sweep/recover — all without real funds.

The `.mcpb` bundle ships with `REGTEST_SERVER_URL=http://127.0.0.1:8080` pre-configured, so you only need to start the regtest server:

1. **Install Bitcoin Core** and start the regtest server:

```bash
brew install bitcoin                         # macOS
python3 server/server.py 8080 --regtest      # starts bitcoind + server
```

2. **Restart Claude Desktop**, then ask Claude:

- *"Generate a regtest taproot wallet with backup key"*
- *"Fund the wallet with 1 BTC"* (uses the regtest faucet)
- *"How much bitcoin do I have?"*
- *"Sweep my paper wallet to bcrt1q..."*

Transactions confirm instantly on regtest (1 block mined after each broadcast).

**Overriding the regtest URL:** If your server runs on a different port, add an `env` block to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bitcoin-gift-wallet": {
      "env": {
        "REGTEST_SERVER_URL": "http://127.0.0.1:9090"
      }
    }
  }
}
```

The env var only affects regtest operations — mainnet and testnet4 always use mempool.space directly.

### Example Prompts

Once the MCP server is installed, try asking Claude:

- *"Generate a bitcoin paper wallet"*
- *"Create a taproot wallet with a backup key"*
- *"Make me a segwit gift wallet on testnet"*
- *"Generate 3 taproot paper wallets for holiday gifts"*
- *"How much bitcoin do I have?"*
- *"Check the balance of bc1q..."*
- *"Sweep my paper wallet to bc1q..."*
- *"Show me the wallets I've already generated"*
- *"Open the sweep page so I can send funds from a paper wallet"*

## Project Structure

### Pages

| Page | Description |
|------|-------------|
| **Generator** (`index.html`) | Create wallets and print bills — SegWit (P2WPKH) or Taproot (P2TR) |
| **Sweep** (`sweep.html`) | Recipient enters the private key from the bill to send funds to their own wallet |
| **Recover** (`recover.html`) | Giver uses their backup key to recover funds via script-path spend |
| **Faucet** (`faucet.html`) | Fund addresses with test Bitcoin on a local regtest network (requires server) |
| **Donate** (`donate.html`) | Support the project with a Bitcoin donation |

### Directory Layout

```
index.html                          # Generator page
sweep.html                          # Recipient sweep page
recover.html                        # Backup recovery page
faucet.html                         # Regtest faucet page
donate.html                         # Donation page
js/
    bitcoin_crypto.js               # Pure JS Bitcoin cryptography (zero dependencies)
    qr_generator.js                 # Pure JS QR code generation
    bill_generator.js               # HTML5 Canvas bill rendering
assets/
    bill_template.png               # Bill background image (1843x784)
    donate_qr.png                   # Donation QR code
server/
    server.py                       # HTTP server (regtest mode only)
    bitcoin_crypto.py               # Python reference implementation
    qr_generator.py                 # Python QR code generation
    bill_generator.py               # Python Pillow bill generation
mcp/
    mcp_server.js                   # Node.js MCP server (recommended)
    mcp_server.py                   # Python MCP server
    manifest.json                   # MCPB bundle manifest
    package.json                    # Node.js dependencies
    build.sh                        # Build .mcpb bundle for distribution
tests/
    test_bitcoin_crypto.html        # In-browser JS test suite (120 tests)
    test_bitcoin.py                 # Python unit tests (54 tests)
    test_regtest_spending.py        # On-chain spending tests (9 tests)
    test_e2e_api.py                 # End-to-end API tests (8 tests)
    test_ui_playwright.py           # Playwright browser UI test
    test_ui_playwright_testnet4.py  # Testnet4 Playwright test
    test_mcp_e2e.js                 # MCP E2E tests with regtest (5 tests)
docs/
    security_assessment.md          # Full security assessment
    mcp_setup.md                    # MCP server setup guide
```

## Support This Project

Building and maintaining zero-dependency Bitcoin tools takes time, caffeine, and compute. If you find this project useful, consider buying me a coffee — with Bitcoin!

<div align="center">

<img src="assets/donate_qr.png" alt="Donate Bitcoin" width="200">

**`bc1qrfagrsfrm8erdsmrku3fgq5yc573zyp2q3uje8`**

*This address was generated using ₿itcoin Gift Paper Wallet*

</div>

Your donation helps cover the cost of Claude (the AI that helped build this), keeps the coffee flowing, and fuels development of more open-source Bitcoin tools. No VC funding, no ads, no tracking — just open-source code and generous supporters like you.

## License

This project is provided as-is, without warranty of any kind. The author is not responsible for any loss of funds. Always verify addresses and back up private keys before sending real Bitcoin.
