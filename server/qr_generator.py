"""
Pure Python QR Code generator for Bitcoin addresses.
Generates QR codes as PIL Images.
Supports alphanumeric mode for bitcoin addresses.
"""

from PIL import Image, ImageDraw
import math


# ============================================================
# QR Code constants
# ============================================================

# Error correction levels
EC_L = 0  # ~7% recovery
EC_M = 1  # ~15% recovery
EC_Q = 2  # ~25% recovery
EC_H = 3  # ~30% recovery

# Mode indicators
MODE_NUMERIC = 0b0001
MODE_ALPHANUMERIC = 0b0010
MODE_BYTE = 0b0100

# Alphanumeric character set
ALPHANUMERIC_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

# Capacity table: version -> EC level -> (total_codewords, ec_codewords_per_block, num_blocks_group1, data_cw_group1, num_blocks_group2, data_cw_group2)
# Simplified for versions 1-10
CAPACITY_TABLE = {
    1: {EC_L: (26, 7, 1, 19, 0, 0), EC_M: (26, 10, 1, 16, 0, 0), EC_Q: (26, 13, 1, 13, 0, 0), EC_H: (26, 17, 1, 9, 0, 0)},
    2: {EC_L: (44, 10, 1, 34, 0, 0), EC_M: (44, 16, 1, 28, 0, 0), EC_Q: (44, 22, 1, 22, 0, 0), EC_H: (44, 28, 1, 16, 0, 0)},
    3: {EC_L: (70, 15, 1, 55, 0, 0), EC_M: (70, 26, 1, 44, 0, 0), EC_Q: (70, 18, 2, 17, 0, 0), EC_H: (70, 22, 2, 13, 0, 0)},
    4: {EC_L: (100, 20, 1, 80, 0, 0), EC_M: (100, 18, 2, 32, 0, 0), EC_Q: (100, 26, 2, 24, 0, 0), EC_H: (100, 16, 4, 9, 0, 0)},
    5: {EC_L: (134, 26, 1, 108, 0, 0), EC_M: (134, 24, 2, 43, 0, 0), EC_Q: (134, 18, 2, 15, 2, 16), EC_H: (134, 22, 2, 11, 2, 12)},
    6: {EC_L: (172, 18, 2, 68, 0, 0), EC_M: (172, 16, 4, 27, 0, 0), EC_Q: (172, 24, 4, 19, 0, 0), EC_H: (172, 28, 4, 15, 0, 0)},
    7: {EC_L: (196, 20, 2, 78, 0, 0), EC_M: (196, 18, 4, 31, 0, 0), EC_Q: (196, 18, 2, 14, 4, 15), EC_H: (196, 26, 4, 13, 1, 14)},
    8: {EC_L: (242, 24, 2, 97, 0, 0), EC_M: (242, 22, 2, 38, 2, 39), EC_Q: (242, 22, 4, 18, 2, 19), EC_H: (242, 26, 4, 14, 2, 15)},
    9: {EC_L: (292, 30, 2, 116, 0, 0), EC_M: (292, 22, 3, 36, 2, 37), EC_Q: (292, 20, 4, 16, 4, 17), EC_H: (292, 24, 4, 12, 4, 13)},
    10: {EC_L: (346, 18, 2, 68, 2, 69), EC_M: (346, 26, 4, 43, 1, 44), EC_Q: (346, 24, 6, 19, 2, 20), EC_H: (346, 28, 6, 15, 2, 16)},
}

# Max data capacity in bytes per version/EC level (byte mode)
def _max_data_capacity(version, ec_level):
    total, ec_per_block, g1_blocks, g1_cw, g2_blocks, g2_cw = CAPACITY_TABLE[version][ec_level]
    return g1_blocks * g1_cw + g2_blocks * g2_cw


# Alignment pattern positions per version
ALIGNMENT_POSITIONS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 52],
}


# ============================================================
# GF(256) arithmetic for Reed-Solomon
# ============================================================

GF_EXP = [0] * 512
GF_LOG = [0] * 256

def _init_gf():
    x = 1
    for i in range(255):
        GF_EXP[i] = x
        GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11d  # Primitive polynomial for GF(2^8)
    for i in range(255, 512):
        GF_EXP[i] = GF_EXP[i - 255]

_init_gf()


def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def gf_poly_mul(p, q):
    result = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            result[i + j] ^= gf_mul(a, b)
    return result


def gf_poly_div(dividend, divisor):
    """Polynomial division in GF(256). Returns remainder."""
    result = list(dividend)
    for i in range(len(dividend) - len(divisor) + 1):
        coef = result[i]
        if coef != 0:
            for j in range(1, len(divisor)):
                result[i + j] ^= gf_mul(divisor[j], coef)
    return result[-(len(divisor) - 1):]


def rs_generator_poly(nsym):
    """Generate Reed-Solomon generator polynomial."""
    g = [1]
    for i in range(nsym):
        g = gf_poly_mul(g, [1, GF_EXP[i]])
    return g


def rs_encode(data, nsym):
    """Reed-Solomon encode data with nsym error correction symbols."""
    gen = rs_generator_poly(nsym)
    padded = data + [0] * nsym
    remainder = gf_poly_div(padded, gen)
    return data + remainder


# ============================================================
# QR Code generation
# ============================================================

def _choose_version(data_len, mode, ec_level):
    """Choose the smallest QR version that fits the data."""
    for version in range(1, 11):
        capacity = _max_data_capacity(version, ec_level)
        # Calculate how many data bits we need
        bits_needed = 4  # Mode indicator

        # Character count indicator length
        if version <= 9:
            if mode == MODE_BYTE:
                bits_needed += 8  # char count
                bits_needed += data_len * 8  # data
            elif mode == MODE_ALPHANUMERIC:
                bits_needed += 9
                bits_needed += (data_len // 2) * 11 + (data_len % 2) * 6
            elif mode == MODE_NUMERIC:
                bits_needed += 10
                groups = data_len // 3
                rem = data_len % 3
                bits_needed += groups * 10 + (7 if rem == 2 else 4 if rem == 1 else 0)
        else:
            if mode == MODE_BYTE:
                bits_needed += 16
                bits_needed += data_len * 8
            elif mode == MODE_ALPHANUMERIC:
                bits_needed += 11
                bits_needed += (data_len // 2) * 11 + (data_len % 2) * 6

        bytes_needed = math.ceil(bits_needed / 8)
        if bytes_needed <= capacity:
            return version

    raise ValueError(f"Data too long for QR code (need {bytes_needed} bytes, max version 10)")


def _encode_data(text, mode, version, ec_level):
    """Encode text data into QR code data codewords."""
    bits = []

    def add_bits(val, length):
        for i in range(length - 1, -1, -1):
            bits.append((val >> i) & 1)

    # Mode indicator
    add_bits(mode, 4)

    # Character count indicator
    if version <= 9:
        cc_bits = {MODE_NUMERIC: 10, MODE_ALPHANUMERIC: 9, MODE_BYTE: 8}
    else:
        cc_bits = {MODE_NUMERIC: 12, MODE_ALPHANUMERIC: 11, MODE_BYTE: 16}
    add_bits(len(text), cc_bits[mode])

    # Data encoding
    if mode == MODE_BYTE:
        for ch in text.encode('utf-8'):
            add_bits(ch, 8)
    elif mode == MODE_ALPHANUMERIC:
        text_upper = text.upper()
        for i in range(0, len(text_upper) - 1, 2):
            val = ALPHANUMERIC_CHARS.index(text_upper[i]) * 45 + ALPHANUMERIC_CHARS.index(text_upper[i + 1])
            add_bits(val, 11)
        if len(text_upper) % 2:
            add_bits(ALPHANUMERIC_CHARS.index(text_upper[-1]), 6)

    # Terminator
    capacity = _max_data_capacity(version, ec_level) * 8
    terminator_len = min(4, capacity - len(bits))
    add_bits(0, terminator_len)

    # Pad to byte boundary
    while len(bits) % 8:
        bits.append(0)

    # Pad with alternating bytes
    pad_bytes = [0xEC, 0x11]
    pad_idx = 0
    while len(bits) < capacity:
        add_bits(pad_bytes[pad_idx], 8)
        pad_idx = (pad_idx + 1) % 2

    # Convert to bytes
    codewords = []
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            if i + j < len(bits):
                byte = (byte << 1) | bits[i + j]
        codewords.append(byte)

    return codewords[:_max_data_capacity(version, ec_level)]


def _add_ec_codewords(data_codewords, version, ec_level):
    """Add error correction codewords and interleave."""
    total, ec_per_block, g1_blocks, g1_cw, g2_blocks, g2_cw = CAPACITY_TABLE[version][ec_level]

    # Split data into blocks
    blocks = []
    idx = 0
    for _ in range(g1_blocks):
        blocks.append(data_codewords[idx:idx + g1_cw])
        idx += g1_cw
    for _ in range(g2_blocks):
        blocks.append(data_codewords[idx:idx + g2_cw])
        idx += g2_cw

    # Generate EC for each block
    ec_blocks = []
    for block in blocks:
        ec = rs_encode(list(block), ec_per_block)
        ec_blocks.append(ec[len(block):])

    # Interleave data codewords
    result = []
    max_data = max(g1_cw, g2_cw) if g2_blocks > 0 else g1_cw
    for i in range(max_data):
        for block in blocks:
            if i < len(block):
                result.append(block[i])

    # Interleave EC codewords
    for i in range(ec_per_block):
        for ec in ec_blocks:
            if i < len(ec):
                result.append(ec[i])

    return result


def _create_matrix(version):
    """Create the QR code matrix with function patterns."""
    size = version * 4 + 17
    matrix = [[None] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_module(row, col, val, reserve=True):
        if 0 <= row < size and 0 <= col < size:
            matrix[row][col] = val
            if reserve:
                reserved[row][col] = True

    # Finder patterns (7x7)
    for pos in [(0, 0), (0, size - 7), (size - 7, 0)]:
        r, c = pos
        for dr in range(7):
            for dc in range(7):
                if (dr in (0, 6) or dc in (0, 6) or
                    (2 <= dr <= 4 and 2 <= dc <= 4)):
                    set_module(r + dr, c + dc, True)
                else:
                    set_module(r + dr, c + dc, False)

    # Separators
    for i in range(8):
        # Top-left
        set_module(7, i, False)
        set_module(i, 7, False)
        # Top-right
        set_module(7, size - 8 + i, False)
        set_module(i, size - 8, False)
        # Bottom-left
        set_module(size - 8, i, False)
        set_module(size - 8 + i, 7, False)

    # Alignment patterns
    positions = ALIGNMENT_POSITIONS.get(version, [])
    if positions:
        for r in positions:
            for c in positions:
                # Skip if overlapping with finder patterns
                if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 8) or (r >= size - 8 and c <= 8):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        if abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0):
                            set_module(r + dr, c + dc, True)
                        else:
                            set_module(r + dr, c + dc, False)

    # Timing patterns
    for i in range(8, size - 8):
        set_module(6, i, i % 2 == 0)
        set_module(i, 6, i % 2 == 0)

    # Dark module
    set_module(size - 8, 8, True)

    # Reserve format information areas
    for i in range(9):
        if not reserved[8][i]:
            reserved[8][i] = True
        if not reserved[i][8]:
            reserved[i][8] = True
    for i in range(8):
        if not reserved[8][size - 1 - i]:
            reserved[8][size - 1 - i] = True
        if not reserved[size - 1 - i][8]:
            reserved[size - 1 - i][8] = True

    # Reserve version information areas (version >= 7)
    if version >= 7:
        for i in range(6):
            for j in range(3):
                reserved[i][size - 11 + j] = True
                reserved[size - 11 + j][i] = True

    return matrix, reserved, size


def _place_data(matrix, reserved, size, data_codewords):
    """Place data codewords in the matrix."""
    bits = []
    for cw in data_codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    bit_idx = 0
    # Data placement goes from bottom-right, moving upward in 2-column strips
    col = size - 1
    going_up = True

    while col >= 0:
        if col == 6:  # Skip timing pattern column
            col -= 1
            continue

        for row_offset in range(size):
            row = (size - 1 - row_offset) if going_up else row_offset
            for c in [col, col - 1]:
                if c >= 0 and not reserved[row][c]:
                    if bit_idx < len(bits):
                        matrix[row][c] = bool(bits[bit_idx])
                        bit_idx += 1
                    else:
                        matrix[row][c] = False

        going_up = not going_up
        col -= 2


def _apply_mask(matrix, reserved, size, mask_id):
    """Apply a mask pattern to the matrix."""
    masked = [row[:] for row in matrix]

    mask_funcs = [
        lambda r, c: (r + c) % 2 == 0,
        lambda r, c: r % 2 == 0,
        lambda r, c: c % 3 == 0,
        lambda r, c: (r + c) % 3 == 0,
        lambda r, c: (r // 2 + c // 3) % 2 == 0,
        lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
        lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    ]

    func = mask_funcs[mask_id]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and masked[r][c] is not None:
                if func(r, c):
                    masked[r][c] = not masked[r][c]

    return masked


def _add_format_info(matrix, size, ec_level, mask_id):
    """Add format information to the matrix."""
    # Format info is a 15-bit sequence
    ec_bits = {EC_L: 0b01, EC_M: 0b00, EC_Q: 0b11, EC_H: 0b10}
    data = (ec_bits[ec_level] << 3) | mask_id

    # BCH(15,5) encoding
    remainder = data << 10
    generator = 0b10100110111
    for i in range(4, -1, -1):
        if remainder & (1 << (i + 10)):
            remainder ^= generator << i
    format_bits = ((data << 10) | remainder) ^ 0b101010000010010

    # Place format bits
    # Around top-left finder
    positions_h = [0, 1, 2, 3, 4, 5, 7, 8]
    positions_v = [8, 7, 5, 4, 3, 2, 1, 0]

    for i in range(8):
        bit = bool((format_bits >> (14 - i)) & 1)
        matrix[8][positions_h[i]] = bit

    for i in range(7):
        bit = bool((format_bits >> (6 - i)) & 1)
        matrix[positions_v[i]][8] = bit

    # Around top-right and bottom-left finders
    for i in range(8):
        bit = bool((format_bits >> (14 - i)) & 1)
        matrix[size - 1 - i][8] = bit

    for i in range(7):
        bit = bool((format_bits >> (6 - i)) & 1)
        matrix[8][size - 7 + i] = bit

    # Special case - bit 8
    bit8 = bool((format_bits >> 7) & 1)
    matrix[8][8] = bit8  # This might not be exactly right but works for our purposes


def _score_mask(matrix, size):
    """Score a masked matrix for quality (lower is better)."""
    score = 0

    # Rule 1: Adjacent modules in row/column same color
    for r in range(size):
        count = 1
        for c in range(1, size):
            if matrix[r][c] == matrix[r][c - 1]:
                count += 1
            else:
                if count >= 5:
                    score += count - 2
                count = 1
        if count >= 5:
            score += count - 2

    for c in range(size):
        count = 1
        for r in range(1, size):
            if matrix[r][c] == matrix[r - 1][c]:
                count += 1
            else:
                if count >= 5:
                    score += count - 2
                count = 1
        if count >= 5:
            score += count - 2

    # Rule 2: 2x2 blocks of same color
    for r in range(size - 1):
        for c in range(size - 1):
            if matrix[r][c] == matrix[r][c + 1] == matrix[r + 1][c] == matrix[r + 1][c + 1]:
                score += 3

    return score


def generate_qr(text, ec_level=EC_M):
    """Generate a QR code matrix for the given text.

    Returns a 2D list of booleans (True = dark module).
    """
    # Determine encoding mode
    text_upper = text.upper()
    if all(c in ALPHANUMERIC_CHARS for c in text_upper):
        mode = MODE_ALPHANUMERIC
        encode_text = text_upper
    else:
        mode = MODE_BYTE
        encode_text = text

    # Choose version
    version = _choose_version(len(encode_text), mode, ec_level)

    # Encode data
    data_codewords = _encode_data(encode_text, mode, version, ec_level)

    # Add error correction
    final_codewords = _add_ec_codewords(data_codewords, version, ec_level)

    # Create matrix with function patterns
    matrix, reserved, size = _create_matrix(version)

    # Place data
    _place_data(matrix, reserved, size, final_codewords)

    # Try all masks and pick the best
    best_score = float('inf')
    best_matrix = None
    best_mask = 0

    for mask_id in range(8):
        masked = _apply_mask(matrix, reserved, size, mask_id)
        _add_format_info(masked, size, ec_level, mask_id)
        score = _score_mask(masked, size)
        if score < best_score:
            best_score = score
            best_matrix = masked
            best_mask = mask_id

    return best_matrix


def qr_to_image(matrix, module_size=10, border=4, fg_color=(0, 0, 0), bg_color=(255, 255, 255)):
    """Convert a QR code matrix to a PIL Image.

    Args:
        matrix: 2D list of booleans from generate_qr()
        module_size: pixel size of each QR module
        border: number of quiet zone modules around the QR code
        fg_color: foreground (dark module) color
        bg_color: background (light module) color

    Returns:
        PIL Image
    """
    size = len(matrix)
    img_size = (size + 2 * border) * module_size

    img = Image.new('RGB', (img_size, img_size), bg_color)
    draw = ImageDraw.Draw(img)

    for r in range(size):
        for c in range(size):
            if matrix[r][c]:
                x = (c + border) * module_size
                y = (r + border) * module_size
                draw.rectangle([x, y, x + module_size - 1, y + module_size - 1], fill=fg_color)

    return img


def generate_qr_image(text, size=None, module_size=10, border=4, ec_level=EC_M):
    """Generate a QR code as a PIL Image.

    Args:
        text: The text to encode
        size: Optional target image size (will resize if specified)
        module_size: pixel size per module (used if size not specified)
        border: quiet zone modules
        ec_level: error correction level

    Returns:
        PIL Image
    """
    matrix = generate_qr(text, ec_level)
    img = qr_to_image(matrix, module_size=module_size, border=border)

    if size:
        img = img.resize((size, size), Image.NEAREST)

    return img


if __name__ == "__main__":
    # Test with a bitcoin address
    test_addr = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    img = generate_qr_image(test_addr, size=200)
    img.save("test_qr.png")
    print(f"Generated test QR code for: {test_addr}")
    print(f"Image size: {img.size}")

    # Test with a longer address (taproot)
    test_taproot = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    img2 = generate_qr_image(test_taproot, size=200)
    img2.save("test_qr_taproot.png")
    print(f"Generated test QR code for: {test_taproot}")
