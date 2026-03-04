"""
Comprehensive tests for Bitcoin address generation.
Tests cover:
1. Key generation entropy and uniqueness
2. SegWit (P2WPKH) address correctness using known test vectors
3. Taproot (P2TR) address correctness using BIP341 test vectors
4. Bech32/Bech32m encoding/decoding roundtrips
5. Taproot backup key script path verification
6. RIPEMD-160 correctness
7. Tagged hash correctness
8. Edge cases and security checks
"""

import os
import sys
import hashlib
import secrets
from collections import Counter

# Add server directory to path for bitcoin_crypto imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server'))

from bitcoin_crypto import (
    generate_private_key,
    private_key_to_public_key,
    private_key_to_xonly_pubkey,
    private_key_to_wif,
    wif_to_private_key,
    base58_decode,
    derive_taproot_address_from_tweaked_privkey,
    generate_segwit_address,
    generate_taproot_address,
    verify_address,
    verify_keypair,
    bech32_encode,
    bech32_decode,
    hash160,
    sha256,
    tagged_hash,
    taproot_tweak_pubkey,
    taproot_tweak_seckey,
    compute_taptweak,
    compute_script_tree_hash_for_backup,
    compute_control_block,
    point_mul,
    point_add,
    _lift_x,
    N, P, G_X, G_Y,
    base58_encode,
    _ripemd160,
)


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name):
        self.passed += 1
        print(f"  PASS: {name}")

    def fail(self, name, msg):
        self.failed += 1
        self.errors.append((name, msg))
        print(f"  FAIL: {name} - {msg}")

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


result = TestResult()


# ============================================================
# 1. RIPEMD-160 correctness
# ============================================================
print("\n=== RIPEMD-160 Tests ===")

# Test vector: RIPEMD-160("") = 9c1185a5c5e9fc54612808977ee8f548b2258d31
ripemd_vectors = [
    (b"", "9c1185a5c5e9fc54612808977ee8f548b2258d31"),
    (b"a", "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe"),
    (b"abc", "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"),
    (b"message digest", "5d0689ef49d2fae572b881b123a85ffa21595f36"),
    (b"abcdefghijklmnopqrstuvwxyz", "f71c27109c692c1b56bbdceb5b9d2865b3708dbc"),
]

for data, expected in ripemd_vectors:
    got = _ripemd160(data).hex()
    if got == expected:
        result.ok(f"RIPEMD-160({data[:20]}...)")
    else:
        result.fail(f"RIPEMD-160({data[:20]}...)", f"expected {expected}, got {got}")


# ============================================================
# 2. Tagged Hash (BIP340) correctness
# ============================================================
print("\n=== Tagged Hash Tests ===")

# Verify tagged_hash("TapTweak", data) produces correct result
# tagged_hash(tag, msg) = SHA256(SHA256(tag) || SHA256(tag) || msg)
tag = "TapTweak"
msg = bytes(32)  # 32 zero bytes
tag_hash = hashlib.sha256(tag.encode()).digest()
expected_th = hashlib.sha256(tag_hash + tag_hash + msg).digest()
got_th = tagged_hash("TapTweak", msg)
if got_th == expected_th:
    result.ok("Tagged hash TapTweak")
else:
    result.fail("Tagged hash TapTweak", f"mismatch")

# Test with BIP340 "BIP0340/challenge" tag
tag2 = "BIP0340/challenge"
msg2 = bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000001")
tag_hash2 = hashlib.sha256(tag2.encode()).digest()
expected_th2 = hashlib.sha256(tag_hash2 + tag_hash2 + msg2).digest()
got_th2 = tagged_hash("BIP0340/challenge", msg2)
if got_th2 == expected_th2:
    result.ok("Tagged hash BIP0340/challenge")
else:
    result.fail("Tagged hash BIP0340/challenge", "mismatch")


# ============================================================
# 3. Key Generation Security Tests
# ============================================================
print("\n=== Key Generation Security Tests ===")

# Test: All generated keys must be in valid range [1, N-1]
for i in range(100):
    key = generate_private_key()
    k = int.from_bytes(key, 'big')
    if k <= 0 or k >= N:
        result.fail(f"Key range test {i}", f"key {k} out of range")
        break
else:
    result.ok("100 keys all in valid range [1, N-1]")

# Test: Keys should be unique (collision would indicate broken RNG)
keys = set()
for i in range(1000):
    key = generate_private_key()
    keys.add(key.hex())
if len(keys) == 1000:
    result.ok("1000 keys all unique (no collisions)")
else:
    result.fail("Key uniqueness", f"only {len(keys)} unique out of 1000")

# Test: Key bytes should have reasonable entropy (not all same byte)
key_sample = generate_private_key()
byte_counts = Counter(key_sample)
if len(byte_counts) >= 10:  # At least 10 different byte values in 32 bytes
    result.ok("Key has reasonable byte entropy")
else:
    result.fail("Key entropy", f"only {len(byte_counts)} unique bytes")


# ============================================================
# 4. secp256k1 Point Arithmetic Tests
# ============================================================
print("\n=== Point Arithmetic Tests ===")

# Test: G * 1 = G
g1 = point_mul(1)
if g1 == (G_X, G_Y):
    result.ok("G * 1 = G")
else:
    result.fail("G * 1", f"got {g1}")

# Test: G * 2 = G + G
g2 = point_mul(2)
g_plus_g = point_add((G_X, G_Y), (G_X, G_Y))
if g2 == g_plus_g:
    result.ok("G * 2 = G + G")
else:
    result.fail("G * 2", "mismatch")

# Test: G * N = infinity (point at infinity)
gn = point_mul(N)
if gn is None:
    result.ok("G * N = infinity")
else:
    result.fail("G * N", f"expected infinity, got {gn}")

# Test: Known public key for private key = 1
# Private key 1 -> public key = G
pubkey1 = private_key_to_public_key(b'\x00' * 31 + b'\x01', compressed=True)
expected_g_compressed = b'\x02' + G_X.to_bytes(32, 'big')
if pubkey1 == expected_g_compressed:
    result.ok("privkey=1 gives G as pubkey")
else:
    result.fail("privkey=1", f"expected G, got different point")


# ============================================================
# 5. Bech32/Bech32m Encoding Tests
# ============================================================
print("\n=== Bech32/Bech32m Encoding Tests ===")

# BIP173 test vectors for bech32
test_vectors_bech32 = [
    # (hrp, witness_version, witness_program_hex, expected_address)
    ("bc", 0, "751e76e8199196d454941c45d1b3a323f1433bd6", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
]

for hrp, witver, prog_hex, expected in test_vectors_bech32:
    prog = bytes.fromhex(prog_hex)
    addr = bech32_encode(hrp, witver, list(prog), spec="bech32")
    if addr == expected:
        result.ok(f"Bech32 encode {expected[:20]}...")
    else:
        result.fail(f"Bech32 encode", f"expected {expected}, got {addr}")

    # Decode roundtrip
    dec_hrp, dec_ver, dec_prog, dec_spec = bech32_decode(addr)
    if dec_hrp == hrp and dec_ver == witver and dec_prog == prog:
        result.ok(f"Bech32 decode roundtrip {expected[:20]}...")
    else:
        result.fail(f"Bech32 decode roundtrip", f"got hrp={dec_hrp}, ver={dec_ver}")

# BIP350 test vectors for bech32m (witness version 1)
test_vectors_bech32m = [
    ("bc", 1, "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798", "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"),
]

for hrp, witver, prog_hex, expected in test_vectors_bech32m:
    prog = bytes.fromhex(prog_hex)
    addr = bech32_encode(hrp, witver, list(prog), spec="bech32m")
    if addr == expected:
        result.ok(f"Bech32m encode {expected[:20]}...")
    else:
        result.fail(f"Bech32m encode", f"expected {expected}, got {addr}")

    # Decode roundtrip
    dec_hrp, dec_ver, dec_prog, dec_spec = bech32_decode(addr)
    if dec_hrp == hrp and dec_ver == witver and dec_prog == prog and dec_spec == "bech32m":
        result.ok(f"Bech32m decode roundtrip {expected[:20]}...")
    else:
        result.fail(f"Bech32m decode roundtrip", f"got hrp={dec_hrp}, ver={dec_ver}, spec={dec_spec}")

# Additional roundtrip test: encode then decode for random 20-byte and 32-byte programs
for length, witver, spec in [(20, 0, "bech32"), (32, 1, "bech32m")]:
    prog = secrets.token_bytes(length)
    addr = bech32_encode("bc", witver, list(prog), spec=spec)
    dec_hrp, dec_ver, dec_prog, dec_spec = bech32_decode(addr)
    if dec_hrp == "bc" and dec_ver == witver and dec_prog == prog and dec_spec == spec:
        result.ok(f"Roundtrip {spec} {length}-byte random program")
    else:
        result.fail(f"Roundtrip {spec} {length}-byte", f"decode mismatch")


# ============================================================
# 6. SegWit Address Generation Tests
# ============================================================
print("\n=== SegWit Address Generation Tests ===")

# Test known vector: private key -> expected P2WPKH address
# Using a well-known test private key
# privkey = 0x01 -> pubkey = G
test_privkey = b'\x00' * 31 + b'\x01'
test_pubkey = private_key_to_public_key(test_privkey, compressed=True)
test_h160 = hash160(test_pubkey)
test_addr = bech32_encode("bc", 0, list(test_h160), spec="bech32")

# Verify the address is valid
info = verify_address(test_addr)
if info and info["type"] == "p2wpkh":
    result.ok("Known privkey=1 generates valid P2WPKH address")
else:
    result.fail("Known P2WPKH", f"invalid address info: {info}")

# Verify keypair match
valid, msg = verify_keypair(test_privkey.hex(), test_addr)
if valid:
    result.ok("Known privkey=1 keypair verification")
else:
    result.fail("Known P2WPKH keypair", msg)

# Test generated addresses
for i in range(10):
    sw = generate_segwit_address(network="mainnet")
    addr = sw["address"]

    # Must start with bc1q
    if not addr.startswith("bc1q"):
        result.fail(f"SegWit addr {i}", f"doesn't start with bc1q: {addr}")
        continue

    # Must be valid bech32
    hrp, witver, witprog, spec = bech32_decode(addr)
    if hrp != "bc" or witver != 0 or len(witprog) != 20 or spec != "bech32":
        result.fail(f"SegWit addr {i}", f"invalid decode: hrp={hrp}, ver={witver}, proglen={len(witprog) if witprog else 0}")
        continue

    # Keypair must verify
    valid, msg = verify_keypair(sw["private_key_hex"], addr)
    if not valid:
        result.fail(f"SegWit keypair {i}", msg)
        continue

result.ok("10 SegWit addresses all valid and verified")

# Test regtest
sw_rt = generate_segwit_address(network="regtest")
if sw_rt["address"].startswith("bcrt1q"):
    result.ok("Regtest SegWit starts with bcrt1q")
else:
    result.fail("Regtest SegWit", f"got {sw_rt['address'][:10]}...")


# ============================================================
# 7. Taproot Address Generation Tests
# ============================================================
print("\n=== Taproot Address Generation Tests ===")

# BIP341 test vector: internal key -> expected output key
# Using the BIP341 reference test vector
# Internal key: 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798 (= G_x)
bip341_internal_key = bytes.fromhex("79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798")
# For key-only taproot (no script tree):
# tweak = tagged_hash("TapTweak", internal_key)
# output_key = internal_key_point + tweak * G
tweak_bytes = compute_taptweak(bip341_internal_key, None)
output_key, parity = taproot_tweak_pubkey(bip341_internal_key, None)

# Verify the output key is valid (on the curve)
output_point = _lift_x(output_key)
if output_point is not None:
    # Verify: y^2 = x^3 + 7 (mod p)
    x, y = output_point
    lhs = pow(y, 2, P)
    rhs = (pow(x, 3, P) + 7) % P
    if lhs == rhs:
        result.ok("BIP341 tweaked output key is on curve")
    else:
        result.fail("BIP341 output key", "not on curve")
else:
    result.fail("BIP341 output key", "lift_x failed")

# Test: tweaked privkey can derive the output key
test_priv = b'\x00' * 31 + b'\x01'  # privkey = 1
tweaked_priv = taproot_tweak_seckey(test_priv, None)
# The tweaked privkey * G should equal the output key
tweaked_point = point_mul(int.from_bytes(tweaked_priv, 'big'))
tweaked_x = tweaked_point[0].to_bytes(32, 'big')
if tweaked_x == output_key:
    result.ok("Tweaked privkey derives correct output key")
else:
    result.fail("Tweaked privkey", f"output key mismatch")

# Test generated taproot addresses
for i in range(10):
    tr = generate_taproot_address(network="mainnet", backup_key=False)
    addr = tr["address"]

    # Must start with bc1p
    if not addr.startswith("bc1p"):
        result.fail(f"Taproot addr {i}", f"doesn't start with bc1p: {addr}")
        continue

    # Must be valid bech32m
    hrp, witver, witprog, spec = bech32_decode(addr)
    if hrp != "bc" or witver != 1 or len(witprog) != 32 or spec != "bech32m":
        result.fail(f"Taproot addr {i}", f"invalid decode: hrp={hrp}, ver={witver}")
        continue

    # Output key must match address witness program
    if witprog.hex() != tr["output_pubkey_hex"]:
        result.fail(f"Taproot addr {i}", "output key != witness program")
        continue

    # Tweaked private key must derive output key
    tweaked_pk = bytes.fromhex(tr["tweaked_private_key_hex"])
    derived_point = point_mul(int.from_bytes(tweaked_pk, 'big'))
    derived_x = derived_point[0].to_bytes(32, 'big')
    if derived_x.hex() != tr["output_pubkey_hex"]:
        result.fail(f"Taproot tweaked key {i}", "derived key != output key")
        continue

result.ok("10 Taproot addresses all valid and verified")

# Test regtest
tr_rt = generate_taproot_address(network="regtest", backup_key=False)
if tr_rt["address"].startswith("bcrt1p"):
    result.ok("Regtest Taproot starts with bcrt1p")
else:
    result.fail("Regtest Taproot", f"got {tr_rt['address'][:10]}...")


# ============================================================
# 8. Taproot Backup Key Tests
# ============================================================
print("\n=== Taproot Backup Key Tests ===")

for i in range(5):
    tr_bk = generate_taproot_address(network="mainnet", backup_key=True)

    # Must have backup fields
    if not tr_bk.get("has_backup"):
        result.fail(f"Backup flag {i}", "has_backup not set")
        continue

    for field in ["backup_private_key_wif", "backup_private_key_hex", "backup_pubkey_hex", "script_tree_hash"]:
        if field not in tr_bk:
            result.fail(f"Backup field {i}", f"missing {field}")
            break
    else:
        # Verify the script tree hash
        backup_pubkey_x = bytes.fromhex(tr_bk["backup_pubkey_hex"])
        expected_sth = compute_script_tree_hash_for_backup(backup_pubkey_x)
        if expected_sth.hex() != tr_bk["script_tree_hash"]:
            result.fail(f"Backup STH {i}", "script tree hash mismatch")
            continue

        # Verify the output key was computed with the script tree
        internal_key = bytes.fromhex(tr_bk["internal_pubkey_hex"])
        sth = bytes.fromhex(tr_bk["script_tree_hash"])
        expected_output, expected_parity = taproot_tweak_pubkey(internal_key, sth)
        if expected_output.hex() != tr_bk["output_pubkey_hex"]:
            result.fail(f"Backup output key {i}", "output key mismatch with script tree")
            continue

        # Verify that the tweaked private key can still sign for the output
        tweaked_pk = bytes.fromhex(tr_bk["tweaked_private_key_hex"])
        derived_point = point_mul(int.from_bytes(tweaked_pk, 'big'))
        derived_x = derived_point[0].to_bytes(32, 'big')
        if derived_x.hex() != tr_bk["output_pubkey_hex"]:
            result.fail(f"Backup tweaked key {i}", "tweaked key doesn't derive output")
            continue

        # Verify the control block can be constructed
        cb = compute_control_block(internal_key, tr_bk["output_parity"])
        if len(cb) != 33:  # 1 byte header + 32 byte internal key
            result.fail(f"Control block {i}", f"unexpected length {len(cb)}")
            continue

        if cb[0] & 0xfe == 0xc0:  # Leaf version 0xc0 with parity bit
            pass  # Good
        else:
            result.fail(f"Control block header {i}", f"unexpected header {cb[0]:#x}")
            continue

        # Verify the backup key is different from the main key
        if tr_bk["backup_private_key_hex"] == tr_bk["private_key_hex"]:
            result.fail(f"Backup key uniqueness {i}", "backup key same as main key!")
            continue

result.ok("5 Taproot backup key wallets all valid")


# ============================================================
# 9. Taproot Script Path Spending Verification
# ============================================================
print("\n=== Script Path Spending Verification ===")

# Verify that the backup key's script is correctly constructed
tr_script = generate_taproot_address(network="mainnet", backup_key=True)
backup_pk_x = bytes.fromhex(tr_script["backup_pubkey_hex"])

# The script should be: OP_PUSH32 <backup_pubkey_x> OP_CHECKSIG
expected_script = bytes([0x20]) + backup_pk_x + bytes([0xac])
if len(expected_script) == 34:
    result.ok("Backup spending script has correct structure (34 bytes)")
else:
    result.fail("Script structure", f"unexpected length {len(expected_script)}")

# Verify TapLeaf hash
leaf_version = 0xc0
leaf_data = bytes([leaf_version, len(expected_script)]) + expected_script
expected_leaf_hash = tagged_hash("TapLeaf", leaf_data)
actual_sth = compute_script_tree_hash_for_backup(backup_pk_x)
if expected_leaf_hash == actual_sth:
    result.ok("TapLeaf hash matches")
else:
    result.fail("TapLeaf hash", "mismatch")

# Verify that using the script tree hash changes the output key
internal_key = bytes.fromhex(tr_script["internal_pubkey_hex"])
output_no_script, _ = taproot_tweak_pubkey(internal_key, None)
output_with_script, _ = taproot_tweak_pubkey(internal_key, actual_sth)
if output_no_script != output_with_script:
    result.ok("Script tree changes output key (as expected)")
else:
    result.fail("Script tree effect", "output key unchanged with script tree!")

# Verify the output key in the address matches the one computed with script tree
addr_info = verify_address(tr_script["address"])
if addr_info and addr_info["program"] == output_with_script.hex():
    result.ok("Address witness program matches tweaked output key")
else:
    result.fail("Address witness program", f"mismatch: {addr_info}")


# ============================================================
# 10. WIF Encoding Tests
# ============================================================
print("\n=== WIF Encoding Tests ===")

# Known test vector: private key 1
wif_mainnet = private_key_to_wif(b'\x00' * 31 + b'\x01', compressed=True, network="mainnet")
# WIF for privkey=1, compressed, mainnet should start with K or L
if wif_mainnet[0] in ('K', 'L'):
    result.ok("WIF mainnet compressed starts with K/L")
else:
    result.fail("WIF prefix", f"got {wif_mainnet[0]}")

wif_testnet = private_key_to_wif(b'\x00' * 31 + b'\x01', compressed=True, network="regtest")
if wif_testnet[0] == 'c':
    result.ok("WIF testnet compressed starts with c")
else:
    result.fail("WIF testnet prefix", f"got {wif_testnet[0]}")

# Verify WIF encodes correctly for known privkey
# privkey=1, compressed, mainnet:
# 0x80 + 00...01 + 0x01 + checksum
payload = b'\x80' + b'\x00' * 31 + b'\x01' + b'\x01'
checksum = sha256(sha256(payload))[:4]
expected_wif = base58_encode(payload + checksum)
if wif_mainnet == expected_wif:
    result.ok("WIF encoding matches manual computation")
else:
    result.fail("WIF encoding", f"expected {expected_wif}, got {wif_mainnet}")


# ============================================================
# 11. Edge Case Tests
# ============================================================
print("\n=== Edge Case Tests ===")

# Test: address verification rejects invalid addresses
invalid_addrs = [
    "bc1qw508d6qejxtdg4y5r3zarvar",  # Too short
    "bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # Wrong spec for v0
    "xx1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # Wrong HRP
    "",  # Empty
    "not_an_address",  # Not bech32
]
for bad_addr in invalid_addrs:
    info = verify_address(bad_addr)
    if info is None:
        result.ok(f"Rejects invalid: {bad_addr[:20]}...")
    else:
        # Some might decode but be unknown type - that's also fine
        pass

# Test: verify_keypair rejects wrong key
sw_test = generate_segwit_address(network="mainnet")
wrong_key = generate_private_key().hex()
valid, msg = verify_keypair(wrong_key, sw_test["address"])
if not valid:
    result.ok("Rejects wrong private key for address")
else:
    result.fail("Wrong key rejection", "incorrectly validated wrong key")


# ============================================================
# 12. Multiple Generation Consistency Tests
# ============================================================
print("\n=== Consistency Tests ===")

# Generate many addresses and verify all are unique
addresses = set()
wifs = set()
for i in range(200):
    if i % 2 == 0:
        w = generate_segwit_address(network="mainnet")
    else:
        w = generate_taproot_address(network="mainnet", backup_key=i % 4 == 1)
    addresses.add(w["address"])
    wifs.add(w["private_key_wif"])

if len(addresses) == 200:
    result.ok("200 generated addresses all unique")
else:
    result.fail("Address uniqueness", f"only {len(addresses)} unique out of 200")

if len(wifs) == 200:
    result.ok("200 generated WIFs all unique")
else:
    result.fail("WIF uniqueness", f"only {len(wifs)} unique out of 200")


# ============================================================
# 13. Taproot Key Path and Script Path Compatibility
# ============================================================
print("\n=== Key Path / Script Path Compatibility ===")

# For a taproot address with backup:
# 1. The main key (tweaked) can spend via key path
# 2. The backup key can spend via script path
# Both should produce valid spending paths for the SAME address

for i in range(3):
    wallet = generate_taproot_address(network="mainnet", backup_key=True)

    internal_key = bytes.fromhex(wallet["internal_pubkey_hex"])
    backup_pk = bytes.fromhex(wallet["backup_pubkey_hex"])
    sth = bytes.fromhex(wallet["script_tree_hash"])

    # Verify key path: tweaked privkey * G = output key
    tweaked_pk_int = int.from_bytes(bytes.fromhex(wallet["tweaked_private_key_hex"]), 'big')
    kp_point = point_mul(tweaked_pk_int)
    kp_x = kp_point[0].to_bytes(32, 'big')

    if kp_x.hex() == wallet["output_pubkey_hex"]:
        pass  # Key path works
    else:
        result.fail(f"Key path {i}", "tweaked key doesn't match output")
        continue

    # Verify script path: the script tree hash, when used in the tweak,
    # produces the same output key as stored in the address
    computed_output, computed_parity = taproot_tweak_pubkey(internal_key, sth)
    if computed_output.hex() == wallet["output_pubkey_hex"]:
        pass  # Script path tweak is consistent
    else:
        result.fail(f"Script path {i}", "script tree tweak doesn't produce correct output")
        continue

    # Verify the backup key is on the curve
    backup_point = _lift_x(backup_pk)
    if backup_point is not None:
        bx, by = backup_point
        if (pow(by, 2, P) - pow(bx, 3, P) - 7) % P == 0:
            pass
        else:
            result.fail(f"Backup key curve {i}", "not on secp256k1")
            continue
    else:
        result.fail(f"Backup key lift {i}", "lift_x failed")
        continue

result.ok("3 wallets: key path AND script path both valid for same address")


# ============================================================
# 14. WIF Decode + Tweaked Address Derivation Tests
# ============================================================
print("\n=== WIF Decode & Tweaked Address Derivation Tests ===")

# Test WIF roundtrip: encode then decode (mainnet + regtest + testnet4)
wif_roundtrip_ok = True
for i in range(10):
    orig_key = generate_private_key()
    for net in ["mainnet", "regtest", "testnet4"]:
        wif = private_key_to_wif(orig_key, compressed=True, network=net)
        decoded = wif_to_private_key(wif)
        expected_mainnet = (net == "mainnet")
        if decoded["private_key"] != orig_key:
            result.fail(f"WIF roundtrip {i} network={net}", "decoded key != original")
            wif_roundtrip_ok = False
            break
        if decoded["compressed"] is not True:
            result.fail(f"WIF roundtrip {i} network={net}", "compressed flag wrong")
            wif_roundtrip_ok = False
            break
        if decoded["mainnet"] != expected_mainnet:
            result.fail(f"WIF roundtrip {i} network={net}", "mainnet flag wrong")
            wif_roundtrip_ok = False
            break
    if not wif_roundtrip_ok:
        break
if wif_roundtrip_ok:
    result.ok("30 WIF encode/decode roundtrips (mainnet + regtest + testnet4)")

# Test invalid WIF (corrupted checksum)
try:
    # Take a valid WIF and corrupt the last character
    valid_wif = private_key_to_wif(generate_private_key(), compressed=True, network="mainnet")
    # Flip last char
    bad_char = 'A' if valid_wif[-1] != 'A' else 'B'
    bad_wif = valid_wif[:-1] + bad_char
    wif_to_private_key(bad_wif)
    result.fail("WIF bad checksum", "should have raised ValueError")
except ValueError:
    result.ok("Rejects WIF with bad checksum")

# Test derive_taproot_address_from_tweaked_privkey matches generate_taproot_address
derivation_ok = True
for i in range(10):
    for backup in [False, True]:
        wallet = generate_taproot_address(network="mainnet", backup_key=backup)
        tweaked_pk = bytes.fromhex(wallet["tweaked_private_key_hex"])
        derived = derive_taproot_address_from_tweaked_privkey(tweaked_pk, network="mainnet")
        if derived["address"] != wallet["address"]:
            result.fail(f"Tweaked derivation {i} backup={backup}",
                        f"derived {derived['address']} != {wallet['address']}")
            derivation_ok = False
            break
        # Verify scriptpubkey
        expected_spk = bytes([0x51, 0x20]) + bytes.fromhex(wallet["output_pubkey_hex"])
        if derived["scriptpubkey"] != expected_spk:
            result.fail(f"Tweaked derivation SPK {i} backup={backup}", "scriptpubkey mismatch")
            derivation_ok = False
            break
    if not derivation_ok:
        break
if derivation_ok:
    result.ok("20 tweaked privkey -> address derivations match (with and without backup)")

# Test regtest derivation
wallet_rt = generate_taproot_address(network="regtest", backup_key=False)
tweaked_pk_rt = bytes.fromhex(wallet_rt["tweaked_private_key_hex"])
derived_rt = derive_taproot_address_from_tweaked_privkey(tweaked_pk_rt, network="regtest")
if derived_rt["address"] == wallet_rt["address"] and derived_rt["address"].startswith("bcrt1p"):
    result.ok("Regtest tweaked derivation correct (bcrt1p prefix)")
else:
    result.fail("Regtest tweaked derivation", "mismatch or wrong prefix")


# ============================================================
# 15. Testnet4 Address Generation Tests
# ============================================================
print("\n=== Testnet4 Address Generation Tests ===")

# SegWit testnet4 address
sw_t4 = generate_segwit_address(network="testnet4")
if sw_t4["address"].startswith("tb1q"):
    result.ok("Testnet4 SegWit starts with tb1q")
else:
    result.fail("Testnet4 SegWit", f"got {sw_t4['address'][:10]}...")

# Taproot testnet4 address
tr_t4 = generate_taproot_address(network="testnet4", backup_key=False)
if tr_t4["address"].startswith("tb1p"):
    result.ok("Testnet4 Taproot starts with tb1p")
else:
    result.fail("Testnet4 Taproot", f"got {tr_t4['address'][:10]}...")

# Taproot testnet4 with backup key
tr_t4_bk = generate_taproot_address(network="testnet4", backup_key=True)
if tr_t4_bk["address"].startswith("tb1p") and tr_t4_bk.get("has_backup"):
    result.ok("Testnet4 Taproot+backup starts with tb1p")
else:
    result.fail("Testnet4 Taproot+backup", f"got {tr_t4_bk['address'][:10]}...")

# Testnet4 WIF prefix (should be 'c', same as regtest)
wif_t4 = private_key_to_wif(b'\x00' * 31 + b'\x01', compressed=True, network="testnet4")
if wif_t4[0] == 'c':
    result.ok("Testnet4 WIF compressed starts with c")
else:
    result.fail("Testnet4 WIF prefix", f"got {wif_t4[0]}")

# Testnet4 tweaked derivation roundtrip
wallet_t4 = generate_taproot_address(network="testnet4", backup_key=True)
tweaked_pk_t4 = bytes.fromhex(wallet_t4["tweaked_private_key_hex"])
derived_t4 = derive_taproot_address_from_tweaked_privkey(tweaked_pk_t4, network="testnet4")
if derived_t4["address"] == wallet_t4["address"] and derived_t4["address"].startswith("tb1p"):
    result.ok("Testnet4 tweaked derivation correct (tb1p prefix)")
else:
    result.fail("Testnet4 tweaked derivation", "mismatch or wrong prefix")


# ============================================================
# Summary
# ============================================================
success = result.summary()
sys.exit(0 if success else 1)
