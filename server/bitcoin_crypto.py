"""
Bitcoin address generation module.
Supports SegWit (bech32, P2WPKH) and Taproot (bech32m, P2TR) addresses.
Pure Python implementation — no external dependencies.
Uses secrets.token_bytes() for cryptographically secure random entropy.
"""

import hashlib
import hmac
import secrets
import struct


def _network_hrp(network):
    """Return bech32 HRP for the given network."""
    return {"mainnet": "bc", "testnet4": "tb", "regtest": "bcrt"}[network]

# ============================================================
# Bech32 / Bech32m encoding (BIP173 / BIP350)
# ============================================================

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_CONST = 1
BECH32M_CONST = 0x2bc830a3


def _bech32_polymod(values):
    """Internal function that computes the Bech32 checksum."""
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp):
    """Expand the HRP into values for checksum computation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_create_checksum(hrp, data, spec):
    """Compute the checksum values given HRP and data."""
    const = BECH32M_CONST if spec == "bech32m" else BECH32_CONST
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _bech32_verify_checksum(hrp, data, spec):
    """Verify a checksum given HRP and converted data characters."""
    const = BECH32M_CONST if spec == "bech32m" else BECH32_CONST
    return _bech32_polymod(_bech32_hrp_expand(hrp) + data) == const


def bech32_encode(hrp, witver, witprog, spec="bech32"):
    """Encode a segwit address."""
    data = [witver] + _convertbits(witprog, 8, 5)
    checksum = _bech32_create_checksum(hrp, data, spec)
    return hrp + "1" + "".join([BECH32_CHARSET[d] for d in data + checksum])


def bech32_decode(addr):
    """Decode a bech32/bech32m address. Returns (hrp, witver, witprog, spec)."""
    if addr.lower() != addr and addr.upper() != addr:
        return None, None, None, None
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        return None, None, None, None
    hrp = addr[:pos]
    data = []
    for c in addr[pos + 1:]:
        if c not in BECH32_CHARSET:
            return None, None, None, None
        data.append(BECH32_CHARSET.index(c))

    # Try bech32m first (for witness version >= 1), then bech32
    for spec in ["bech32m", "bech32"]:
        if _bech32_verify_checksum(hrp, data, spec):
            witver = data[0]
            witprog = _convertbits(data[1:-6], 5, 8, pad=False)
            if witprog is None:
                return None, None, None, None
            return hrp, witver, bytes(witprog), spec

    return None, None, None, None


def _convertbits(data, frombits, tobits, pad=True):
    """General power-of-2 base conversion."""
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


# ============================================================
# Hash utilities
# ============================================================

def hash160(data):
    """RIPEMD160(SHA256(data))"""
    sha = hashlib.sha256(data).digest()
    try:
        r = hashlib.new('ripemd160')
        r.update(sha)
        return r.digest()
    except (ValueError, AttributeError):
        # OpenSSL may not support ripemd160 - use pure Python implementation
        return _ripemd160(sha)


def _ripemd160(data):
    """Pure Python RIPEMD-160 implementation."""
    # Initial hash values
    h0 = 0x67452301
    h1 = 0xEFCDAB89
    h2 = 0x98BADCFE
    h3 = 0x10325476
    h4 = 0xC3D2E1F0

    # Pre-processing: adding padding bits
    msg = bytearray(data)
    msg_len = len(data)
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0x00)
    msg += struct.pack('<Q', msg_len * 8)

    def _left_rotate(n, b):
        return ((n << b) | (n >> (32 - b))) & 0xFFFFFFFF

    def _f(j, x, y, z):
        if j < 16:
            return x ^ y ^ z
        elif j < 32:
            return (x & y) | (~x & z)
        elif j < 48:
            return (x | ~y) ^ z
        elif j < 64:
            return (x & z) | (y & ~z)
        else:
            return x ^ (y | ~z)

    # Constants
    K_LEFT =  [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
    K_RIGHT = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]

    # Message schedule selection
    R_LEFT = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
        3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
        1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
        4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13
    ]
    R_RIGHT = [
        5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
        6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
        15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
        8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
        12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11
    ]
    S_LEFT = [
        11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
        7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
        11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
        11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
        9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6
    ]
    S_RIGHT = [
        8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
        9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
        9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
        15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
        8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11
    ]

    # Process each 512-bit block
    for i in range(0, len(msg), 64):
        block = msg[i:i+64]
        X = [int.from_bytes(block[j:j+4], 'little') for j in range(0, 64, 4)]

        al, bl, cl, dl, el = h0, h1, h2, h3, h4
        ar, br, cr, dr, er = h0, h1, h2, h3, h4

        for j in range(80):
            rnd = j // 16
            # Left
            fl = _f(j, bl, cl, dl) & 0xFFFFFFFF
            t = (al + fl + X[R_LEFT[j]] + K_LEFT[rnd]) & 0xFFFFFFFF
            t = (_left_rotate(t, S_LEFT[j]) + el) & 0xFFFFFFFF
            al = el
            el = dl
            dl = _left_rotate(cl, 10)
            cl = bl
            bl = t

            # Right
            fr = _f(79 - j, br, cr, dr) & 0xFFFFFFFF
            t = (ar + fr + X[R_RIGHT[j]] + K_RIGHT[rnd]) & 0xFFFFFFFF
            t = (_left_rotate(t, S_RIGHT[j]) + er) & 0xFFFFFFFF
            ar = er
            er = dr
            dr = _left_rotate(cr, 10)
            cr = br
            br = t

        t = (h1 + cl + dr) & 0xFFFFFFFF
        h1 = (h2 + dl + er) & 0xFFFFFFFF
        h2 = (h3 + el + ar) & 0xFFFFFFFF
        h3 = (h4 + al + br) & 0xFFFFFFFF
        h4 = (h0 + bl + cr) & 0xFFFFFFFF
        h0 = t

    return struct.pack('<5I', h0, h1, h2, h3, h4)


def sha256(data):
    """SHA256 hash."""
    return hashlib.sha256(data).digest()


def tagged_hash(tag, data):
    """BIP340 tagged hash: SHA256(SHA256(tag) || SHA256(tag) || data)"""
    tag_hash = sha256(tag.encode('utf-8'))
    return sha256(tag_hash + tag_hash + data)


# ============================================================
# secp256k1 curve parameters and point arithmetic
# ============================================================

# secp256k1 curve parameters
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G_X = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
G_Y = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _modinv(a, m):
    """Modular multiplicative inverse using extended Euclidean algorithm."""
    if a < 0:
        a = a % m
    g, x, _ = _extended_gcd(a, m)
    if g != 1:
        raise ValueError("Modular inverse does not exist")
    return x % m


def _extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = _extended_gcd(b % a, a)
    return g, y - (b // a) * x, x


def point_add(p1, p2):
    """Add two points on secp256k1. Points are (x, y) tuples or None for infinity."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if y1 != y2:
            return None  # Point at infinity
        # Point doubling
        lam = (3 * x1 * x1 * _modinv(2 * y1, P)) % P
    else:
        lam = ((y2 - y1) * _modinv(x2 - x1, P)) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def point_mul(k, point=None):
    """Scalar multiplication on secp256k1 using double-and-add."""
    if point is None:
        point = (G_X, G_Y)
    result = None
    addend = point
    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        k >>= 1
    return result


def point_from_bytes(data):
    """Decode a public key from bytes (compressed or uncompressed)."""
    if len(data) == 33:
        # Compressed
        prefix = data[0]
        x = int.from_bytes(data[1:], 'big')
        y_sq = (pow(x, 3, P) + 7) % P
        y = pow(y_sq, (P + 1) // 4, P)
        if y % 2 != (prefix - 2):
            y = P - y
        return (x, y)
    elif len(data) == 65:
        # Uncompressed
        x = int.from_bytes(data[1:33], 'big')
        y = int.from_bytes(data[33:], 'big')
        return (x, y)
    elif len(data) == 32:
        # x-only (BIP340)
        x = int.from_bytes(data, 'big')
        y_sq = (pow(x, 3, P) + 7) % P
        y = pow(y_sq, (P + 1) // 4, P)
        if y % 2 != 0:
            y = P - y
        return (x, y)
    raise ValueError(f"Invalid public key length: {len(data)}")


# ============================================================
# Key generation
# ============================================================

def generate_private_key():
    """Generate a cryptographically secure random private key for secp256k1."""
    while True:
        key_bytes = secrets.token_bytes(32)
        key_int = int.from_bytes(key_bytes, 'big')
        if 0 < key_int < N:
            return key_bytes


def private_key_to_public_key(privkey_bytes, compressed=True):
    """Derive the public key from a private key."""
    k = int.from_bytes(privkey_bytes, 'big')
    point = point_mul(k)
    x = point[0].to_bytes(32, 'big')
    y = point[1].to_bytes(32, 'big')
    if compressed:
        prefix = b'\x02' if point[1] % 2 == 0 else b'\x03'
        return prefix + x
    return b'\x04' + x + y


def private_key_to_xonly_pubkey(privkey_bytes):
    """Derive x-only public key (BIP340) from private key.
    Returns (x_only_pubkey_bytes, negated) where negated indicates if the
    private key was negated to ensure even y coordinate."""
    k = int.from_bytes(privkey_bytes, 'big')
    point = point_mul(k)
    x_bytes = point[0].to_bytes(32, 'big')
    negated = point[1] % 2 != 0
    return x_bytes, negated


def private_key_to_wif(privkey_bytes, compressed=True, network="mainnet"):
    """Convert private key to Wallet Import Format (WIF).

    Args:
        privkey_bytes: 32-byte raw private key
        compressed: Whether to use compressed format
        network: "mainnet", "testnet4", or "regtest"
    """
    prefix = b'\x80' if network == "mainnet" else b'\xef'
    payload = prefix + privkey_bytes
    if compressed:
        payload += b'\x01'
    checksum = sha256(sha256(payload))[:4]
    return base58_encode(payload + checksum)


# ============================================================
# Base58 encoding (for WIF)
# ============================================================

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def base58_encode(data):
    """Encode bytes to base58."""
    n = int.from_bytes(data, 'big')
    result = ''
    while n > 0:
        n, remainder = divmod(n, 58)
        result = BASE58_ALPHABET[remainder] + result
    # Add leading '1's for leading zero bytes
    for byte in data:
        if byte == 0:
            result = '1' + result
        else:
            break
    return result


def base58_decode(s):
    """Decode a base58 string to bytes."""
    n = 0
    for char in s:
        n = n * 58 + BASE58_ALPHABET.index(char)
    # Convert to bytes
    result = []
    while n > 0:
        n, remainder = divmod(n, 256)
        result.append(remainder)
    result.reverse()
    # Add leading zero bytes for leading '1' characters
    leading_ones = 0
    for char in s:
        if char == '1':
            leading_ones += 1
        else:
            break
    return bytes(leading_ones) + bytes(result)


def wif_to_private_key(wif_string):
    """Decode a WIF-encoded private key back to raw 32-byte key.

    Returns dict with:
        - private_key: 32 bytes raw private key
        - compressed: bool
        - mainnet: bool

    Raises ValueError if checksum is invalid or format is wrong.
    """
    raw = base58_decode(wif_string)

    # Verify checksum (last 4 bytes)
    payload = raw[:-4]
    checksum = raw[-4:]
    expected_checksum = sha256(sha256(payload))[:4]
    if checksum != expected_checksum:
        raise ValueError("Invalid WIF checksum")

    # Parse version byte
    version = payload[0:1]
    if version == b'\x80':
        mainnet = True
    elif version == b'\xef':
        mainnet = False
    else:
        raise ValueError(f"Unknown WIF version byte: {version.hex()}")

    # Parse key data
    key_data = payload[1:]
    if len(key_data) == 33 and key_data[-1] == 0x01:
        private_key = key_data[:-1]
        compressed = True
    elif len(key_data) == 32:
        private_key = key_data
        compressed = False
    else:
        raise ValueError(f"Invalid WIF key data length: {len(key_data)}")

    # Validate key is in valid range
    k = int.from_bytes(private_key, 'big')
    if k <= 0 or k >= N:
        raise ValueError("Private key out of valid range")

    return {
        "private_key": private_key,
        "compressed": compressed,
        "mainnet": mainnet,
    }


def derive_taproot_address_from_tweaked_privkey(tweaked_privkey_bytes, network="mainnet"):
    """Derive the Taproot (P2TR) address from a tweaked private key.

    The tweaked privkey * G gives the output point. The x-coordinate of that
    point IS the witness program in the bc1p address. This is what a bill
    recipient uses to verify the address matches the bill and to construct
    the scriptPubKey for spending.

    Args:
        tweaked_privkey_bytes: 32-byte tweaked private key
        network: "mainnet" (bc1p), "testnet4" (tb1p), or "regtest" (bcrt1p)

    Returns dict with:
        - address: the bech32m address
        - output_key_x: 32-byte x-only output public key
        - scriptpubkey: bytes of the scriptPubKey (OP_1 <32-byte-key>)
    """
    x_bytes, _ = private_key_to_xonly_pubkey(tweaked_privkey_bytes)

    hrp = _network_hrp(network)
    address = bech32_encode(hrp, 1, list(x_bytes), spec="bech32m")

    # scriptPubKey = OP_1 (0x51) + PUSH32 (0x20) + output_key_x
    scriptpubkey = bytes([0x51, 0x20]) + x_bytes

    return {
        "address": address,
        "output_key_x": x_bytes,
        "scriptpubkey": scriptpubkey,
    }


def derive_segwit_address_from_privkey(privkey_bytes, network="mainnet"):
    """Derive the SegWit (P2WPKH) address from a raw private key.

    Args:
        privkey_bytes: 32-byte raw private key
        network: "mainnet" (bc1q), "testnet4" (tb1q), or "regtest" (bcrt1q)

    Returns dict with:
        - address: the bech32 address
        - pubkey: compressed public key bytes (33 bytes)
        - pubkey_hash: HASH160 of the compressed pubkey (20 bytes)
        - scriptpubkey: bytes of the scriptPubKey (OP_0 <20-byte-hash>)
    """
    pubkey = private_key_to_public_key(privkey_bytes, compressed=True)
    pubkey_hash = hash160(pubkey)

    hrp = _network_hrp(network)
    address = bech32_encode(hrp, 0, list(pubkey_hash), spec="bech32")

    # scriptPubKey = OP_0 (0x00) + PUSH20 (0x14) + pubkey_hash
    scriptpubkey = bytes([0x00, 0x14]) + pubkey_hash

    return {
        "address": address,
        "pubkey": pubkey,
        "pubkey_hash": pubkey_hash,
        "scriptpubkey": scriptpubkey,
    }


# ============================================================
# SegWit P2WPKH address generation
# ============================================================

def generate_segwit_address(network="mainnet"):
    """Generate a SegWit (P2WPKH, native bech32) address.

    Args:
        network: "mainnet" (bc1q), "testnet4" (tb1q), or "regtest" (bcrt1q)

    Returns dict with:
        - address: the bech32 address (bc1q...)
        - private_key_wif: WIF-encoded private key
        - private_key_hex: hex-encoded raw private key
        - public_key_hex: hex-encoded compressed public key
    """
    privkey = generate_private_key()
    pubkey = private_key_to_public_key(privkey, compressed=True)

    # P2WPKH: witness program is HASH160(compressed_pubkey)
    witness_program = hash160(pubkey)

    hrp = _network_hrp(network)
    address = bech32_encode(hrp, 0, list(witness_program), spec="bech32")

    wif = private_key_to_wif(privkey, compressed=True, network=network)

    return {
        "address": address,
        "private_key_wif": wif,
        "private_key_hex": privkey.hex(),
        "public_key_hex": pubkey.hex(),
        "type": "segwit_p2wpkh",
    }


# ============================================================
# Taproot P2TR address generation (BIP341/BIP340)
# ============================================================

def _lift_x(x_bytes):
    """Lift x coordinate to a point with even y (BIP340)."""
    x = int.from_bytes(x_bytes, 'big')
    if x >= P:
        return None
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if pow(y, 2, P) != y_sq:
        return None
    if y % 2 != 0:
        y = P - y
    return (x, y)


def compute_taptweak(pubkey_x_bytes, script_tree_hash=None):
    """Compute the taproot tweak per BIP341.

    tweak = tagged_hash("TapTweak", pubkey_x || script_tree_hash)
    if script_tree_hash is None, tweak = tagged_hash("TapTweak", pubkey_x)
    """
    if script_tree_hash is not None:
        return tagged_hash("TapTweak", pubkey_x_bytes + script_tree_hash)
    else:
        return tagged_hash("TapTweak", pubkey_x_bytes)


def taproot_tweak_pubkey(internal_pubkey_x, script_tree_hash=None):
    """Apply the taproot tweak to an internal public key.

    Returns (output_key_x_bytes, parity) where output_key_x_bytes is the
    x-only tweaked public key for the P2TR output.
    """
    P_point = _lift_x(internal_pubkey_x)
    if P_point is None:
        raise ValueError("Invalid internal public key")

    tweak_bytes = compute_taptweak(internal_pubkey_x, script_tree_hash)
    tweak_int = int.from_bytes(tweak_bytes, 'big')

    if tweak_int >= N:
        raise ValueError("Tweak is too large")

    # Q = P + t*G
    t_point = point_mul(tweak_int)
    Q = point_add(P_point, t_point)

    if Q is None:
        raise ValueError("Resulting point is at infinity")

    parity = Q[1] % 2
    output_key_x = Q[0].to_bytes(32, 'big')

    return output_key_x, parity


def taproot_tweak_seckey(privkey_bytes, script_tree_hash=None):
    """Compute the tweaked private key for spending.

    Returns the tweaked private key bytes that can sign for the P2TR output.
    """
    k = int.from_bytes(privkey_bytes, 'big')

    # Get x-only pubkey
    point = point_mul(k)

    # If y is odd, negate the private key
    if point[1] % 2 != 0:
        k = N - k

    x_bytes = point[0].to_bytes(32, 'big')
    tweak = compute_taptweak(x_bytes, script_tree_hash)
    tweak_int = int.from_bytes(tweak, 'big')

    tweaked_k = (k + tweak_int) % N
    return tweaked_k.to_bytes(32, 'big')


def compute_script_tree_hash_for_backup(backup_pubkey_x):
    """Compute a script tree hash for a simple backup key spending path.

    Creates a Tapscript leaf with: <backup_pubkey> OP_CHECKSIG
    This allows the backup key holder to spend via the script path.
    """
    # Build the tapscript: <32-byte-xonly-pubkey> OP_CHECKSIG
    # OP_CHECKSIG = 0xac
    script = bytes([0x20]) + backup_pubkey_x + bytes([0xac])

    # Leaf version 0xc0 (tapscript)
    leaf_version = 0xc0

    # TapLeaf hash = tagged_hash("TapLeaf", leaf_version || compact_size(script) || script)
    leaf_data = bytes([leaf_version]) + _compact_size(len(script)) + script
    return tagged_hash("TapLeaf", leaf_data)


def _compact_size(n):
    """Encode a compact size integer."""
    if n < 253:
        return bytes([n])
    elif n <= 0xffff:
        return b'\xfd' + struct.pack('<H', n)
    elif n <= 0xffffffff:
        return b'\xfe' + struct.pack('<I', n)
    else:
        return b'\xff' + struct.pack('<Q', n)


def generate_taproot_address(network="mainnet", backup_key=False):
    """Generate a Taproot (P2TR, bech32m) address.

    Args:
        network: "mainnet" (bc1p), "testnet4" (tb1p), or "regtest" (bcrt1p)
        backup_key: If True, also generate a backup key that can spend via script path

    Returns dict with:
        - address: the bech32m address (bc1p...)
        - private_key_wif: WIF-encoded private key
        - private_key_hex: hex-encoded raw private key
        - internal_pubkey_hex: hex-encoded x-only internal public key
        - output_pubkey_hex: hex-encoded x-only output public key
        - tweaked_private_key_hex: hex-encoded tweaked private key (for key path spending)
        - backup_private_key_wif: (if backup_key) WIF-encoded backup private key
        - backup_private_key_hex: (if backup_key) hex-encoded backup private key
        - backup_pubkey_hex: (if backup_key) hex-encoded x-only backup public key
    """
    # Generate the internal key
    privkey = generate_private_key()
    internal_pubkey_x, _ = private_key_to_xonly_pubkey(privkey)

    script_tree_hash = None
    backup_info = {}

    if backup_key:
        # Generate backup key
        backup_privkey = generate_private_key()
        backup_pubkey_x, _ = private_key_to_xonly_pubkey(backup_privkey)

        # Compute script tree hash for backup key spending path
        script_tree_hash = compute_script_tree_hash_for_backup(backup_pubkey_x)

        backup_wif = private_key_to_wif(backup_privkey, compressed=True, network=network)
        backup_info = {
            "backup_private_key_wif": backup_wif,
            "backup_private_key_hex": backup_privkey.hex(),
            "backup_pubkey_hex": backup_pubkey_x.hex(),
            "script_tree_hash": script_tree_hash.hex(),
        }

    # Compute the tweaked output key
    output_key_x, parity = taproot_tweak_pubkey(internal_pubkey_x, script_tree_hash)

    # Compute the tweaked private key for key-path spending
    tweaked_privkey = taproot_tweak_seckey(privkey, script_tree_hash)

    # Create bech32m address (witness version 1)
    hrp = _network_hrp(network)
    address = bech32_encode(hrp, 1, list(output_key_x), spec="bech32m")

    wif = private_key_to_wif(privkey, compressed=True, network=network)

    result = {
        "address": address,
        "private_key_wif": wif,
        "private_key_hex": privkey.hex(),
        "internal_pubkey_hex": internal_pubkey_x.hex(),
        "output_pubkey_hex": output_key_x.hex(),
        "tweaked_private_key_hex": tweaked_privkey.hex(),
        "output_parity": parity,
        "type": "taproot_p2tr",
        "has_backup": backup_key,
    }
    result.update(backup_info)

    return result


# ============================================================
# ECDSA signing (for SegWit P2WPKH spending)
# ============================================================

def _deterministic_k(privkey_int, msg_hash, extra_entropy=None):
    """RFC 6979 deterministic k generation for ECDSA."""
    # Simplified RFC 6979 implementation
    x = privkey_int.to_bytes(32, 'big')
    h1 = msg_hash
    V = b'\x01' * 32
    K = b'\x00' * 32
    K = hmac.new(K, V + b'\x00' + x + h1 + (extra_entropy or b''), hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b'\x01' + x + h1 + (extra_entropy or b''), hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, 'big')
        if 0 < k < N:
            return k
        K = hmac.new(K, V + b'\x00', hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def ecdsa_sign(privkey_bytes, msg_hash):
    """ECDSA sign a 32-byte message hash. Returns DER-encoded signature."""
    d = int.from_bytes(privkey_bytes, 'big')
    z = int.from_bytes(msg_hash, 'big')
    k = _deterministic_k(d, msg_hash)
    R = point_mul(k)
    r = R[0] % N
    if r == 0:
        raise ValueError("r is zero")
    k_inv = pow(k, N - 2, N)
    s = (k_inv * (z + r * d)) % N
    if s == 0:
        raise ValueError("s is zero")
    # Use low-s (BIP62)
    if s > N // 2:
        s = N - s
    return _der_encode_sig(r, s)


def _der_encode_sig(r, s):
    """DER-encode an ECDSA signature (r, s)."""
    def _int_to_der(v):
        b = v.to_bytes((v.bit_length() + 7) // 8, 'big')
        if b[0] & 0x80:
            b = b'\x00' + b
        return bytes([0x02, len(b)]) + b
    rb = _int_to_der(r)
    sb = _int_to_der(s)
    return bytes([0x30, len(rb) + len(sb)]) + rb + sb


# ============================================================
# Schnorr signing (BIP340, for Taproot spending)
# ============================================================

def schnorr_sign(privkey_bytes, msg_hash, aux_rand=None):
    """BIP340 Schnorr signature over a 32-byte message hash.

    privkey_bytes must be the tweaked private key for key path spending,
    or the raw backup private key for script path spending.
    Returns 64-byte signature.
    """
    if aux_rand is None:
        aux_rand = secrets.token_bytes(32)

    d0 = int.from_bytes(privkey_bytes, 'big')
    if d0 == 0 or d0 >= N:
        raise ValueError("Invalid private key")

    pub = point_mul(d0)
    # Negate d if y is odd (BIP340: we work with x-only pubkeys, even y)
    d = d0 if pub[1] % 2 == 0 else N - d0
    pk_bytes = pub[0].to_bytes(32, 'big')

    # t = d XOR tagged_hash("BIP0340/aux", aux_rand)
    t_hash = tagged_hash("BIP0340/aux", aux_rand)
    t = (d ^ int.from_bytes(t_hash, 'big')).to_bytes(32, 'big')

    # nonce: k0 = tagged_hash("BIP0340/nonce", t || pk || msg) mod n
    nonce_hash = tagged_hash("BIP0340/nonce", t + pk_bytes + msg_hash)
    k0 = int.from_bytes(nonce_hash, 'big') % N
    if k0 == 0:
        raise ValueError("Nonce is zero")

    R = point_mul(k0)
    k = k0 if R[1] % 2 == 0 else N - k0
    r_bytes = R[0].to_bytes(32, 'big')

    # e = tagged_hash("BIP0340/challenge", R.x || P.x || msg) mod n
    e_hash = tagged_hash("BIP0340/challenge", r_bytes + pk_bytes + msg_hash)
    e = int.from_bytes(e_hash, 'big') % N

    sig_s = (k + e * d) % N
    sig = r_bytes + sig_s.to_bytes(32, 'big')

    # Verify before returning
    if not schnorr_verify(pk_bytes, msg_hash, sig):
        raise ValueError("Generated signature failed verification")
    return sig


def schnorr_verify(pubkey_x_bytes, msg_hash, sig):
    """BIP340 Schnorr signature verification."""
    if len(sig) != 64:
        return False
    r_bytes = sig[:32]
    s_bytes = sig[32:]
    r = int.from_bytes(r_bytes, 'big')
    s = int.from_bytes(s_bytes, 'big')
    if r >= P or s >= N:
        return False

    e_hash = tagged_hash("BIP0340/challenge", r_bytes + pubkey_x_bytes + msg_hash)
    e = int.from_bytes(e_hash, 'big') % N

    P_point = _lift_x(pubkey_x_bytes)
    if P_point is None:
        return False

    # R = s*G - e*P
    sG = point_mul(s)
    eP = point_mul(e, P_point)
    neg_eP = (eP[0], P - eP[1]) if eP else None
    R = point_add(sG, neg_eP)

    if R is None:
        return False
    if R[1] % 2 != 0:
        return False
    if R[0] != r:
        return False
    return True


# ============================================================
# Transaction construction helpers (for regtest spending tests)
# ============================================================

def _serialize_witness_item(data):
    """Serialize a single witness stack item."""
    return _compact_size(len(data)) + data


def build_p2wpkh_scriptcode(pubkey_hash):
    """Build the scriptCode for P2WPKH BIP143 sighash.

    scriptCode = OP_DUP OP_HASH160 <20-byte hash> OP_EQUALVERIFY OP_CHECKSIG
    """
    return bytes([0x19, 0x76, 0xa9, 0x14]) + pubkey_hash + bytes([0x88, 0xac])


def sighash_segwit_v0_full(tx_version, tx_locktime, inputs, outputs,
                            input_idx, scriptcode, value, hash_type=0x01):
    """Compute BIP143 sighash for SegWit v0 (P2WPKH).

    inputs: list of (prev_txid_bytes, prev_vout, sequence)
    outputs: list of (value, scriptPubKey)
    value: satoshi amount of the input being signed
    """
    # hashPrevouts
    prevouts = b''
    for txid, vout, _ in inputs:
        prevouts += txid + struct.pack('<I', vout)
    hash_prevouts = sha256(sha256(prevouts))

    # hashSequence
    sequences = b''
    for _, _, seq in inputs:
        sequences += struct.pack('<I', seq)
    hash_sequence = sha256(sha256(sequences))

    # hashOutputs
    outputs_ser = b''
    for out_value, out_script in outputs:
        outputs_ser += struct.pack('<q', out_value)
        outputs_ser += _compact_size(len(out_script)) + out_script
    hash_outputs = sha256(sha256(outputs_ser))

    # BIP143 preimage
    txid, vout, seq = inputs[input_idx]
    preimage = struct.pack('<I', tx_version)
    preimage += hash_prevouts
    preimage += hash_sequence
    preimage += txid + struct.pack('<I', vout)
    preimage += scriptcode
    preimage += struct.pack('<q', value)
    preimage += struct.pack('<I', seq)
    preimage += hash_outputs
    preimage += struct.pack('<I', tx_locktime)
    preimage += struct.pack('<I', hash_type)

    return sha256(sha256(preimage))


def sighash_taproot_keypath(tx_version, tx_locktime, inputs, outputs,
                             input_idx, amounts, scriptpubkeys, hash_type=0x00):
    """Compute BIP341 sighash for Taproot key path spending (SIGHASH_DEFAULT).

    inputs: list of (prev_txid_bytes, prev_vout, sequence)
    outputs: list of (value, scriptPubKey)
    amounts: list of satoshi amounts for ALL inputs
    scriptpubkeys: list of scriptPubKey bytes for ALL inputs
    """
    # epoch
    preimage = bytes([0x00])

    # hash_type
    preimage += bytes([hash_type])

    # nVersion
    preimage += struct.pack('<i', tx_version)

    # nLockTime
    preimage += struct.pack('<I', tx_locktime)

    # sha_prevouts
    prevouts = b''
    for txid, vout, _ in inputs:
        prevouts += txid + struct.pack('<I', vout)
    preimage += sha256(prevouts)

    # sha_amounts
    amounts_ser = b''
    for amt in amounts:
        amounts_ser += struct.pack('<q', amt)
    preimage += sha256(amounts_ser)

    # sha_scriptpubkeys
    spks = b''
    for spk in scriptpubkeys:
        spks += _compact_size(len(spk)) + spk
    preimage += sha256(spks)

    # sha_sequences
    sequences = b''
    for _, _, seq in inputs:
        sequences += struct.pack('<I', seq)
    preimage += sha256(sequences)

    # sha_outputs
    outputs_ser = b''
    for out_value, out_script in outputs:
        outputs_ser += struct.pack('<q', out_value)
        outputs_ser += _compact_size(len(out_script)) + out_script
    preimage += sha256(outputs_ser)

    # spend_type: 0x00 for key path, no annex
    preimage += bytes([0x00])

    # input_index
    preimage += struct.pack('<I', input_idx)

    return tagged_hash("TapSighash", preimage)


def sighash_taproot_script_path(tx_version, tx_locktime, inputs, outputs,
                                 input_idx, amounts, scriptpubkeys,
                                 leaf_script, leaf_version=0xc0, hash_type=0x00):
    """Compute BIP341 sighash for Taproot script path spending.

    Same as key path but with ext_flag=1 and the tapleaf hash appended.
    """
    # epoch
    preimage = bytes([0x00])
    preimage += bytes([hash_type])
    preimage += struct.pack('<i', tx_version)
    preimage += struct.pack('<I', tx_locktime)

    # sha_prevouts
    prevouts = b''
    for txid, vout, _ in inputs:
        prevouts += txid + struct.pack('<I', vout)
    preimage += sha256(prevouts)

    # sha_amounts
    amounts_ser = b''
    for amt in amounts:
        amounts_ser += struct.pack('<q', amt)
    preimage += sha256(amounts_ser)

    # sha_scriptpubkeys
    spks = b''
    for spk in scriptpubkeys:
        spks += _compact_size(len(spk)) + spk
    preimage += sha256(spks)

    # sha_sequences
    sequences = b''
    for _, _, seq in inputs:
        sequences += struct.pack('<I', seq)
    preimage += sha256(sequences)

    # sha_outputs
    outputs_ser = b''
    for out_value, out_script in outputs:
        outputs_ser += struct.pack('<q', out_value)
        outputs_ser += _compact_size(len(out_script)) + out_script
    preimage += sha256(outputs_ser)

    # spend_type: 0x02 for script path (ext_flag=1, no annex)
    preimage += bytes([0x02])

    # input_index
    preimage += struct.pack('<I', input_idx)

    # tapleaf_hash
    leaf_data = bytes([leaf_version]) + _compact_size(len(leaf_script)) + leaf_script
    tapleaf_hash = tagged_hash("TapLeaf", leaf_data)
    preimage += tapleaf_hash

    # key_version (0x00)
    preimage += bytes([0x00])

    # codesep_pos (0xffffffff = no OP_CODESEPARATOR)
    preimage += struct.pack('<i', -1)

    return tagged_hash("TapSighash", preimage)


def build_signed_segwit_tx(privkey_bytes, pubkey_bytes, prev_txid_hex, prev_vout,
                            input_value, dest_address, dest_value,
                            change_address=None, change_value=0):
    """Build a fully signed SegWit v0 P2WPKH transaction.

    Returns hex-encoded raw transaction ready for broadcast.
    """
    prev_txid = bytes.fromhex(prev_txid_hex)[::-1]  # Internal byte order

    # Build outputs
    outputs = []
    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs.append((dest_value, dest_spk))
    if change_address and change_value > 0:
        change_spk = _address_to_scriptpubkey(change_address)
        outputs.append((change_value, change_spk))

    inputs = [(prev_txid, prev_vout, 0xfffffffd)]

    # scriptCode for P2WPKH
    pubkey_hash = hash160(pubkey_bytes)
    scriptcode = build_p2wpkh_scriptcode(pubkey_hash)

    sighash = sighash_segwit_v0_full(
        tx_version=2, tx_locktime=0,
        inputs=inputs, outputs=outputs,
        input_idx=0, scriptcode=scriptcode,
        value=input_value, hash_type=0x01
    )

    sig_der = ecdsa_sign(privkey_bytes, sighash)
    sig = sig_der + bytes([0x01])  # SIGHASH_ALL

    # Serialize the transaction
    tx = b''
    tx += struct.pack('<I', 2)     # version
    tx += bytes([0x00, 0x01])       # segwit marker + flag
    tx += _compact_size(1)          # input count
    # Input
    tx += prev_txid + struct.pack('<I', prev_vout)
    tx += bytes([0x00])             # scriptSig (empty for segwit)
    tx += struct.pack('<I', 0xfffffffd)
    # Outputs
    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk
    # Witness
    tx += _compact_size(2)          # 2 witness items
    tx += _serialize_witness_item(sig)
    tx += _serialize_witness_item(pubkey_bytes)
    tx += struct.pack('<I', 0)     # locktime

    return tx.hex()


def build_signed_taproot_keypath_tx(tweaked_privkey_bytes, prev_txid_hex, prev_vout,
                                     input_value, input_scriptpubkey,
                                     dest_address, dest_value,
                                     change_address=None, change_value=0):
    """Build a fully signed Taproot key path transaction.

    Returns hex-encoded raw transaction ready for broadcast.
    """
    prev_txid = bytes.fromhex(prev_txid_hex)[::-1]

    outputs = []
    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs.append((dest_value, dest_spk))
    if change_address and change_value > 0:
        change_spk = _address_to_scriptpubkey(change_address)
        outputs.append((change_value, change_spk))

    inputs = [(prev_txid, prev_vout, 0xfffffffd)]
    amounts = [input_value]
    scriptpubkeys = [input_scriptpubkey]

    sighash = sighash_taproot_keypath(
        tx_version=2, tx_locktime=0,
        inputs=inputs, outputs=outputs,
        input_idx=0, amounts=amounts,
        scriptpubkeys=scriptpubkeys, hash_type=0x00
    )

    sig = schnorr_sign(tweaked_privkey_bytes, sighash)
    # SIGHASH_DEFAULT (0x00) means no hash_type byte appended

    # Serialize
    tx = b''
    tx += struct.pack('<I', 2)
    tx += bytes([0x00, 0x01])       # segwit marker
    tx += _compact_size(1)
    tx += prev_txid + struct.pack('<I', prev_vout)
    tx += bytes([0x00])
    tx += struct.pack('<I', 0xfffffffd)
    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk
    # Witness: just the signature
    tx += _compact_size(1)
    tx += _serialize_witness_item(sig)
    tx += struct.pack('<I', 0)

    return tx.hex()


def build_signed_taproot_scriptpath_tx(backup_privkey_bytes, backup_pubkey_x,
                                        internal_pubkey_x, output_parity,
                                        prev_txid_hex, prev_vout,
                                        input_value, input_scriptpubkey,
                                        dest_address, dest_value,
                                        change_address=None, change_value=0):
    """Build a fully signed Taproot script path transaction using the backup key.

    Returns hex-encoded raw transaction ready for broadcast.
    """
    prev_txid = bytes.fromhex(prev_txid_hex)[::-1]

    outputs = []
    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs.append((dest_value, dest_spk))
    if change_address and change_value > 0:
        change_spk = _address_to_scriptpubkey(change_address)
        outputs.append((change_value, change_spk))

    inputs = [(prev_txid, prev_vout, 0xfffffffd)]
    amounts = [input_value]
    scriptpubkeys = [input_scriptpubkey]

    # The tapscript being executed
    leaf_script = bytes([0x20]) + backup_pubkey_x + bytes([0xac])

    sighash = sighash_taproot_script_path(
        tx_version=2, tx_locktime=0,
        inputs=inputs, outputs=outputs,
        input_idx=0, amounts=amounts,
        scriptpubkeys=scriptpubkeys,
        leaf_script=leaf_script, leaf_version=0xc0, hash_type=0x00
    )

    sig = schnorr_sign(backup_privkey_bytes, sighash)

    # Build witness: [signature, script, control_block]
    control_block = compute_control_block(internal_pubkey_x, output_parity)

    # Serialize
    tx = b''
    tx += struct.pack('<I', 2)
    tx += bytes([0x00, 0x01])
    tx += _compact_size(1)
    tx += prev_txid + struct.pack('<I', prev_vout)
    tx += bytes([0x00])
    tx += struct.pack('<I', 0xfffffffd)
    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk
    # Witness: 3 items [sig, script, control_block]
    tx += _compact_size(3)
    tx += _serialize_witness_item(sig)
    tx += _serialize_witness_item(leaf_script)
    tx += _serialize_witness_item(control_block)
    tx += struct.pack('<I', 0)

    return tx.hex()


def build_signed_taproot_sweep_tx(tweaked_privkey_bytes, utxos, input_scriptpubkey,
                                    dest_address, dest_value, extra_outputs=None):
    """Build a fully signed Taproot key path transaction sweeping multiple UTXOs.

    All UTXOs must belong to the same tweaked key (same address).

    Args:
        tweaked_privkey_bytes: 32-byte tweaked private key
        utxos: list of dicts with keys: txid (hex str), vout (int), value_sat (int)
        input_scriptpubkey: bytes, the scriptPubKey for all inputs (same address)
        dest_address: destination bech32/bech32m address
        dest_value: satoshis to send (total - fee)
        extra_outputs: optional list of dicts with keys: address (str), value (int)

    Returns hex-encoded raw transaction.
    """
    inputs = []
    amounts = []
    scriptpubkeys = []

    for u in utxos:
        prev_txid = bytes.fromhex(u["txid"])[::-1]  # Internal byte order
        inputs.append((prev_txid, u["vout"], 0xfffffffd))
        amounts.append(u["value_sat"])
        scriptpubkeys.append(input_scriptpubkey)

    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs = [(dest_value, dest_spk)]
    if extra_outputs:
        for eo in extra_outputs:
            outputs.append((eo["value"], _address_to_scriptpubkey(eo["address"])))

    # Sign each input
    signatures = []
    for idx in range(len(inputs)):
        sh = sighash_taproot_keypath(
            tx_version=2, tx_locktime=0,
            inputs=inputs, outputs=outputs,
            input_idx=idx, amounts=amounts,
            scriptpubkeys=scriptpubkeys, hash_type=0x00
        )
        sig = schnorr_sign(tweaked_privkey_bytes, sh)
        signatures.append(sig)

    # Serialize
    tx = b''
    tx += struct.pack('<I', 2)          # version
    tx += bytes([0x00, 0x01])           # segwit marker + flag
    tx += _compact_size(len(inputs))    # input count

    for prev_txid, vout, seq in inputs:
        tx += prev_txid + struct.pack('<I', vout)
        tx += bytes([0x00])             # empty scriptSig
        tx += struct.pack('<I', seq)

    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk

    # Witness data for each input
    for sig in signatures:
        tx += _compact_size(1)          # 1 witness item per input
        tx += _serialize_witness_item(sig)

    tx += struct.pack('<I', 0)          # locktime

    return tx.hex()


def build_signed_segwit_sweep_tx(privkey_bytes, utxos, dest_address, dest_value, extra_outputs=None):
    """Build a fully signed SegWit P2WPKH transaction sweeping multiple UTXOs.

    All UTXOs must belong to the same key (same address).

    Args:
        privkey_bytes: 32-byte raw private key
        utxos: list of dicts with keys: txid (hex str), vout (int), value_sat (int)
        dest_address: destination bech32/bech32m address
        dest_value: satoshis to send (total - fee)
        extra_outputs: optional list of dicts with keys: address (str), value (int)

    Returns hex-encoded raw transaction.
    """
    pubkey = private_key_to_public_key(privkey_bytes, compressed=True)
    pubkey_hash = hash160(pubkey)
    scriptcode = build_p2wpkh_scriptcode(pubkey_hash)

    inputs = []
    for u in utxos:
        prev_txid = bytes.fromhex(u["txid"])[::-1]
        inputs.append((prev_txid, u["vout"], 0xfffffffd))

    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs = [(dest_value, dest_spk)]
    if extra_outputs:
        for eo in extra_outputs:
            outputs.append((eo["value"], _address_to_scriptpubkey(eo["address"])))

    # Sign each input
    signatures = []
    for idx in range(len(inputs)):
        sighash = sighash_segwit_v0_full(
            tx_version=2, tx_locktime=0,
            inputs=inputs, outputs=outputs,
            input_idx=idx, scriptcode=scriptcode,
            value=utxos[idx]["value_sat"], hash_type=0x01
        )
        sig_der = ecdsa_sign(privkey_bytes, sighash)
        sig = sig_der + bytes([0x01])  # SIGHASH_ALL
        signatures.append(sig)

    # Serialize
    tx = b''
    tx += struct.pack('<I', 2)          # version
    tx += bytes([0x00, 0x01])           # segwit marker + flag
    tx += _compact_size(len(inputs))    # input count

    for prev_txid, vout, seq in inputs:
        tx += prev_txid + struct.pack('<I', vout)
        tx += bytes([0x00])             # empty scriptSig
        tx += struct.pack('<I', seq)

    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk

    # Witness data for each input: 2 items (sig + pubkey)
    for sig in signatures:
        tx += _compact_size(2)          # 2 witness items
        tx += _serialize_witness_item(sig)
        tx += _serialize_witness_item(pubkey)

    tx += struct.pack('<I', 0)          # locktime

    return tx.hex()


def build_signed_taproot_scriptpath_sweep_tx(backup_privkey_bytes, backup_pubkey_x,
                                              internal_pubkey_x, output_parity,
                                              utxos, input_scriptpubkey,
                                              dest_address, dest_value, extra_outputs=None):
    """Build a fully signed Taproot script path sweep transaction using the backup key.

    All UTXOs must belong to the same address.

    Args:
        backup_privkey_bytes: 32-byte backup private key
        backup_pubkey_x: 32-byte x-only backup public key
        internal_pubkey_x: 32-byte x-only internal public key
        output_parity: int, parity of the output key (0 or 1)
        utxos: list of dicts with keys: txid (hex str), vout (int), value_sat (int)
        input_scriptpubkey: bytes, the scriptPubKey for all inputs
        dest_address: destination address
        dest_value: satoshis to send (total - fee)

    Returns hex-encoded raw transaction.
    """
    inputs = []
    amounts = []
    scriptpubkeys = []

    for u in utxos:
        prev_txid = bytes.fromhex(u["txid"])[::-1]
        inputs.append((prev_txid, u["vout"], 0xfffffffd))
        amounts.append(u["value_sat"])
        scriptpubkeys.append(input_scriptpubkey)

    dest_spk = _address_to_scriptpubkey(dest_address)
    outputs = [(dest_value, dest_spk)]
    if extra_outputs:
        for eo in extra_outputs:
            outputs.append((eo["value"], _address_to_scriptpubkey(eo["address"])))

    # The tapscript being executed: <backup_pubkey> OP_CHECKSIG
    leaf_script = bytes([0x20]) + backup_pubkey_x + bytes([0xac])
    control_block = compute_control_block(internal_pubkey_x, output_parity)

    # Sign each input
    signatures = []
    for idx in range(len(inputs)):
        sighash = sighash_taproot_script_path(
            tx_version=2, tx_locktime=0,
            inputs=inputs, outputs=outputs,
            input_idx=idx, amounts=amounts,
            scriptpubkeys=scriptpubkeys,
            leaf_script=leaf_script, leaf_version=0xc0, hash_type=0x00
        )
        sig = schnorr_sign(backup_privkey_bytes, sighash)
        signatures.append(sig)

    # Serialize
    tx = b''
    tx += struct.pack('<I', 2)
    tx += bytes([0x00, 0x01])
    tx += _compact_size(len(inputs))

    for prev_txid, vout, seq in inputs:
        tx += prev_txid + struct.pack('<I', vout)
        tx += bytes([0x00])
        tx += struct.pack('<I', seq)

    tx += _compact_size(len(outputs))
    for out_val, out_spk in outputs:
        tx += struct.pack('<q', out_val)
        tx += _compact_size(len(out_spk)) + out_spk

    # Witness data for each input: 3 items [sig, script, control_block]
    for sig in signatures:
        tx += _compact_size(3)
        tx += _serialize_witness_item(sig)
        tx += _serialize_witness_item(leaf_script)
        tx += _serialize_witness_item(control_block)

    tx += struct.pack('<I', 0)

    return tx.hex()


def _address_to_scriptpubkey(address):
    """Convert a bech32/bech32m address to its scriptPubKey."""
    hrp, witver, witprog, spec = bech32_decode(address)
    if hrp is None:
        raise ValueError(f"Invalid address: {address}")
    # scriptPubKey: OP_witver OP_PUSH<len> <witprog>
    if witver == 0:
        op_ver = bytes([0x00])
    else:
        op_ver = bytes([0x50 + witver])  # OP_1 = 0x51, etc.
    return op_ver + bytes([len(witprog)]) + witprog


# ============================================================
# Taproot script path spending helpers (for testing)
# ============================================================

def compute_control_block(internal_pubkey_x, output_parity, merkle_path=None):
    """Compute the control block for script path spending.

    control_block = (leaf_version | parity_bit) || internal_pubkey_x || merkle_path
    """
    leaf_version = 0xc0
    parity_byte = leaf_version | (output_parity & 1)
    cb = bytes([parity_byte]) + internal_pubkey_x
    if merkle_path:
        for h in merkle_path:
            cb += h
    return cb



# ============================================================
# Verification utilities
# ============================================================

def verify_address(address):
    """Verify a bitcoin address and return its type."""
    hrp, witver, witprog, spec = bech32_decode(address)
    if hrp is None:
        return None

    if witver == 0 and len(witprog) == 20:
        return {"type": "p2wpkh", "witness_version": 0, "program": witprog.hex(), "spec": spec}
    elif witver == 1 and len(witprog) == 32:
        return {"type": "p2tr", "witness_version": 1, "program": witprog.hex(), "spec": spec}

    return {"type": "unknown", "witness_version": witver, "program": witprog.hex(), "spec": spec}


def verify_keypair(privkey_hex, address):
    """Verify that a private key corresponds to the given address."""
    privkey = bytes.fromhex(privkey_hex)
    k = int.from_bytes(privkey, 'big')
    if k <= 0 or k >= N:
        return False, "Invalid private key"

    info = verify_address(address)
    if info is None:
        return False, "Invalid address"

    if info["type"] == "p2wpkh":
        pubkey = private_key_to_public_key(privkey, compressed=True)
        expected_program = hash160(pubkey).hex()
        if expected_program == info["program"]:
            return True, "Key matches P2WPKH address"
        return False, f"Key does not match: expected {expected_program}, got {info['program']}"

    elif info["type"] == "p2tr":
        # For taproot, the output key is the tweaked internal key
        # We can verify by checking if the tweaked key matches
        internal_pubkey_x, _ = private_key_to_xonly_pubkey(privkey)

        # Try without script tree (key-only taproot)
        output_key_x, _ = taproot_tweak_pubkey(internal_pubkey_x, None)
        if output_key_x.hex() == info["program"]:
            return True, "Key matches P2TR address (key-only path)"

        # If it doesn't match, it might have a script tree
        return False, "Key does not match P2TR output (may have script tree)"

    return False, f"Unsupported address type: {info['type']}"


if __name__ == "__main__":
    # Quick self-test
    print("=== SegWit Address Generation ===")
    sw = generate_segwit_address(network="mainnet")
    for k, v in sw.items():
        print(f"  {k}: {v}")

    valid, msg = verify_keypair(sw["private_key_hex"], sw["address"])
    print(f"  Verification: {valid} - {msg}")

    print("\n=== Taproot Address Generation (key-only) ===")
    tr = generate_taproot_address(network="mainnet", backup_key=False)
    for k, v in tr.items():
        print(f"  {k}: {v}")

    valid, msg = verify_keypair(tr["private_key_hex"], tr["address"])
    print(f"  Verification: {valid} - {msg}")

    print("\n=== Taproot Address with Backup Key ===")
    tr_backup = generate_taproot_address(network="mainnet", backup_key=True)
    for k, v in tr_backup.items():
        print(f"  {k}: {v}")

    # Verify address format
    info = verify_address(tr_backup["address"])
    print(f"  Address info: {info}")
