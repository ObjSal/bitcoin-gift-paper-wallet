# Bitcoin Gift Wallet — MCP Server Setup

## One-Click Install (.mcpb bundle)

Download `bitcoin-gift-wallet.mcpb` from the [latest GitHub release](https://github.com/ObjSal/bitcoin-gift-wallet/releases) and double-click to install in Claude Desktop. No Node.js, npm, or manual configuration required.

---

## Manual Setup (from source)

Two MCP server implementations are provided: **Node.js** (recommended) and **Python**. Both expose the same tools and reuse the project's existing crypto modules.

## Node.js MCP Server (recommended)

### 1. Install dependencies (one-time)

```bash
cd bitcoin-gift-wallet/mcp
npm install
```

This installs two packages:
- `@modelcontextprotocol/sdk` — MCP protocol (Claude Desktop communication)
- `@napi-rs/canvas` — Node.js Canvas API (bill image rendering)

### 2. Add to Claude Desktop config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

### 3. Add to Claude Code

Add to `.claude/settings.local.json` or `~/.claude/settings.json`:

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

### 4. Restart Claude Desktop / Claude Code

Quit fully and reopen. You should see `bitcoin-gift-wallet` appear in the tools indicator.

---

## Python MCP Server

### 1. Install dependencies (one-time)

```bash
pip install mcp Pillow
```

### 2. Add to Claude Desktop config

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

---

## Available Tools

| Tool | Description |
|------|-------------|
| `generate_segwit_wallet` | Generate a SegWit (bc1q...) wallet + open bill in Preview |
| `generate_taproot_wallet` | Generate a Taproot (bc1p...) wallet, with optional backup key |
| `open_wallet_app` | Open index / sweep / recover page in browser |
| `list_generated_wallets` | List all previously generated bills |
| `open_wallet_bill` | Reopen a specific bill by filename |

Generated bills are saved to `generated-bills/` in the project root.

---

## Example Prompts

- *"Generate a taproot paper wallet"*
- *"Make me a segwit gift wallet on testnet"*
- *"Generate a taproot wallet with a backup key"*
- *"Show me the wallets I've already generated"*
- *"Open the sweep page"*

---

## How It Works

The MCP servers reuse the exact same crypto modules as the website:

```
mcp/mcp_server.js (Node.js)
  ├── js/bitcoin_crypto.js  — key generation (pure JS, same as browser)
  ├── js/qr_generator.js    — QR codes (pure JS, same as browser)
  └── js/bill_generator.js  — bill rendering (Canvas API, same as browser)
       └── @napi-rs/canvas  — provides createCanvas/loadImage in Node.js

mcp/mcp_server.py (Python)
  ├── server/bitcoin_crypto.py  — key generation (Python reference)
  └── server/bill_generator.py  — bill rendering (Pillow)
```

---

## Building the .mcpb Bundle

To build the downloadable bundle for distribution:

```bash
cd mcp
./build.sh
```

This creates `mcp/dist/bitcoin-gift-wallet.mcpb` — a self-contained bundle with all JS modules, assets, and Node.js dependencies. Upload to GitHub Releases for distribution.

The bundle is built using `@anthropic-ai/mcpb`. In bundle mode, generated bills are saved to `~/bitcoin-gift-wallet/generated-bills/`.

---

## Security Notes

- Private keys generated via OS CSPRNG (`crypto.getRandomValues()` / `secrets.token_bytes()`)
- No network calls — all generation is local
- Bill PNGs saved to local disk only
- Claude sees the address and WIF in the tool result — use on a trusted machine only
