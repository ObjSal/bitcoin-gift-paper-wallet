/**
 * Bitcoin address generation module (JavaScript port).
 * Supports SegWit (bech32, P2WPKH) and Taproot (bech32m, P2TR) addresses.
 * Pure JavaScript implementation — no external dependencies.
 * Uses crypto.getRandomValues() for cryptographically secure random entropy.
 *
 * Port of bitcoin_crypto.py — phase by phase.
 */

// ============================================================
// Byte / hex / BigInt helpers
// ============================================================

/**
 * Concatenate multiple Uint8Arrays into one.
 */
function concatBytes(...arrays) {
    let totalLen = 0;
    for (const a of arrays) totalLen += a.length;
    const result = new Uint8Array(totalLen);
    let offset = 0;
    for (const a of arrays) {
        result.set(a, offset);
        offset += a.length;
    }
    return result;
}

/**
 * Encode a Uint8Array as a hex string.
 */
function bytesToHex(bytes) {
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
        hex += bytes[i].toString(16).padStart(2, '0');
    }
    return hex;
}

/**
 * Decode a hex string to Uint8Array.
 */
function hexToBytes(hex) {
    if (hex.length % 2 !== 0) throw new Error('Hex string must have even length');
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i++) {
        bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    return bytes;
}

/**
 * Convert a Uint8Array (big-endian) to BigInt.
 * Equivalent to Python: int.from_bytes(b, 'big')
 */
function bytesToBigInt(bytes) {
    let result = 0n;
    for (let i = 0; i < bytes.length; i++) {
        result = (result << 8n) | BigInt(bytes[i]);
    }
    return result;
}

/**
 * Convert a BigInt to a Uint8Array of exactly `length` bytes (big-endian).
 * Equivalent to Python: n.to_bytes(length, 'big')
 */
function bigIntToBytes(n, length) {
    const bytes = new Uint8Array(length);
    let val = n;
    for (let i = length - 1; i >= 0; i--) {
        bytes[i] = Number(val & 0xFFn);
        val >>= 8n;
    }
    return bytes;
}

/**
 * Write a 32-bit unsigned integer in little-endian.
 * Equivalent to Python: struct.pack('<I', n)
 */
function writeUint32LE(buf, offset, n) {
    buf[offset] = n & 0xFF;
    buf[offset + 1] = (n >>> 8) & 0xFF;
    buf[offset + 2] = (n >>> 16) & 0xFF;
    buf[offset + 3] = (n >>> 24) & 0xFF;
}

/**
 * Read a 32-bit unsigned integer from little-endian bytes.
 * Equivalent to Python: int.from_bytes(b[offset:offset+4], 'little')
 */
function readUint32LE(buf, offset) {
    return (buf[offset] |
        (buf[offset + 1] << 8) |
        (buf[offset + 2] << 16) |
        (buf[offset + 3] << 24)) >>> 0;
}

/**
 * Write a 64-bit unsigned integer in little-endian.
 * Equivalent to Python: struct.pack('<Q', n)
 * Uses BigInt for the value since JS numbers can't represent full 64-bit.
 */
function writeUint64LE(buf, offset, n) {
    const big = BigInt(n);
    buf[offset] = Number(big & 0xFFn);
    buf[offset + 1] = Number((big >> 8n) & 0xFFn);
    buf[offset + 2] = Number((big >> 16n) & 0xFFn);
    buf[offset + 3] = Number((big >> 24n) & 0xFFn);
    buf[offset + 4] = Number((big >> 32n) & 0xFFn);
    buf[offset + 5] = Number((big >> 40n) & 0xFFn);
    buf[offset + 6] = Number((big >> 48n) & 0xFFn);
    buf[offset + 7] = Number((big >> 56n) & 0xFFn);
}

/**
 * Write a 32-bit signed integer in little-endian.
 * Equivalent to Python: struct.pack('<i', n)
 */
function writeInt32LE(buf, offset, n) {
    writeUint32LE(buf, offset, n >>> 0);
}

/**
 * Write a 16-bit unsigned integer in little-endian.
 * Equivalent to Python: struct.pack('<H', n)
 */
function writeUint16LE(buf, offset, n) {
    buf[offset] = n & 0xFF;
    buf[offset + 1] = (n >>> 8) & 0xFF;
}


// ============================================================
// SHA-256 (synchronous, pure JS — not WebCrypto)
// ============================================================

const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/**
 * SHA-256 hash. Returns a 32-byte Uint8Array.
 * Equivalent to Python: hashlib.sha256(data).digest()
 */
function sha256(data) {
    // Ensure input is Uint8Array
    if (!(data instanceof Uint8Array)) {
        if (typeof data === 'string') {
            data = new TextEncoder().encode(data);
        } else {
            data = new Uint8Array(data);
        }
    }

    // Pre-processing: padding
    const bitLen = data.length * 8;
    // Need: data + 0x80 + zeros + 8-byte length = multiple of 64
    const padLen = 64 - ((data.length + 9) % 64);
    const totalLen = data.length + 1 + (padLen === 64 ? 0 : padLen) + 8;
    const msg = new Uint8Array(totalLen);
    msg.set(data);
    msg[data.length] = 0x80;
    // Write bit length as big-endian 64-bit at end
    // For messages < 2^32 bytes, high 32 bits are 0
    const view = new DataView(msg.buffer);
    view.setUint32(totalLen - 4, bitLen >>> 0, false); // big-endian
    if (bitLen > 0xFFFFFFFF) {
        view.setUint32(totalLen - 8, Math.floor(bitLen / 0x100000000) >>> 0, false);
    }

    // Initial hash values
    let h0 = 0x6a09e667 | 0;
    let h1 = 0xbb67ae85 | 0;
    let h2 = 0x3c6ef372 | 0;
    let h3 = 0xa54ff53a | 0;
    let h4 = 0x510e527f | 0;
    let h5 = 0x9b05688c | 0;
    let h6 = 0x1f83d9ab | 0;
    let h7 = 0x5be0cd19 | 0;

    // Message schedule array
    const W = new Int32Array(64);

    // Process each 512-bit (64-byte) block
    for (let offset = 0; offset < totalLen; offset += 64) {
        // Prepare message schedule
        for (let i = 0; i < 16; i++) {
            W[i] = view.getInt32(offset + i * 4, false); // big-endian
        }
        for (let i = 16; i < 64; i++) {
            const s0 = (_rotr32(W[i-15], 7) ^ _rotr32(W[i-15], 18) ^ (W[i-15] >>> 3)) | 0;
            const s1 = (_rotr32(W[i-2], 17) ^ _rotr32(W[i-2], 19) ^ (W[i-2] >>> 10)) | 0;
            W[i] = (W[i-16] + s0 + W[i-7] + s1) | 0;
        }

        // Compression
        let a = h0, b = h1, c = h2, d = h3;
        let e = h4, f = h5, g = h6, h = h7;

        for (let i = 0; i < 64; i++) {
            const S1 = (_rotr32(e, 6) ^ _rotr32(e, 11) ^ _rotr32(e, 25)) | 0;
            const ch = ((e & f) ^ (~e & g)) | 0;
            const temp1 = (h + S1 + ch + SHA256_K[i] + W[i]) | 0;
            const S0 = (_rotr32(a, 2) ^ _rotr32(a, 13) ^ _rotr32(a, 22)) | 0;
            const maj = ((a & b) ^ (a & c) ^ (b & c)) | 0;
            const temp2 = (S0 + maj) | 0;

            h = g;
            g = f;
            f = e;
            e = (d + temp1) | 0;
            d = c;
            c = b;
            b = a;
            a = (temp1 + temp2) | 0;
        }

        h0 = (h0 + a) | 0;
        h1 = (h1 + b) | 0;
        h2 = (h2 + c) | 0;
        h3 = (h3 + d) | 0;
        h4 = (h4 + e) | 0;
        h5 = (h5 + f) | 0;
        h6 = (h6 + g) | 0;
        h7 = (h7 + h) | 0;
    }

    // Produce output
    const out = new Uint8Array(32);
    const outView = new DataView(out.buffer);
    outView.setUint32(0, h0 >>> 0, false);
    outView.setUint32(4, h1 >>> 0, false);
    outView.setUint32(8, h2 >>> 0, false);
    outView.setUint32(12, h3 >>> 0, false);
    outView.setUint32(16, h4 >>> 0, false);
    outView.setUint32(20, h5 >>> 0, false);
    outView.setUint32(24, h6 >>> 0, false);
    outView.setUint32(28, h7 >>> 0, false);
    return out;
}

/** Right rotate a 32-bit integer. */
function _rotr32(x, n) {
    return ((x >>> n) | (x << (32 - n))) | 0;
}


// ============================================================
// RIPEMD-160 (pure JS port of _ripemd160 from bitcoin_crypto.py)
// ============================================================

/**
 * RIPEMD-160 hash. Returns a 20-byte Uint8Array.
 */
function ripemd160(data) {
    if (!(data instanceof Uint8Array)) {
        data = new Uint8Array(data);
    }

    // Initial hash values
    let h0 = 0x67452301;
    let h1 = 0xEFCDAB89;
    let h2 = 0x98BADCFE;
    let h3 = 0x10325476;
    let h4 = 0xC3D2E1F0;

    // Pre-processing: padding
    const msgLen = data.length;
    // data + 0x80 + zeros + 8 bytes length = multiple of 64
    const padNeeded = 64 - ((msgLen + 9) % 64);
    const totalLen = msgLen + 1 + (padNeeded === 64 ? 0 : padNeeded) + 8;
    const msg = new Uint8Array(totalLen);
    msg.set(data);
    msg[msgLen] = 0x80;
    // Write bit length as little-endian 64-bit at end
    writeUint64LE(msg, totalLen - 8, BigInt(msgLen) * 8n);

    function leftRotate(n, b) {
        return ((n << b) | (n >>> (32 - b))) >>> 0;
    }

    function f(j, x, y, z) {
        if (j < 16) return (x ^ y ^ z) >>> 0;
        if (j < 32) return ((x & y) | (~x & z)) >>> 0;
        if (j < 48) return ((x | (~y >>> 0)) ^ z) >>> 0;
        if (j < 64) return ((x & z) | (y & (~z >>> 0))) >>> 0;
        return (x ^ (y | (~z >>> 0))) >>> 0;
    }

    // Constants
    const K_LEFT  = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E];
    const K_RIGHT = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000];

    // Message schedule selection
    const R_LEFT = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
        3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
        1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
        4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13,
    ];
    const R_RIGHT = [
        5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
        6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
        15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
        8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
        12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11,
    ];
    const S_LEFT = [
        11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
        7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
        11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
        11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
        9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6,
    ];
    const S_RIGHT = [
        8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
        9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
        9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
        15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
        8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11,
    ];

    // Process each 512-bit (64-byte) block
    for (let i = 0; i < totalLen; i += 64) {
        // Parse block into 16 x 32-bit words (little-endian)
        const X = new Uint32Array(16);
        for (let j = 0; j < 16; j++) {
            X[j] = readUint32LE(msg, i + j * 4);
        }

        let al = h0, bl = h1, cl = h2, dl = h3, el = h4;
        let ar = h0, br = h1, cr = h2, dr = h3, er = h4;

        for (let j = 0; j < 80; j++) {
            const rnd = (j / 16) | 0;

            // Left
            let fl = f(j, bl, cl, dl);
            let t = ((al + fl) >>> 0);
            t = ((t + X[R_LEFT[j]]) >>> 0);
            t = ((t + K_LEFT[rnd]) >>> 0);
            t = ((leftRotate(t, S_LEFT[j]) + el) >>> 0);
            al = el;
            el = dl;
            dl = leftRotate(cl, 10);
            cl = bl;
            bl = t;

            // Right
            let fr = f(79 - j, br, cr, dr);
            t = ((ar + fr) >>> 0);
            t = ((t + X[R_RIGHT[j]]) >>> 0);
            t = ((t + K_RIGHT[rnd]) >>> 0);
            t = ((leftRotate(t, S_RIGHT[j]) + er) >>> 0);
            ar = er;
            er = dr;
            dr = leftRotate(cr, 10);
            cr = br;
            br = t;
        }

        const t = ((h1 + cl + dr) >>> 0);
        h1 = ((h2 + dl + er) >>> 0);
        h2 = ((h3 + el + ar) >>> 0);
        h3 = ((h4 + al + br) >>> 0);
        h4 = ((h0 + bl + cr) >>> 0);
        h0 = t;
    }

    // Output as 5 x 32-bit little-endian
    const out = new Uint8Array(20);
    writeUint32LE(out, 0, h0);
    writeUint32LE(out, 4, h1);
    writeUint32LE(out, 8, h2);
    writeUint32LE(out, 12, h3);
    writeUint32LE(out, 16, h4);
    return out;
}


// ============================================================
// HMAC-SHA256 (for RFC 6979 deterministic k)
// ============================================================

/**
 * HMAC-SHA256. Returns a 32-byte Uint8Array.
 * Equivalent to Python: hmac.new(key, msg, hashlib.sha256).digest()
 */
function hmacSha256(key, message) {
    if (!(key instanceof Uint8Array)) key = new Uint8Array(key);
    if (!(message instanceof Uint8Array)) message = new Uint8Array(message);

    const blockSize = 64; // SHA-256 block size

    // If key is longer than block size, hash it
    if (key.length > blockSize) {
        key = sha256(key);
    }

    // Pad key to block size
    const paddedKey = new Uint8Array(blockSize);
    paddedKey.set(key);

    // Inner and outer pads
    const ipad = new Uint8Array(blockSize);
    const opad = new Uint8Array(blockSize);
    for (let i = 0; i < blockSize; i++) {
        ipad[i] = paddedKey[i] ^ 0x36;
        opad[i] = paddedKey[i] ^ 0x5C;
    }

    // HMAC = H(opad || H(ipad || message))
    const inner = sha256(concatBytes(ipad, message));
    return sha256(concatBytes(opad, inner));
}


// ============================================================
// Composite hash functions
// ============================================================

/**
 * hash160(data) = RIPEMD160(SHA256(data))
 * Used in P2WPKH (SegWit) address generation.
 */
function hash160(data) {
    return ripemd160(sha256(data));
}

/**
 * BIP340 tagged hash: SHA256(SHA256(tag) || SHA256(tag) || data)
 * Used in Taproot tweaking, Schnorr challenges, etc.
 */
function taggedHash(tag, data) {
    const tagBytes = new TextEncoder().encode(tag);
    const tagHash = sha256(tagBytes);
    return sha256(concatBytes(tagHash, tagHash, data));
}


// ============================================================
// secp256k1 curve parameters and point arithmetic
// ============================================================

const P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2Fn;
const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const G_X = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n;
const G_Y = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n;

/**
 * Modular exponentiation: base^exp mod m.
 * JS BigInt has no built-in modPow, so we implement binary exponentiation.
 */
function modPow(base, exp, m) {
    base = ((base % m) + m) % m;
    let result = 1n;
    while (exp > 0n) {
        if (exp & 1n) {
            result = (result * base) % m;
        }
        exp >>= 1n;
        base = (base * base) % m;
    }
    return result;
}

/**
 * Extended Euclidean algorithm. Returns [g, x, y] such that a*x + b*y = g.
 */
function _extendedGcd(a, b) {
    if (a === 0n) return [b, 0n, 1n];
    const [g, x, y] = _extendedGcd(b % a, a);
    return [g, y - (b / a) * x, x];
}

/**
 * Modular multiplicative inverse: a^(-1) mod m.
 */
function _modinv(a, m) {
    a = ((a % m) + m) % m;
    const [g, x] = _extendedGcd(a, m);
    if (g !== 1n) throw new Error('Modular inverse does not exist');
    return ((x % m) + m) % m;
}

/**
 * Add two points on secp256k1. Points are [x, y] arrays or null for infinity.
 */
function pointAdd(p1, p2) {
    if (p1 === null) return p2;
    if (p2 === null) return p1;
    const [x1, y1] = p1;
    const [x2, y2] = p2;

    let lam;
    if (x1 === x2) {
        if (y1 !== y2) return null; // Point at infinity
        // Point doubling
        lam = (3n * x1 * x1 * _modinv(2n * y1, P)) % P;
    } else {
        lam = (((y2 - y1) % P + P) % P * _modinv(((x2 - x1) % P + P) % P, P)) % P;
    }

    const x3 = ((lam * lam - x1 - x2) % P + P) % P;
    const y3 = ((lam * ((x1 - x3 + P) % P) - y1) % P + P) % P;
    return [x3, y3];
}

/**
 * Scalar multiplication on secp256k1 using double-and-add.
 * If point is omitted, uses generator G.
 */
function pointMul(k, point) {
    if (point === undefined) point = [G_X, G_Y];
    let result = null;
    let addend = point;
    while (k > 0n) {
        if (k & 1n) {
            result = pointAdd(result, addend);
        }
        addend = pointAdd(addend, addend);
        k >>= 1n;
    }
    return result;
}

/**
 * Decode a public key from bytes (compressed, uncompressed, or x-only).
 */
function pointFromBytes(data) {
    if (data.length === 33) {
        // Compressed
        const prefix = data[0];
        const x = bytesToBigInt(data.slice(1));
        const ySq = (modPow(x, 3n, P) + 7n) % P;
        let y = modPow(ySq, (P + 1n) / 4n, P);
        if (y % 2n !== BigInt(prefix - 2)) {
            y = P - y;
        }
        return [x, y];
    } else if (data.length === 65) {
        // Uncompressed
        const x = bytesToBigInt(data.slice(1, 33));
        const y = bytesToBigInt(data.slice(33));
        return [x, y];
    } else if (data.length === 32) {
        // x-only (BIP340)
        const x = bytesToBigInt(data);
        const ySq = (modPow(x, 3n, P) + 7n) % P;
        let y = modPow(ySq, (P + 1n) / 4n, P);
        if (y % 2n !== 0n) {
            y = P - y;
        }
        return [x, y];
    }
    throw new Error(`Invalid public key length: ${data.length}`);
}

/**
 * Lift x coordinate to a point with even y (BIP340).
 */
function _liftX(xBytes) {
    const x = bytesToBigInt(xBytes);
    if (x >= P) return null;
    const ySq = (modPow(x, 3n, P) + 7n) % P;
    const y = modPow(ySq, (P + 1n) / 4n, P);
    if (modPow(y, 2n, P) !== ySq) return null;
    return y % 2n !== 0n ? [x, P - y] : [x, y];
}


// ============================================================
// Key generation
// ============================================================

/**
 * Generate a cryptographically secure random private key for secp256k1.
 * Uses crypto.getRandomValues() (Web Crypto CSPRNG).
 * Rejection sampling ensures key is in valid range [1, N-1].
 */
function generatePrivateKey() {
    while (true) {
        const keyBytes = new Uint8Array(32);
        crypto.getRandomValues(keyBytes);
        const keyInt = bytesToBigInt(keyBytes);
        if (keyInt > 0n && keyInt < N) {
            return keyBytes;
        }
    }
}

/**
 * Derive the public key from a private key.
 * Returns compressed (33 bytes) or uncompressed (65 bytes) public key.
 */
function privateKeyToPublicKey(privkeyBytes, compressed) {
    if (compressed === undefined) compressed = true;
    const k = bytesToBigInt(privkeyBytes);
    const point = pointMul(k);
    const x = bigIntToBytes(point[0], 32);
    const y = bigIntToBytes(point[1], 32);
    if (compressed) {
        const prefix = new Uint8Array([point[1] % 2n === 0n ? 0x02 : 0x03]);
        return concatBytes(prefix, x);
    }
    return concatBytes(new Uint8Array([0x04]), x, y);
}

/**
 * Derive x-only public key (BIP340) from private key.
 * Returns { xOnly: Uint8Array(32), negated: boolean }
 */
function privateKeyToXonlyPubkey(privkeyBytes) {
    const k = bytesToBigInt(privkeyBytes);
    const point = pointMul(k);
    const xBytes = bigIntToBytes(point[0], 32);
    const negated = point[1] % 2n !== 0n;
    return { xOnly: xBytes, negated };
}


// ============================================================
// Bech32 / Bech32m encoding (BIP173 / BIP350)
// ============================================================

const BECH32_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const BECH32_CONST = 1;
const BECH32M_CONST = 0x2bc830a3;

function _bech32Polymod(values) {
    const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    let chk = 1;
    for (const v of values) {
        const b = chk >>> 25;
        chk = ((chk & 0x1ffffff) << 5) ^ v;
        for (let i = 0; i < 5; i++) {
            if ((b >>> i) & 1) chk ^= GEN[i];
        }
    }
    return chk;
}

function _bech32HrpExpand(hrp) {
    const ret = [];
    for (const c of hrp) ret.push(c.charCodeAt(0) >> 5);
    ret.push(0);
    for (const c of hrp) ret.push(c.charCodeAt(0) & 31);
    return ret;
}

function _bech32CreateChecksum(hrp, data, spec) {
    const cnst = spec === 'bech32m' ? BECH32M_CONST : BECH32_CONST;
    const values = _bech32HrpExpand(hrp).concat(data);
    const polymod = _bech32Polymod(values.concat([0, 0, 0, 0, 0, 0])) ^ cnst;
    return [0, 1, 2, 3, 4, 5].map(i => (polymod >> (5 * (5 - i))) & 31);
}

function _bech32VerifyChecksum(hrp, data, spec) {
    const cnst = spec === 'bech32m' ? BECH32M_CONST : BECH32_CONST;
    return _bech32Polymod(_bech32HrpExpand(hrp).concat(data)) === cnst;
}

/**
 * General power-of-2 base conversion.
 */
function _convertBits(data, frombits, tobits, pad) {
    if (pad === undefined) pad = true;
    let acc = 0, bits = 0;
    const ret = [];
    const maxv = (1 << tobits) - 1;
    for (const value of data) {
        if (value < 0 || (value >> frombits)) return null;
        acc = (acc << frombits) | value;
        bits += frombits;
        while (bits >= tobits) {
            bits -= tobits;
            ret.push((acc >> bits) & maxv);
        }
    }
    if (pad) {
        if (bits) ret.push((acc << (tobits - bits)) & maxv);
    } else if (bits >= frombits || ((acc << (tobits - bits)) & maxv)) {
        return null;
    }
    return ret;
}

/**
 * Encode a segwit address.
 */
function bech32Encode(hrp, witver, witprog, spec) {
    if (spec === undefined) spec = 'bech32';
    const data = [witver].concat(_convertBits(witprog, 8, 5));
    const checksum = _bech32CreateChecksum(hrp, data, spec);
    return hrp + '1' + data.concat(checksum).map(d => BECH32_CHARSET[d]).join('');
}

/**
 * Decode a bech32/bech32m address.
 * Returns { hrp, witver, witprog, spec } or { hrp: null } on failure.
 */
function bech32Decode(addr) {
    const fail = { hrp: null, witver: null, witprog: null, spec: null };
    if (addr.toLowerCase() !== addr && addr.toUpperCase() !== addr) return fail;
    addr = addr.toLowerCase();
    const pos = addr.lastIndexOf('1');
    if (pos < 1 || pos + 7 > addr.length || addr.length > 90) return fail;
    const hrp = addr.slice(0, pos);
    const data = [];
    for (const c of addr.slice(pos + 1)) {
        const idx = BECH32_CHARSET.indexOf(c);
        if (idx === -1) return fail;
        data.push(idx);
    }

    // Try bech32m first (witness version >= 1), then bech32
    for (const spec of ['bech32m', 'bech32']) {
        if (_bech32VerifyChecksum(hrp, data, spec)) {
            const witver = data[0];
            const witprog = _convertBits(data.slice(1, -6), 5, 8, false);
            if (witprog === null) return fail;
            return { hrp, witver, witprog: new Uint8Array(witprog), spec };
        }
    }
    return fail;
}

/**
 * Return bech32 HRP for the given network.
 */
function _networkHrp(network) {
    const map = { mainnet: 'bc', testnet4: 'tb', regtest: 'bcrt' };
    const hrp = map[network];
    if (!hrp) throw new Error(`Unknown network: ${network}`);
    return hrp;
}


// ============================================================
// Base58 encoding (for WIF)
// ============================================================

const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

/**
 * Encode bytes to base58.
 */
function base58Encode(data) {
    let n = bytesToBigInt(data);
    let result = '';
    while (n > 0n) {
        const rem = Number(n % 58n);
        n = n / 58n;
        result = BASE58_ALPHABET[rem] + result;
    }
    // Add leading '1's for leading zero bytes
    for (const byte of data) {
        if (byte === 0) result = '1' + result;
        else break;
    }
    return result;
}

/**
 * Decode a base58 string to Uint8Array.
 */
function base58Decode(s) {
    let n = 0n;
    for (const char of s) {
        const idx = BASE58_ALPHABET.indexOf(char);
        if (idx === -1) throw new Error(`Invalid base58 character: ${char}`);
        n = n * 58n + BigInt(idx);
    }
    // Convert to bytes
    const result = [];
    while (n > 0n) {
        result.push(Number(n % 256n));
        n = n / 256n;
    }
    result.reverse();
    // Add leading zero bytes for leading '1' characters
    let leadingOnes = 0;
    for (const char of s) {
        if (char === '1') leadingOnes++;
        else break;
    }
    const leading = new Uint8Array(leadingOnes);
    return concatBytes(leading, new Uint8Array(result));
}


// ============================================================
// WIF encoding / decoding
// ============================================================

/**
 * Convert private key to Wallet Import Format (WIF).
 */
function privateKeyToWif(privkeyBytes, compressed, network) {
    if (compressed === undefined) compressed = true;
    if (network === undefined) network = 'mainnet';
    const prefix = network === 'mainnet' ? 0x80 : 0xEF;
    let payload = concatBytes(new Uint8Array([prefix]), privkeyBytes);
    if (compressed) {
        payload = concatBytes(payload, new Uint8Array([0x01]));
    }
    const checksum = sha256(sha256(payload)).slice(0, 4);
    return base58Encode(concatBytes(payload, checksum));
}

/**
 * Decode a WIF-encoded private key back to raw 32-byte key.
 * Returns { privateKey: Uint8Array, compressed: boolean, mainnet: boolean }
 */
function wifToPrivateKey(wifString) {
    const raw = base58Decode(wifString);

    // Verify checksum (last 4 bytes)
    const payload = raw.slice(0, -4);
    const checksum = raw.slice(-4);
    const expectedChecksum = sha256(sha256(payload)).slice(0, 4);

    for (let i = 0; i < 4; i++) {
        if (checksum[i] !== expectedChecksum[i]) {
            throw new Error('Invalid WIF checksum');
        }
    }

    // Parse version byte
    const version = payload[0];
    let mainnet;
    if (version === 0x80) {
        mainnet = true;
    } else if (version === 0xEF) {
        mainnet = false;
    } else {
        throw new Error(`Unknown WIF version byte: 0x${version.toString(16)}`);
    }

    // Parse key data
    const keyData = payload.slice(1);
    let privateKey, compressed;
    if (keyData.length === 33 && keyData[32] === 0x01) {
        privateKey = keyData.slice(0, 32);
        compressed = true;
    } else if (keyData.length === 32) {
        privateKey = keyData;
        compressed = false;
    } else {
        throw new Error(`Invalid WIF key data length: ${keyData.length}`);
    }

    // Validate key is in valid range
    const k = bytesToBigInt(privateKey);
    if (k <= 0n || k >= N) {
        throw new Error('Private key out of valid range');
    }

    return { privateKey, compressed, mainnet };
}


// ============================================================
// Compact size encoding (for script serialization)
// ============================================================

/**
 * Encode a compact size integer (Bitcoin varint).
 */
function _compactSize(n) {
    if (n < 253) {
        return new Uint8Array([n]);
    } else if (n <= 0xFFFF) {
        const buf = new Uint8Array(3);
        buf[0] = 0xFD;
        writeUint16LE(buf, 1, n);
        return buf;
    } else if (n <= 0xFFFFFFFF) {
        const buf = new Uint8Array(5);
        buf[0] = 0xFE;
        writeUint32LE(buf, 1, n);
        return buf;
    } else {
        const buf = new Uint8Array(9);
        buf[0] = 0xFF;
        writeUint64LE(buf, 1, BigInt(n));
        return buf;
    }
}


// ============================================================
// Taproot tweaking (BIP341)
// ============================================================

/**
 * Compute the taproot tweak per BIP341.
 */
function computeTaptweak(pubkeyXBytes, scriptTreeHash) {
    if (scriptTreeHash) {
        return taggedHash('TapTweak', concatBytes(pubkeyXBytes, scriptTreeHash));
    }
    return taggedHash('TapTweak', pubkeyXBytes);
}

/**
 * Apply the taproot tweak to an internal public key.
 * Returns { outputKeyX: Uint8Array(32), parity: number }
 */
function taprootTweakPubkey(internalPubkeyX, scriptTreeHash) {
    const pPoint = _liftX(internalPubkeyX);
    if (pPoint === null) throw new Error('Invalid internal public key');

    const tweakBytes = computeTaptweak(internalPubkeyX, scriptTreeHash);
    const tweakInt = bytesToBigInt(tweakBytes);
    if (tweakInt >= N) throw new Error('Tweak is too large');

    // Q = P + t*G
    const tPoint = pointMul(tweakInt);
    const Q = pointAdd(pPoint, tPoint);
    if (Q === null) throw new Error('Resulting point is at infinity');

    const parity = Number(Q[1] % 2n);
    const outputKeyX = bigIntToBytes(Q[0], 32);
    return { outputKeyX, parity };
}

/**
 * Compute the tweaked private key for key-path spending.
 * Returns the tweaked private key bytes.
 */
function taprootTweakSeckey(privkeyBytes, scriptTreeHash) {
    let k = bytesToBigInt(privkeyBytes);
    const point = pointMul(k);

    // If y is odd, negate the private key
    if (point[1] % 2n !== 0n) {
        k = N - k;
    }

    const xBytes = bigIntToBytes(point[0], 32);
    const tweak = computeTaptweak(xBytes, scriptTreeHash);
    const tweakInt = bytesToBigInt(tweak);

    const tweakedK = (k + tweakInt) % N;
    return bigIntToBytes(tweakedK, 32);
}

/**
 * Compute a script tree hash for a simple backup key spending path.
 * Creates a Tapscript leaf: <backup_pubkey> OP_CHECKSIG
 */
function computeScriptTreeHashForBackup(backupPubkeyX) {
    // Build tapscript: OP_PUSH32 <32-byte-xonly-pubkey> OP_CHECKSIG
    const script = concatBytes(new Uint8Array([0x20]), backupPubkeyX, new Uint8Array([0xAC]));

    // Leaf version 0xC0 (tapscript)
    const leafVersion = 0xC0;

    // TapLeaf = tagged_hash("TapLeaf", leaf_version || compact_size(script) || script)
    const leafData = concatBytes(
        new Uint8Array([leafVersion]),
        _compactSize(script.length),
        script
    );
    return taggedHash('TapLeaf', leafData);
}

/**
 * Compute the control block for script path spending.
 */
function computeControlBlock(internalPubkeyX, outputParity, merklePath) {
    const leafVersion = 0xC0;
    const parityByte = leafVersion | (outputParity & 1);
    let cb = concatBytes(new Uint8Array([parityByte]), internalPubkeyX);
    if (merklePath) {
        for (const h of merklePath) {
            cb = concatBytes(cb, h);
        }
    }
    return cb;
}


// ============================================================
// Address generation
// ============================================================

/**
 * Derive SegWit (P2WPKH) address from a raw private key.
 */
function deriveSegwitAddressFromPrivkey(privkeyBytes, network) {
    if (network === undefined) network = 'mainnet';
    const pubkey = privateKeyToPublicKey(privkeyBytes, true);
    const pubkeyHash = hash160(pubkey);
    const hrp = _networkHrp(network);
    const address = bech32Encode(hrp, 0, Array.from(pubkeyHash), 'bech32');
    // scriptPubKey = OP_0 (0x00) + PUSH20 (0x14) + pubkey_hash
    const scriptpubkey = concatBytes(new Uint8Array([0x00, 0x14]), pubkeyHash);
    return { address, pubkey, pubkeyHash, scriptpubkey };
}

/**
 * Derive Taproot (P2TR) address from a tweaked private key.
 */
function deriveTaprootAddressFromTweakedPrivkey(tweakedPrivkeyBytes, network) {
    if (network === undefined) network = 'mainnet';
    const { xOnly } = privateKeyToXonlyPubkey(tweakedPrivkeyBytes);
    const hrp = _networkHrp(network);
    const address = bech32Encode(hrp, 1, Array.from(xOnly), 'bech32m');
    const scriptpubkey = concatBytes(new Uint8Array([0x51, 0x20]), xOnly);
    return { address, outputKeyX: xOnly, scriptpubkey };
}

/**
 * Generate a SegWit (P2WPKH, native bech32) address.
 */
function generateSegwitAddress(network) {
    if (network === undefined) network = 'mainnet';
    const privkey = generatePrivateKey();
    const pubkey = privateKeyToPublicKey(privkey, true);
    const witnessProgram = hash160(pubkey);
    const hrp = _networkHrp(network);
    const address = bech32Encode(hrp, 0, Array.from(witnessProgram), 'bech32');
    const wif = privateKeyToWif(privkey, true, network);

    return {
        address,
        private_key_wif: wif,
        private_key_hex: bytesToHex(privkey),
        public_key_hex: bytesToHex(pubkey),
        type: 'segwit_p2wpkh',
    };
}

/**
 * Generate a Taproot (P2TR, bech32m) address.
 */
function generateTaprootAddress(network, backupKey) {
    if (network === undefined) network = 'mainnet';
    if (backupKey === undefined) backupKey = false;

    // Generate the internal key
    const privkey = generatePrivateKey();
    const { xOnly: internalPubkeyX } = privateKeyToXonlyPubkey(privkey);

    let scriptTreeHash = null;
    let backupInfo = {};

    if (backupKey) {
        // Generate backup key
        const backupPrivkey = generatePrivateKey();
        const { xOnly: backupPubkeyX } = privateKeyToXonlyPubkey(backupPrivkey);

        // Compute script tree hash for backup key spending path
        scriptTreeHash = computeScriptTreeHashForBackup(backupPubkeyX);

        const backupWif = privateKeyToWif(backupPrivkey, true, network);
        backupInfo = {
            backup_private_key_wif: backupWif,
            backup_private_key_hex: bytesToHex(backupPrivkey),
            backup_pubkey_hex: bytesToHex(backupPubkeyX),
            script_tree_hash: bytesToHex(scriptTreeHash),
        };
    }

    // Compute the tweaked output key
    const { outputKeyX, parity } = taprootTweakPubkey(internalPubkeyX, scriptTreeHash);

    // Compute the tweaked private key for key-path spending
    const tweakedPrivkey = taprootTweakSeckey(privkey, scriptTreeHash);

    // Create bech32m address (witness version 1)
    const hrp = _networkHrp(network);
    const address = bech32Encode(hrp, 1, Array.from(outputKeyX), 'bech32m');

    const wif = privateKeyToWif(privkey, true, network);

    const result = {
        address,
        private_key_wif: wif,
        private_key_hex: bytesToHex(privkey),
        internal_pubkey_hex: bytesToHex(internalPubkeyX),
        output_pubkey_hex: bytesToHex(outputKeyX),
        tweaked_private_key_hex: bytesToHex(tweakedPrivkey),
        output_parity: parity,
        type: 'taproot_p2tr',
        has_backup: backupKey,
    };

    Object.assign(result, backupInfo);
    return result;
}


// ============================================================
// Verification utilities
// ============================================================

/**
 * Verify a bitcoin address and return its type.
 */
function verifyAddress(address) {
    const { hrp, witver, witprog, spec } = bech32Decode(address);
    if (hrp === null) return null;

    if (witver === 0 && witprog.length === 20) {
        return { type: 'p2wpkh', witness_version: 0, program: bytesToHex(witprog), spec };
    } else if (witver === 1 && witprog.length === 32) {
        return { type: 'p2tr', witness_version: 1, program: bytesToHex(witprog), spec };
    }
    return { type: 'unknown', witness_version: witver, program: bytesToHex(witprog), spec };
}

/**
 * Verify that a private key corresponds to the given address.
 */
function verifyKeypair(privkeyHex, address) {
    const privkey = hexToBytes(privkeyHex);
    const k = bytesToBigInt(privkey);
    if (k <= 0n || k >= N) return { valid: false, message: 'Invalid private key' };

    const info = verifyAddress(address);
    if (info === null) return { valid: false, message: 'Invalid address' };

    if (info.type === 'p2wpkh') {
        const pubkey = privateKeyToPublicKey(privkey, true);
        const expectedProgram = bytesToHex(hash160(pubkey));
        if (expectedProgram === info.program) {
            return { valid: true, message: 'Key matches P2WPKH address' };
        }
        return { valid: false, message: `Key does not match: expected ${expectedProgram}, got ${info.program}` };
    } else if (info.type === 'p2tr') {
        const { xOnly } = privateKeyToXonlyPubkey(privkey);
        const { outputKeyX } = taprootTweakPubkey(xOnly, null);
        if (bytesToHex(outputKeyX) === info.program) {
            return { valid: true, message: 'Key matches P2TR address (key-only path)' };
        }
        return { valid: false, message: 'Key does not match P2TR output (may have script tree)' };
    }
    return { valid: false, message: `Unsupported address type: ${info.type}` };
}

/**
 * Convert a scriptPubKey from an address.
 * Used in transaction construction (Phase 3).
 */
function _addressToScriptpubkey(address) {
    const { hrp, witver, witprog } = bech32Decode(address);
    if (hrp === null) throw new Error(`Invalid address: ${address}`);

    if (witver === 0 && witprog.length === 20) {
        // P2WPKH: OP_0 PUSH20 <hash>
        return concatBytes(new Uint8Array([0x00, 0x14]), witprog);
    } else if (witver === 1 && witprog.length === 32) {
        // P2TR: OP_1 PUSH32 <key>
        return concatBytes(new Uint8Array([0x51, 0x20]), witprog);
    }
    throw new Error(`Unsupported witness program: version=${witver}, length=${witprog.length}`);
}


// ============================================================
// ECDSA signing (for SegWit P2WPKH spending)
// ============================================================

/**
 * RFC 6979 deterministic k generation for ECDSA.
 */
function _deterministicK(privkeyInt, msgHash, extraEntropy) {
    const x = bigIntToBytes(privkeyInt, 32);
    const h1 = msgHash;
    let V = new Uint8Array(32).fill(0x01);
    let K = new Uint8Array(32).fill(0x00);
    const extra = extraEntropy || new Uint8Array(0);

    K = hmacSha256(K, concatBytes(V, new Uint8Array([0x00]), x, h1, extra));
    V = hmacSha256(K, V);
    K = hmacSha256(K, concatBytes(V, new Uint8Array([0x01]), x, h1, extra));
    V = hmacSha256(K, V);

    while (true) {
        V = hmacSha256(K, V);
        const k = bytesToBigInt(V);
        if (k > 0n && k < N) return k;
        K = hmacSha256(K, concatBytes(V, new Uint8Array([0x00])));
        V = hmacSha256(K, V);
    }
}

/**
 * ECDSA sign a 32-byte message hash. Returns DER-encoded signature.
 */
function ecdsaSign(privkeyBytes, msgHash) {
    const d = bytesToBigInt(privkeyBytes);
    const z = bytesToBigInt(msgHash);
    const k = _deterministicK(d, msgHash);
    const R = pointMul(k);
    const r = R[0] % N;
    if (r === 0n) throw new Error('r is zero');
    const kInv = modPow(k, N - 2n, N);
    let s = (kInv * ((z + r * d) % N)) % N;
    if (s === 0n) throw new Error('s is zero');
    // Low-s normalization (BIP62)
    if (s > N / 2n) s = N - s;
    return _derEncodeSig(r, s);
}

/**
 * DER-encode an ECDSA signature (r, s).
 */
function _derEncodeSig(r, s) {
    function intToDer(v) {
        // Minimum bytes needed
        let byteLen = 1;
        let tmp = v;
        while (tmp > 0xFFn) { byteLen++; tmp >>= 8n; }
        let b = bigIntToBytes(v, byteLen);
        // If high bit set, prepend 0x00
        if (b[0] & 0x80) {
            b = concatBytes(new Uint8Array([0x00]), b);
        }
        return concatBytes(new Uint8Array([0x02, b.length]), b);
    }
    const rb = intToDer(r);
    const sb = intToDer(s);
    return concatBytes(new Uint8Array([0x30, rb.length + sb.length]), rb, sb);
}


// ============================================================
// Schnorr signing (BIP340, for Taproot spending)
// ============================================================

/**
 * BIP340 Schnorr signature over a 32-byte message hash.
 * Returns 64-byte signature.
 */
function schnorrSign(privkeyBytes, msgHash, auxRand) {
    if (!auxRand) {
        auxRand = new Uint8Array(32);
        crypto.getRandomValues(auxRand);
    }

    const d0 = bytesToBigInt(privkeyBytes);
    if (d0 === 0n || d0 >= N) throw new Error('Invalid private key');

    const pub = pointMul(d0);
    // Negate d if y is odd
    const d = pub[1] % 2n === 0n ? d0 : N - d0;
    const pkBytes = bigIntToBytes(pub[0], 32);

    // t = d XOR tagged_hash("BIP0340/aux", aux_rand)
    const tHash = taggedHash('BIP0340/aux', auxRand);
    const tInt = d ^ bytesToBigInt(tHash);
    const t = bigIntToBytes(tInt, 32);

    // nonce: k0 = tagged_hash("BIP0340/nonce", t || pk || msg) mod n
    const nonceHash = taggedHash('BIP0340/nonce', concatBytes(t, pkBytes, msgHash));
    const k0 = bytesToBigInt(nonceHash) % N;
    if (k0 === 0n) throw new Error('Nonce is zero');

    const R = pointMul(k0);
    const k = R[1] % 2n === 0n ? k0 : N - k0;
    const rBytes = bigIntToBytes(R[0], 32);

    // e = tagged_hash("BIP0340/challenge", R.x || P.x || msg) mod n
    const eHash = taggedHash('BIP0340/challenge', concatBytes(rBytes, pkBytes, msgHash));
    const e = bytesToBigInt(eHash) % N;

    const sigS = (k + e * d) % N;
    const sig = concatBytes(rBytes, bigIntToBytes(sigS, 32));

    // Verify before returning
    if (!schnorrVerify(pkBytes, msgHash, sig)) {
        throw new Error('Generated signature failed verification');
    }
    return sig;
}

/**
 * BIP340 Schnorr signature verification.
 */
function schnorrVerify(pubkeyXBytes, msgHash, sig) {
    if (sig.length !== 64) return false;
    const rBytes = sig.slice(0, 32);
    const sBytes = sig.slice(32);
    const r = bytesToBigInt(rBytes);
    const s = bytesToBigInt(sBytes);
    if (r >= P || s >= N) return false;

    const eHash = taggedHash('BIP0340/challenge', concatBytes(rBytes, pubkeyXBytes, msgHash));
    const e = bytesToBigInt(eHash) % N;

    const pPoint = _liftX(pubkeyXBytes);
    if (pPoint === null) return false;

    // R = s*G - e*P
    const sG = pointMul(s);
    const eP = pointMul(e, pPoint);
    const negEP = eP ? [eP[0], P - eP[1]] : null;
    const R = pointAdd(sG, negEP);

    if (R === null) return false;
    if (R[1] % 2n !== 0n) return false;
    if (R[0] !== r) return false;
    return true;
}


// ============================================================
// Transaction construction helpers
// ============================================================

/**
 * Serialize a single witness stack item.
 */
function _serializeWitnessItem(data) {
    return concatBytes(_compactSize(data.length), data);
}

/**
 * Build the scriptCode for P2WPKH BIP143 sighash.
 * scriptCode = OP_DUP OP_HASH160 <20-byte hash> OP_EQUALVERIFY OP_CHECKSIG
 */
function buildP2wpkhScriptcode(pubkeyHash) {
    return concatBytes(new Uint8Array([0x19, 0x76, 0xA9, 0x14]), pubkeyHash, new Uint8Array([0x88, 0xAC]));
}

/**
 * Helper: write a 64-bit signed integer in LE (for struct.pack('<q', n)).
 * Value is a regular JS number (safe up to 2^53).
 */
function _writeInt64LE(buf, offset, value) {
    // Convert to BigInt for correct bit handling with negative values
    const big = BigInt(value);
    writeUint64LE(buf, offset, big < 0n ? big + (1n << 64n) : big);
}

/**
 * Build a buffer from parts (each part is a Uint8Array or function returning one).
 */
function _buildBuffer(...parts) {
    return concatBytes(...parts);
}


// ============================================================
// Sighash computation
// ============================================================

/**
 * Compute BIP143 sighash for SegWit v0 (P2WPKH).
 * inputs: array of { txid: Uint8Array, vout: number, sequence: number }
 * outputs: array of { value: number, scriptPubKey: Uint8Array }
 */
function sighashSegwitV0Full(txVersion, txLocktime, inputs, outputs,
                              inputIdx, scriptcode, value, hashType) {
    if (hashType === undefined) hashType = 0x01;

    // hashPrevouts
    const prevoutParts = [];
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        prevoutParts.push(inp.txid, voutBuf);
    }
    const hashPrevouts = sha256(sha256(concatBytes(...prevoutParts)));

    // hashSequence
    const seqParts = [];
    for (const inp of inputs) {
        const seqBuf = new Uint8Array(4);
        writeUint32LE(seqBuf, 0, inp.sequence);
        seqParts.push(seqBuf);
    }
    const hashSequence = sha256(sha256(concatBytes(...seqParts)));

    // hashOutputs
    const outParts = [];
    for (const out of outputs) {
        const valBuf = new Uint8Array(8);
        _writeInt64LE(valBuf, 0, out.value);
        outParts.push(valBuf, _compactSize(out.scriptPubKey.length), out.scriptPubKey);
    }
    const hashOutputs = sha256(sha256(concatBytes(...outParts)));

    // BIP143 preimage
    const inp = inputs[inputIdx];
    const voutBuf = new Uint8Array(4);
    writeUint32LE(voutBuf, 0, inp.vout);
    const valueBuf = new Uint8Array(8);
    _writeInt64LE(valueBuf, 0, value);
    const seqBuf = new Uint8Array(4);
    writeUint32LE(seqBuf, 0, inp.sequence);
    const versionBuf = new Uint8Array(4);
    writeUint32LE(versionBuf, 0, txVersion);
    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, txLocktime);
    const htBuf = new Uint8Array(4);
    writeUint32LE(htBuf, 0, hashType);

    const preimage = concatBytes(
        versionBuf,
        hashPrevouts,
        hashSequence,
        inp.txid, voutBuf,
        scriptcode,
        valueBuf,
        seqBuf,
        hashOutputs,
        locktimeBuf,
        htBuf
    );

    return sha256(sha256(preimage));
}

/**
 * Compute BIP341 sighash for Taproot key path spending.
 */
function sighashTaprootKeypath(txVersion, txLocktime, inputs, outputs,
                                inputIdx, amounts, scriptpubkeys, hashType) {
    if (hashType === undefined) hashType = 0x00;

    const versionBuf = new Uint8Array(4);
    writeInt32LE(versionBuf, 0, txVersion);
    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, txLocktime);

    // sha_prevouts
    const prevoutParts = [];
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        prevoutParts.push(inp.txid, voutBuf);
    }
    const shaPrevouts = sha256(concatBytes(...prevoutParts));

    // sha_amounts
    const amtParts = [];
    for (const amt of amounts) {
        const b = new Uint8Array(8);
        _writeInt64LE(b, 0, amt);
        amtParts.push(b);
    }
    const shaAmounts = sha256(concatBytes(...amtParts));

    // sha_scriptpubkeys
    const spkParts = [];
    for (const spk of scriptpubkeys) {
        spkParts.push(_compactSize(spk.length), spk);
    }
    const shaScriptpubkeys = sha256(concatBytes(...spkParts));

    // sha_sequences
    const seqParts = [];
    for (const inp of inputs) {
        const b = new Uint8Array(4);
        writeUint32LE(b, 0, inp.sequence);
        seqParts.push(b);
    }
    const shaSequences = sha256(concatBytes(...seqParts));

    // sha_outputs
    const outParts = [];
    for (const out of outputs) {
        const b = new Uint8Array(8);
        _writeInt64LE(b, 0, out.value);
        outParts.push(b, _compactSize(out.scriptPubKey.length), out.scriptPubKey);
    }
    const shaOutputs = sha256(concatBytes(...outParts));

    // input_index
    const idxBuf = new Uint8Array(4);
    writeUint32LE(idxBuf, 0, inputIdx);

    const preimage = concatBytes(
        new Uint8Array([0x00]),      // epoch
        new Uint8Array([hashType]),
        versionBuf,
        locktimeBuf,
        shaPrevouts,
        shaAmounts,
        shaScriptpubkeys,
        shaSequences,
        shaOutputs,
        new Uint8Array([0x00]),      // spend_type: key path, no annex
        idxBuf
    );

    return taggedHash('TapSighash', preimage);
}

/**
 * Compute BIP341 sighash for Taproot script path spending.
 */
function sighashTaprootScriptPath(txVersion, txLocktime, inputs, outputs,
                                    inputIdx, amounts, scriptpubkeys,
                                    leafScript, leafVersion, hashType) {
    if (leafVersion === undefined) leafVersion = 0xC0;
    if (hashType === undefined) hashType = 0x00;

    const versionBuf = new Uint8Array(4);
    writeInt32LE(versionBuf, 0, txVersion);
    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, txLocktime);

    // sha_prevouts
    const prevoutParts = [];
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        prevoutParts.push(inp.txid, voutBuf);
    }
    const shaPrevouts = sha256(concatBytes(...prevoutParts));

    // sha_amounts
    const amtParts = [];
    for (const amt of amounts) {
        const b = new Uint8Array(8);
        _writeInt64LE(b, 0, amt);
        amtParts.push(b);
    }
    const shaAmounts = sha256(concatBytes(...amtParts));

    // sha_scriptpubkeys
    const spkParts = [];
    for (const spk of scriptpubkeys) {
        spkParts.push(_compactSize(spk.length), spk);
    }
    const shaScriptpubkeys = sha256(concatBytes(...spkParts));

    // sha_sequences
    const seqParts = [];
    for (const inp of inputs) {
        const b = new Uint8Array(4);
        writeUint32LE(b, 0, inp.sequence);
        seqParts.push(b);
    }
    const shaSequences = sha256(concatBytes(...seqParts));

    // sha_outputs
    const outParts = [];
    for (const out of outputs) {
        const b = new Uint8Array(8);
        _writeInt64LE(b, 0, out.value);
        outParts.push(b, _compactSize(out.scriptPubKey.length), out.scriptPubKey);
    }
    const shaOutputs = sha256(concatBytes(...outParts));

    // input_index
    const idxBuf = new Uint8Array(4);
    writeUint32LE(idxBuf, 0, inputIdx);

    // tapleaf_hash
    const leafData = concatBytes(
        new Uint8Array([leafVersion]),
        _compactSize(leafScript.length),
        leafScript
    );
    const tapleafHash = taggedHash('TapLeaf', leafData);

    // codesep_pos = 0xFFFFFFFF (no OP_CODESEPARATOR)
    const codesepBuf = new Uint8Array(4);
    writeUint32LE(codesepBuf, 0, 0xFFFFFFFF);

    const preimage = concatBytes(
        new Uint8Array([0x00]),      // epoch
        new Uint8Array([hashType]),
        versionBuf,
        locktimeBuf,
        shaPrevouts,
        shaAmounts,
        shaScriptpubkeys,
        shaSequences,
        shaOutputs,
        new Uint8Array([0x02]),      // spend_type: script path (ext_flag=1, no annex)
        idxBuf,
        tapleafHash,
        new Uint8Array([0x00]),      // key_version
        codesepBuf
    );

    return taggedHash('TapSighash', preimage);
}


// ============================================================
// Transaction builders (multi-input sweep variants)
// ============================================================

/**
 * Build a fully signed SegWit P2WPKH sweep transaction.
 * utxos: array of { txid: hex string, vout: number, value_sat: number }
 */
function buildSignedSegwitSweepTx(privkeyBytes, utxos, destAddress, destValue) {
    const pubkey = privateKeyToPublicKey(privkeyBytes, true);
    const pubkeyHash = hash160(pubkey);
    const scriptcode = buildP2wpkhScriptcode(pubkeyHash);

    const inputs = utxos.map(u => ({
        txid: hexToBytes(u.txid).reverse(),  // internal byte order
        vout: u.vout,
        sequence: 0xFFFFFFFD,
    }));

    const destSpk = _addressToScriptpubkey(destAddress);
    const outputs = [{ value: destValue, scriptPubKey: destSpk }];

    // Sign each input
    const signatures = [];
    for (let idx = 0; idx < inputs.length; idx++) {
        const sighash = sighashSegwitV0Full(
            2, 0, inputs, outputs, idx, scriptcode,
            utxos[idx].value_sat, 0x01
        );
        const sigDer = ecdsaSign(privkeyBytes, sighash);
        signatures.push(concatBytes(sigDer, new Uint8Array([0x01]))); // SIGHASH_ALL
    }

    // Serialize
    const parts = [];
    const versionBuf = new Uint8Array(4);
    writeUint32LE(versionBuf, 0, 2);
    parts.push(versionBuf);
    parts.push(new Uint8Array([0x00, 0x01])); // segwit marker

    parts.push(_compactSize(inputs.length));
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        parts.push(new Uint8Array(inp.txid));
        parts.push(voutBuf);
        parts.push(new Uint8Array([0x00])); // empty scriptSig
        const seqBuf = new Uint8Array(4);
        writeUint32LE(seqBuf, 0, inp.sequence);
        parts.push(seqBuf);
    }

    parts.push(_compactSize(outputs.length));
    for (const out of outputs) {
        const valBuf = new Uint8Array(8);
        _writeInt64LE(valBuf, 0, out.value);
        parts.push(valBuf);
        parts.push(_compactSize(out.scriptPubKey.length));
        parts.push(out.scriptPubKey);
    }

    // Witness data: 2 items per input (sig + pubkey)
    for (const sig of signatures) {
        parts.push(_compactSize(2));
        parts.push(_serializeWitnessItem(sig));
        parts.push(_serializeWitnessItem(pubkey));
    }

    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, 0);
    parts.push(locktimeBuf);

    return bytesToHex(concatBytes(...parts));
}

/**
 * Build a fully signed Taproot key path sweep transaction.
 * utxos: array of { txid: hex string, vout: number, value_sat: number }
 */
function buildSignedTaprootSweepTx(tweakedPrivkeyBytes, utxos, inputScriptpubkey,
                                     destAddress, destValue) {
    const inputs = utxos.map(u => ({
        txid: hexToBytes(u.txid).reverse(),
        vout: u.vout,
        sequence: 0xFFFFFFFD,
    }));

    const amounts = utxos.map(u => u.value_sat);
    const scriptpubkeys = utxos.map(() => inputScriptpubkey);

    const destSpk = _addressToScriptpubkey(destAddress);
    const outputs = [{ value: destValue, scriptPubKey: destSpk }];

    // Sign each input
    const signatures = [];
    for (let idx = 0; idx < inputs.length; idx++) {
        const sh = sighashTaprootKeypath(
            2, 0, inputs, outputs, idx, amounts, scriptpubkeys, 0x00
        );
        signatures.push(schnorrSign(tweakedPrivkeyBytes, sh));
    }

    // Serialize
    const parts = [];
    const versionBuf = new Uint8Array(4);
    writeUint32LE(versionBuf, 0, 2);
    parts.push(versionBuf);
    parts.push(new Uint8Array([0x00, 0x01]));

    parts.push(_compactSize(inputs.length));
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        parts.push(new Uint8Array(inp.txid));
        parts.push(voutBuf);
        parts.push(new Uint8Array([0x00]));
        const seqBuf = new Uint8Array(4);
        writeUint32LE(seqBuf, 0, inp.sequence);
        parts.push(seqBuf);
    }

    parts.push(_compactSize(outputs.length));
    for (const out of outputs) {
        const valBuf = new Uint8Array(8);
        _writeInt64LE(valBuf, 0, out.value);
        parts.push(valBuf);
        parts.push(_compactSize(out.scriptPubKey.length));
        parts.push(out.scriptPubKey);
    }

    // Witness: 1 item per input (just the signature)
    for (const sig of signatures) {
        parts.push(_compactSize(1));
        parts.push(_serializeWitnessItem(sig));
    }

    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, 0);
    parts.push(locktimeBuf);

    return bytesToHex(concatBytes(...parts));
}

/**
 * Build a fully signed Taproot script path sweep transaction (backup key recovery).
 * utxos: array of { txid: hex string, vout: number, value_sat: number }
 */
function buildSignedTaprootScriptpathSweepTx(backupPrivkeyBytes, backupPubkeyX,
                                               internalPubkeyX, outputParity,
                                               utxos, inputScriptpubkey,
                                               destAddress, destValue) {
    const inputs = utxos.map(u => ({
        txid: hexToBytes(u.txid).reverse(),
        vout: u.vout,
        sequence: 0xFFFFFFFD,
    }));

    const amounts = utxos.map(u => u.value_sat);
    const scriptpubkeys = utxos.map(() => inputScriptpubkey);

    const destSpk = _addressToScriptpubkey(destAddress);
    const outputs = [{ value: destValue, scriptPubKey: destSpk }];

    // The tapscript: <backup_pubkey> OP_CHECKSIG
    const leafScript = concatBytes(new Uint8Array([0x20]), backupPubkeyX, new Uint8Array([0xAC]));
    const controlBlock = computeControlBlock(internalPubkeyX, outputParity);

    // Sign each input
    const signatures = [];
    for (let idx = 0; idx < inputs.length; idx++) {
        const sighash = sighashTaprootScriptPath(
            2, 0, inputs, outputs, idx, amounts, scriptpubkeys,
            leafScript, 0xC0, 0x00
        );
        signatures.push(schnorrSign(backupPrivkeyBytes, sighash));
    }

    // Serialize
    const parts = [];
    const versionBuf = new Uint8Array(4);
    writeUint32LE(versionBuf, 0, 2);
    parts.push(versionBuf);
    parts.push(new Uint8Array([0x00, 0x01]));

    parts.push(_compactSize(inputs.length));
    for (const inp of inputs) {
        const voutBuf = new Uint8Array(4);
        writeUint32LE(voutBuf, 0, inp.vout);
        parts.push(new Uint8Array(inp.txid));
        parts.push(voutBuf);
        parts.push(new Uint8Array([0x00]));
        const seqBuf = new Uint8Array(4);
        writeUint32LE(seqBuf, 0, inp.sequence);
        parts.push(seqBuf);
    }

    parts.push(_compactSize(outputs.length));
    for (const out of outputs) {
        const valBuf = new Uint8Array(8);
        _writeInt64LE(valBuf, 0, out.value);
        parts.push(valBuf);
        parts.push(_compactSize(out.scriptPubKey.length));
        parts.push(out.scriptPubKey);
    }

    // Witness: 3 items per input [sig, script, control_block]
    for (const sig of signatures) {
        parts.push(_compactSize(3));
        parts.push(_serializeWitnessItem(sig));
        parts.push(_serializeWitnessItem(leafScript));
        parts.push(_serializeWitnessItem(controlBlock));
    }

    const locktimeBuf = new Uint8Array(4);
    writeUint32LE(locktimeBuf, 0, 0);
    parts.push(locktimeBuf);

    return bytesToHex(concatBytes(...parts));
}


// ============================================================
// Exports (for use in other JS files and tests)
// ============================================================

// Make functions available globally when loaded via <script> tag,
// and also support ES module / Node.js environments.
const BitcoinCrypto = {
    // Byte helpers
    concatBytes,
    bytesToHex,
    hexToBytes,
    bytesToBigInt,
    bigIntToBytes,
    writeUint32LE,
    readUint32LE,
    writeUint64LE,
    writeInt32LE,
    writeUint16LE,

    // Hash functions
    sha256,
    ripemd160,
    hmacSha256,
    hash160,
    taggedHash,

    // secp256k1
    P, N, G_X, G_Y,
    modPow,
    pointAdd,
    pointMul,
    pointFromBytes,
    _liftX,

    // Key generation
    generatePrivateKey,
    privateKeyToPublicKey,
    privateKeyToXonlyPubkey,

    // Bech32
    bech32Encode,
    bech32Decode,
    _networkHrp,
    _convertBits,

    // Base58 / WIF
    base58Encode,
    base58Decode,
    privateKeyToWif,
    wifToPrivateKey,

    // Taproot tweaking
    computeTaptweak,
    taprootTweakPubkey,
    taprootTweakSeckey,
    computeScriptTreeHashForBackup,
    computeControlBlock,

    // Address generation
    deriveSegwitAddressFromPrivkey,
    deriveTaprootAddressFromTweakedPrivkey,
    generateSegwitAddress,
    generateTaprootAddress,

    // Verification
    verifyAddress,
    verifyKeypair,
    _addressToScriptpubkey,

    // Signing (Phase 3)
    ecdsaSign,
    schnorrSign,
    schnorrVerify,

    // Sighash (Phase 3)
    sighashSegwitV0Full,
    sighashTaprootKeypath,
    sighashTaprootScriptPath,
    buildP2wpkhScriptcode,

    // Transaction builders (Phase 3)
    buildSignedSegwitSweepTx,
    buildSignedTaprootSweepTx,
    buildSignedTaprootScriptpathSweepTx,

    // Internal
    _compactSize,
    _modinv,
    _serializeWitnessItem,
};

// Browser global
if (typeof window !== 'undefined') {
    window.BitcoinCrypto = BitcoinCrypto;
}

// Node.js / CommonJS
if (typeof module !== 'undefined' && module.exports) {
    module.exports = BitcoinCrypto;
}
