#!/usr/bin/env python3
"""
Tests for the Python MCP server.

Uses the MCP SDK client to connect over stdio and exercise all 9 tools.

Usage:
    python3 tests/test_mcp_server.py
"""

import asyncio
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_SERVER = os.path.join(PROJECT_ROOT, "mcp", "mcp_server.py")
BILLS_DIR = os.path.join(PROJECT_ROOT, "generated-bills")

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


passed = 0
failed = 0


def pass_test(name):
    global passed
    passed += 1
    print(f"  PASS: {name}")


def fail_test(name, err):
    global failed
    failed += 1
    print(f"  FAIL: {name} — {err}")


async def call_tool_json(session, name, args=None):
    result = await session.call_tool(name, args or {})
    assert result.content and len(result.content) > 0, "Empty content"
    return json.loads(result.content[0].text)


async def run_tests():
    global passed, failed

    print("=" * 60)
    print("Testing Python MCP server")
    print("=" * 60)

    server_params = StdioServerParameters(
        command="python3",
        args=[PYTHON_SERVER],
        cwd=PROJECT_ROOT,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            generated_files = []

            # ── test_list_tools ───────────────────────────────────────
            try:
                result = await session.list_tools()
                names = {t.name for t in result.tools}
                expected = {
                    "generate_segwit_wallet",
                    "generate_taproot_wallet",
                    "check_balance",
                    "check_all_balances",
                    "sweep_wallet",
                    "recover_wallet",
                    "open_wallet_app",
                    "list_generated_wallets",
                    "open_wallet_bill",
                }
                assert names == expected, f"Expected {expected}, got {names}"
                pass_test(f"list_tools returns all {len(expected)} tools")
            except Exception as e:
                fail_test("list_tools", e)

            # ── test_generate_segwit_mainnet ──────────────────────────
            segwit_bill = None
            segwit_address = None
            try:
                r = await call_tool_json(session, "generate_segwit_wallet", {
                    "network": "mainnet", "open_preview": False,
                })
                assert r["type"] == "SegWit P2WPKH", f"Wrong type: {r['type']}"
                assert r["address"].startswith("bc1q"), f"Bad address: {r['address']}"
                assert r["private_key_wif"][0] in ("K", "L"), f"Bad WIF: {r['private_key_wif']}"
                assert os.path.exists(r["bill_image"]), f"Bill not found: {r['bill_image']}"
                with open(r["bill_image"], "rb") as f:
                    assert f.read(4) == b"\x89PNG", "Not a valid PNG"
                # Verify metadata JSON is always saved
                assert "metadata_json" in r and os.path.exists(r["metadata_json"]), "Metadata JSON not found"
                with open(r["metadata_json"]) as f:
                    meta = json.load(f)
                assert meta["address"] == r["address"], "Metadata address mismatch"
                assert meta["network"] == "mainnet", "Metadata network mismatch"
                segwit_bill = r["bill_image"]
                segwit_address = r["address"]
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("generate_segwit_wallet (mainnet + metadata JSON)")
            except Exception as e:
                fail_test("generate_segwit_wallet (mainnet)", e)

            # ── test_generate_segwit_testnet4 ─────────────────────────
            try:
                r = await call_tool_json(session, "generate_segwit_wallet", {
                    "network": "testnet4", "open_preview": False,
                })
                assert r["address"].startswith("tb1q"), f"Bad address: {r['address']}"
                assert r["private_key_wif"].startswith("c"), f"Bad WIF: {r['private_key_wif']}"
                assert os.path.exists(r["bill_image"])
                assert "metadata_json" in r and os.path.exists(r["metadata_json"])
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("generate_segwit_wallet (testnet4)")
            except Exception as e:
                fail_test("generate_segwit_wallet (testnet4)", e)

            # ── test_generate_taproot_no_backup ───────────────────────
            try:
                r = await call_tool_json(session, "generate_taproot_wallet", {
                    "network": "mainnet", "backup_key": False, "open_preview": False,
                })
                assert r["type"] == "Taproot P2TR", f"Wrong type: {r['type']}"
                assert r["address"].startswith("bc1p"), f"Bad address: {r['address']}"
                assert r["has_backup_key"] is False
                assert "backup_private_key_wif" not in r
                assert os.path.exists(r["bill_image"])
                assert "metadata_json" in r and os.path.exists(r["metadata_json"])
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("generate_taproot_wallet (no backup + metadata JSON)")
            except Exception as e:
                fail_test("generate_taproot_wallet (no backup)", e)

            # ── test_generate_taproot_with_backup ─────────────────────
            try:
                r = await call_tool_json(session, "generate_taproot_wallet", {
                    "network": "mainnet", "backup_key": True, "open_preview": False,
                })
                assert r["address"].startswith("bc1p"), f"Bad address: {r['address']}"
                assert r["has_backup_key"] is True
                assert "backup_private_key_wif" in r, "Missing backup WIF"
                assert r["backup_private_key_wif"][0] in ("K", "L"), "Bad backup WIF"
                assert os.path.exists(r["bill_image"])
                assert "metadata_json" in r and os.path.exists(r["metadata_json"])
                with open(r["metadata_json"]) as f:
                    meta = json.load(f)
                assert meta["backup_private_key_wif"] == r["backup_private_key_wif"], "Metadata backup WIF mismatch"
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("generate_taproot_wallet (with backup)")
            except Exception as e:
                fail_test("generate_taproot_wallet (with backup)", e)

            # ── test_generate_taproot_testnet4 ────────────────────────
            try:
                r = await call_tool_json(session, "generate_taproot_wallet", {
                    "network": "testnet4", "open_preview": False,
                })
                assert r["address"].startswith("tb1p"), f"Bad address: {r['address']}"
                assert os.path.exists(r["bill_image"])
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("generate_taproot_wallet (testnet4)")
            except Exception as e:
                fail_test("generate_taproot_wallet (testnet4)", e)

            # ── test_default_parameters ───────────────────────────────
            try:
                r = await call_tool_json(session, "generate_segwit_wallet", {})
                assert r["address"].startswith("bc1q"), f"Default should be mainnet: {r['address']}"
                assert os.path.exists(r["bill_image"])
                generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("default parameters (empty args → mainnet segwit)")
            except Exception as e:
                fail_test("default parameters", e)

            # ── test_check_balance ────────────────────────────────────
            if segwit_address:
                try:
                    r = await call_tool_json(session, "check_balance", {
                        "address": segwit_address, "network": "mainnet",
                    })
                    assert r["address"] == segwit_address, "Address mismatch"
                    assert r["network"] == "mainnet", "Network mismatch"
                    assert isinstance(r["balance_btc"], (int, float)), "Missing balance_btc"
                    assert isinstance(r["balance_sats"], (int, float)), "Missing balance_sats"
                    assert isinstance(r["utxo_count"], int), "Missing utxo_count"
                    assert isinstance(r["utxos"], list), "utxos should be list"
                    assert r["balance_sats"] == 0, f"Fresh wallet should have 0, got {r['balance_sats']}"
                    pass_test("check_balance (fresh wallet → 0 sats)")
                except Exception as e:
                    fail_test("check_balance", e)

            # ── test_check_all_balances ───────────────────────────────
            try:
                r = await call_tool_json(session, "check_all_balances", {"network": "mainnet"})
                assert isinstance(r["total_wallets"], int), "Missing total_wallets"
                assert isinstance(r["total_balance_btc"], (int, float)), "Missing total_balance_btc"
                assert isinstance(r["total_balance_sats"], (int, float)), "Missing total_balance_sats"
                assert isinstance(r["wallets"], list), "wallets should be list"
                assert r["total_wallets"] >= 1, "Should have at least 1 wallet"
                w = r["wallets"][0]
                assert w.get("address"), "Wallet missing address"
                assert w.get("network"), "Wallet missing network"
                assert isinstance(w.get("balance_sats"), (int, float)), "Wallet missing balance_sats"
                pass_test(f"check_all_balances ({r['total_wallets']} mainnet wallets)")
            except Exception as e:
                fail_test("check_all_balances", e)

            # ── test_sweep_wallet_no_funds ────────────────────────────
            try:
                gen = await call_tool_json(session, "generate_segwit_wallet", {
                    "network": "mainnet", "open_preview": False,
                })
                generated_files.extend([gen["bill_image"], gen["metadata_json"]])

                r = await call_tool_json(session, "sweep_wallet", {
                    "wif": gen["private_key_wif"],
                    "destination": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
                    "network": "mainnet",
                })
                assert r.get("error") == "No funds found", f"Expected 'No funds found', got: {r.get('error')}"
                assert "checked_addresses" in r, "Missing checked_addresses"
                pass_test("sweep_wallet (no funds → error)")
            except Exception as e:
                fail_test("sweep_wallet (no funds)", e)

            # ── test_recover_wallet_no_funds ──────────────────────────
            try:
                gen = await call_tool_json(session, "generate_taproot_wallet", {
                    "network": "mainnet", "backup_key": True, "open_preview": False,
                })
                generated_files.extend([gen["bill_image"], gen["metadata_json"]])

                r = await call_tool_json(session, "recover_wallet", {
                    "backup_wif": gen["backup_private_key_wif"],
                    "internal_pubkey_hex": gen["internal_pubkey_hex"],
                    "destination": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
                    "network": "mainnet",
                })
                assert r.get("error") == "No funds found", f"Expected 'No funds found', got: {r.get('error')}"
                assert r.get("address"), "Missing reconstructed address"
                assert r["address"] == gen["address"], f"Address mismatch: {r['address']} vs {gen['address']}"
                pass_test("recover_wallet (no funds → error, address matches)")
            except Exception as e:
                fail_test("recover_wallet (no funds)", e)

            # ── test_list_generated_wallets ────────────────────────────
            try:
                r = await call_tool_json(session, "list_generated_wallets", {"open_folder": False})
                assert isinstance(r["count"], int), "Missing count"
                assert r["count"] >= 1, "Should have at least 1 wallet"
                assert isinstance(r["wallets"], list), "wallets should be list"
                w = r["wallets"][0]
                assert w.get("metadata_json"), "Missing metadata_json"
                assert w.get("address"), "Missing address in list"
                assert w.get("type"), "Missing type in list"
                assert w.get("network"), "Missing network in list"
                pass_test(f"list_generated_wallets ({r['count']} wallets with metadata)")
            except Exception as e:
                fail_test("list_generated_wallets", e)

            # ── test_open_wallet_bill_not_found ───────────────────────
            try:
                r = await call_tool_json(session, "open_wallet_bill", {"filename": "nonexistent.png"})
                assert "error" in r, f"Expected error, got: {r}"
                pass_test("open_wallet_bill (not found → error)")
            except Exception as e:
                fail_test("open_wallet_bill (not found)", e)

            # ── test_open_wallet_bill_exists ──────────────────────────
            if segwit_bill:
                try:
                    basename = os.path.basename(segwit_bill)
                    r = await call_tool_json(session, "open_wallet_bill", {"filename": basename})
                    assert r["status"] == "opened", f"Expected opened, got: {r}"
                    pass_test("open_wallet_bill (existing file)")
                except Exception as e:
                    fail_test("open_wallet_bill (existing file)", e)

            # ── test_open_wallet_app ──────────────────────────────────
            try:
                for page in ["index", "sweep", "recover"]:
                    r = await call_tool_json(session, "open_wallet_app", {"page": page})
                    assert r["status"] == "opened", f"Failed to open {page}"
                    assert r["page"] == page
                pass_test("open_wallet_app (all 3 pages)")
            except Exception as e:
                fail_test("open_wallet_app", e)

            # ── test_address_uniqueness ───────────────────────────────
            try:
                addresses = set()
                for _ in range(5):
                    r = await call_tool_json(session, "generate_segwit_wallet", {
                        "network": "mainnet", "open_preview": False,
                    })
                    assert r["address"] not in addresses, f"Duplicate: {r['address']}"
                    addresses.add(r["address"])
                    generated_files.extend([r["bill_image"], r["metadata_json"]])
                pass_test("address uniqueness (5 consecutive wallets)")
            except Exception as e:
                fail_test("address uniqueness", e)

            # ── Cleanup generated test files ──────────────────────────
            for f in generated_files:
                try:
                    os.unlink(f)
                except OSError:
                    pass

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
