#!/usr/bin/env python3
"""
Playwright UI test for Bitcoin Gift Wallet — chained sweep and recovery.

Tests the full browser UI flow across 4 pages:
  1. Generate 3 Taproot+backup wallets via the Generator page
  2. Fund Address 1 via the Faucet page (1.0 BTC)
  3. Sweep Address 1 → Address 2 via the Sweep page (tweaked WIF key-path)
  4. Recover Address 2 → Address 3 via the Recovery page (backup key script-path)
  5. Verify the fee chain: 1.0 BTC - sweep_fee - recover_fee = final_amount

Requires:
  - Bitcoin Core (bitcoind + bitcoin-cli) installed and in PATH
  - Python Playwright: pip install playwright && playwright install chromium

Usage:
    python3 test_ui_playwright.py              # headless (CI/CD default)
    python3 test_ui_playwright.py --headed     # visible browser for debugging
"""

import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen
from urllib.error import URLError


# ============================================================
# Configuration
# ============================================================

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
SCREENSHOT_DIR = os.path.join(_PROJECT_ROOT, "test-screenshots")
SERVER_READY_TIMEOUT = 90   # seconds (bitcoind mines 101 blocks at startup)
ACTION_TIMEOUT = 30_000     # ms — for button clicks that trigger API calls
STEP_TIMEOUT = 15_000       # ms — for card transitions


# ============================================================
# Server lifecycle
# ============================================================

def find_free_port():
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    """Start server.py --regtest as a subprocess, wait for HTTP readiness.

    Uses os.setsid() to create a new process group so that both the server
    and its child bitcoind can be cleaned up together on shutdown.
    """
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_PROJECT_ROOT, "server", "server.py"), str(port), "--regtest"],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,  # creates new process group (pgid = proc.pid)
    )
    # Poll HTTP endpoint until server is ready
    for i in range(SERVER_READY_TIMEOUT):
        try:
            urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except (URLError, ConnectionRefusedError, OSError):
            time.sleep(1)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"Server exited prematurely (rc={proc.returncode})\n{stderr}")
    proc.kill()
    raise RuntimeError(
        f"Server failed to become ready within {SERVER_READY_TIMEOUT}s")


def stop_server(proc):
    """Gracefully stop the server and its child bitcoind process.

    Sends SIGINT first (triggers KeyboardInterrupt → clean shutdown with
    bitcoind stop + temp dir cleanup). Falls back to killing the entire
    process group if graceful shutdown times out.
    """
    if not proc or proc.poll() is not None:
        return

    pgid = os.getpgid(proc.pid)

    # 1. Send SIGINT → triggers server's KeyboardInterrupt handler
    #    which calls _regtest_node.stop() (stops bitcoind, cleans datadir)
    try:
        os.kill(proc.pid, signal.SIGINT)
        proc.wait(timeout=20)
        print("  Server stopped gracefully.")
        return
    except (subprocess.TimeoutExpired, OSError):
        pass

    # 2. SIGINT didn't work — kill the entire process group
    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=10)
        print("  Server process group terminated.")
        return
    except (subprocess.TimeoutExpired, OSError):
        pass

    # 3. Last resort — force kill the process group
    try:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait(timeout=5)
        print("  Server process group force-killed.")
    except (OSError, subprocess.TimeoutExpired):
        pass


# ============================================================
# Screenshot helper
# ============================================================

def screenshot(page, name):
    """Save a screenshot checkpoint."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    print(f"    📸 {name}.png")


# ============================================================
# DOM extraction helpers
# ============================================================

def extract_wallet_details(page):
    """Extract wallet details from #walletDetails after generation.

    Parses .detail-row elements, matching labels by prefix to handle
    unicode em-dash characters in label text.
    """
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

    assert address.startswith("bcrt1p"), \
        f"Expected bcrt1p... Taproot regtest address, got: {address}"
    assert bill_wif, "Failed to extract bill WIF from wallet details"

    return {
        "address": address,
        "bill_wif": bill_wif,
        "backup_wif": backup_wif,
        "internal_pubkey": internal_pubkey,
    }


def extract_result_data(page):
    """Extract transaction result from #resultBox (.info-label/.info-value pairs).

    Works for both sweep ("Amount Sent") and recovery ("Amount Recovered").
    """
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

    tip_text = data.get("Tip", "")
    tip_sat = int(tip_text.split(" sats")[0].replace(",", "")) if tip_text else 0

    return {
        "txid": data.get("Transaction ID", ""),
        "amount_sat": amount_sat,
        "fee_sat": fee_sat,
        "tip_sat": tip_sat,
        "dest_address": data.get("Destination", ""),
    }


# ============================================================
# Page interaction functions
# ============================================================

def generate_wallet(page, base_url, wallet_index):
    """Generate a Taproot+backup wallet on the Generator page."""
    page.goto(f"{base_url}/")
    page.wait_for_load_state("networkidle")

    # Select Taproot (default, but click to be sure)
    page.click("#optTaproot")

    # Enable backup checkbox
    backup_cb = page.locator("#backupCheckbox")
    if not backup_cb.is_checked():
        backup_cb.check()

    # Select regtest network
    page.select_option("#networkSelect", "regtest")

    # Click Generate
    page.click("#generateBtn")

    # Wait for results to appear
    page.wait_for_selector("#results.visible", timeout=ACTION_TIMEOUT)

    # Extract wallet details
    wallet = extract_wallet_details(page)
    screenshot(page, f"step{wallet_index + 1}_generate_wallet_{wallet_index + 1}")

    return wallet


def fund_via_faucet(page, base_url, address):
    """Fund an address with 1.0 BTC via the Faucet page."""
    page.goto(f"{base_url}/faucet.html")
    page.wait_for_load_state("networkidle")

    # Enter address
    page.fill("#addressInput", address)

    # Amount defaults to 1.0 — leave as-is

    # Click Fund
    page.click("#fundBtn")

    # Wait for result card
    page.wait_for_selector("#resultCard.visible", timeout=ACTION_TIMEOUT)

    # Verify result
    result_address = page.text_content("#resultAddress").strip()
    assert result_address == address, \
        f"Faucet result address mismatch: {result_address} != {address}"

    result_status = page.text_content("#resultStatus").strip()
    assert "Confirmed" in result_status, \
        f"Expected 'Confirmed' in faucet status, got: {result_status}"

    screenshot(page, "step4_fund_faucet")


def sweep_funds(page, base_url, bill_wif, expected_source, dest_addr):
    """Sweep from bill_wif to dest_addr via the Sweep page.

    Returns dict with txid, amount_sat, fee_sat, dest_address.
    """
    page.goto(f"{base_url}/sweep.html")
    page.wait_for_load_state("networkidle")

    # --- Step 1: Enter private key ---
    page.fill("#wifInput", bill_wif)

    # Select Taproot (tweaked)
    page.check('input[name="addrType"][value="taproot_tweaked"]')

    # Select regtest network
    page.select_option("#networkSelect", "regtest")

    # Click Derive Address
    page.click("#btnDerive")
    page.wait_for_selector("#cardStep2:not(.hidden)", timeout=STEP_TIMEOUT)

    # Verify derived address
    derived = page.text_content("#derivedAddress").strip()
    assert derived == expected_source, \
        f"Sweep: derived address mismatch: {derived} != {expected_source}"

    screenshot(page, "step5_sweep_derived")

    # --- Step 2: Check balance ---
    page.click("#btnCheckBalance")
    page.wait_for_selector("#cardStep3:not(.hidden)", timeout=STEP_TIMEOUT)

    screenshot(page, "step5_sweep_balance")

    # --- Step 3: Sweep ---
    page.fill("#destAddress", dest_addr)
    # Expand the collapsible fee rate section before filling
    page.click("#feeRateToggle")
    page.fill("#feeRate", "2")
    # Expand the tip section to verify the default 0.99% tip is active
    page.click("#tipToggle")
    active_tip = page.text_content('.tip-preset.active')
    assert active_tip and "0.99%" in active_tip, \
        f"Expected default 0.99% tip preset active, got: {active_tip}"

    page.click("#btnSweep")
    page.wait_for_selector("#cardResult:not(.hidden)", timeout=ACTION_TIMEOUT)

    # Verify "Transaction Confirmed"
    heading = page.text_content("#resultHeading").strip()
    assert "Confirmed" in heading, \
        f"Sweep: expected 'Confirmed', got: {heading}"

    result = extract_result_data(page)

    # Verify destination
    assert result["dest_address"] == dest_addr, \
        f"Sweep destination mismatch: {result['dest_address']} != {dest_addr}"

    screenshot(page, "step5_sweep_confirmed")
    return result


def recover_funds(page, base_url, backup_wif, internal_pubkey,
                  expected_source, dest_addr):
    """Recover via backup key to dest_addr via the Recovery page.

    Returns dict with txid, amount_sat, fee_sat, dest_address.
    """
    page.goto(f"{base_url}/recover.html")
    page.wait_for_load_state("networkidle")

    # --- Step 1: Enter backup key details ---
    page.fill("#backupWifInput", backup_wif)
    page.fill("#internalPubkeyInput", internal_pubkey)

    # Select regtest network
    page.select_option("#networkSelect", "regtest")

    # Click Reconstruct Address
    page.click("#btnDerive")
    page.wait_for_selector("#cardStep2:not(.hidden)", timeout=STEP_TIMEOUT)

    # Verify reconstructed address
    derived = page.text_content("#derivedAddress").strip()
    assert derived == expected_source, \
        f"Recover: reconstructed address mismatch: {derived} != {expected_source}"

    screenshot(page, "step6_recover_derived")

    # --- Step 2: Check balance ---
    page.click("#btnCheckBalance")
    page.wait_for_selector("#cardStep3:not(.hidden)", timeout=STEP_TIMEOUT)

    screenshot(page, "step6_recover_balance")

    # --- Step 3: Recover ---
    page.fill("#destAddress", dest_addr)
    page.fill("#feeRate", "2")

    page.click("#btnRecover")
    page.wait_for_selector("#cardResult:not(.hidden)", timeout=ACTION_TIMEOUT)

    # Verify "Transaction Confirmed"
    heading = page.text_content("#resultHeading").strip()
    assert "Confirmed" in heading, \
        f"Recover: expected 'Confirmed', got: {heading}"

    result = extract_result_data(page)

    # Verify destination
    assert result["dest_address"] == dest_addr, \
        f"Recover destination mismatch: {result['dest_address']} != {dest_addr}"

    screenshot(page, "step6_recover_confirmed")
    return result


# ============================================================
# Main test
# ============================================================

def main():
    # Check prerequisites
    for binary in ["bitcoind", "bitcoin-cli"]:
        if shutil.which(binary) is None:
            print(f"ERROR: '{binary}' not found in PATH. "
                  f"Install Bitcoin Core: brew install bitcoin")
            sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        print("  pip install playwright && playwright install chromium")
        sys.exit(1)

    headed = "--headed" in sys.argv

    print("=" * 60)
    print("Bitcoin Gift Wallet — Playwright UI Test")
    print("  Chained: Faucet → Sweep → Recovery")
    print("=" * 60)

    port = find_free_port()
    server_proc = None
    pw = None
    browser = None
    dialogs = []

    try:
        # Start regtest server
        print(f"\n--- Starting regtest server on port {port} ---")
        server_proc = start_server(port)
        base_url = f"http://127.0.0.1:{port}"
        print(f"  Server ready at {base_url}")

        # Launch browser
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page()

        # Register dialog handler (capture unexpected alert() errors)
        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()
        page.on("dialog", on_dialog)

        # ---- Step 1-3: Generate 3 Taproot+backup wallets ----
        print("\n--- Steps 1-3: Generate 3 Taproot+backup wallets ---")
        wallets = []
        for i in range(3):
            w = generate_wallet(page, base_url, i)
            wallets.append(w)
            print(f"  Wallet {i+1}: {w['address'][:20]}...{w['address'][-8:]}")

        addr1, addr2, addr3 = (w["address"] for w in wallets)

        # ---- Step 4: Fund Address 1 via Faucet ----
        print("\n--- Step 4: Fund Address 1 via Faucet (1.0 BTC) ---")
        fund_via_faucet(page, base_url, addr1)
        print(f"  Funded: 1.0 BTC → Address 1")

        # ---- Step 5: Sweep Address 1 → Address 2 ----
        print("\n--- Step 5: Sweep Address 1 → Address 2 ---")
        sweep_result = sweep_funds(
            page, base_url,
            wallets[0]["bill_wif"],
            addr1,
            addr2,
        )
        print(f"  Amount: {sweep_result['amount_sat']:,} sats")
        print(f"  Fee:    {sweep_result['fee_sat']:,} sats")
        print(f"  Tip:    {sweep_result['tip_sat']:,} sats")

        # ---- Step 6: Recover Address 2 → Address 3 ----
        print("\n--- Step 6: Recover Address 2 → Address 3 ---")
        recover_result = recover_funds(
            page, base_url,
            wallets[1]["backup_wif"],
            wallets[1]["internal_pubkey"],
            addr2,
            addr3,
        )
        print(f"  Amount: {recover_result['amount_sat']:,} sats")
        print(f"  Fee:    {recover_result['fee_sat']:,} sats")

        # ---- Step 7: Verify fee chain ----
        print("\n--- Step 7: Verify Fee Chain ---")
        initial_sats = 100_000_000
        sweep_fee = sweep_result["fee_sat"]
        sweep_tip = sweep_result["tip_sat"]
        recover_fee = recover_result["fee_sat"]
        total_deductions = sweep_fee + sweep_tip + recover_fee
        expected_final = initial_sats - total_deductions

        # Verify tip is ~0.99% of the funded amount
        expected_tip = int(initial_sats * 0.99 / 100)
        assert sweep_tip == expected_tip, \
            (f"Tip mismatch: expected {expected_tip} (0.99% of {initial_sats}), "
             f"got {sweep_tip}")

        assert recover_result["amount_sat"] == expected_final, \
            (f"Fee chain mismatch: expected {expected_final}, "
             f"got {recover_result['amount_sat']} "
             f"(1.0 BTC - {sweep_fee} fee - {sweep_tip} tip - {recover_fee} fee)")

        # Print summary table
        print(f"\n  {'Step':<10} {'From':<12} {'To':<12} "
              f"{'Amount':>15} {'Fee':>10} {'Tip':>10}")
        print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*15} {'-'*10} {'-'*10}")
        print(f"  {'Faucet':<10} {'coinbase':<12} {'Address 1':<12} "
              f"{'100,000,000':>15} {'—':>10} {'—':>10}")
        print(f"  {'Sweep':<10} {'Address 1':<12} {'Address 2':<12} "
              f"{sweep_result['amount_sat']:>15,} {sweep_fee:>10,} {sweep_tip:>10,}")
        print(f"  {'Recover':<10} {'Address 2':<12} {'Address 3':<12} "
              f"{recover_result['amount_sat']:>15,} {recover_fee:>10,} {'—':>10}")
        print(f"\n  Total deductions: {total_deductions:,} sats"
              f" (fees: {sweep_fee + recover_fee:,}, tip: {sweep_tip:,})")
        print(f"  Fee chain: 100,000,000 - {sweep_fee:,} - {sweep_tip:,}"
              f" - {recover_fee:,} = {expected_final:,} ✓")

        # Check for unexpected dialogs
        if dialogs:
            print(f"\n  ⚠️  Unexpected dialogs captured: {dialogs}")

        print(f"\n{'='*60}")
        print("PASS: All UI test steps completed successfully")
        print(f"{'='*60}")
        sys.exit(0)

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"FAIL: {e}")
        traceback.print_exc()
        if dialogs:
            print(f"\nCaptured dialogs: {dialogs}")
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
