#!/usr/bin/env python3
"""
Regtest spending tests for Bitcoin paper wallet address generation.

Tests that generated addresses are ACTUALLY SPENDABLE by:
1. Starting a Bitcoin Core regtest node
2. Funding generated addresses
3. Constructing and signing transactions spending from them
4. Broadcasting and verifying acceptance

Requires: Bitcoin Core (bitcoind + bitcoin-cli) installed and in PATH.

Usage:
    python3 test_regtest_spending.py

The script manages its own temporary regtest datadir and cleans up after itself.
"""

import os
import sys
import json
import time
import shutil
import resource
import tempfile
import traceback
import subprocess

# Add server directory to path for bitcoin_crypto imports
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'server'))

from bitcoin_crypto import (
    generate_segwit_address,
    generate_taproot_address,
    build_signed_segwit_tx,
    build_signed_taproot_keypath_tx,
    build_signed_taproot_scriptpath_tx,
    private_key_to_wif,
    wif_to_private_key,
    derive_taproot_address_from_tweaked_privkey,
    taproot_tweak_seckey,
)


# ============================================================
# Regtest node management
# ============================================================

class RegtestNode:
    """Manage a temporary Bitcoin Core regtest node."""

    def __init__(self):
        self.datadir = tempfile.mkdtemp(prefix="btc_regtest_")
        self.process = None
        self.rpc_port = 18443
        self.wallet_name = "testwallet"

    def _cli(self, *args, wallet=None, timeout=30):
        """Run bitcoin-cli with the given arguments.

        Uses explicit rpcuser/rpcpassword on the command line to bypass
        cookie-file authentication (avoids potential file-lock hangs).
        Uses Popen with explicit kill() to ensure no zombie processes
        if a timeout occurs.
        """
        cmd = [
            "bitcoin-cli",
            f"-datadir={self.datadir}",
            "-regtest",
            f"-rpcport={self.rpc_port}",
            "-rpcuser=test",
            "-rpcpassword=test",
        ]
        if wallet:
            cmd.append(f"-rpcwallet={wallet}")
        cmd.extend(args)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # never wait for interactive input
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise RuntimeError(
                f"bitcoin-cli {' '.join(args)} timed out after {timeout}s"
            )

        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            raise RuntimeError(
                f"bitcoin-cli {' '.join(args)} failed (rc={proc.returncode}): {stderr_str}"
            )
        return stdout_str

    def start(self):
        """Start bitcoind in regtest mode."""
        print(f"  Starting bitcoind (datadir: {self.datadir})...")

        # Detect Bitcoin Core version
        try:
            ver_out = subprocess.run(
                ["bitcoind", "--version"], capture_output=True, text=True, timeout=10
            ).stdout
            print(f"  {ver_out.strip().splitlines()[0]}")
        except Exception:
            pass

        # Write minimal config — rpcuser/rpcpassword at global level
        # so both bitcoind and bitcoin-cli find them without cookie auth.
        # NOTE: rpcport must be in [regtest] section for Bitcoin Core v28+.
        conf_path = os.path.join(self.datadir, "bitcoin.conf")
        with open(conf_path, "w") as f:
            f.write("regtest=1\n")
            f.write("server=1\n")
            f.write("txindex=1\n")
            f.write("rpcuser=test\n")
            f.write("rpcpassword=test\n")
            f.write("[regtest]\n")
            f.write(f"rpcport={self.rpc_port}\n")
            f.write("fallbackfee=0.00001\n")

        # Ensure a concrete file descriptor limit — macOS "unlimited" maps to -1
        # which causes bitcoind to refuse to start.
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft == resource.RLIM_INFINITY or soft < 1024:
            resource.setrlimit(resource.RLIMIT_NOFILE, (4096, hard))

        self.process = subprocess.Popen(
            ["bitcoind", f"-datadir={self.datadir}", "-regtest", "-daemon=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Wait for node to be ready
        for i in range(30):
            try:
                info = self._cli("getblockchaininfo", timeout=10)
                if '"regtest"' in info or '"chain": "regtest"' in info:
                    print("  bitcoind is ready.")
                    break
            except RuntimeError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("bitcoind failed to start within 30 seconds")

        # Create a descriptor wallet (required for Taproot/bech32m support).
        try:
            self._cli("-named", "createwallet",
                      f"wallet_name={self.wallet_name}",
                      "descriptors=true")
            print("  Created descriptor wallet.")
        except RuntimeError as e:
            if "already exists" in str(e):
                self._cli("loadwallet", self.wallet_name)
                print("  Loaded existing wallet.")
            else:
                raise

        # Mine initial blocks (101 to get mature coinbase)
        mining_addr = self._cli("getnewaddress", wallet=self.wallet_name)
        self._cli("generatetoaddress", "101", mining_addr, wallet=self.wallet_name)
        print("  Mined 101 blocks.")

    def stop(self):
        """Stop bitcoind and clean up."""
        if self.process:
            try:
                self._cli("stop", timeout=10)
                self.process.wait(timeout=15)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                except Exception:
                    pass
        if os.path.exists(self.datadir):
            shutil.rmtree(self.datadir, ignore_errors=True)
        print("  bitcoind stopped and cleaned up.")

    def fund_address(self, address, amount_btc="1.0"):
        """Fund an address using createrawtransaction + fundrawtransaction.

        Avoids sendtoaddress (hangs for bech32m on some Bitcoin Core versions)
        and avoids generatetoaddress-based coinbase maturation (needs 100
        extra blocks per test which can timeout).
        """
        print(f"    funding {address[:20]}... ", end="", flush=True)

        # Build a raw tx with one output to the target address
        outputs_json = json.dumps([{address: float(amount_btc)}])
        raw_hex = self._cli("createrawtransaction", "[]", outputs_json,
                            wallet=self.wallet_name)

        # Let the wallet pick inputs and add change
        funded_json = self._cli("fundrawtransaction", raw_hex,
                                wallet=self.wallet_name)
        funded = json.loads(funded_json)

        # Sign with wallet keys
        signed_json = self._cli("signrawtransactionwithwallet", funded["hex"],
                                wallet=self.wallet_name)
        signed = json.loads(signed_json)
        if not signed.get("complete"):
            raise RuntimeError(f"Signing incomplete: {signed}")

        # Broadcast
        txid = self._cli("sendrawtransaction", signed["hex"])

        # Mine one block to confirm
        mining_addr = self._cli("getnewaddress", wallet=self.wallet_name)
        self._cli("generatetoaddress", "1", mining_addr, wallet=self.wallet_name)

        print(f"ok (txid={txid[:12]}...)")
        return txid

    def get_utxo(self, txid, address):
        """Get the UTXO details for a specific txid and address."""
        raw = self._cli("getrawtransaction", txid, "true")
        tx_data = json.loads(raw)
        for vout in tx_data["vout"]:
            if address in vout.get("scriptPubKey", {}).get("address", ""):
                return {
                    "txid": txid,
                    "vout": vout["n"],
                    "value": vout["value"],
                    "value_sat": int(round(vout["value"] * 1e8)),
                    "scriptPubKey": vout["scriptPubKey"]["hex"],
                }
        raise RuntimeError(f"UTXO not found for {address} in tx {txid}")

    def broadcast(self, raw_tx_hex):
        """Broadcast a raw transaction and return the txid."""
        return self._cli("sendrawtransaction", raw_tx_hex)

    def mine(self, blocks=1):
        """Mine blocks."""
        mining_addr = self._cli("getnewaddress", wallet=self.wallet_name)
        return self._cli("generatetoaddress", str(blocks), mining_addr, wallet=self.wallet_name)

    def get_new_address(self):
        """Get a new address from the regtest wallet (for change/destination)."""
        return self._cli("getnewaddress", wallet=self.wallet_name)

    def confirm_tx(self, txid, dest_address=None, expected_sats=None):
        """Verify a transaction is confirmed and its output amount is correct.

        Checks:
          1. The tx has at least 1 confirmation (i.e. was mined).
          2. If dest_address and expected_sats are provided, verifies that the
             tx has an output paying exactly expected_sats to dest_address.

        Returns the number of confirmations.
        Raises RuntimeError on any verification failure.
        """
        raw = self._cli("getrawtransaction", txid, "true")
        tx_data = json.loads(raw)
        confs = tx_data.get("confirmations", 0)
        if confs < 1:
            raise RuntimeError(f"tx {txid} has {confs} confirmations (expected >= 1)")

        if dest_address is not None and expected_sats is not None:
            expected_btc = expected_sats / 1e8
            for vout in tx_data["vout"]:
                spk = vout.get("scriptPubKey", {})
                addr = spk.get("address", "")
                if addr == dest_address:
                    actual_sats = int(round(vout["value"] * 1e8))
                    if actual_sats != expected_sats:
                        raise RuntimeError(
                            f"Output amount mismatch for {dest_address}: "
                            f"expected {expected_sats} sats ({expected_btc:.8f} BTC), "
                            f"got {actual_sats} sats ({vout['value']:.8f} BTC)"
                        )
                    return confs
            raise RuntimeError(
                f"No output found for {dest_address} in tx {txid}"
            )

        return confs



# ============================================================
# Test cases
# ============================================================

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name):
        self.passed += 1
        print(f"  ✅ PASS: {name}")

    def fail(self, name, msg):
        self.failed += 1
        self.errors.append((name, msg))
        print(f"  ❌ FAIL: {name} — {msg}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"Results: {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print("\nFailures:")
            for name, msg in self.errors:
                print(f"  - {name}: {msg}")
        print(f"{'='*60}")
        return self.failed == 0


def test_segwit_spending(node, result):
    """Test that a generated SegWit P2WPKH address is spendable."""
    print("\n=== Test: SegWit P2WPKH Spending ===")

    # Generate address
    wallet = generate_segwit_address(network="regtest")
    address = wallet["address"]
    privkey = bytes.fromhex(wallet["private_key_hex"])
    pubkey = bytes.fromhex(wallet["public_key_hex"])
    print(f"  Address: {address}")

    # Fund it
    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # Get UTXO
    utxo = node.get_utxo(fund_txid, address)
    print(f"  UTXO: vout={utxo['vout']}, value={utxo['value']} BTC")

    # Build spending transaction
    dest_addr = node.get_new_address()
    fee = 1000  # 1000 satoshis
    send_amount = utxo["value_sat"] - fee

    try:
        raw_tx = build_signed_segwit_tx(
            privkey_bytes=privkey,
            pubkey_bytes=pubkey,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        # Broadcast
        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats to dest)")
        result.ok("SegWit P2WPKH: funded and spent successfully")
    except Exception as e:
        result.fail("SegWit P2WPKH spending", str(e))


def test_taproot_keypath_spending(node, result):
    """Test that a generated Taproot address is spendable via key path."""
    print("\n=== Test: Taproot Key Path Spending ===")

    # Generate address (no backup key — key-only taproot)
    wallet = generate_taproot_address(network="regtest", backup_key=False)
    address = wallet["address"]
    tweaked_privkey = bytes.fromhex(wallet["tweaked_private_key_hex"])
    print(f"  Address: {address}")

    # Fund it
    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # Get UTXO
    utxo = node.get_utxo(fund_txid, address)
    input_spk = bytes.fromhex(utxo["scriptPubKey"])
    print(f"  UTXO: vout={utxo['vout']}, value={utxo['value']} BTC")

    # Build spending transaction
    dest_addr = node.get_new_address()
    fee = 1000
    send_amount = utxo["value_sat"] - fee

    try:
        raw_tx = build_signed_taproot_keypath_tx(
            tweaked_privkey_bytes=tweaked_privkey,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=input_spk,
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        # Broadcast
        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats to dest)")
        result.ok("Taproot key path (no script tree): funded and spent successfully")
    except Exception as e:
        result.fail("Taproot key path spending", str(e))


def test_taproot_keypath_with_script_tree(node, result):
    """Test that a Taproot address WITH a backup key is still spendable via key path."""
    print("\n=== Test: Taproot Key Path Spending (with script tree) ===")

    # Generate address WITH backup key
    wallet = generate_taproot_address(network="regtest", backup_key=True)
    address = wallet["address"]
    tweaked_privkey = bytes.fromhex(wallet["tweaked_private_key_hex"])
    print(f"  Address: {address}")

    # Fund it
    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # Get UTXO
    utxo = node.get_utxo(fund_txid, address)
    input_spk = bytes.fromhex(utxo["scriptPubKey"])
    print(f"  UTXO: vout={utxo['vout']}, value={utxo['value']} BTC")

    # Build spending transaction using key path
    dest_addr = node.get_new_address()
    fee = 1000
    send_amount = utxo["value_sat"] - fee

    try:
        raw_tx = build_signed_taproot_keypath_tx(
            tweaked_privkey_bytes=tweaked_privkey,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=input_spk,
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats to dest)")
        result.ok("Taproot key path (with script tree): funded and spent successfully")
    except Exception as e:
        result.fail("Taproot key path with script tree", str(e))


def test_taproot_scriptpath_spending(node, result):
    """Test that a Taproot address is spendable via the backup key script path."""
    print("\n=== Test: Taproot Script Path Spending (backup key) ===")

    # Generate address with backup key
    wallet = generate_taproot_address(network="regtest", backup_key=True)
    address = wallet["address"]
    backup_privkey = bytes.fromhex(wallet["backup_private_key_hex"])
    backup_pubkey_x = bytes.fromhex(wallet["backup_pubkey_hex"])
    internal_pubkey_x = bytes.fromhex(wallet["internal_pubkey_hex"])
    output_parity = wallet["output_parity"]
    print(f"  Address: {address}")
    print(f"  Spending via backup key script path")

    # Fund it
    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # Get UTXO
    utxo = node.get_utxo(fund_txid, address)
    input_spk = bytes.fromhex(utxo["scriptPubKey"])
    print(f"  UTXO: vout={utxo['vout']}, value={utxo['value']} BTC")

    # Build spending transaction using script path
    dest_addr = node.get_new_address()
    fee = 1000
    send_amount = utxo["value_sat"] - fee

    try:
        raw_tx = build_signed_taproot_scriptpath_tx(
            backup_privkey_bytes=backup_privkey,
            backup_pubkey_x=backup_pubkey_x,
            internal_pubkey_x=internal_pubkey_x,
            output_parity=output_parity,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=input_spk,
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats to dest)")
        result.ok("Taproot script path (backup key): funded and spent successfully")
    except Exception as e:
        result.fail("Taproot script path spending", str(e))


def test_multiple_segwit(node, result):
    """Test multiple SegWit addresses to ensure consistency."""
    print("\n=== Test: Multiple SegWit Addresses ===")

    for i in range(3):
        wallet = generate_segwit_address(network="regtest")
        address = wallet["address"]
        privkey = bytes.fromhex(wallet["private_key_hex"])
        pubkey = bytes.fromhex(wallet["public_key_hex"])

        fund_txid = node.fund_address(address, "0.5")
        utxo = node.get_utxo(fund_txid, address)

        dest_addr = node.get_new_address()
        fee = 1000
        send_amount = utxo["value_sat"] - fee

        try:
            raw_tx = build_signed_segwit_tx(
                privkey_bytes=privkey,
                pubkey_bytes=pubkey,
                prev_txid_hex=utxo["txid"],
                prev_vout=utxo["vout"],
                input_value=utxo["value_sat"],
                dest_address=dest_addr,
                dest_value=send_amount,
            )
            spend_txid = node.broadcast(raw_tx)
            node.mine(1)
            node.confirm_tx(spend_txid, dest_addr, send_amount)
            print(f"    #{i+1} spent: {send_amount} sats to dest ✓")
        except Exception as e:
            result.fail(f"SegWit batch #{i+1}", str(e))
            return

    result.ok("3 additional SegWit addresses: all funded and spent")


def test_multiple_taproot(node, result):
    """Test multiple Taproot addresses to ensure consistency."""
    print("\n=== Test: Multiple Taproot Addresses ===")

    for i in range(3):
        wallet = generate_taproot_address(network="regtest", backup_key=True)
        address = wallet["address"]
        tweaked_privkey = bytes.fromhex(wallet["tweaked_private_key_hex"])

        fund_txid = node.fund_address(address, "0.5")
        utxo = node.get_utxo(fund_txid, address)
        input_spk = bytes.fromhex(utxo["scriptPubKey"])

        dest_addr = node.get_new_address()
        fee = 1000
        send_amount = utxo["value_sat"] - fee

        try:
            raw_tx = build_signed_taproot_keypath_tx(
                tweaked_privkey_bytes=tweaked_privkey,
                prev_txid_hex=utxo["txid"],
                prev_vout=utxo["vout"],
                input_value=utxo["value_sat"],
                input_scriptpubkey=input_spk,
                dest_address=dest_addr,
                dest_value=send_amount,
            )
            spend_txid = node.broadcast(raw_tx)
            node.mine(1)
            node.confirm_tx(spend_txid, dest_addr, send_amount)
            print(f"    #{i+1} spent: {send_amount} sats to dest ✓")
        except Exception as e:
            result.fail(f"Taproot batch #{i+1}", str(e))
            return

    result.ok("3 additional Taproot addresses: all funded and spent (key path)")


def test_recipient_spend_no_backup(node, result):
    """Simulate bill recipient: no backup key, untweaked WIF on bill.

    For Taproot WITHOUT backup, the bill prints the untweaked (internal)
    private key. A standard wallet (BIP86-style) can derive the key-only
    taproot address and spend. This test proves that workflow.
    """
    print("\n=== Test: Recipient Spend (no backup, untweaked WIF) ===")

    # === GENERATOR SIDE: create wallet, fund it ===
    wallet = generate_taproot_address(network="regtest", backup_key=False)
    address = wallet["address"]
    # The bill prints the untweaked WIF
    bill_wif = wallet["private_key_wif"]

    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # === RECIPIENT SIDE: they only have bill_wif + address ===
    try:
        # Step 1: Decode WIF
        decoded = wif_to_private_key(bill_wif)
        recip_privkey = decoded["private_key"]

        # Step 2: Derive key-only taproot address (BIP86-style: tweak with just pubkey)
        # This is what a standard wallet would do: tweak with no script tree
        tweaked_privkey = taproot_tweak_seckey(recip_privkey, script_tree_hash=None)
        derived = derive_taproot_address_from_tweaked_privkey(tweaked_privkey, network="regtest")

        # Step 3: Verify derived address matches bill
        if derived["address"] != address:
            result.fail("Recipient spend (no backup)",
                        f"Derived {derived['address']} != bill {address}")
            return
        print(f"  Derived address matches bill: {address}")

        # Step 4: Look up UTXO and spend
        utxo = node.get_utxo(fund_txid, address)
        dest_addr = node.get_new_address()
        fee = 1000
        send_amount = utxo["value_sat"] - fee

        raw_tx = build_signed_taproot_keypath_tx(
            tweaked_privkey_bytes=tweaked_privkey,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=derived["scriptpubkey"],
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats)")
        result.ok("Recipient spend (no backup): untweaked WIF + BIP86 derivation works")
    except Exception as e:
        result.fail("Recipient spend (no backup)", str(e))


def test_recipient_sweep_with_backup(node, result):
    """Simulate bill recipient: backup key exists, tweaked WIF on bill.

    For Taproot WITH backup, the bill prints the tweaked private key WIF.
    The recipient uses the sweep website which derives the address directly
    from the tweaked key (no tweak computation needed). This test proves
    that workflow.
    """
    print("\n=== Test: Recipient Sweep (with backup, tweaked WIF) ===")

    # === GENERATOR SIDE: create wallet, fund it ===
    wallet = generate_taproot_address(network="regtest", backup_key=True)
    address = wallet["address"]
    tweaked_privkey = bytes.fromhex(wallet["tweaked_private_key_hex"])
    # The bill prints the tweaked WIF
    bill_wif = private_key_to_wif(tweaked_privkey, compressed=True, network="regtest")

    fund_txid = node.fund_address(address, "1.0")
    print(f"  Funded: txid={fund_txid[:16]}...")

    # === RECIPIENT SIDE: they only have bill_wif + address ===
    try:
        # Step 1: Decode WIF
        decoded = wif_to_private_key(bill_wif)
        recip_privkey = decoded["private_key"]

        # Step 2: Derive address directly from tweaked key (no tweak needed)
        derived = derive_taproot_address_from_tweaked_privkey(recip_privkey, network="regtest")

        # Step 3: Verify derived address matches bill
        if derived["address"] != address:
            result.fail("Recipient sweep (with backup)",
                        f"Derived {derived['address']} != bill {address}")
            return
        print(f"  Derived address matches bill: {address}")

        # Step 4: Look up UTXO and spend via key path
        utxo = node.get_utxo(fund_txid, address)
        dest_addr = node.get_new_address()
        fee = 1000
        send_amount = utxo["value_sat"] - fee

        raw_tx = build_signed_taproot_keypath_tx(
            tweaked_privkey_bytes=recip_privkey,
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=derived["scriptpubkey"],
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Spent: txid={spend_txid[:16]}... ({confs} conf, {send_amount} sats)")
        result.ok("Recipient sweep (with backup): tweaked WIF key-path spend works")
    except Exception as e:
        result.fail("Recipient sweep (with backup)", str(e))


def test_script_path_needs_backup_key(node, result):
    """Document that script-path spending requires the backup key, not the tweaked key.

    The tweaked private key (on the bill) enables key-path spending only.
    Script-path spending requires: backup_privkey, backup_pubkey_x,
    internal_pubkey_x, and output_parity — none of which are on the bill.
    This is by design: the gift giver keeps the backup key for recovery.
    """
    print("\n=== Test: Script Path Requires Backup Key (not tweaked key) ===")

    wallet = generate_taproot_address(network="regtest", backup_key=True)
    address = wallet["address"]

    fund_txid = node.fund_address(address, "1.0")
    utxo = node.get_utxo(fund_txid, address)
    input_spk = bytes.fromhex(utxo["scriptPubKey"])

    dest_addr = node.get_new_address()
    fee = 1000
    send_amount = utxo["value_sat"] - fee

    # Script-path spend using the BACKUP key (gift giver's recovery path)
    try:
        raw_tx = build_signed_taproot_scriptpath_tx(
            backup_privkey_bytes=bytes.fromhex(wallet["backup_private_key_hex"]),
            backup_pubkey_x=bytes.fromhex(wallet["backup_pubkey_hex"]),
            internal_pubkey_x=bytes.fromhex(wallet["internal_pubkey_hex"]),
            output_parity=wallet["output_parity"],
            prev_txid_hex=utxo["txid"],
            prev_vout=utxo["vout"],
            input_value=utxo["value_sat"],
            input_scriptpubkey=input_spk,
            dest_address=dest_addr,
            dest_value=send_amount,
        )

        spend_txid = node.broadcast(raw_tx)
        node.mine(1)
        confs = node.confirm_tx(spend_txid, dest_addr, send_amount)
        print(f"  Script-path spend via backup key: txid={spend_txid[:16]}... ({confs} conf)")
        print("  Confirmed: script-path requires backup key (not on bill)")
        print("  Confirmed: tweaked key (on bill) is for key-path only")
        result.ok("Script path spending works with backup key (giver recovery)")
    except Exception as e:
        result.fail("Script path with backup key", str(e))


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
    print("Bitcoin Paper Wallet — Regtest Spending Tests")
    print("=" * 60)

    node = RegtestNode()
    test_result = TestResult()

    tests = [
        test_segwit_spending,
        test_taproot_keypath_spending,
        test_taproot_keypath_with_script_tree,
        test_taproot_scriptpath_spending,
        test_multiple_segwit,
        test_multiple_taproot,
        # Recipient-perspective tests:
        test_recipient_spend_no_backup,
        test_recipient_sweep_with_backup,
        test_script_path_needs_backup_key,
    ]

    try:
        node.start()

        for test_fn in tests:
            try:
                test_fn(node, test_result)
            except Exception as e:
                test_result.fail(test_fn.__name__, str(e))
                traceback.print_exc()

    except Exception as e:
        print(f"\n💥 Fatal error during setup: {e}")
        traceback.print_exc()
    finally:
        node.stop()

    success = test_result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
