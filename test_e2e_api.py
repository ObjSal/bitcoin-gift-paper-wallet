#!/usr/bin/env python3
"""
End-to-end API tests for Bitcoin Gift Wallet.

Tests the full lifecycle through HTTP API endpoints:
  Generator (POST /api/generate)
  → Fund on regtest
  → Sweep (POST /api/sweep) or Recover (POST /api/recover)
  → Verify on-chain

8 tests covering: SegWit sweep, Taproot sweep (no backup), Taproot sweep
(with backup/tweaked), Taproot recovery (script-path), multi-UTXO sweep,
repeated multi-UTXO sweep (all 3 address types × 2 rounds), repeated
multi-UTXO recovery (script-path × 2 rounds), and chained sweep→recovery
(faucet → sweep → recover across 3 addresses).

Requires: Bitcoin Core (bitcoind + bitcoin-cli) installed and in PATH.

Usage:
    python3 test_e2e_api.py
"""

import json
import os
import shutil
import socket
import sys
import threading
import traceback
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_regtest_spending import RegtestNode, TestResult
import server as server_module
from server import ReusableTCPServer, WalletHandler


# ============================================================
# HTTP helper
# ============================================================

def api_post(base_url, path, payload):
    """POST JSON to the server and return parsed response."""
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data,
                  headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"API {path} returned {e.code}: {body}"
        )


# ============================================================
# Test cases
# ============================================================

def test_segwit_sweep(node, base_url, result):
    """E2E: Generate SegWit → fund → sweep via API → verify on-chain."""
    print("\n=== E2E Test: SegWit Sweep ===")

    # 1. Generate via API
    gen = api_post(base_url, "/api/generate", {
        "type": "segwit", "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    wif = wallet["private_key_wif"]
    print(f"  Generated: {address}")

    # 2. Fund on regtest
    node.fund_address(address, "1.0")

    # 3. Derive via API — verify address matches
    derive = api_post(base_url, "/api/sweep/derive", {
        "wif": wif, "address_type": "segwit", "network": "regtest",
    })
    assert derive["success"], f"Derive failed: {derive}"
    assert derive["address"] == address, \
        f"Derived {derive['address']} != generated {address}"
    print(f"  Derive verified: {derive['address']}")

    # 4. Sweep via API
    dest_addr = node.get_new_address()
    fee_rate = 2
    sweep = api_post(base_url, "/api/sweep", {
        "wif": wif, "address_type": "segwit", "network": "regtest",
        "dest_address": dest_addr, "fee_rate": fee_rate,
    })
    assert sweep["success"], f"Sweep failed: {sweep}"
    txid = sweep["txid"]
    amount_sat = sweep["amount_sat"]
    fee_sat = sweep["fee_sat"]
    print(f"  Swept: txid={txid[:16]}... amount={amount_sat} fee={fee_sat}")

    # 5. Mine and verify on-chain
    node.mine(1)
    try:
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        print(f"  Confirmed: {confs} conf, {amount_sat} sats to dest")
        result.ok("E2E SegWit sweep: generate → fund → sweep → confirmed")
    except Exception as e:
        result.fail("E2E SegWit sweep", str(e))


def test_taproot_no_backup_sweep(node, base_url, result):
    """E2E: Generate Taproot (no backup) → fund → sweep via API → verify."""
    print("\n=== E2E Test: Taproot (no backup) Sweep ===")

    # 1. Generate via API
    gen = api_post(base_url, "/api/generate", {
        "type": "taproot", "backup": False, "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    wif = wallet["private_key_wif"]  # untweaked for no-backup
    print(f"  Generated: {address}")
    assert not wallet.get("has_backup"), "Expected no backup"

    # 2. Fund on regtest
    node.fund_address(address, "1.0")

    # 3. Derive via API (server applies BIP86 key-only tweak internally)
    derive = api_post(base_url, "/api/sweep/derive", {
        "wif": wif, "address_type": "taproot", "network": "regtest",
    })
    assert derive["success"], f"Derive failed: {derive}"
    assert derive["address"] == address, \
        f"Derived {derive['address']} != generated {address}"
    print(f"  Derive verified: {derive['address']}")

    # 4. Sweep via API
    dest_addr = node.get_new_address()
    fee_rate = 2
    sweep = api_post(base_url, "/api/sweep", {
        "wif": wif, "address_type": "taproot", "network": "regtest",
        "dest_address": dest_addr, "fee_rate": fee_rate,
    })
    assert sweep["success"], f"Sweep failed: {sweep}"
    txid = sweep["txid"]
    amount_sat = sweep["amount_sat"]
    fee_sat = sweep["fee_sat"]
    print(f"  Swept: txid={txid[:16]}... amount={amount_sat} fee={fee_sat}")

    # 5. Mine and verify on-chain
    node.mine(1)
    try:
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        print(f"  Confirmed: {confs} conf, {amount_sat} sats to dest")
        result.ok("E2E Taproot (no backup) sweep: generate → fund → sweep → confirmed")
    except Exception as e:
        result.fail("E2E Taproot (no backup) sweep", str(e))


def test_taproot_backup_sweep(node, base_url, result):
    """E2E: Generate Taproot (with backup) → fund → recipient sweep via tweaked WIF."""
    print("\n=== E2E Test: Taproot (with backup) Recipient Sweep ===")

    # 1. Generate via API
    gen = api_post(base_url, "/api/generate", {
        "type": "taproot", "backup": True, "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    bill_wif = wallet["bill_wif"]  # tweaked WIF (what's printed on the bill)
    print(f"  Generated: {address}")
    assert wallet.get("has_backup"), "Expected backup"
    assert gen.get("is_tweaked"), "Expected is_tweaked flag"

    # 2. Fund on regtest
    node.fund_address(address, "1.0")

    # 3. Derive via API (tweaked key → address directly)
    derive = api_post(base_url, "/api/sweep/derive", {
        "wif": bill_wif, "address_type": "taproot_tweaked",
        "network": "regtest",
    })
    assert derive["success"], f"Derive failed: {derive}"
    assert derive["address"] == address, \
        f"Derived {derive['address']} != generated {address}"
    print(f"  Derive verified: {derive['address']}")

    # 4. Sweep via API using tweaked WIF
    dest_addr = node.get_new_address()
    fee_rate = 2
    sweep = api_post(base_url, "/api/sweep", {
        "wif": bill_wif, "address_type": "taproot_tweaked",
        "network": "regtest",
        "dest_address": dest_addr, "fee_rate": fee_rate,
    })
    assert sweep["success"], f"Sweep failed: {sweep}"
    txid = sweep["txid"]
    amount_sat = sweep["amount_sat"]
    fee_sat = sweep["fee_sat"]
    print(f"  Swept: txid={txid[:16]}... amount={amount_sat} fee={fee_sat}")

    # 5. Mine and verify on-chain
    node.mine(1)
    try:
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        print(f"  Confirmed: {confs} conf, {amount_sat} sats to dest")
        result.ok("E2E Taproot (backup) recipient sweep: tweaked WIF → confirmed")
    except Exception as e:
        result.fail("E2E Taproot (backup) recipient sweep", str(e))


def test_taproot_backup_recover(node, base_url, result):
    """E2E: Generate Taproot (with backup) → fund → giver recovery via backup key."""
    print("\n=== E2E Test: Taproot (with backup) Giver Recovery ===")

    # 1. Generate via API
    gen = api_post(base_url, "/api/generate", {
        "type": "taproot", "backup": True, "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    backup_wif = wallet["backup_private_key_wif"]
    internal_pubkey_hex = wallet["internal_pubkey_hex"]
    print(f"  Generated: {address}")
    print(f"  Internal pubkey: {internal_pubkey_hex[:16]}...")

    # 2. Fund on regtest
    node.fund_address(address, "1.0")

    # 3. Derive via recover API — verify address matches
    derive = api_post(base_url, "/api/recover/derive", {
        "backup_wif": backup_wif,
        "internal_pubkey_hex": internal_pubkey_hex,
        "network": "regtest",
    })
    assert derive["success"], f"Recover derive failed: {derive}"
    assert derive["address"] == address, \
        f"Recovered {derive['address']} != generated {address}"
    print(f"  Recover derive verified: {derive['address']}")

    # 4. Recover via API (script-path spend)
    dest_addr = node.get_new_address()
    fee_rate = 2
    recover = api_post(base_url, "/api/recover", {
        "backup_wif": backup_wif,
        "internal_pubkey_hex": internal_pubkey_hex,
        "network": "regtest",
        "dest_address": dest_addr, "fee_rate": fee_rate,
    })
    assert recover["success"], f"Recover failed: {recover}"
    txid = recover["txid"]
    amount_sat = recover["amount_sat"]
    fee_sat = recover["fee_sat"]
    print(f"  Recovered: txid={txid[:16]}... amount={amount_sat} fee={fee_sat}")

    # 5. Mine and verify on-chain
    node.mine(1)
    try:
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        print(f"  Confirmed: {confs} conf, {amount_sat} sats to dest")
        result.ok("E2E Taproot (backup) giver recovery: script-path → confirmed")
    except Exception as e:
        result.fail("E2E Taproot (backup) giver recovery", str(e))


def test_multi_utxo_sweep(node, base_url, result):
    """E2E: Fund address 3 times → single sweep consumes all UTXOs."""
    print("\n=== E2E Test: Multi-UTXO Sweep ===")

    # 1. Generate via API (taproot with backup — uses tweaked WIF)
    gen = api_post(base_url, "/api/generate", {
        "type": "taproot", "backup": True, "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    bill_wif = wallet["bill_wif"]
    print(f"  Generated: {address}")

    # 2. Fund 3 times (creates 3 UTXOs)
    fund_amount = "0.5"
    fund_sats = 50_000_000
    for i in range(3):
        node.fund_address(address, fund_amount)
    total_funded = fund_sats * 3
    print(f"  Funded 3x {fund_amount} BTC = {total_funded} sats total")

    # 3. Verify UTXOs via API
    utxo_resp = api_post(base_url, "/api/utxos", {
        "address": address, "network": "regtest",
    })
    assert utxo_resp["success"], f"UTXO lookup failed: {utxo_resp}"
    assert utxo_resp["count"] == 3, \
        f"Expected 3 UTXOs, got {utxo_resp['count']}"
    assert utxo_resp["total_sat"] == total_funded, \
        f"Expected {total_funded} sats, got {utxo_resp['total_sat']}"
    print(f"  UTXOs: {utxo_resp['count']} totaling {utxo_resp['total_sat']} sats")

    # 4. Sweep all in one transaction
    dest_addr = node.get_new_address()
    fee_rate = 2
    sweep = api_post(base_url, "/api/sweep", {
        "wif": bill_wif, "address_type": "taproot_tweaked",
        "network": "regtest",
        "dest_address": dest_addr, "fee_rate": fee_rate,
    })
    assert sweep["success"], f"Sweep failed: {sweep}"
    txid = sweep["txid"]
    amount_sat = sweep["amount_sat"]
    fee_sat = sweep["fee_sat"]
    vsize = sweep["vsize"]

    # Verify fee calculation: taproot key-path vsize = 11 + N*58 + 43
    expected_vsize = 11 + 3 * 58 + 43
    assert vsize == expected_vsize, \
        f"Expected vsize {expected_vsize}, got {vsize}"
    expected_fee = expected_vsize * fee_rate
    assert fee_sat == expected_fee, \
        f"Expected fee {expected_fee}, got {fee_sat}"
    expected_amount = total_funded - expected_fee
    assert amount_sat == expected_amount, \
        f"Expected amount {expected_amount}, got {amount_sat}"
    print(f"  Swept 3 UTXOs: txid={txid[:16]}... vsize={vsize} "
          f"fee={fee_sat} amount={amount_sat}")

    # 5. Mine and verify on-chain
    node.mine(1)
    try:
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        print(f"  Confirmed: {confs} conf, {amount_sat} sats to dest")
        result.ok("E2E Multi-UTXO sweep: 3 inputs → single tx → confirmed")
    except Exception as e:
        result.fail("E2E Multi-UTXO sweep", str(e))


def test_repeated_multi_utxo_sweep(node, base_url, result):
    """E2E: Fund address N times → sweep all → fund again N times → sweep all.

    Repeats the fund-then-sweep cycle twice for each address type (SegWit,
    Taproot no-backup, Taproot with backup) to verify multi-input sweeps
    work correctly and that the address is reusable after being swept.
    """
    print("\n=== E2E Test: Repeated Multi-UTXO Sweep (3 address types) ===")

    # Test each of the 3 address types we generate
    configs = [
        {
            "label": "SegWit",
            "gen_params": {"type": "segwit", "network": "regtest"},
            "addr_type": "segwit",
            "wif_key": "private_key_wif",
            # SegWit vsize: 11 + N*69 + 31
            "vsize_fn": lambda n: 11 + n * 69 + 31,
        },
        {
            "label": "Taproot (no backup)",
            "gen_params": {"type": "taproot", "backup": False, "network": "regtest"},
            "addr_type": "taproot",
            "wif_key": "private_key_wif",
            # Taproot key-path (no script tree): 11 + N*58 + 43
            "vsize_fn": lambda n: 11 + n * 58 + 43,
        },
        {
            "label": "Taproot (with backup)",
            "gen_params": {"type": "taproot", "backup": True, "network": "regtest"},
            "addr_type": "taproot_tweaked",
            "wif_key": "bill_wif",
            # Taproot key-path (with script tree): 11 + N*58 + 43
            "vsize_fn": lambda n: 11 + n * 58 + 43,
        },
    ]

    fee_rate = 2

    for cfg in configs:
        label = cfg["label"]
        print(f"\n  --- {label} ---")

        # 1. Generate address
        gen = api_post(base_url, "/api/generate", cfg["gen_params"])
        assert gen["success"], f"{label}: Generate failed: {gen}"
        wallet = gen["wallet"]
        address = wallet["address"]
        wif = wallet[cfg["wif_key"]]
        print(f"  Address: {address}")

        # Two rounds of fund-then-sweep
        for round_num in range(1, 3):
            num_funds = round_num + 2  # round 1: 3 UTXOs, round 2: 4 UTXOs
            fund_btc = "0.3"
            fund_sats = 30_000_000

            # Fund N times
            for i in range(num_funds):
                node.fund_address(address, fund_btc)
            total_funded = fund_sats * num_funds
            print(f"  Round {round_num}: funded {num_funds}x {fund_btc} BTC "
                  f"= {total_funded:,} sats")

            # Verify UTXO count
            utxos = api_post(base_url, "/api/utxos", {
                "address": address, "network": "regtest",
            })
            assert utxos["success"], f"{label}: UTXO lookup failed: {utxos}"
            assert utxos["count"] == num_funds, \
                f"{label} round {round_num}: expected {num_funds} UTXOs, " \
                f"got {utxos['count']}"
            assert utxos["total_sat"] == total_funded, \
                f"{label} round {round_num}: expected {total_funded} sats, " \
                f"got {utxos['total_sat']}"

            # Sweep all
            dest_addr = node.get_new_address()
            sweep = api_post(base_url, "/api/sweep", {
                "wif": wif, "address_type": cfg["addr_type"],
                "network": "regtest",
                "dest_address": dest_addr, "fee_rate": fee_rate,
            })
            assert sweep["success"], \
                f"{label} round {round_num}: Sweep failed: {sweep}"

            txid = sweep["txid"]
            amount_sat = sweep["amount_sat"]
            fee_sat = sweep["fee_sat"]
            vsize = sweep["vsize"]

            # Verify vsize and fee
            expected_vsize = cfg["vsize_fn"](num_funds)
            assert vsize == expected_vsize, \
                f"{label} round {round_num}: expected vsize " \
                f"{expected_vsize}, got {vsize}"
            expected_fee = expected_vsize * fee_rate
            assert fee_sat == expected_fee, \
                f"{label} round {round_num}: expected fee " \
                f"{expected_fee}, got {fee_sat}"
            expected_amount = total_funded - expected_fee
            assert amount_sat == expected_amount, \
                f"{label} round {round_num}: expected amount " \
                f"{expected_amount}, got {amount_sat}"

            print(f"  Round {round_num}: swept {num_funds} UTXOs → "
                  f"{amount_sat:,} sats (fee={fee_sat}, vsize={vsize})")

            # Mine and verify on-chain
            node.mine(1)
            confs = node.confirm_tx(txid, dest_addr, amount_sat)
            assert confs >= 1, \
                f"{label} round {round_num}: expected ≥1 conf, got {confs}"

            # Verify address is now empty
            utxos_after = api_post(base_url, "/api/utxos", {
                "address": address, "network": "regtest",
            })
            assert utxos_after["count"] == 0, \
                f"{label} round {round_num}: expected 0 UTXOs after sweep, " \
                f"got {utxos_after['count']}"

        print(f"  ✓ {label}: 2 rounds of multi-UTXO sweep verified")

    result.ok("E2E Repeated multi-UTXO sweep: "
              "3 address types × 2 rounds × fund-then-sweep → confirmed")


def test_repeated_multi_utxo_recover(node, base_url, result):
    """E2E: Fund Taproot+backup address N times → recover all → fund again → recover all.

    Repeats the fund-then-recover cycle twice to verify multi-input script-path
    recovery works correctly and that the address is reusable after being recovered.
    """
    print("\n=== E2E Test: Repeated Multi-UTXO Recovery ===")

    fee_rate = 2

    # Generate a Taproot+backup wallet
    gen = api_post(base_url, "/api/generate", {
        "type": "taproot", "backup": True, "network": "regtest",
    })
    assert gen["success"], f"Generate failed: {gen}"
    wallet = gen["wallet"]
    address = wallet["address"]
    backup_wif = wallet["backup_private_key_wif"]
    internal_pubkey_hex = wallet["internal_pubkey_hex"]
    print(f"  Address: {address}")

    # Verify derive matches
    derive = api_post(base_url, "/api/recover/derive", {
        "backup_wif": backup_wif,
        "internal_pubkey_hex": internal_pubkey_hex,
        "network": "regtest",
    })
    assert derive["success"], f"Recover derive failed: {derive}"
    assert derive["address"] == address, \
        f"Reconstructed {derive['address']} != generated {address}"

    # Two rounds of fund-then-recover
    for round_num in range(1, 3):
        num_funds = round_num + 2  # round 1: 3 UTXOs, round 2: 4 UTXOs
        fund_btc = "0.3"
        fund_sats = 30_000_000

        # Fund N times
        for i in range(num_funds):
            node.fund_address(address, fund_btc)
        total_funded = fund_sats * num_funds
        print(f"  Round {round_num}: funded {num_funds}x {fund_btc} BTC "
              f"= {total_funded:,} sats")

        # Verify UTXO count
        utxos = api_post(base_url, "/api/utxos", {
            "address": address, "network": "regtest",
        })
        assert utxos["success"], f"UTXO lookup failed: {utxos}"
        assert utxos["count"] == num_funds, \
            f"Round {round_num}: expected {num_funds} UTXOs, " \
            f"got {utxos['count']}"
        assert utxos["total_sat"] == total_funded, \
            f"Round {round_num}: expected {total_funded} sats, " \
            f"got {utxos['total_sat']}"

        # Recover all via script-path
        dest_addr = node.get_new_address()
        recover = api_post(base_url, "/api/recover", {
            "backup_wif": backup_wif,
            "internal_pubkey_hex": internal_pubkey_hex,
            "network": "regtest",
            "dest_address": dest_addr, "fee_rate": fee_rate,
        })
        assert recover["success"], \
            f"Round {round_num}: Recover failed: {recover}"

        txid = recover["txid"]
        amount_sat = recover["amount_sat"]
        fee_sat = recover["fee_sat"]
        vsize = recover["vsize"]

        # Verify vsize and fee (script-path: 11 + N*107 + 43)
        expected_vsize = 11 + num_funds * 107 + 43
        assert vsize == expected_vsize, \
            f"Round {round_num}: expected vsize " \
            f"{expected_vsize}, got {vsize}"
        expected_fee = expected_vsize * fee_rate
        assert fee_sat == expected_fee, \
            f"Round {round_num}: expected fee " \
            f"{expected_fee}, got {fee_sat}"
        expected_amount = total_funded - expected_fee
        assert amount_sat == expected_amount, \
            f"Round {round_num}: expected amount " \
            f"{expected_amount}, got {amount_sat}"

        print(f"  Round {round_num}: recovered {num_funds} UTXOs → "
              f"{amount_sat:,} sats (fee={fee_sat}, vsize={vsize})")

        # Mine and verify on-chain
        node.mine(1)
        confs = node.confirm_tx(txid, dest_addr, amount_sat)
        assert confs >= 1, \
            f"Round {round_num}: expected ≥1 conf, got {confs}"

        # Verify address is now empty
        utxos_after = api_post(base_url, "/api/utxos", {
            "address": address, "network": "regtest",
        })
        assert utxos_after["count"] == 0, \
            f"Round {round_num}: expected 0 UTXOs after recover, " \
            f"got {utxos_after['count']}"

    result.ok("E2E Repeated multi-UTXO recovery: "
              "2 rounds × fund-then-recover (script-path) → confirmed")


def test_chained_sweep_then_recover(node, base_url, result):
    """E2E: Faucet → Address1, sweep Address1 → Address2, recover Address2 → Address3.

    Chains funds through 3 Taproot+backup addresses:
      Address 1 (funded via faucet) --sweep(tweaked WIF)--> Address 2
      Address 2 --recover(backup key)--> Address 3
    """
    print("\n=== E2E Test: Chained Sweep → Recovery ===")

    # 1. Generate 3 Taproot+backup wallets
    wallets = []
    for i in range(3):
        gen = api_post(base_url, "/api/generate", {
            "type": "taproot", "backup": True, "network": "regtest",
        })
        assert gen["success"], f"Generate wallet {i+1} failed: {gen}"
        w = gen["wallet"]
        assert w.get("has_backup"), f"Wallet {i+1} missing backup"
        wallets.append(w)
        print(f"  Address {i+1}: {w['address']}")

    addr1 = wallets[0]["address"]
    addr2 = wallets[1]["address"]
    addr3 = wallets[2]["address"]

    # 2. Fund Address 1 via faucet API (1.0 BTC)
    faucet = api_post(base_url, "/api/faucet", {
        "address": addr1, "amount": "1.0",
    })
    assert faucet["success"], f"Faucet failed: {faucet}"
    print(f"  Faucet: {faucet['amount_btc']} BTC → Address 1 (txid={faucet['txid'][:16]}...)")

    # Verify UTXOs on Address 1
    utxos = api_post(base_url, "/api/utxos", {
        "address": addr1, "network": "regtest",
    })
    assert utxos["success"], f"UTXO lookup failed: {utxos}"
    assert utxos["count"] >= 1, f"Expected ≥1 UTXO, got {utxos['count']}"
    assert utxos["total_sat"] == 100_000_000, \
        f"Expected 100000000 sats, got {utxos['total_sat']}"
    print(f"  Address 1 balance: {utxos['total_sat']} sats ({utxos['count']} UTXO)")

    # 3. Sweep Address 1 → Address 2 (key-path via tweaked WIF)
    bill_wif_1 = wallets[0]["bill_wif"]
    fee_rate = 2
    sweep = api_post(base_url, "/api/sweep", {
        "wif": bill_wif_1, "address_type": "taproot_tweaked",
        "network": "regtest",
        "dest_address": addr2, "fee_rate": fee_rate,
    })
    assert sweep["success"], f"Sweep failed: {sweep}"
    sweep_txid = sweep["txid"]
    sweep_amount = sweep["amount_sat"]
    sweep_fee = sweep["fee_sat"]
    print(f"  Sweep: Address 1 → Address 2: {sweep_amount} sats "
          f"(fee={sweep_fee}, txid={sweep_txid[:16]}...)")

    # Mine and verify sweep on-chain
    node.mine(1)
    confs = node.confirm_tx(sweep_txid, addr2, sweep_amount)
    print(f"  Sweep confirmed: {confs} conf")

    # 4. Recover Address 2 → Address 3 (script-path via backup key)
    backup_wif_2 = wallets[1]["backup_private_key_wif"]
    internal_pubkey_2 = wallets[1]["internal_pubkey_hex"]

    # Derive via recover API — verify reconstructed address matches
    derive = api_post(base_url, "/api/recover/derive", {
        "backup_wif": backup_wif_2,
        "internal_pubkey_hex": internal_pubkey_2,
        "network": "regtest",
    })
    assert derive["success"], f"Recover derive failed: {derive}"
    assert derive["address"] == addr2, \
        f"Reconstructed {derive['address']} != expected {addr2}"
    print(f"  Recover derive verified: {derive['address']}")

    recover = api_post(base_url, "/api/recover", {
        "backup_wif": backup_wif_2,
        "internal_pubkey_hex": internal_pubkey_2,
        "network": "regtest",
        "dest_address": addr3, "fee_rate": fee_rate,
    })
    assert recover["success"], f"Recover failed: {recover}"
    recover_txid = recover["txid"]
    recover_amount = recover["amount_sat"]
    recover_fee = recover["fee_sat"]
    print(f"  Recover: Address 2 → Address 3: {recover_amount} sats "
          f"(fee={recover_fee}, txid={recover_txid[:16]}...)")

    # Mine and verify recovery on-chain
    node.mine(1)
    confs = node.confirm_tx(recover_txid, addr3, recover_amount)
    print(f"  Recover confirmed: {confs} conf")

    # 5. Verify fee chain: total deducted = sweep_fee + recover_fee
    total_fees = sweep_fee + recover_fee
    expected_final = 100_000_000 - total_fees
    assert recover_amount == expected_final, \
        f"Final amount {recover_amount} != 100000000 - {total_fees} = {expected_final}"
    print(f"  Fee chain verified: 1.0 BTC - {total_fees} sats fees = {recover_amount} sats")

    result.ok("E2E Chained: faucet → sweep(tweaked) → recover(backup) → confirmed")


# ============================================================
# Main
# ============================================================

def main():
    # Check that bitcoin-cli and bitcoind are available
    for binary in ["bitcoind", "bitcoin-cli"]:
        if shutil.which(binary) is None:
            print(f"ERROR: '{binary}' not found in PATH.")
            print("Install Bitcoin Core and ensure bitcoind/bitcoin-cli are in your PATH.")
            print("  macOS: brew install bitcoin")
            print("  Linux: see https://bitcoincore.org/en/download/")
            sys.exit(1)

    print("=" * 60)
    print("Bitcoin Paper Wallet — End-to-End API Tests")
    print("=" * 60)

    node = RegtestNode()
    test_result = TestResult()
    httpd = None

    tests = [
        test_segwit_sweep,
        test_taproot_no_backup_sweep,
        test_taproot_backup_sweep,
        test_taproot_backup_recover,
        test_multi_utxo_sweep,
        test_repeated_multi_utxo_sweep,
        test_repeated_multi_utxo_recover,
        test_chained_sweep_then_recover,
    ]

    try:
        # Start regtest node
        node.start()

        # Expose regtest node to server module (enables /api/faucet & auto-mine)
        server_module._regtest_node = node

        # Start HTTP server on a dynamic port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        httpd = ReusableTCPServer(("127.0.0.1", port), WalletHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        print(f"  HTTP server started on {base_url}")

        # Run tests
        for test_fn in tests:
            try:
                test_fn(node, base_url, test_result)
            except Exception as e:
                test_result.fail(test_fn.__name__, str(e))
                traceback.print_exc()

    except Exception as e:
        print(f"\n  Fatal error during setup: {e}")
        traceback.print_exc()
    finally:
        server_module._regtest_node = None
        if httpd:
            httpd.shutdown()
            print("  HTTP server stopped.")
        node.stop()

    success = test_result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
