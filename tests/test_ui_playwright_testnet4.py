#!/usr/bin/env python3
"""
Playwright UI test for Bitcoin Gift Wallet — Testnet4 sweep and return.

Tests the browser UI flow on Bitcoin Testnet4 via mempool.space:
  1. Sweep a pre-funded Testnet4 address → freshly generated address
  2. Return funds back to the original address (spends unconfirmed output)
  3. Verify fee chain

No confirmation waits — the test completes as soon as transactions are accepted
by the mempool. Explorer links are printed for manual verification.

Supports both SegWit (tb1q...) and Taproot tweaked (tb1p...) funded addresses.
The address type is auto-detected from the prefix.

Requires a pre-funded Testnet4 address. Pass the WIF and address as CLI
arguments or set TESTNET4_WIF / TESTNET4_ADDRESS environment variables.

Usage:
    python3 test_ui_playwright_testnet4.py --wif "cXXX..." --address "tb1q..."
    python3 test_ui_playwright_testnet4.py --wif "cXXX..." --address "tb1p..." --headed

    # Or via environment variables:
    export TESTNET4_WIF="cXXX..."
    export TESTNET4_ADDRESS="tb1q..."
    python3 test_ui_playwright_testnet4.py
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen, Request
from urllib.error import URLError

import json


# ============================================================
# Configuration
# ============================================================

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
SCREENSHOT_DIR = os.path.join(_PROJECT_ROOT, "test-screenshots")
SERVER_READY_TIMEOUT = 30    # seconds (no bitcoind startup, just HTTP server)
ACTION_TIMEOUT = 30_000      # ms — for button clicks that trigger API calls
STEP_TIMEOUT = 15_000        # ms — for card transitions
MEMPOOL_PROPAGATION_WAIT = 10  # seconds to wait for mempool propagation
RECOVERY_FILE = os.path.join(_PROJECT_ROOT, "test-screenshots", "testnet4_recovery.json")


# ============================================================
# Address type detection
# ============================================================

def detect_address_type(address):
    """Detect address type from prefix.

    Returns the sweep page radio button value:
      - "segwit" for tb1q... (SegWit P2WPKH)
      - "taproot_tweaked" for tb1p... (Taproot with tweaked key)
    """
    if address.startswith("tb1q"):
        return "segwit"
    elif address.startswith("tb1p"):
        return "taproot_tweaked"
    else:
        raise ValueError(f"Unknown testnet4 address type: {address[:10]}...")


# ============================================================
# Server lifecycle
# ============================================================

def find_free_port():
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    """Start server.py (standard mode, no --regtest) as a subprocess."""
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_PROJECT_ROOT, "server", "server.py"), str(port)],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Poll HTTP endpoint until server is ready
    for i in range(SERVER_READY_TIMEOUT):
        try:
            urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except (URLError, ConnectionRefusedError, OSError):
            time.sleep(1)
        if proc.poll() is not None:
            raise RuntimeError(
                f"Server exited prematurely (rc={proc.returncode})")
    proc.kill()
    raise RuntimeError(
        f"Server failed to become ready within {SERVER_READY_TIMEOUT}s")


def stop_server(proc):
    """Stop the server subprocess."""
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ============================================================
# Screenshot helper
# ============================================================

def screenshot(page, name):
    """Save a screenshot checkpoint."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"testnet4_{name}.png")
    page.screenshot(path=path)
    print(f"    📸 testnet4_{name}.png")


# ============================================================
# DOM extraction helpers
# ============================================================

def extract_wallet_details(page):
    """Extract wallet details from #walletDetails after generation."""
    details = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#walletDetails .detail-row');
            const result = {};
            rows.forEach(row => {
                const label = row.querySelector('.label')?.textContent?.trim() || '';
                const value = row.querySelector('.value')?.textContent?.trim() || '';
                if (label) result[label] = value;
            });
            return result;
        }
    """)

    def find_by_prefix(prefix):
        for label, value in details.items():
            if label.startswith(prefix):
                return value
        return None

    address = details.get("Address", "")
    bill_wif = find_by_prefix("Tweaked Private Key (WIF)")
    backup_wif = find_by_prefix("Backup Private Key (WIF)")
    internal_pubkey = find_by_prefix("Internal Public Key")

    assert address.startswith("tb1p"), \
        f"Expected tb1p... Taproot testnet4 address, got: {address}"
    assert bill_wif, "Failed to extract bill WIF from wallet details"

    return {
        "address": address,
        "bill_wif": bill_wif,
        "backup_wif": backup_wif,
        "internal_pubkey": internal_pubkey,
    }


def extract_result_data(page):
    """Extract transaction result from #resultBox (.info-label/.info-value pairs)."""
    data = page.evaluate("""
        () => {
            const box = document.getElementById('resultBox');
            const labels = box.querySelectorAll('.info-label');
            const values = box.querySelectorAll('.info-value');
            const result = {};
            for (let i = 0; i < labels.length; i++) {
                result[labels[i].textContent.trim()] = values[i].textContent.trim();
            }
            return result;
        }
    """)

    # Parse "99,999,776 sats (0.99999776 BTC)" → integer sats
    amount_text = data.get("Amount Sent") or data.get("Amount Recovered", "")
    amount_sat = int(amount_text.split(" sats")[0].replace(",", ""))

    fee_text = data.get("Fee", "")
    fee_sat = int(fee_text.split(" sats")[0].replace(",", ""))

    return {
        "txid": data.get("Transaction ID", ""),
        "amount_sat": amount_sat,
        "fee_sat": fee_sat,
        "dest_address": data.get("Destination", ""),
    }


# ============================================================
# Pre-flight balance check (runs before browser launch)
# ============================================================

MIN_BALANCE_SATS = 1_000  # minimum 1000 sats needed to cover sweep + return fees


def preflight_balance_check(address):
    """Query mempool.space testnet4 API for UTXOs (confirmed or unconfirmed).

    Returns total balance in sats.
    Raises SystemExit if address has no spendable UTXOs.
    """
    url = f"https://mempool.space/testnet4/api/address/{address}/utxo"
    print(f"\n--- Pre-flight: checking balance of {address[:20]}... ---")

    try:
        req = Request(url, headers={"User-Agent": "BitcoinGiftWallet/1.0"})
        resp = urlopen(req, timeout=15)
        utxos = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ERROR: Could not reach mempool.space testnet4 API: {e}")
        print(f"  Check: https://mempool.space/testnet4/address/{address}")
        sys.exit(1)

    if not utxos:
        print(f"  ✗ No UTXOs found (confirmed or unconfirmed).")
        print(f"\n  Please fund this address with testnet4 tBTC:")
        print(f"    https://mempool.space/testnet4/faucet")
        print(f"    Address: {address}")
        sys.exit(1)

    # Count confirmed vs unconfirmed
    confirmed = [u for u in utxos if u.get("status", {}).get("confirmed", False)]
    unconfirmed = [u for u in utxos if not u.get("status", {}).get("confirmed", False)]
    total_sats = sum(u.get("value", 0) for u in utxos)
    total_btc = total_sats / 1e8

    if total_sats < MIN_BALANCE_SATS:
        print(f"  ✗ Balance too low: {total_sats:,} sats ({total_btc:.8f} BTC)")
        print(f"    Minimum required: {MIN_BALANCE_SATS:,} sats "
              f"(to cover sweep + return fees)")
        print(f"\n  Please fund this address with more testnet4 tBTC:")
        print(f"    https://mempool.space/testnet4/faucet")
        print(f"    Address: {address}")
        sys.exit(1)

    conf_label = f"{len(confirmed)} confirmed" if confirmed else ""
    unconf_label = f"{len(unconfirmed)} unconfirmed" if unconfirmed else ""
    utxo_desc = " + ".join(filter(None, [conf_label, unconf_label]))

    print(f"  ✓ Balance: {total_sats:,} sats ({total_btc:.8f} BTC) "
          f"across {utxo_desc} UTXO(s)")
    return total_sats



# ============================================================
# Page interaction functions
# ============================================================

def generate_testnet4_wallet(page, base_url):
    """Generate a Taproot+backup wallet on Testnet4 via the Generator page."""
    page.goto(f"{base_url}/")
    page.wait_for_load_state("networkidle")

    # Select Taproot
    page.click("#optTaproot")

    # Enable backup checkbox
    backup_cb = page.locator("#backupCheckbox")
    if not backup_cb.is_checked():
        backup_cb.check()

    # Select testnet4 network
    page.select_option("#networkSelect", "testnet4")

    # Click Generate
    page.click("#generateBtn")

    # Wait for results
    page.wait_for_selector("#results.visible", timeout=ACTION_TIMEOUT)

    wallet = extract_wallet_details(page)
    screenshot(page, "generate_dest_wallet")
    return wallet


def sweep_testnet4(page, base_url, wif, expected_source, dest_addr,
                   addr_type, label):
    """Sweep from wif to dest_addr on Testnet4 via the Sweep page.

    Args:
        addr_type: "segwit", "taproot", or "taproot_tweaked" — radio button value

    Returns dict with txid, amount_sat, fee_sat, dest_address.
    """
    page.goto(f"{base_url}/sweep.html")
    page.wait_for_load_state("networkidle")

    # --- Step 1: Enter private key ---
    page.fill("#wifInput", wif)
    page.check(f'input[name="addrType"][value="{addr_type}"]')
    page.select_option("#networkSelect", "testnet4")

    page.click("#btnDerive")
    page.wait_for_selector("#cardStep2:not(.hidden)", timeout=STEP_TIMEOUT)

    # Verify derived address
    derived = page.text_content("#derivedAddress").strip()
    assert derived == expected_source, \
        f"{label}: derived address mismatch: {derived} != {expected_source}"

    screenshot(page, f"{label}_derived")

    # --- Step 2: Check balance ---
    page.click("#btnCheckBalance")
    page.wait_for_selector("#cardStep3:not(.hidden)", timeout=STEP_TIMEOUT)

    screenshot(page, f"{label}_balance")

    # --- Step 3: Sweep ---
    page.fill("#destAddress", dest_addr)
    page.fill("#feeRate", "2")

    page.click("#btnSweep")
    page.wait_for_selector("#cardResult:not(.hidden)", timeout=ACTION_TIMEOUT)

    # On testnet4, the result may say "Broadcast" instead of "Confirmed"
    heading = page.text_content("#resultHeading").strip()
    assert "Broadcast" in heading or "Confirmed" in heading, \
        f"{label}: expected 'Broadcast' or 'Confirmed', got: {heading}"

    result = extract_result_data(page)

    assert result["dest_address"] == dest_addr, \
        f"{label} destination mismatch: {result['dest_address']} != {dest_addr}"

    screenshot(page, f"{label}_result")
    return result


# ============================================================
# Main test
# ============================================================

def recover_funds():
    """Recover funds from a failed/timed-out test run using the recovery file.

    Reads dest_wif and funded_address from the recovery file, then sweeps
    dest_address → funded_address via the sweep page.
    """
    if not os.path.exists(RECOVERY_FILE):
        print("ERROR: No recovery file found.")
        print(f"  Expected: {RECOVERY_FILE}")
        print("  This file is created during a test run and removed on success.")
        sys.exit(1)

    with open(RECOVERY_FILE) as f:
        data = json.load(f)

    dest_address = data["dest_address"]
    dest_wif = data["dest_wif"]
    dest_addr_type = data["dest_addr_type"]
    funded_address = data["funded_address"]

    print("=" * 60)
    print("Bitcoin Gift Wallet — Testnet4 RECOVERY MODE")
    print(f"  Recovering funds from: {dest_address[:20]}...{dest_address[-8:]}")
    print(f"  Returning to:          {funded_address[:20]}...{funded_address[-8:]}")
    print("=" * 60)

    # Check that destination has funds
    preflight_balance_check(dest_address)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        sys.exit(1)

    port = find_free_port()
    server_proc = None
    pw = None
    browser = None

    try:
        print(f"\n--- Starting server on port {port} ---")
        server_proc = start_server(port)
        base_url = f"http://127.0.0.1:{port}"
        print(f"  Server ready at {base_url}")

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda d: d.dismiss())

        print("\n--- Sweeping funds back to original address ---")
        result = sweep_testnet4(
            page, base_url,
            dest_wif, dest_address, funded_address,
            dest_addr_type, "recovery",
        )
        print(f"  TXID:   {result['txid']}")
        print(f"  Amount: {result['amount_sat']:,} sats")
        print(f"  Fee:    {result['fee_sat']:,} sats")

        # Clean up recovery file
        os.remove(RECOVERY_FILE)
        print(f"\n{'='*60}")
        print(f"RECOVERED: {result['amount_sat']:,} sats returned to "
              f"{funded_address[:20]}...")
        print(f"  Explorer: https://mempool.space/testnet4/tx/{result['txid']}")
        print(f"{'='*60}")
        sys.exit(0)

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"RECOVERY FAILED: {e}")
        traceback.print_exc()
        print(f"{'='*60}")
        sys.exit(1)

    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()
        stop_server(server_proc)


def main():
    parser = argparse.ArgumentParser(
        description="Playwright Testnet4 UI test for Bitcoin Gift Wallet")
    parser.add_argument("--wif",
                        default=os.environ.get("TESTNET4_WIF"),
                        help="WIF for the pre-funded testnet4 address "
                             "(or set TESTNET4_WIF env var)")
    parser.add_argument("--address",
                        default=os.environ.get("TESTNET4_ADDRESS"),
                        help="Pre-funded testnet4 address "
                             "(or set TESTNET4_ADDRESS env var)")
    parser.add_argument("--headed", action="store_true",
                        help="Run browser in visible (headed) mode")
    parser.add_argument("--recover", action="store_true",
                        help="Recover funds from a failed test run using "
                             "the saved recovery file")
    args = parser.parse_args()

    # Recovery mode: sweep funds back from a previous failed run
    if args.recover:
        recover_funds()
        return

    funded_wif = args.wif
    funded_address = args.address

    if not funded_wif or not funded_address:
        print("ERROR: --wif and --address are required.")
        print("  Pass as CLI args or set TESTNET4_WIF / TESTNET4_ADDRESS env vars.")
        sys.exit(1)

    # Detect address type from prefix
    if not (funded_address.startswith("tb1q") or
            funded_address.startswith("tb1p")):
        print(f"ERROR: Expected tb1q... or tb1p... address, "
              f"got: {funded_address}")
        sys.exit(1)

    funded_addr_type = detect_address_type(funded_address)
    addr_type_label = "SegWit" if funded_addr_type == "segwit" else "Taproot (tweaked)"

    # Check for leftover recovery file from a previous failed run
    if os.path.exists(RECOVERY_FILE):
        print("WARNING: Recovery file exists from a previous failed run.")
        print(f"  File: {RECOVERY_FILE}")
        print("  Run with --recover to return those funds first, or delete")
        print("  the file if you've already handled it.")
        sys.exit(1)

    # Pre-flight: verify the address has funds before launching browser/server
    preflight_balance_check(funded_address)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        print("  pip install playwright && playwright install chromium")
        sys.exit(1)

    print("=" * 60)
    print("Bitcoin Gift Wallet — Testnet4 Playwright UI Test")
    print("  Sweep → Return (funds go back to original address)")
    print(f"  Funded address: {funded_address[:20]}...{funded_address[-8:]}")
    print(f"  Address type:   {addr_type_label}")
    print("=" * 60)

    port = find_free_port()
    server_proc = None
    pw = None
    browser = None
    dialogs = []

    try:
        # Start server (standard mode, no --regtest)
        print(f"\n--- Starting server on port {port} ---")
        server_proc = start_server(port)
        base_url = f"http://127.0.0.1:{port}"
        print(f"  Server ready at {base_url}")

        # Launch browser
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page()

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()
        page.on("dialog", on_dialog)

        # ---- Step 1: Generate destination wallet ----
        print("\n--- Step 1: Generate Testnet4 destination wallet ---")
        dest_wallet = generate_testnet4_wallet(page, base_url)
        dest_address = dest_wallet["address"]
        dest_wif = dest_wallet["bill_wif"]
        print(f"  Destination: {dest_address[:20]}...{dest_address[-8:]}")

        # Save recovery info so funds can be returned if the test
        # crashes or times out waiting for confirmation.
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        recovery_data = {
            "funded_address": funded_address,
            "funded_wif": funded_wif,
            "funded_addr_type": funded_addr_type,
            "dest_address": dest_address,
            "dest_wif": dest_wif,
            "dest_addr_type": "taproot_tweaked",
            "note": "Use dest_wif to sweep dest_address back to funded_address",
        }
        with open(RECOVERY_FILE, "w") as f:
            json.dump(recovery_data, f, indent=2)
        print(f"  Recovery file saved: {RECOVERY_FILE}")

        # ---- Step 2: Sweep funded → destination ----
        print(f"\n--- Step 2: Sweep funded address ({addr_type_label}) "
              f"→ destination ---")
        sweep_result = sweep_testnet4(
            page, base_url,
            funded_wif, funded_address, dest_address,
            funded_addr_type, "sweep",
        )
        print(f"  TXID:   {sweep_result['txid']}")
        print(f"  Amount: {sweep_result['amount_sat']:,} sats")
        print(f"  Fee:    {sweep_result['fee_sat']:,} sats")

        # Brief wait for mempool propagation before spending unconfirmed output
        print(f"\n  Waiting {MEMPOOL_PROPAGATION_WAIT}s for mempool propagation...")
        time.sleep(MEMPOOL_PROPAGATION_WAIT)

        # ---- Step 3: Return funds — sweep destination → original ----
        # Destination is always Taproot+backup (tweaked), spends unconfirmed output
        print(f"\n--- Step 3: Return funds → original address ---")
        return_result = sweep_testnet4(
            page, base_url,
            dest_wif, dest_address, funded_address,
            "taproot_tweaked", "return",
        )
        print(f"  TXID:   {return_result['txid']}")
        print(f"  Amount: {return_result['amount_sat']:,} sats")
        print(f"  Fee:    {return_result['fee_sat']:,} sats")

        # ---- Step 4: Verify fee chain ----
        print("\n--- Step 4: Verify Fee Chain ---")
        sweep_fee = sweep_result["fee_sat"]
        return_fee = return_result["fee_sat"]
        total_fees = sweep_fee + return_fee

        # The return amount should be sweep_amount - return_fee
        expected_return = sweep_result["amount_sat"] - return_fee
        assert return_result["amount_sat"] == expected_return, \
            (f"Fee chain mismatch: expected {expected_return}, "
             f"got {return_result['amount_sat']}")

        # Print summary table
        print(f"\n  {'Step':<10} {'From':<12} {'To':<12} "
              f"{'Amount':>15} {'Fee':>10}")
        print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*15} {'-'*10}")
        print(f"  {'Sweep':<10} {'funded':<12} {'generated':<12} "
              f"{sweep_result['amount_sat']:>15,} {sweep_fee:>10,}")
        print(f"  {'Return':<10} {'generated':<12} {'funded':<12} "
              f"{return_result['amount_sat']:>15,} {return_fee:>10,}")
        print(f"\n  Total fees: {total_fees:,} sats")
        print(f"  Funds returned to: {funded_address[:20]}...")

        # Explorer links for manual verification
        sweep_url = (f"https://mempool.space/testnet4/tx/"
                     f"{sweep_result['txid']}")
        return_url = (f"https://mempool.space/testnet4/tx/"
                      f"{return_result['txid']}")
        print(f"\n  Transactions:")
        print(f"    Sweep:  {sweep_url}")
        print(f"    Return: {return_url}")

        # Clean up recovery file — funds are safely back
        if os.path.exists(RECOVERY_FILE):
            os.remove(RECOVERY_FILE)
            print("\n  Recovery file cleaned up (funds returned successfully)")

        if dialogs:
            print(f"\n  ⚠️  Unexpected dialogs captured: {dialogs}")

        print(f"\n{'='*60}")
        print("PASS: Testnet4 UI test completed successfully")
        print(f"{'='*60}")
        sys.exit(0)

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"FAIL: {e}")
        traceback.print_exc()
        if dialogs:
            print(f"\nCaptured dialogs: {dialogs}")
        if os.path.exists(RECOVERY_FILE):
            print(f"\n  💡 Funds may be stuck at the destination address.")
            print(f"     Run with --recover to sweep them back:")
            print(f"     python3 test_ui_playwright_testnet4.py --recover")
        print(f"{'='*60}")
        sys.exit(1)

    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()
        stop_server(server_proc)


if __name__ == "__main__":
    main()
