"""
Web server for Bitcoin Gift Wallet Generator.
Serves the HTML frontend and handles API requests for address generation,
bill creation, sweep (recipient), and recovery (giver backup).
"""

import json
import io
import os
import resource
import shutil
import subprocess
import sys
import struct
import tempfile
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from bitcoin_crypto import (
    generate_segwit_address,
    generate_taproot_address,
    private_key_to_wif,
    wif_to_private_key,
    derive_taproot_address_from_tweaked_privkey,
    derive_segwit_address_from_privkey,
    taproot_tweak_seckey,
    build_signed_taproot_sweep_tx,
    build_signed_segwit_sweep_tx,
    build_signed_taproot_scriptpath_sweep_tx,
    compute_script_tree_hash_for_backup,
    taproot_tweak_pubkey,
    private_key_to_xonly_pubkey,
    private_key_to_public_key,
    hash160,
    bech32_encode,
    _network_hrp,
    _lift_x,
)
from bill_generator import generate_bill_image, bill_to_base64, bill_to_png_bytes

# Resolve file paths relative to this script's directory
_DIR = os.path.dirname(os.path.abspath(__file__))

# Global regtest node — set when running with --regtest
_regtest_node = None


# ============================================================
# Managed regtest node (started with --regtest flag)
# ============================================================

class RegtestNode:
    """Manage a Bitcoin Core regtest node for local development/testing."""

    def __init__(self):
        self.datadir = tempfile.mkdtemp(prefix="btc_regtest_")
        self.process = None
        self.rpc_port = 18443
        self.wallet_name = "giftwallet"

    def _cli(self, *args, wallet=None, timeout=30):
        """Run bitcoin-cli with managed node credentials."""
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
            stdin=subprocess.DEVNULL,
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
                f"bitcoin-cli {' '.join(args)} failed (rc={proc.returncode}): "
                f"{stderr_str}"
            )
        return stdout_str

    def start(self):
        """Start bitcoind in regtest mode with a funded wallet."""
        print(f"  Starting bitcoind (datadir: {self.datadir})...")

        # Detect version
        try:
            ver_out = subprocess.run(
                ["bitcoind", "--version"], capture_output=True, text=True,
                timeout=10,
            ).stdout
            print(f"  {ver_out.strip().splitlines()[0]}")
        except Exception:
            pass

        # Write bitcoin.conf
        conf_path = os.path.join(self.datadir, "bitcoin.conf")
        with open(conf_path, "w") as f:
            f.write("regtest=1\nserver=1\ntxindex=1\n")
            f.write("rpcuser=test\nrpcpassword=test\n")
            f.write("[regtest]\n")
            f.write(f"rpcport={self.rpc_port}\n")
            f.write("fallbackfee=0.00001\n")

        # macOS fix: concrete file descriptor limit (unlimited = -1 crashes bitcoind)
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft == resource.RLIM_INFINITY or soft < 1024:
            resource.setrlimit(resource.RLIMIT_NOFILE, (4096, hard))

        # Start bitcoind (foreground, devnull to prevent pipe buffer deadlock)
        self.process = subprocess.Popen(
            ["bitcoind", f"-datadir={self.datadir}", "-regtest", "-daemon=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Wait for ready
        for i in range(30):
            try:
                info = self._cli("getblockchaininfo", timeout=10)
                if "regtest" in info:
                    print("  bitcoind is ready.")
                    break
            except RuntimeError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("bitcoind failed to start within 30 seconds")

        # Create descriptor wallet (handle stale state gracefully)
        try:
            self._cli("-named", "createwallet",
                      f"wallet_name={self.wallet_name}",
                      "descriptors=true")
            print("  Created descriptor wallet.")
        except RuntimeError as e:
            if "already exists" in str(e):
                try:
                    self._cli("loadwallet", self.wallet_name)
                    print("  Loaded existing wallet.")
                except RuntimeError as e2:
                    if "already loaded" in str(e2):
                        print("  Wallet already loaded.")
                    else:
                        raise
            else:
                raise

        # Mine initial blocks (101 for mature coinbase)
        mining_addr = self._cli("getnewaddress", wallet=self.wallet_name)
        self._cli("generatetoaddress", "101", mining_addr,
                  wallet=self.wallet_name)
        print("  Mined 101 blocks (coinbase mature).")

    def stop(self):
        """Stop bitcoind and clean up temp datadir."""
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
        """Fund an address: create tx → sign → broadcast → mine 1 block."""
        outputs_json = json.dumps([{address: float(amount_btc)}])
        raw_hex = self._cli("createrawtransaction", "[]", outputs_json,
                            wallet=self.wallet_name)
        funded_json = self._cli("fundrawtransaction", raw_hex,
                                wallet=self.wallet_name)
        funded = json.loads(funded_json)
        signed_json = self._cli("signrawtransactionwithwallet", funded["hex"],
                                wallet=self.wallet_name)
        signed = json.loads(signed_json)
        if not signed.get("complete"):
            raise RuntimeError(f"Signing incomplete: {signed}")
        txid = self._cli("sendrawtransaction", signed["hex"])
        # Mine to confirm
        self.mine(1)
        return txid

    def mine(self, blocks=1):
        """Mine blocks to confirm pending transactions."""
        mining_addr = self._cli("getnewaddress", wallet=self.wallet_name)
        self._cli("generatetoaddress", str(blocks), mining_addr,
                  wallet=self.wallet_name)

    def get_new_address(self):
        """Get a fresh address from the regtest wallet."""
        return self._cli("getnewaddress", wallet=self.wallet_name)


# ============================================================
# UTXO lookup and broadcast helpers
# ============================================================

def _mempool_base_url(network):
    """Return the mempool.space base API URL for the given network."""
    if network == "testnet4":
        return "https://mempool.space/testnet4/api"
    elif network == "signet":
        return "https://mempool.space/signet/api"
    else:
        return "https://mempool.space/api"


def _fetch_utxos_mempool(address, network):
    """Fetch UTXOs from mempool.space API (mainnet/testnet/signet)."""
    url = f"{_mempool_base_url(network)}/address/{address}/utxo"
    req = Request(url, headers={"User-Agent": "BitcoinGiftWallet/1.0"})
    resp = urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    utxos = []
    for u in data:
        utxos.append({
            "txid": u["txid"],
            "vout": u["vout"],
            "value_sat": u["value"],
            "confirmed": u.get("status", {}).get("confirmed", False),
        })
    return utxos


def _fetch_utxos_regtest(address):
    """Fetch UTXOs from a local regtest node using scantxoutset."""
    try:
        result = subprocess.run(
            ["bitcoin-cli", "-regtest",
             "-rpcuser=test", "-rpcpassword=test", "-rpcport=18443",
             "scantxoutset", "start", json.dumps([f"addr({address})"])],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(f"bitcoin-cli error: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        utxos = []
        for u in data.get("unspents", []):
            utxos.append({
                "txid": u["txid"],
                "vout": u["vout"],
                "value_sat": int(round(u["amount"] * 1e8)),
                "confirmed": True,
            })
        return utxos
    except FileNotFoundError:
        raise RuntimeError("bitcoin-cli not found. Is Bitcoin Core installed?")


def fetch_utxos(address, network):
    """Fetch UTXOs for an address on the specified network."""
    if network == "regtest":
        return _fetch_utxos_regtest(address)
    else:
        return _fetch_utxos_mempool(address, network)


def _broadcast_mempool(raw_hex, network):
    """Broadcast a raw transaction via mempool.space API."""
    url = f"{_mempool_base_url(network)}/tx"
    req = Request(url, data=raw_hex.encode("utf-8"),
                  headers={"Content-Type": "text/plain"},
                  method="POST")
    resp = urlopen(req, timeout=15)
    txid = resp.read().decode("utf-8").strip()
    return txid


def _broadcast_regtest(raw_hex):
    """Broadcast a raw transaction to local regtest node.

    If the managed regtest node is running, auto-mines 1 block so the
    transaction is immediately confirmed (otherwise regtest txs stay
    in mempool forever).
    """
    result = subprocess.run(
        ["bitcoin-cli", "-regtest",
         "-rpcuser=test", "-rpcpassword=test", "-rpcport=18443",
         "sendrawtransaction", raw_hex],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(f"Broadcast failed: {result.stderr.strip()}")
    txid = result.stdout.strip()

    # Auto-mine so the tx is confirmed immediately
    if _regtest_node:
        try:
            _regtest_node.mine(1)
        except Exception:
            pass  # non-fatal — tx is broadcast even if mine fails

    return txid


def broadcast_tx(raw_hex, network):
    """Broadcast a raw transaction on the specified network."""
    if network == "regtest":
        return _broadcast_regtest(raw_hex)
    else:
        return _broadcast_mempool(raw_hex, network)


def _explorer_url(txid, network):
    """Return a block explorer URL for the txid."""
    if network == "regtest":
        return None
    elif network == "testnet4":
        return f"https://mempool.space/testnet4/tx/{txid}"
    elif network == "signet":
        return f"https://mempool.space/signet/tx/{txid}"
    else:
        return f"https://mempool.space/tx/{txid}"


# ============================================================
# Request handler
# ============================================================

class WalletHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for the wallet generator."""

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_file("index.html", "text/html")
        elif parsed.path == "/sweep.html":
            self._serve_file("sweep.html", "text/html")
        elif parsed.path == "/recover.html":
            self._serve_file("recover.html", "text/html")
        elif parsed.path == "/faucet.html":
            self._serve_file("faucet.html", "text/html")
        elif parsed.path == "/api/health":
            self._handle_health()
        elif parsed.path == "/api/generate":
            self._handle_generate(parse_qs(parsed.query))
        elif parsed.path == "/api/download":
            self._handle_download(parse_qs(parsed.query))
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_json_body()
        if body is None:
            return  # error already sent

        if parsed.path == "/api/generate":
            self._handle_generate_post(body)
        elif parsed.path == "/api/download":
            self._handle_download_post(body)
        elif parsed.path == "/api/utxos":
            self._handle_utxos(body)
        elif parsed.path == "/api/sweep/derive":
            self._handle_sweep_derive(body)
        elif parsed.path == "/api/sweep":
            self._handle_sweep(body)
        elif parsed.path == "/api/recover/derive":
            self._handle_recover_derive(body)
        elif parsed.path == "/api/recover":
            self._handle_recover(body)
        elif parsed.path == "/api/faucet":
            self._handle_faucet(body)
        elif parsed.path == "/api/mine":
            self._handle_mine(body)
        elif parsed.path == "/api/broadcast":
            self._handle_broadcast(body)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self):
        """Read and parse JSON request body. Returns dict or sends error."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
            return None

    def _serve_file(self, filename, content_type):
        filepath = os.path.join(_DIR, filename)
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)

    def _send_json(self, data, status=200):
        response = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response)

    def _send_png(self, png_bytes, filename="bitcoin_bill.png"):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", len(png_bytes))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(png_bytes)

    # ------------------------------------------------------------------
    # Wallet generation
    # ------------------------------------------------------------------

    def _handle_generate(self, params):
        addr_type = params.get("type", ["taproot"])[0]
        backup = params.get("backup", ["false"])[0].lower() == "true"
        network = params.get("network", ["mainnet"])[0]
        self._do_generate(addr_type, backup, network)

    def _handle_generate_post(self, params):
        addr_type = params.get("type", "taproot")
        backup = params.get("backup", False)
        network = params.get("network", "mainnet")
        self._do_generate(addr_type, backup, network)

    def _do_generate(self, addr_type, backup, network):
        try:
            if addr_type == "segwit":
                wallet = generate_segwit_address(network=network)
            elif addr_type == "taproot":
                wallet = generate_taproot_address(network=network, backup_key=backup)
            else:
                self._send_json({"error": f"Unknown address type: {addr_type}"}, 400)
                return

            # Determine which WIF to print on the bill.
            #   - SegWit / Taproot (no backup): untweaked private key WIF
            #     (standard wallets can import and derive the correct address)
            #   - Taproot WITH backup: tweaked private key WIF + "(tweaked)" label
            #     (the tweaked key allows key-path spending; recipient uses sweep page)
            is_tweaked = False
            bill_wif = wallet["private_key_wif"]

            if addr_type == "taproot" and wallet.get("has_backup"):
                tweaked_hex = wallet["tweaked_private_key_hex"]
                bill_wif = private_key_to_wif(
                    bytes.fromhex(tweaked_hex), network=network)
                is_tweaked = True
                wallet["bill_wif"] = bill_wif

            bill = generate_bill_image(
                wallet["address"],
                bill_wif,
                addr_type,
                is_tweaked=is_tweaked,
            )
            bill_b64 = bill_to_base64(bill)

            response = {
                "success": True,
                "wallet": wallet,
                "bill_image": f"data:image/png;base64,{bill_b64}",
                "is_tweaked": is_tweaked,
            }

            self._send_json(response)

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # Bill download
    # ------------------------------------------------------------------

    def _handle_download(self, params):
        address = params.get("address", [None])[0]
        wif = params.get("wif", [None])[0]
        is_tweaked = params.get("tweaked", ["false"])[0].lower() == "true"

        if not address or not wif:
            self._send_json({"error": "Missing address or wif parameter"}, 400)
            return

        try:
            bill = generate_bill_image(address, wif, is_tweaked=is_tweaked)
            png = bill_to_png_bytes(bill)
            self._send_png(png, f"bitcoin_bill_{address[:12]}.png")
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_download_post(self, params):
        address = params.get("address")
        wif = params.get("wif")
        is_tweaked = params.get("tweaked", False)

        if not address or not wif:
            self._send_json({"error": "Missing address or wif parameter"}, 400)
            return

        try:
            bill = generate_bill_image(address, wif, is_tweaked=is_tweaked)
            png = bill_to_png_bytes(bill)
            self._send_png(png, f"bitcoin_bill_{address[:12]}.png")
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # UTXO lookup (shared by sweep and recover pages)
    # ------------------------------------------------------------------

    def _handle_utxos(self, params):
        """Look up UTXOs for an address on the specified network.
        POST /api/utxos  {address, network}
        """
        address = params.get("address")
        network = params.get("network", "mainnet")

        if not address:
            self._send_json({"error": "Missing address"}, 400)
            return

        try:
            utxos = fetch_utxos(address, network)
            total_sat = sum(u["value_sat"] for u in utxos)
            self._send_json({
                "success": True,
                "address": address,
                "utxos": utxos,
                "total_sat": total_sat,
                "count": len(utxos),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # Sweep: recipient spends from the bill's private key
    # ------------------------------------------------------------------

    def _handle_sweep_derive(self, params):
        """Derive the address from a WIF key (preview for verification).
        POST /api/sweep/derive  {wif, address_type, network}
        address_type: "segwit" | "taproot" | "taproot_tweaked"
        """
        wif = params.get("wif")
        address_type = params.get("address_type", "taproot_tweaked")
        network = params.get("network", "mainnet")

        if not wif:
            self._send_json({"error": "Missing wif"}, 400)
            return

        try:
            key_info = wif_to_private_key(wif)
            privkey = key_info["private_key"]

            if address_type == "segwit":
                result = derive_segwit_address_from_privkey(privkey, network=network)
                self._send_json({
                    "success": True,
                    "address": result["address"],
                    "address_type": "segwit",
                })
            elif address_type == "taproot":
                # Untweaked key (no backup bill) → need to compute key-only tweak
                tweaked_key = taproot_tweak_seckey(privkey, script_tree_hash=None)
                result = derive_taproot_address_from_tweaked_privkey(
                    tweaked_key, network=network)
                self._send_json({
                    "success": True,
                    "address": result["address"],
                    "address_type": "taproot",
                })
            elif address_type == "taproot_tweaked":
                # Tweaked key (backup bill) → derive directly
                result = derive_taproot_address_from_tweaked_privkey(
                    privkey, network=network)
                self._send_json({
                    "success": True,
                    "address": result["address"],
                    "address_type": "taproot_tweaked",
                })
            else:
                self._send_json(
                    {"error": f"Unknown address_type: {address_type}"}, 400)

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_sweep(self, params):
        """Build, sign, and broadcast a sweep transaction.
        POST /api/sweep  {wif, address_type, network, dest_address, fee_rate}
        """
        wif = params.get("wif")
        address_type = params.get("address_type", "taproot_tweaked")
        network = params.get("network", "mainnet")
        dest_address = params.get("dest_address")
        fee_rate = params.get("fee_rate", 10)  # sat/vB

        if not wif or not dest_address:
            self._send_json({"error": "Missing wif or dest_address"}, 400)
            return

        try:
            key_info = wif_to_private_key(wif)
            privkey = key_info["private_key"]
            fee_rate = int(fee_rate)

            # Derive source address and scriptPubKey
            if address_type == "segwit":
                addr_info = derive_segwit_address_from_privkey(
                    privkey, network=network)
                source_address = addr_info["address"]
                input_spk = addr_info["scriptpubkey"]
                signing_key = privkey
            elif address_type == "taproot":
                tweaked_key = taproot_tweak_seckey(privkey, script_tree_hash=None)
                addr_info = derive_taproot_address_from_tweaked_privkey(
                    tweaked_key, network=network)
                source_address = addr_info["address"]
                input_spk = addr_info["scriptpubkey"]
                signing_key = tweaked_key
            elif address_type == "taproot_tweaked":
                addr_info = derive_taproot_address_from_tweaked_privkey(
                    privkey, network=network)
                source_address = addr_info["address"]
                input_spk = addr_info["scriptpubkey"]
                signing_key = privkey
            else:
                self._send_json(
                    {"error": f"Unknown address_type: {address_type}"}, 400)
                return

            # Fetch UTXOs
            utxos = fetch_utxos(source_address, network)
            if not utxos:
                self._send_json(
                    {"error": f"No UTXOs found at {source_address}"}, 400)
                return

            total_sat = sum(u["value_sat"] for u in utxos)
            n_inputs = len(utxos)

            # Estimate vsize and fee
            if address_type == "segwit":
                vsize = 11 + n_inputs * 69 + 31
            else:
                vsize = 11 + n_inputs * 58 + 43
            fee_sat = vsize * fee_rate

            dest_value = total_sat - fee_sat
            if dest_value <= 0:
                self._send_json(
                    {"error": f"Insufficient funds: {total_sat} sats, "
                              f"fee would be {fee_sat} sats"}, 400)
                return

            # Build and sign
            if address_type == "segwit":
                raw_hex = build_signed_segwit_sweep_tx(
                    signing_key, utxos, dest_address, dest_value)
            else:
                raw_hex = build_signed_taproot_sweep_tx(
                    signing_key, utxos, input_spk, dest_address, dest_value)

            # Broadcast
            txid = broadcast_tx(raw_hex, network)
            explorer = _explorer_url(txid, network)

            self._send_json({
                "success": True,
                "txid": txid,
                "source_address": source_address,
                "dest_address": dest_address,
                "amount_sat": dest_value,
                "fee_sat": fee_sat,
                "vsize": vsize,
                "explorer_url": explorer,
                "raw_hex": raw_hex,
            })

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # Recover: giver spends via backup key (script path)
    # ------------------------------------------------------------------

    def _handle_recover_derive(self, params):
        """Reconstruct the original address from backup key + internal pubkey.
        POST /api/recover/derive  {backup_wif, internal_pubkey_hex, network}
        """
        backup_wif = params.get("backup_wif")
        internal_pubkey_hex = params.get("internal_pubkey_hex")
        network = params.get("network", "mainnet")

        if not backup_wif or not internal_pubkey_hex:
            self._send_json(
                {"error": "Missing backup_wif or internal_pubkey_hex"}, 400)
            return

        try:
            key_info = wif_to_private_key(backup_wif)
            backup_privkey = key_info["private_key"]

            # Derive backup x-only pubkey
            backup_pubkey_x, _ = private_key_to_xonly_pubkey(backup_privkey)
            internal_pubkey_x = bytes.fromhex(internal_pubkey_hex)

            if len(internal_pubkey_x) != 32:
                self._send_json(
                    {"error": "Internal pubkey must be 32 bytes (64 hex chars)"},
                    400)
                return

            # Validate the internal pubkey is a valid curve point
            # (catch common mistake: pasting the private key hex instead)
            if _lift_x(internal_pubkey_x) is None:
                self._send_json(
                    {"error": "Invalid internal public key — this value "
                     "is not a valid point on the secp256k1 curve. "
                     "Make sure you copied the \"Internal Public Key\" "
                     "(not the \"Internal Private Key (Hex)\" which "
                     "looks similar)."},
                    400)
                return

            # Reconstruct the script tree hash and tweaked output key
            script_tree_hash = compute_script_tree_hash_for_backup(backup_pubkey_x)
            output_pubkey_x, parity = taproot_tweak_pubkey(
                internal_pubkey_x, script_tree_hash)

            hrp = _network_hrp(network)
            address = bech32_encode(hrp, 1, list(output_pubkey_x), spec="bech32m")

            self._send_json({
                "success": True,
                "address": address,
                "backup_pubkey_hex": backup_pubkey_x.hex(),
                "internal_pubkey_hex": internal_pubkey_hex,
                "output_parity": parity,
            })

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_recover(self, params):
        """Build, sign, and broadcast a script-path recovery transaction.
        POST /api/recover  {backup_wif, internal_pubkey_hex, network,
                            dest_address, fee_rate}
        """
        backup_wif = params.get("backup_wif")
        internal_pubkey_hex = params.get("internal_pubkey_hex")
        network = params.get("network", "mainnet")
        dest_address = params.get("dest_address")
        fee_rate = params.get("fee_rate", 10)

        if not backup_wif or not internal_pubkey_hex or not dest_address:
            self._send_json(
                {"error": "Missing backup_wif, internal_pubkey_hex, "
                          "or dest_address"}, 400)
            return

        try:
            key_info = wif_to_private_key(backup_wif)
            backup_privkey = key_info["private_key"]
            fee_rate = int(fee_rate)

            # Derive backup x-only pubkey and reconstruct address
            backup_pubkey_x, _ = private_key_to_xonly_pubkey(backup_privkey)
            internal_pubkey_x = bytes.fromhex(internal_pubkey_hex)

            script_tree_hash = compute_script_tree_hash_for_backup(backup_pubkey_x)
            output_pubkey_x, parity = taproot_tweak_pubkey(
                internal_pubkey_x, script_tree_hash)

            hrp = _network_hrp(network)
            source_address = bech32_encode(
                hrp, 1, list(output_pubkey_x), spec="bech32m")

            input_spk = bytes([0x51, 0x20]) + output_pubkey_x

            # Fetch UTXOs
            utxos = fetch_utxos(source_address, network)
            if not utxos:
                self._send_json(
                    {"error": f"No UTXOs found at {source_address}"}, 400)
                return

            total_sat = sum(u["value_sat"] for u in utxos)
            n_inputs = len(utxos)

            # Script-path vsize is larger (witness includes script + control block)
            vsize = 11 + n_inputs * 107 + 43
            fee_sat = vsize * fee_rate

            dest_value = total_sat - fee_sat
            if dest_value <= 0:
                self._send_json(
                    {"error": f"Insufficient funds: {total_sat} sats, "
                              f"fee would be {fee_sat} sats"}, 400)
                return

            # Build and sign script-path sweep tx
            raw_hex = build_signed_taproot_scriptpath_sweep_tx(
                backup_privkey, backup_pubkey_x,
                internal_pubkey_x, parity,
                utxos, input_spk,
                dest_address, dest_value)

            # Broadcast
            txid = broadcast_tx(raw_hex, network)
            explorer = _explorer_url(txid, network)

            self._send_json({
                "success": True,
                "txid": txid,
                "source_address": source_address,
                "dest_address": dest_address,
                "amount_sat": dest_value,
                "fee_sat": fee_sat,
                "vsize": vsize,
                "explorer_url": explorer,
                "raw_hex": raw_hex,
            })

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------------------------------------------------------------------
    # Faucet: fund addresses on regtest
    # ------------------------------------------------------------------

    def _handle_faucet(self, params):
        """Fund an address on regtest via the managed node.
        POST /api/faucet  {address, amount}
        """
        if not _regtest_node:
            self._send_json(
                {"error": "Faucet requires --regtest mode"}, 400)
            return

        address = params.get("address")
        amount = params.get("amount", "1.0")

        if not address:
            self._send_json({"error": "Missing address"}, 400)
            return

        try:
            amount_str = str(float(amount))
            txid = _regtest_node.fund_address(address, amount_str)
            self._send_json({
                "success": True,
                "txid": txid,
                "address": address,
                "amount_btc": amount_str,
                "amount_sat": int(float(amount_str) * 1e8),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_mine(self, params):
        """Mine blocks on regtest.
        POST /api/mine  {blocks}
        """
        if not _regtest_node:
            self._send_json(
                {"error": "Mining requires --regtest mode"}, 400)
            return

        blocks = int(params.get("blocks", 1))
        if blocks < 1 or blocks > 100:
            self._send_json({"error": "blocks must be 1-100"}, 400)
            return

        try:
            _regtest_node.mine(blocks)
            self._send_json({
                "success": True,
                "blocks_mined": blocks,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_health(self):
        """GET /api/health — Server availability check for dual-mode pages."""
        self._send_json({
            "status": "ok",
            "regtest": _regtest_node is not None,
        })

    def _handle_broadcast(self, params):
        """POST /api/broadcast  {raw_hex, network}
        Broadcast a raw signed transaction.  Used by the client-side JS
        sweep/recover pages in dual-mode (regtest via server, mainnet/testnet4
        via mempool.space directly from the browser).
        """
        raw_hex = params.get("raw_hex", "")
        network = params.get("network", "regtest")

        if not raw_hex:
            self._send_json({"error": "raw_hex is required"}, 400)
            return

        try:
            txid = broadcast_tx(raw_hex, network)
            self._send_json({"txid": txid})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def log_message(self, format, *args):
        """Override to show cleaner logs."""
        sys.stderr.write(f"[Server] {args[0]}\n")


class ReusableTCPServer(HTTPServer):
    """HTTPServer subclass that sets SO_REUSEADDR to avoid 'Address already in use'."""
    allow_reuse_address = True
    allow_reuse_port = True


def run_server(port=8080, regtest=False):
    """Start the HTTP server, optionally with a managed regtest node."""
    global _regtest_node

    if regtest:
        # Check Bitcoin Core is installed
        for binary in ["bitcoind", "bitcoin-cli"]:
            if shutil.which(binary) is None:
                print(f"ERROR: '{binary}' not found in PATH.")
                print("Install Bitcoin Core: brew install bitcoin (macOS)")
                sys.exit(1)

        print("=" * 60)
        print("Starting Bitcoin Core regtest node...")
        print("=" * 60)
        _regtest_node = RegtestNode()
        _regtest_node.start()
        print()

    server = ReusableTCPServer(("0.0.0.0", port), WalletHandler)
    print(f"Bitcoin Gift Wallet Server running on http://localhost:{port}")
    print(f"  Generator: http://localhost:{port}/")
    print(f"  Sweep:     http://localhost:{port}/sweep.html")
    print(f"  Recover:   http://localhost:{port}/recover.html")
    if regtest:
        print(f"  Faucet:    http://localhost:{port}/faucet.html")
        print(f"\n  Mode: REGTEST (test coins, no real value)")
    print("\nPress Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
        if _regtest_node:
            _regtest_node.stop()
            _regtest_node = None
        print("Done.")


if __name__ == "__main__":
    # Parse args: [port] [--regtest]
    port = 8080
    regtest = False
    for arg in sys.argv[1:]:
        if arg == "--regtest":
            regtest = True
        else:
            try:
                port = int(arg)
            except ValueError:
                print(f"Unknown argument: {arg}")
                print("Usage: python3 server.py [port] [--regtest]")
                sys.exit(1)

    run_server(port, regtest=regtest)
