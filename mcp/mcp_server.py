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
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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

def _save_bill(wallet: dict, address_type: str, is_tweaked: bool = False) -> tuple[str, str]:
    """Render a bill PNG and save companion JSON. Returns (bill_path, json_path)."""
    image = bg.generate_bill_image(
        address=wallet["address"],
        private_key_wif=wallet["private_key_wif"],
        address_type=address_type,
        is_tweaked=is_tweaked,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"wallet_{address_type}_{timestamp}.png"
    bill_path = os.path.join(BILLS_DIR, filename)
    image.save(bill_path)
    return bill_path


def _save_metadata(bill_path: str, wallet_data: dict) -> str:
    """Save wallet metadata JSON alongside the bill PNG."""
    json_path = bill_path.replace(".png", ".json")
    metadata = {"generated_at": datetime.now().isoformat(), **wallet_data}
    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=2)
    return json_path


def _open_file(path: str):
    """Open a file with the default macOS app (Preview for PNGs)."""
    subprocess.Popen(["open", path])


# ── Tip/donation addresses ───────────────────────────────────────────────────

TIP_ADDRESSES = {
    "mainnet":  "bc1qrfagrsfrm8erdsmrku3fgq5yc573zyp2q3uje8",
    "testnet4": "tb1q2ylq48ne37ng9clds23xjcrxp8hmn707j5vpyk",
    "regtest":  "bcrt1qrx4ree6dujheqmpd62cnws9zs0eak8v7vtuhv9",
}

# ── Network API helpers ───────────────────────────────────────────────────────
#
# Supports mainnet/testnet4 via mempool.space and regtest via local server.
# Set REGTEST_SERVER_URL env var to point to the local server (e.g. http://127.0.0.1:8080).

REGTEST_SERVER_URL = os.environ.get("REGTEST_SERVER_URL", "")


def _mempool_base_url(network: str) -> str:
    if network == "testnet4":
        return "https://mempool.space/testnet4/api"
    return "https://mempool.space/api"


def _fetch_utxos(address: str, network: str) -> list[dict]:
    if network == "regtest":
        if not REGTEST_SERVER_URL:
            raise RuntimeError("REGTEST_SERVER_URL not set")
        url = f"{REGTEST_SERVER_URL}/api/utxos"
        data = json.dumps({"address": address, "network": "regtest"}).encode()
        req = Request(url, data=data, method="POST",
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        return [{"txid": u["txid"], "vout": u["vout"],
                 "value_sat": u.get("value_sat", u.get("value", 0))}
                for u in result.get("utxos", [])]
    url = f"{_mempool_base_url(network)}/address/{address}/utxo"
    req = Request(url, headers={"User-Agent": "bitcoin-gift-wallet-mcp/1.0"})
    with urlopen(req, timeout=30) as resp:
        utxos = json.loads(resp.read())
    return [{"txid": u["txid"], "vout": u["vout"], "value_sat": u["value"]} for u in utxos]


def _broadcast_tx(raw_hex: str, network: str) -> str:
    if network == "regtest":
        if not REGTEST_SERVER_URL:
            raise RuntimeError("REGTEST_SERVER_URL not set")
        url = f"{REGTEST_SERVER_URL}/api/broadcast"
        data = json.dumps({"raw_hex": raw_hex, "network": "regtest"}).encode()
        req = Request(url, data=data, method="POST",
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        return result["txid"]
    url = f"{_mempool_base_url(network)}/tx"
    req = Request(url, data=raw_hex.encode(), method="POST",
                  headers={"Content-Type": "text/plain", "User-Agent": "bitcoin-gift-wallet-mcp/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode().strip()


def _fetch_fee_rates(network: str) -> dict:
    if network == "regtest":
        return {"fastestFee": 2, "halfHourFee": 1, "hourFee": 1}
    url = f"{_mempool_base_url(network)}/v1/fees/recommended"
    req = Request(url, headers={"User-Agent": "bitcoin-gift-wallet-mcp/1.0"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _explorer_url(txid: str, network: str) -> str:
    if network == "regtest":
        return f"regtest:{txid}"
    if network == "testnet4":
        return f"https://mempool.space/testnet4/tx/{txid}"
    return f"https://mempool.space/tx/{txid}"


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
                        "enum": ["mainnet", "testnet4", "regtest"],
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
                        "enum": ["mainnet", "testnet4", "regtest"],
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
            name="check_balance",
            description=(
                "Check the Bitcoin balance of an address. "
                "Fetches UTXOs from mempool.space and returns the total balance in BTC and satoshis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Bitcoin address to check (bc1q..., bc1p..., tb1q..., tb1p...).",
                    },
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4", "regtest"],
                        "default": "mainnet",
                        "description": "Bitcoin network.",
                    },
                },
                "required": ["address"],
            },
        ),
        types.Tool(
            name="check_all_balances",
            description=(
                "Check balances of all previously generated wallets. "
                "Reads wallet metadata from generated-bills/ and fetches each balance from mempool.space. "
                "Use this when the user asks 'how much bitcoin do I have?' or similar."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4", "regtest"],
                        "description": "Only check wallets on this network. If omitted, checks all.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="sweep_wallet",
            description=(
                "Sweep all funds from a paper wallet to a destination address. "
                "Takes the private key (WIF) from the bill, fetches UTXOs, builds a signed transaction, "
                "and broadcasts it. Supports SegWit and Taproot (both tweaked and untweaked) keys. "
                "IMPORTANT: Before sweeping, ask the user which tip percentage they'd like to include "
                "(0.99% recommended, 0.5%, 0.1%, or no tip). "
                "WARNING: This sends real Bitcoin — double-check the destination address."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "wif": {
                        "type": "string",
                        "description": "Private key in WIF format (from the paper wallet bill).",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination Bitcoin address to send funds to.",
                    },
                    "fee_rate": {
                        "type": "number",
                        "description": "Fee rate in sat/vB. If omitted, uses the 'half hour' recommended fee.",
                    },
                    "tip_percent": {
                        "type": "number",
                        "enum": [0.99, 0.5, 0.1, 0],
                        "default": 0.99,
                        "description": "Tip percentage to support the project. Ask the user to choose: 0.99% (recommended), 0.5%, 0.1%, or 0 (no tip).",
                    },
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4", "regtest"],
                        "default": "mainnet",
                        "description": "Bitcoin network.",
                    },
                },
                "required": ["wif", "destination"],
            },
        ),
        types.Tool(
            name="recover_wallet",
            description=(
                "Recover funds from a Taproot paper wallet using the backup key (script-path spend). "
                "Requires the backup private key WIF and the internal public key (both from the backup JSON). "
                "IMPORTANT: Before recovering, ask the user which tip percentage they'd like to include "
                "(0.99% recommended, 0.5%, 0.1%, or no tip). "
                "WARNING: This sends real Bitcoin — double-check the destination address."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "backup_wif": {
                        "type": "string",
                        "description": "Backup private key in WIF format (from the backup JSON).",
                    },
                    "internal_pubkey_hex": {
                        "type": "string",
                        "description": "Internal public key as 64-char hex string (from the backup JSON).",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination Bitcoin address to send recovered funds to.",
                    },
                    "fee_rate": {
                        "type": "number",
                        "description": "Fee rate in sat/vB. If omitted, uses the 'half hour' recommended fee.",
                    },
                    "tip_percent": {
                        "type": "number",
                        "enum": [0.99, 0.5, 0.1, 0],
                        "default": 0.99,
                        "description": "Tip percentage to support the project. Ask the user to choose: 0.99% (recommended), 0.5%, 0.1%, or 0 (no tip).",
                    },
                    "network": {
                        "type": "string",
                        "enum": ["mainnet", "testnet4", "regtest"],
                        "default": "mainnet",
                        "description": "Bitcoin network.",
                    },
                },
                "required": ["backup_wif", "internal_pubkey_hex", "destination"],
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
                        "enum": ["index", "sweep", "recover"],
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

        wallet_data = {
            "type": "SegWit P2WPKH",
            "network": network,
            "address": wallet["address"],
            "private_key_wif": wallet["private_key_wif"],
        }
        json_path = _save_metadata(bill_path, wallet_data)

        if open_preview:
            _open_file(bill_path)

        result = {
            **wallet_data,
            "bill_image": bill_path,
            "metadata_json": json_path,
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

        # For backup wallets, the bill shows the tweaked WIF (key-path spending).
        # For non-backup wallets, the bill shows the untweaked (internal) WIF.
        if backup_key:
            tweaked_key_bytes = bytes.fromhex(wallet["tweaked_private_key_hex"])
            bill_wif = bc.private_key_to_wif(tweaked_key_bytes, compressed=True, network=network)
        else:
            bill_wif = wallet["private_key_wif"]

        # Override wallet WIF for bill generation
        wallet_for_bill = dict(wallet)
        wallet_for_bill["private_key_wif"] = bill_wif
        bill_path = _save_bill(wallet_for_bill, address_type="taproot", is_tweaked=is_tweaked)

        wallet_data = {
            "type": "Taproot P2TR",
            "network": network,
            "address": wallet["address"],
            "private_key_wif": bill_wif,
            "internal_pubkey_hex": wallet.get("internal_pubkey_hex", ""),
            "has_backup_key": backup_key,
        }
        if backup_key:
            wallet_data["backup_private_key_wif"] = wallet["backup_private_key_wif"]
            wallet_data["backup_pubkey_hex"] = wallet.get("backup_pubkey_hex", "")
            wallet_data["tweaked_private_key_hex"] = wallet.get("tweaked_private_key_hex", "")
            wallet_data["script_tree_hash"] = wallet.get("script_tree_hash", "")

        json_path = _save_metadata(bill_path, wallet_data)

        if open_preview:
            _open_file(bill_path)

        result = {
            **wallet_data,
            "bill_image": bill_path,
            "metadata_json": json_path,
        }

        if backup_key:
            result["note"] = (
                "Backup key generated. The bill shows the tweaked WIF. "
                "Store backup_private_key_wif securely — needed for script-path recovery."
            )
        else:
            result["note"] = (
                "Bill opened in Preview. Fund the address, then fold and gift."
            )

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── check_balance ─────────────────────────────────────────────────────────
    elif name == "check_balance":
        address = arguments["address"]
        network = arguments.get("network", "mainnet")

        utxos = _fetch_utxos(address, network)
        total_sat = sum(u["value_sat"] for u in utxos)

        result = {
            "address": address,
            "network": network,
            "balance_btc": total_sat / 1e8,
            "balance_sats": total_sat,
            "utxo_count": len(utxos),
            "utxos": utxos,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── check_all_balances ────────────────────────────────────────────────────
    elif name == "check_all_balances":
        filter_network = arguments.get("network", None)
        all_files = sorted(os.listdir(BILLS_DIR), reverse=True)
        json_files = [f for f in all_files if f.endswith(".json")]

        wallets = []
        grand_total_sat = 0

        for jf in json_files:
            try:
                with open(os.path.join(BILLS_DIR, jf)) as f:
                    data = json.load(f)
                if not data.get("address") or not data.get("network"):
                    continue
                if filter_network and data["network"] != filter_network:
                    continue

                utxos = _fetch_utxos(data["address"], data["network"])
                total_sat = sum(u["value_sat"] for u in utxos)
                grand_total_sat += total_sat

                wallets.append({
                    "file": jf,
                    "type": data.get("type"),
                    "network": data["network"],
                    "address": data["address"],
                    "balance_btc": total_sat / 1e8,
                    "balance_sats": total_sat,
                    "utxo_count": len(utxos),
                })
            except Exception:
                continue

        result = {
            "total_wallets": len(wallets),
            "total_balance_btc": grand_total_sat / 1e8,
            "total_balance_sats": grand_total_sat,
            "wallets": wallets,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── sweep_wallet ──────────────────────────────────────────────────────────
    elif name == "sweep_wallet":
        wif_str = arguments["wif"]
        destination = arguments["destination"]
        network = arguments.get("network", "mainnet")

        # Decode WIF
        decoded = bc.wif_to_private_key(wif_str)
        privkey_bytes = decoded["private_key"]
        privkey_int = int.from_bytes(privkey_bytes, "big")

        # Try all address types to find which one has funds
        segwit_info = bc.derive_segwit_address_from_privkey(privkey_bytes, network)
        taproot_tweaked_info = bc.derive_taproot_address_from_tweaked_privkey(privkey_bytes, network)
        tweaked_key_bytes = bc.taproot_tweak_seckey(privkey_bytes, None)
        tweaked_key_int = int.from_bytes(tweaked_key_bytes, "big")
        taproot_untweaked_info = bc.derive_taproot_address_from_tweaked_privkey(tweaked_key_bytes, network)

        candidates = [
            {"addr": segwit_info["address"], "type": "segwit", "key_int": privkey_int},
            {"addr": taproot_tweaked_info["address"], "type": "taproot_tweaked", "key_int": privkey_int},
            {"addr": taproot_untweaked_info["address"], "type": "taproot_untweaked", "key_int": tweaked_key_int},
        ]

        address = None
        address_type = None
        signing_key_int = None
        utxos = []

        for c in candidates:
            u = _fetch_utxos(c["addr"], network)
            if u:
                address = c["addr"]
                address_type = c["type"]
                signing_key_int = c["key_int"]
                utxos = u
                break

        if not address:
            return [types.TextContent(type="text", text=json.dumps({
                "error": "No funds found",
                "checked_addresses": {c["type"]: c["addr"] for c in candidates},
                "note": "None of the derived addresses have any UTXOs.",
            }, indent=2))]

        total_sat = sum(u["value_sat"] for u in utxos)

        # Fee rate
        fee_rate = arguments.get("fee_rate")
        if not fee_rate:
            fees = _fetch_fee_rates(network)
            fee_rate = fees["halfHourFee"]

        # Compute tip
        tip_percent = arguments.get("tip_percent", 0.99)
        tip_sat = int(total_sat * tip_percent / 100) if tip_percent > 0 else 0
        tip_addr = TIP_ADDRESSES.get(network, TIP_ADDRESSES["mainnet"])

        # Estimate vsize (add 31 vB for P2WPKH tip output if tipping)
        n_inputs = len(utxos)
        tip_extra = 31 if tip_sat > 0 else 0
        if address_type == "segwit":
            vsize = 11 + n_inputs * 69 + 31 + tip_extra
        else:
            vsize = 11 + n_inputs * 58 + 43 + tip_extra

        fee_sat = int(vsize * fee_rate + 0.999)
        send_sat = total_sat - fee_sat - tip_sat

        if send_sat <= 0:
            return [types.TextContent(type="text", text=json.dumps({
                "error": "Insufficient funds",
                "balance_sats": total_sat,
                "estimated_fee_sats": fee_sat,
                "tip_sats": tip_sat,
                "fee_rate": fee_rate,
            }, indent=2))]

        # Build extra outputs for tip
        extra_outputs = [{"address": tip_addr, "value": tip_sat}] if tip_sat > 0 else None

        # Build and sign
        signing_key_bytes = signing_key_int.to_bytes(32, "big")
        if address_type == "segwit":
            raw_hex = bc.build_signed_segwit_sweep_tx(signing_key_bytes, utxos, destination, send_sat, extra_outputs)
        else:
            input_sp = bc._address_to_scriptpubkey(address)
            raw_hex = bc.build_signed_taproot_sweep_tx(signing_key_bytes, utxos, input_sp, destination, send_sat, extra_outputs)

        txid = _broadcast_tx(raw_hex, network)

        result = {
            "status": "broadcast",
            "txid": txid,
            "from_address": address,
            "address_type": address_type,
            "to_address": destination,
            "amount_sats": send_sat,
            "amount_btc": send_sat / 1e8,
            "fee_sats": fee_sat,
            "fee_rate_sat_vb": fee_rate,
            "tip_sats": tip_sat,
            "tip_percent": tip_percent,
        }
        if tip_sat > 0:
            result["tip_address"] = tip_addr
        result["explorer_url"] = _explorer_url(txid, network)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── recover_wallet ────────────────────────────────────────────────────────
    elif name == "recover_wallet":
        backup_wif = arguments["backup_wif"]
        internal_pubkey_hex = arguments["internal_pubkey_hex"]
        destination = arguments["destination"]
        network = arguments.get("network", "mainnet")

        # Decode backup WIF
        decoded = bc.wif_to_private_key(backup_wif)
        backup_privkey_bytes = decoded["private_key"]
        backup_privkey_int = int.from_bytes(backup_privkey_bytes, "big")

        # Derive backup public key (x-only)
        backup_pub = bc.point_mul(backup_privkey_int)
        backup_pubkey_x = backup_pub[0].to_bytes(32, "big")

        # Parse internal pubkey
        internal_pubkey_x = bytes.fromhex(internal_pubkey_hex)

        # Compute script tree hash and tweaked output key
        script_tree_hash = bc.compute_script_tree_hash_for_backup(backup_pubkey_x)
        output_key_x, parity = bc.taproot_tweak_pubkey(internal_pubkey_x, script_tree_hash)

        # Derive address
        hrp = "bcrt" if network == "regtest" else "tb" if network == "testnet4" else "bc"
        address = bc.bech32_encode(hrp, 1, list(output_key_x), spec="bech32m")

        # Fetch UTXOs
        utxos = _fetch_utxos(address, network)
        total_sat = sum(u["value_sat"] for u in utxos)

        if not utxos:
            return [types.TextContent(type="text", text=json.dumps({
                "error": "No funds found",
                "address": address,
                "note": "The reconstructed address has no UTXOs.",
            }, indent=2))]

        # Fee rate
        fee_rate = arguments.get("fee_rate")
        if not fee_rate:
            fees = _fetch_fee_rates(network)
            fee_rate = fees["halfHourFee"]

        # Compute tip
        tip_percent = arguments.get("tip_percent", 0.99)
        tip_sat = int(total_sat * tip_percent / 100) if tip_percent > 0 else 0
        tip_addr = TIP_ADDRESSES.get(network, TIP_ADDRESSES["mainnet"])

        # Estimate vsize (script-path, add 31 vB for P2WPKH tip output if tipping)
        n_inputs = len(utxos)
        tip_extra = 31 if tip_sat > 0 else 0
        vsize = 11 + n_inputs * 107 + 43 + tip_extra
        fee_sat = int(vsize * fee_rate + 0.999)
        send_sat = total_sat - fee_sat - tip_sat

        if send_sat <= 0:
            return [types.TextContent(type="text", text=json.dumps({
                "error": "Insufficient funds",
                "balance_sats": total_sat,
                "estimated_fee_sats": fee_sat,
                "tip_sats": tip_sat,
                "fee_rate": fee_rate,
            }, indent=2))]

        # Build extra outputs for tip
        extra_outputs = [{"address": tip_addr, "value": tip_sat}] if tip_sat > 0 else None

        # Build and sign script-path transaction
        input_scriptpubkey = bc._address_to_scriptpubkey(address)
        raw_hex = bc.build_signed_taproot_scriptpath_sweep_tx(
            backup_privkey_bytes, backup_pubkey_x,
            internal_pubkey_x, parity,
            utxos, input_scriptpubkey,
            destination, send_sat, extra_outputs,
        )

        txid = _broadcast_tx(raw_hex, network)

        result = {
            "status": "broadcast",
            "txid": txid,
            "from_address": address,
            "address_type": "taproot_script_path",
            "to_address": destination,
            "amount_sats": send_sat,
            "amount_btc": send_sat / 1e8,
            "fee_sats": fee_sat,
            "fee_rate_sat_vb": fee_rate,
            "tip_sats": tip_sat,
            "tip_percent": tip_percent,
        }
        if tip_sat > 0:
            result["tip_address"] = tip_addr
        result["explorer_url"] = _explorer_url(txid, network)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── open_wallet_app ───────────────────────────────────────────────────────
    elif name == "open_wallet_app":
        page = arguments.get("page", "index")
        page_map = {
            "index": "index.html",
            "sweep": "sweep.html",
            "recover": "recover.html",
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
        all_files = sorted(os.listdir(BILLS_DIR), reverse=True)
        json_files = [f for f in all_files if f.endswith(".json")]

        wallets = []
        for jf in json_files:
            try:
                with open(os.path.join(BILLS_DIR, jf)) as f:
                    data = json.load(f)
                bill = jf.replace(".json", ".png")
                wallets.append({
                    "bill": bill if bill in all_files else None,
                    "metadata_json": jf,
                    "type": data.get("type"),
                    "network": data.get("network"),
                    "address": data.get("address"),
                    "has_backup_key": data.get("has_backup_key", False),
                })
            except Exception:
                wallets.append({"metadata_json": jf})

        if open_folder:
            _open_file(BILLS_DIR)
        return [types.TextContent(
            type="text",
            text=json.dumps({
                "directory": BILLS_DIR,
                "count": len(wallets),
                "wallets": wallets,
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
