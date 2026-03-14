#!/usr/bin/env python3
"""
Bitcoin Gift Wallet — MCP Server
Exposes wallet generation tools to Claude Desktop.
Runs locally; private keys never leave this machine.
"""

import json
import os
import subprocess
import sys
import asyncio
from datetime import datetime

# ── Resolve paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")
sys.path.insert(0, SERVER_DIR)

import bitcoin_crypto as bc
import bill_generator as bg

# ── MCP imports ───────────────────────────────────────────────────────────────
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("bitcoin-gift-wallet")

# ── Output directory for generated bills ──────────────────────────────────────
BILLS_DIR = os.path.join(PROJECT_ROOT, "generated-bills")
os.makedirs(BILLS_DIR, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_bill(wallet: dict, address_type: str, is_tweaked: bool = False) -> str:
    """Render a bill PNG and return its file path."""
    image = bg.generate_bill_image(
        address=wallet["address"],
        private_key_wif=wallet["private_key_wif"],
        address_type=address_type,
        is_tweaked=is_tweaked,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"wallet_{address_type}_{timestamp}.png"
    path = os.path.join(BILLS_DIR, filename)
    image.save(path)
    return path


def _open_file(path: str):
    """Open a file with the default macOS app (Preview for PNGs)."""
    subprocess.Popen(["open", path])


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="generate_segwit_wallet",
            description=(
                "Generate a Bitcoin SegWit (P2WPKH, bc1q...) paper wallet. "
                "Creates a gift-ready bill image and opens it in Preview. "
                "All keys are generated locally using OS CSPRNG — nothing leaves this machine."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4"],
                        "default": "mainnet",
                        "description": "Bitcoin network. Use mainnet for real wallets.",
                    },
                    "open_preview": {
                        "type": "boolean",
                        "default": True,
                        "description": "Automatically open the bill PNG in Preview.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="generate_taproot_wallet",
            description=(
                "Generate a Bitcoin Taproot (P2TR, bc1p...) paper wallet. "
                "Optionally includes a backup key for script-path recovery. "
                "Creates a gift-ready bill image and opens it in Preview. "
                "All keys are generated locally using OS CSPRNG — nothing leaves this machine."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4"],
                        "default": "mainnet",
                        "description": "Bitcoin network. Use mainnet for real wallets.",
                    },
                    "backup_key": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Generate a second backup key for script-path recovery. "
                            "The bill will show the tweaked WIF. "
                            "Store the backup WIF separately — it is needed for recovery."
                        ),
                    },
                    "open_preview": {
                        "type": "boolean",
                        "default": True,
                        "description": "Automatically open the bill PNG in Preview.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="open_wallet_app",
            description=(
                "Open the Bitcoin Gift Wallet web app in the browser. "
                "Useful for manual generation, sweeping, or backup recovery. "
                "All crypto runs entirely client-side — no data leaves this machine."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "enum": ["index", "sweep", "recover", "donate"],
                        "default": "index",
                        "description": "Which page to open.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_generated_wallets",
            description=(
                "List previously generated wallet bill images in the generated-bills folder."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "open_folder": {
                        "type": "boolean",
                        "default": False,
                        "description": "Open the generated-bills folder in Finder.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="open_wallet_bill",
            description="Open a previously generated wallet bill image in Preview by filename.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename (e.g. wallet_taproot_20250101_120000.png). Use list_generated_wallets to see available files.",
                    },
                },
                "required": ["filename"],
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    # ── generate_segwit_wallet ────────────────────────────────────────────────
    if name == "generate_segwit_wallet":
        network = arguments.get("network", "mainnet")
        open_preview = arguments.get("open_preview", True)

        wallet = bc.generate_segwit_address(network=network)
        bill_path = _save_bill(wallet, address_type="segwit")

        if open_preview:
            _open_file(bill_path)

        result = {
            "type": "SegWit P2WPKH",
            "network": network,
            "address": wallet["address"],
            "private_key_wif": wallet["private_key_wif"],
            "bill_image": bill_path,
            "note": (
                "Bill opened in Preview. Fund the address, then fold and gift. "
                "Keep the WIF secret until the recipient is ready to sweep."
            ),
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── generate_taproot_wallet ───────────────────────────────────────────────
    elif name == "generate_taproot_wallet":
        network = arguments.get("network", "mainnet")
        backup_key = arguments.get("backup_key", False)
        open_preview = arguments.get("open_preview", True)

        wallet = bc.generate_taproot_address(network=network, backup_key=backup_key)
        is_tweaked = backup_key
        bill_path = _save_bill(wallet, address_type="taproot", is_tweaked=is_tweaked)

        if open_preview:
            _open_file(bill_path)

        result = {
            "type": "Taproot P2TR",
            "network": network,
            "address": wallet["address"],
            "private_key_wif": wallet["private_key_wif"],
            "has_backup_key": backup_key,
            "bill_image": bill_path,
        }

        if backup_key:
            result["backup_private_key_wif"] = wallet["backup_private_key_wif"]
            result["tweaked_private_key_hex"] = wallet["tweaked_private_key_hex"]
            result["note"] = (
                "Backup key generated. The bill shows the tweaked WIF. "
                "Store backup_private_key_wif securely — needed for script-path recovery."
            )
        else:
            result["note"] = (
                "Bill opened in Preview. Fund the address, then fold and gift."
            )

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── open_wallet_app ───────────────────────────────────────────────────────
    elif name == "open_wallet_app":
        page = arguments.get("page", "index")
        page_map = {
            "index": "index.html",
            "sweep": "sweep.html",
            "recover": "recover.html",
            "donate": "donate.html",
        }
        filename = page_map.get(page, "index.html")
        path = os.path.join(PROJECT_ROOT, filename)
        _open_file(path)
        return [types.TextContent(
            type="text",
            text=json.dumps({
                "status": "opened",
                "page": page,
                "path": path,
                "note": "All crypto runs client-side in your browser — no data leaves this machine.",
            }, indent=2)
        )]

    # ── list_generated_wallets ────────────────────────────────────────────────
    elif name == "list_generated_wallets":
        open_folder = arguments.get("open_folder", False)
        files = sorted(
            [f for f in os.listdir(BILLS_DIR) if f.endswith(".png")],
            reverse=True,
        )
        if open_folder:
            _open_file(BILLS_DIR)
        return [types.TextContent(
            type="text",
            text=json.dumps({
                "directory": BILLS_DIR,
                "count": len(files),
                "wallets": files,
            }, indent=2)
        )]

    # ── open_wallet_bill ──────────────────────────────────────────────────────
    elif name == "open_wallet_bill":
        filename = arguments.get("filename", "")
        path = os.path.join(BILLS_DIR, filename)
        if not os.path.exists(path):
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": f"File not found: {filename}. Use list_generated_wallets to see available files."})
            )]
        _open_file(path)
        return [types.TextContent(
            type="text",
            text=json.dumps({"status": "opened", "path": path}, indent=2)
        )]

    else:
        return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
