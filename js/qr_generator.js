/**
 * Pure JavaScript QR Code generator for Bitcoin addresses.
 * Generates QR codes as 2D boolean matrices and renders to Canvas.
 * Supports alphanumeric mode for bitcoin addresses.
 *
 * Port of qr_generator.py — zero external dependencies.
 */

// ============================================================
// QR Code constants
// ============================================================

const EC_L = 0; // ~7% recovery
const EC_M = 1; // ~15% recovery
const EC_Q = 2; // ~25% recovery
const EC_H = 3; // ~30% recovery

const MODE_NUMERIC = 0b0001;
const MODE_ALPHANUMERIC = 0b0010;
const MODE_BYTE = 0b0100;

const ALPHANUMERIC_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:';

// Capacity table: version -> EC level -> [total_cw, ec_per_block, g1_blocks, g1_cw, g2_blocks, g2_cw]
const CAPACITY_TABLE = {
    1:  { [EC_L]: [26,7,1,19,0,0],  [EC_M]: [26,10,1,16,0,0],  [EC_Q]: [26,13,1,13,0,0],  [EC_H]: [26,17,1,9,0,0] },
    2:  { [EC_L]: [44,10,1,34,0,0], [EC_M]: [44,16,1,28,0,0],  [EC_Q]: [44,22,1,22,0,0],  [EC_H]: [44,28,1,16,0,0] },
    3:  { [EC_L]: [70,15,1,55,0,0], [EC_M]: [70,26,1,44,0,0],  [EC_Q]: [70,18,2,17,0,0],  [EC_H]: [70,22,2,13,0,0] },
    4:  { [EC_L]: [100,20,1,80,0,0],[EC_M]: [100,18,2,32,0,0], [EC_Q]: [100,26,2,24,0,0], [EC_H]: [100,16,4,9,0,0] },
    5:  { [EC_L]: [134,26,1,108,0,0],[EC_M]: [134,24,2,43,0,0],[EC_Q]: [134,18,2,15,2,16],[EC_H]: [134,22,2,11,2,12] },
    6:  { [EC_L]: [172,18,2,68,0,0],[EC_M]: [172,16,4,27,0,0], [EC_Q]: [172,24,4,19,0,0], [EC_H]: [172,28,4,15,0,0] },
    7:  { [EC_L]: [196,20,2,78,0,0],[EC_M]: [196,18,4,31,0,0], [EC_Q]: [196,18,2,14,4,15],[EC_H]: [196,26,4,13,1,14] },
    8:  { [EC_L]: [242,24,2,97,0,0],[EC_M]: [242,22,2,38,2,39],[EC_Q]: [242,22,4,18,2,19],[EC_H]: [242,26,4,14,2,15] },
    9:  { [EC_L]: [292,30,2,116,0,0],[EC_M]: [292,22,3,36,2,37],[EC_Q]: [292,20,4,16,4,17],[EC_H]: [292,24,4,12,4,13] },
    10: { [EC_L]: [346,18,2,68,2,69],[EC_M]: [346,26,4,43,1,44],[EC_Q]: [346,24,6,19,2,20],[EC_H]: [346,28,6,15,2,16] },
};

function _maxDataCapacity(version, ecLevel) {
    const [, , g1Blocks, g1Cw, g2Blocks, g2Cw] = CAPACITY_TABLE[version][ecLevel];
    return g1Blocks * g1Cw + g2Blocks * g2Cw;
}

const ALIGNMENT_POSITIONS = {
    1: [], 2: [6,18], 3: [6,22], 4: [6,26], 5: [6,30],
    6: [6,34], 7: [6,22,38], 8: [6,24,42], 9: [6,26,46], 10: [6,28,52],
};


// ============================================================
// GF(256) arithmetic for Reed-Solomon
// ============================================================

const GF_EXP = new Array(512).fill(0);
const GF_LOG = new Array(256).fill(0);

(function initGF() {
    let x = 1;
    for (let i = 0; i < 255; i++) {
        GF_EXP[i] = x;
        GF_LOG[x] = i;
        x <<= 1;
        if (x & 0x100) x ^= 0x11D; // Primitive polynomial for GF(2^8)
    }
    for (let i = 255; i < 512; i++) {
        GF_EXP[i] = GF_EXP[i - 255];
    }
})();

function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

function gfPolyMul(p, q) {
    const result = new Array(p.length + q.length - 1).fill(0);
    for (let i = 0; i < p.length; i++) {
        for (let j = 0; j < q.length; j++) {
            result[i + j] ^= gfMul(p[i], q[j]);
        }
    }
    return result;
}

function gfPolyDiv(dividend, divisor) {
    const result = [...dividend];
    for (let i = 0; i <= dividend.length - divisor.length; i++) {
        const coef = result[i];
        if (coef !== 0) {
            for (let j = 1; j < divisor.length; j++) {
                result[i + j] ^= gfMul(divisor[j], coef);
            }
        }
    }
    return result.slice(-(divisor.length - 1));
}

function rsGeneratorPoly(nsym) {
    let g = [1];
    for (let i = 0; i < nsym; i++) {
        g = gfPolyMul(g, [1, GF_EXP[i]]);
    }
    return g;
}

function rsEncode(data, nsym) {
    const gen = rsGeneratorPoly(nsym);
    const padded = [...data, ...new Array(nsym).fill(0)];
    const remainder = gfPolyDiv(padded, gen);
    return [...data, ...remainder];
}


// ============================================================
// QR Code generation
// ============================================================

function _chooseVersion(dataLen, mode, ecLevel) {
    for (let version = 1; version <= 10; version++) {
        const capacity = _maxDataCapacity(version, ecLevel);
        let bitsNeeded = 4; // mode indicator

        if (version <= 9) {
            if (mode === MODE_BYTE) {
                bitsNeeded += 8 + dataLen * 8;
            } else if (mode === MODE_ALPHANUMERIC) {
                bitsNeeded += 9;
                bitsNeeded += Math.floor(dataLen / 2) * 11 + (dataLen % 2) * 6;
            } else if (mode === MODE_NUMERIC) {
                bitsNeeded += 10;
                const groups = Math.floor(dataLen / 3);
                const rem = dataLen % 3;
                bitsNeeded += groups * 10 + (rem === 2 ? 7 : rem === 1 ? 4 : 0);
            }
        } else {
            if (mode === MODE_BYTE) {
                bitsNeeded += 16 + dataLen * 8;
            } else if (mode === MODE_ALPHANUMERIC) {
                bitsNeeded += 11;
                bitsNeeded += Math.floor(dataLen / 2) * 11 + (dataLen % 2) * 6;
            }
        }

        const bytesNeeded = Math.ceil(bitsNeeded / 8);
        if (bytesNeeded <= capacity) return version;
    }
    throw new Error('Data too long for QR code (max version 10)');
}

function _encodeData(text, mode, version, ecLevel) {
    const bits = [];

    function addBits(val, length) {
        for (let i = length - 1; i >= 0; i--) {
            bits.push((val >> i) & 1);
        }
    }

    // Mode indicator
    addBits(mode, 4);

    // Character count indicator
    const ccBitsMap = version <= 9
        ? { [MODE_NUMERIC]: 10, [MODE_ALPHANUMERIC]: 9, [MODE_BYTE]: 8 }
        : { [MODE_NUMERIC]: 12, [MODE_ALPHANUMERIC]: 11, [MODE_BYTE]: 16 };
    addBits(text.length, ccBitsMap[mode]);

    // Data encoding
    if (mode === MODE_BYTE) {
        const encoded = new TextEncoder().encode(text);
        for (const ch of encoded) addBits(ch, 8);
    } else if (mode === MODE_ALPHANUMERIC) {
        const textUpper = text.toUpperCase();
        for (let i = 0; i < textUpper.length - 1; i += 2) {
            const val = ALPHANUMERIC_CHARS.indexOf(textUpper[i]) * 45 +
                        ALPHANUMERIC_CHARS.indexOf(textUpper[i + 1]);
            addBits(val, 11);
        }
        if (textUpper.length % 2) {
            addBits(ALPHANUMERIC_CHARS.indexOf(textUpper[textUpper.length - 1]), 6);
        }
    }

    // Terminator
    const capacity = _maxDataCapacity(version, ecLevel) * 8;
    const terminatorLen = Math.min(4, capacity - bits.length);
    addBits(0, terminatorLen);

    // Pad to byte boundary
    while (bits.length % 8) bits.push(0);

    // Pad with alternating bytes
    const padBytes = [0xEC, 0x11];
    let padIdx = 0;
    while (bits.length < capacity) {
        addBits(padBytes[padIdx], 8);
        padIdx = (padIdx + 1) % 2;
    }

    // Convert to bytes
    const codewords = [];
    for (let i = 0; i < bits.length; i += 8) {
        let byte = 0;
        for (let j = 0; j < 8; j++) {
            if (i + j < bits.length) byte = (byte << 1) | bits[i + j];
        }
        codewords.push(byte);
    }

    return codewords.slice(0, _maxDataCapacity(version, ecLevel));
}

function _addEcCodewords(dataCodewords, version, ecLevel) {
    const [, ecPerBlock, g1Blocks, g1Cw, g2Blocks, g2Cw] = CAPACITY_TABLE[version][ecLevel];

    // Split data into blocks
    const blocks = [];
    let idx = 0;
    for (let i = 0; i < g1Blocks; i++) {
        blocks.push(dataCodewords.slice(idx, idx + g1Cw));
        idx += g1Cw;
    }
    for (let i = 0; i < g2Blocks; i++) {
        blocks.push(dataCodewords.slice(idx, idx + g2Cw));
        idx += g2Cw;
    }

    // Generate EC for each block
    const ecBlocks = [];
    for (const block of blocks) {
        const ec = rsEncode([...block], ecPerBlock);
        ecBlocks.push(ec.slice(block.length));
    }

    // Interleave data codewords
    const result = [];
    const maxData = g2Blocks > 0 ? Math.max(g1Cw, g2Cw) : g1Cw;
    for (let i = 0; i < maxData; i++) {
        for (const block of blocks) {
            if (i < block.length) result.push(block[i]);
        }
    }

    // Interleave EC codewords
    for (let i = 0; i < ecPerBlock; i++) {
        for (const ec of ecBlocks) {
            if (i < ec.length) result.push(ec[i]);
        }
    }

    return result;
}

function _createMatrix(version) {
    const size = version * 4 + 17;
    const matrix = Array.from({ length: size }, () => new Array(size).fill(null));
    const reserved = Array.from({ length: size }, () => new Array(size).fill(false));

    function setModule(row, col, val, reserve) {
        if (reserve === undefined) reserve = true;
        if (row >= 0 && row < size && col >= 0 && col < size) {
            matrix[row][col] = val;
            if (reserve) reserved[row][col] = true;
        }
    }

    // Finder patterns (7x7)
    for (const [r, c] of [[0, 0], [0, size - 7], [size - 7, 0]]) {
        for (let dr = 0; dr < 7; dr++) {
            for (let dc = 0; dc < 7; dc++) {
                const isDark = (dr === 0 || dr === 6 || dc === 0 || dc === 6 ||
                    (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
                setModule(r + dr, c + dc, isDark);
            }
        }
    }

    // Separators
    for (let i = 0; i < 8; i++) {
        setModule(7, i, false);
        setModule(i, 7, false);
        setModule(7, size - 8 + i, false);
        setModule(i, size - 8, false);
        setModule(size - 8, i, false);
        setModule(size - 8 + i, 7, false);
    }

    // Alignment patterns
    const positions = ALIGNMENT_POSITIONS[version] || [];
    if (positions.length > 0) {
        for (const r of positions) {
            for (const c of positions) {
                if ((r <= 8 && c <= 8) || (r <= 8 && c >= size - 8) || (r >= size - 8 && c <= 8)) continue;
                for (let dr = -2; dr <= 2; dr++) {
                    for (let dc = -2; dc <= 2; dc++) {
                        const isDark = Math.abs(dr) === 2 || Math.abs(dc) === 2 || (dr === 0 && dc === 0);
                        setModule(r + dr, c + dc, isDark);
                    }
                }
            }
        }
    }

    // Timing patterns
    for (let i = 8; i < size - 8; i++) {
        setModule(6, i, i % 2 === 0);
        setModule(i, 6, i % 2 === 0);
    }

    // Dark module
    setModule(size - 8, 8, true);

    // Reserve format information areas
    for (let i = 0; i < 9; i++) {
        if (!reserved[8][i]) reserved[8][i] = true;
        if (!reserved[i][8]) reserved[i][8] = true;
    }
    for (let i = 0; i < 8; i++) {
        if (!reserved[8][size - 1 - i]) reserved[8][size - 1 - i] = true;
        if (!reserved[size - 1 - i][8]) reserved[size - 1 - i][8] = true;
    }

    // Reserve version information areas (version >= 7)
    if (version >= 7) {
        for (let i = 0; i < 6; i++) {
            for (let j = 0; j < 3; j++) {
                reserved[i][size - 11 + j] = true;
                reserved[size - 11 + j][i] = true;
            }
        }
    }

    return { matrix, reserved, size };
}

function _placeData(matrix, reserved, size, dataCodewords) {
    const bits = [];
    for (const cw of dataCodewords) {
        for (let i = 7; i >= 0; i--) {
            bits.push((cw >> i) & 1);
        }
    }

    let bitIdx = 0;
    let col = size - 1;
    let goingUp = true;

    while (col >= 0) {
        if (col === 6) { col--; continue; } // Skip timing pattern column

        for (let rowOffset = 0; rowOffset < size; rowOffset++) {
            const row = goingUp ? (size - 1 - rowOffset) : rowOffset;
            for (const c of [col, col - 1]) {
                if (c >= 0 && !reserved[row][c]) {
                    matrix[row][c] = bitIdx < bits.length ? !!bits[bitIdx++] : false;
                }
            }
        }

        goingUp = !goingUp;
        col -= 2;
    }
}

function _applyMask(matrix, reserved, size, maskId) {
    const masked = matrix.map(row => [...row]);

    const maskFuncs = [
        (r, c) => (r + c) % 2 === 0,
        (r, c) => r % 2 === 0,
        (r, c) => c % 3 === 0,
        (r, c) => (r + c) % 3 === 0,
        (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
        (r, c) => (r * c) % 2 + (r * c) % 3 === 0,
        (r, c) => ((r * c) % 2 + (r * c) % 3) % 2 === 0,
        (r, c) => ((r + c) % 2 + (r * c) % 3) % 2 === 0,
    ];

    const func = maskFuncs[maskId];
    for (let r = 0; r < size; r++) {
        for (let c = 0; c < size; c++) {
            if (!reserved[r][c] && masked[r][c] !== null) {
                if (func(r, c)) masked[r][c] = !masked[r][c];
            }
        }
    }
    return masked;
}

function _addFormatInfo(matrix, size, ecLevel, maskId) {
    const ecBits = { [EC_L]: 0b01, [EC_M]: 0b00, [EC_Q]: 0b11, [EC_H]: 0b10 };
    const data = (ecBits[ecLevel] << 3) | maskId;

    // BCH(15,5) encoding
    let remainder = data << 10;
    const generator = 0b10100110111;
    for (let i = 4; i >= 0; i--) {
        if (remainder & (1 << (i + 10))) {
            remainder ^= generator << i;
        }
    }
    const formatBits = ((data << 10) | remainder) ^ 0b101010000010010;

    // Place format bits around top-left finder
    const positionsH = [0, 1, 2, 3, 4, 5, 7, 8];
    const positionsV = [8, 7, 5, 4, 3, 2, 1, 0];

    for (let i = 0; i < 8; i++) {
        matrix[8][positionsH[i]] = !!((formatBits >> (14 - i)) & 1);
    }
    for (let i = 0; i < 7; i++) {
        matrix[positionsV[i]][8] = !!((formatBits >> (6 - i)) & 1);
    }

    // Around top-right and bottom-left finders
    for (let i = 0; i < 8; i++) {
        matrix[size - 1 - i][8] = !!((formatBits >> (14 - i)) & 1);
    }
    for (let i = 0; i < 7; i++) {
        matrix[8][size - 7 + i] = !!((formatBits >> (6 - i)) & 1);
    }

    // Bit 8 special case
    matrix[8][8] = !!((formatBits >> 7) & 1);
}

function _scoreMask(matrix, size) {
    let score = 0;

    // Rule 1: Adjacent modules in row/column same color
    for (let r = 0; r < size; r++) {
        let count = 1;
        for (let c = 1; c < size; c++) {
            if (matrix[r][c] === matrix[r][c - 1]) {
                count++;
            } else {
                if (count >= 5) score += count - 2;
                count = 1;
            }
        }
        if (count >= 5) score += count - 2;
    }

    for (let c = 0; c < size; c++) {
        let count = 1;
        for (let r = 1; r < size; r++) {
            if (matrix[r][c] === matrix[r - 1][c]) {
                count++;
            } else {
                if (count >= 5) score += count - 2;
                count = 1;
            }
        }
        if (count >= 5) score += count - 2;
    }

    // Rule 2: 2x2 blocks of same color
    for (let r = 0; r < size - 1; r++) {
        for (let c = 0; c < size - 1; c++) {
            if (matrix[r][c] === matrix[r][c + 1] &&
                matrix[r][c] === matrix[r + 1][c] &&
                matrix[r][c] === matrix[r + 1][c + 1]) {
                score += 3;
            }
        }
    }

    return score;
}


// ============================================================
// Public API
// ============================================================

/**
 * Generate a QR code matrix for the given text.
 * Returns a 2D array of booleans (true = dark module).
 */
function generateQR(text, ecLevel) {
    if (ecLevel === undefined) ecLevel = EC_M;

    // Determine encoding mode
    const textUpper = text.toUpperCase();
    let mode, encodeText;
    if ([...textUpper].every(c => ALPHANUMERIC_CHARS.includes(c))) {
        mode = MODE_ALPHANUMERIC;
        encodeText = textUpper;
    } else {
        mode = MODE_BYTE;
        encodeText = text;
    }

    const version = _chooseVersion(encodeText.length, mode, ecLevel);
    const dataCodewords = _encodeData(encodeText, mode, version, ecLevel);
    const finalCodewords = _addEcCodewords(dataCodewords, version, ecLevel);

    const { matrix, reserved, size } = _createMatrix(version);
    _placeData(matrix, reserved, size, finalCodewords);

    // Try all masks and pick the best
    let bestScore = Infinity;
    let bestMatrix = null;

    for (let maskId = 0; maskId < 8; maskId++) {
        const masked = _applyMask(matrix, reserved, size, maskId);
        _addFormatInfo(masked, size, ecLevel, maskId);
        const score = _scoreMask(masked, size);
        if (score < bestScore) {
            bestScore = score;
            bestMatrix = masked;
        }
    }

    return bestMatrix;
}

/**
 * Draw a QR code matrix onto a Canvas 2D context at the given position.
 *
 * @param {boolean[][]} matrix - QR code matrix from generateQR()
 * @param {CanvasRenderingContext2D} ctx - Canvas 2D rendering context
 * @param {number} x - X position on canvas
 * @param {number} y - Y position on canvas
 * @param {number} moduleSize - Pixel size of each QR module
 * @param {number} border - Number of quiet zone modules
 * @param {string} fgColor - Foreground (dark) color (default: 'black')
 * @param {string} bgColor - Background (light) color (default: 'white')
 */
function qrToCanvas(matrix, ctx, x, y, moduleSize, border, fgColor, bgColor) {
    if (moduleSize === undefined) moduleSize = 4;
    if (border === undefined) border = 2;
    if (fgColor === undefined) fgColor = 'black';
    if (bgColor === undefined) bgColor = 'white';

    const size = matrix.length;
    const totalSize = (size + 2 * border) * moduleSize;

    // Draw background
    ctx.fillStyle = bgColor;
    ctx.fillRect(x, y, totalSize, totalSize);

    // Draw dark modules
    ctx.fillStyle = fgColor;
    for (let r = 0; r < size; r++) {
        for (let c = 0; c < size; c++) {
            if (matrix[r][c]) {
                ctx.fillRect(
                    x + (c + border) * moduleSize,
                    y + (r + border) * moduleSize,
                    moduleSize,
                    moduleSize
                );
            }
        }
    }
}

/**
 * Generate a QR code and return it as a data URL (PNG).
 *
 * @param {string} text - Text to encode
 * @param {number} targetSize - Target image size in pixels
 * @param {number} ecLevel - Error correction level (default: EC_M)
 * @returns {string} Data URL of the QR code image
 */
function generateQRDataURL(text, targetSize, ecLevel) {
    if (targetSize === undefined) targetSize = 200;
    if (ecLevel === undefined) ecLevel = EC_M;

    const matrix = generateQR(text, ecLevel);
    const size = matrix.length;
    const border = 2;
    const moduleSize = Math.max(1, Math.floor(targetSize / (size + 2 * border)));
    const imgSize = (size + 2 * border) * moduleSize;

    const canvas = document.createElement('canvas');
    canvas.width = imgSize;
    canvas.height = imgSize;
    const ctx = canvas.getContext('2d');

    qrToCanvas(matrix, ctx, 0, 0, moduleSize, border);

    return canvas.toDataURL('image/png');
}


// ============================================================
// Exports
// ============================================================

const QRGenerator = {
    EC_L, EC_M, EC_Q, EC_H,
    generateQR,
    qrToCanvas,
    generateQRDataURL,
};

if (typeof window !== 'undefined') {
    window.QRGenerator = QRGenerator;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = QRGenerator;
}
