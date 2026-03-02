/**
 * Canvas-based bill image generator.
 * Overlays Bitcoin address/key text and QR codes onto the bill template.
 *
 * Port of bill_generator.py — uses HTML5 Canvas instead of Pillow.
 * Zero external dependencies (uses qr_generator.js for QR codes).
 */

// ============================================================
// Template overlay positions (pixels, from bill_generator.py)
// Bill image: 1843 x 784 pixels
// ============================================================

const BILL_WIDTH = 1843;
const BILL_HEIGHT = 784;

// Fallback sweep page URL for bill QR codes (used when window.location is
// not available, e.g. Node.js / test environments).  When running in a
// browser, the QR always uses window.location so it matches the current host.
const SWEEP_PAGE_URL = 'https://ObjSal.github.io/bitcoin-gift-paper-wallet/sweep.html';

const ADDRESS_QR_BOX    = { x1: 35,   y1: 469, x2: 319,  y2: 752 };  // 284×284
const PRIVKEY_QR_BOX    = { x1: 1525, y1: 40,  x2: 1808, y2: 324 };  // 284×285
const PRIVKEY_TEXT_AREA  = { x1: 1100, y1: 2,   x2: 1808, y2: 30  };  // Orange strip
const ADDRESS_TEXT_BOX   = { x1: 348,  y1: 694, x2: 1148, y2: 751 };  // 801×58

// Banner properties
const BANNER_COLOR      = 'rgb(253, 229, 167)';
const BANNER_TEXT_COLOR  = 'rgb(0, 161, 210)';
const BANNER_LEFT_BOX   = { x1: 1082, y1: 305, x2: 1225, y2: 339 };
const BANNER_RIGHT_BOX  = { x1: 1326, y1: 305, x2: 1425, y2: 339 };


// ============================================================
// Font helpers
// ============================================================

/**
 * Find the largest font size (descending from startSize) where text fits
 * within the given width and height constraints.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {string} text
 * @param {number} maxWidth
 * @param {number} maxHeight
 * @param {number} startSize
 * @param {string} fontFamily
 * @returns {{ font: string, width: number, height: number, size: number }}
 */
function _fitFontToBox(ctx, text, maxWidth, maxHeight, startSize, fontFamily) {
    if (startSize === undefined) startSize = 36;
    if (fontFamily === undefined) fontFamily = '"Courier New", monospace';

    for (let size = startSize; size > 5; size--) {
        const font = `${size}px ${fontFamily}`;
        ctx.font = font;
        const metrics = ctx.measureText(text);
        const tw = metrics.width;
        // Approximate text height from font size (Canvas doesn't have reliable height metrics)
        const th = size * 1.2;
        if (tw <= maxWidth && th <= maxHeight) {
            return { font, width: tw, height: th, size };
        }
    }
    const font = `6px ${fontFamily}`;
    ctx.font = font;
    const metrics = ctx.measureText(text);
    return { font, width: metrics.width, height: 6 * 1.2, size: 6 };
}


// ============================================================
// Main generator
// ============================================================

/**
 * Generate a bill image on an HTML5 Canvas.
 *
 * @param {HTMLCanvasElement} canvas - Target canvas element
 * @param {HTMLImageElement} templateImg - Pre-loaded bill template image
 * @param {string} address - Bitcoin address (bc1q... or bc1p...)
 * @param {string} privateKeyWif - WIF-encoded private key
 * @param {string} addressType - "segwit" or "taproot"
 * @param {boolean} isTweaked - If true, add "(tweaked)" labels
 * @param {string} network - "mainnet", "testnet4", or "regtest"
 */
function generateBillOnCanvas(canvas, templateImg, address, privateKeyWif, addressType, isTweaked, network) {
    if (addressType === undefined) addressType = 'taproot';
    if (isTweaked === undefined) isTweaked = false;
    if (network === undefined) network = 'mainnet';

    canvas.width = BILL_WIDTH;
    canvas.height = BILL_HEIGHT;
    const ctx = canvas.getContext('2d');

    // Draw template
    ctx.drawImage(templateImg, 0, 0, BILL_WIDTH, BILL_HEIGHT);

    // ------------------------------------------------------------------
    // 0. Redraw "VIRES IN NUMERIS" and year on the banner
    // ------------------------------------------------------------------
    const sansFont = 'Arial, Helvetica, sans-serif';

    // Cover old text with banner color (don't overlap medallion)
    ctx.fillStyle = BANNER_COLOR;
    ctx.fillRect(1082, 305, 1225 - 1082, 339 - 305);
    ctx.fillRect(BANNER_RIGHT_BOX.x1, BANNER_RIGHT_BOX.y1,
        BANNER_RIGHT_BOX.x2 - BANNER_RIGHT_BOX.x1,
        BANNER_RIGHT_BOX.y2 - BANNER_RIGHT_BOX.y1);

    // Left segment: "VIRES IN NUMERIS"
    {
        const lx1 = 1082 + 4, ly1 = 301;
        const lw = 1225 - 2 - lx1, lh = 343 - ly1;
        const motto = 'VIRES IN NUMERIS';
        ctx.fillStyle = BANNER_TEXT_COLOR;
        const fit = _fitFontToBox(ctx, motto, lw, lh, 24, sansFont);
        ctx.font = `bold ${fit.size}px ${sansFont}`;
        const metrics = ctx.measureText(motto);
        const tx = lx1 + (lw - metrics.width) / 2;
        const ty = ly1 + (lh + fit.size * 0.8) / 2;
        ctx.fillText(motto, tx, ty);
    }

    // Right segment: current year
    {
        const rx1 = BANNER_RIGHT_BOX.x1, ry1 = BANNER_RIGHT_BOX.y1;
        const rw = BANNER_RIGHT_BOX.x2 - rx1, rh = BANNER_RIGHT_BOX.y2 - ry1;
        const yearStr = new Date().getUTCFullYear().toString();
        ctx.fillStyle = BANNER_TEXT_COLOR;
        const fit = _fitFontToBox(ctx, yearStr, rw, rh, 24, sansFont);
        ctx.font = `bold ${fit.size}px ${sansFont}`;
        const metrics = ctx.measureText(yearStr);
        const tx = rx1 + (rw - metrics.width) / 2;
        const ty = ry1 + (rh + fit.size * 0.8) / 2;
        ctx.fillText(yearStr, tx, ty);
    }

    // ------------------------------------------------------------------
    // 1. Address QR code — fill the left white box (284×284)
    // ------------------------------------------------------------------
    {
        const { x1, y1, x2, y2 } = ADDRESS_QR_BOX;
        const boxW = x2 - x1;
        const boxH = y2 - y1;
        const qrSide = Math.min(boxW, boxH);
        const matrix = QRGenerator.generateQR(address, QRGenerator.EC_M);
        const pasteX = x1 + Math.floor((boxW - qrSide) / 2);
        const pasteY = y1 + Math.floor((boxH - qrSide) / 2);
        const moduleSize = qrSide / (matrix.length + 4); // +4 for 2-module border each side
        QRGenerator.qrToCanvas(matrix, ctx, pasteX, pasteY, moduleSize, 2);
    }

    // ------------------------------------------------------------------
    // 2. Private Key QR code — fill the right white box (284×285)
    //    Encodes a sweep URL (with WIF, network, and type params) so
    //    scanning the QR opens the sweep page with fields pre-populated.
    // ------------------------------------------------------------------
    {
        const { x1, y1, x2, y2 } = PRIVKEY_QR_BOX;
        const boxW = x2 - x1;
        const boxH = y2 - y1;
        const qrSide = Math.min(boxW, boxH);

        // Build sweep URL for the QR code.
        // Use the current host so the QR works whether the site is served
        // from GitHub Pages, a local Python server, or any other origin.
        // Falls back to the GitHub Pages URL when window.location is unavailable.
        let sweepBase;
        if (typeof window !== 'undefined' && window.location) {
            sweepBase = new URL('sweep.html', window.location.href).href;
        } else {
            sweepBase = SWEEP_PAGE_URL;
        }
        const params = new URLSearchParams();
        params.set('wif', privateKeyWif);
        params.set('network', network);
        let sweepType = addressType || 'taproot';
        if (addressType === 'taproot' && isTweaked) sweepType = 'taproot_tweaked';
        params.set('type', sweepType);
        const qrContent = sweepBase + '?' + params.toString();

        const matrix = QRGenerator.generateQR(qrContent, QRGenerator.EC_M);
        const pasteX = x1 + Math.floor((boxW - qrSide) / 2);
        const pasteY = y1 + Math.floor((boxH - qrSide) / 2);
        const moduleSize = qrSide / (matrix.length + 4);
        QRGenerator.qrToCanvas(matrix, ctx, pasteX, pasteY, moduleSize, 2);
    }

    // ------------------------------------------------------------------
    // 2b. "(tweaked)" label inside private key QR white box
    // ------------------------------------------------------------------
    if (isTweaked) {
        const tweakedLabel = '(tweaked)';
        const fontSize = 12;
        ctx.font = `${fontSize}px ${sansFont}`;
        ctx.fillStyle = 'rgb(30, 30, 30)';
        const metrics = ctx.measureText(tweakedLabel);
        const tx = PRIVKEY_QR_BOX.x2 - metrics.width - 3;
        const ty = PRIVKEY_QR_BOX.y2 - 3;
        ctx.fillText(tweakedLabel, tx, ty);
    }

    // ------------------------------------------------------------------
    // 3. Private key text — orange strip above the dollar box and QR
    // ------------------------------------------------------------------
    {
        const { x1, y1, x2, y2 } = PRIVKEY_TEXT_AREA;
        const tw = x2 - x1;
        const th = y2 - y1;
        const monoFont = '"Courier New", monospace';

        ctx.fillStyle = 'rgb(30, 30, 30)';

        if (isTweaked) {
            const suffix = ' (tweaked)';
            const suffixFont = `12px ${sansFont}`;
            ctx.font = suffixFont;
            const suffixW = ctx.measureText(suffix).width;

            const wifAvailW = tw - suffixW - 2;
            const fit = _fitFontToBox(ctx, privateKeyWif, wifAvailW, th, 24, monoFont);

            // Right-align: suffix flush to x=1808, WIF just before
            const suffixX = 1808 - suffixW;
            const textX = suffixX - fit.width - 2;
            const textY = y1 + (th + fit.size * 0.8) / 2;

            ctx.font = fit.font;
            ctx.fillText(privateKeyWif, textX, textY);

            ctx.font = suffixFont;
            const suffixY = y1 + (th + 12 * 0.8) / 2;
            ctx.fillText(suffix, suffixX, suffixY);
        } else {
            const fit = _fitFontToBox(ctx, privateKeyWif, tw, th, 24, monoFont);
            // Right-align to x=1808
            const textX = 1808 - fit.width;
            const textY = y1 + (th + fit.size * 0.8) / 2;
            ctx.font = fit.font;
            ctx.fillText(privateKeyWif, textX, textY);
        }
    }

    // ------------------------------------------------------------------
    // 4. Address text — bottom white band (801×58)
    //    Use condensed font for Taproot (longer addresses)
    // ------------------------------------------------------------------
    {
        const { x1, y1, x2, y2 } = ADDRESS_TEXT_BOX;
        const tw = x2 - x1;
        const th = y2 - y1;
        // Use condensed for taproot, regular mono for segwit
        const addrFontFamily = addressType === 'taproot'
            ? '"Arial Narrow", "Helvetica Neue", Arial, sans-serif'
            : '"Courier New", monospace';
        const fit = _fitFontToBox(ctx, address, tw, th, 36, addrFontFamily);

        ctx.fillStyle = 'rgb(30, 30, 30)';
        ctx.font = fit.font;
        const textX = x1 + (tw - fit.width) / 2;
        const textY = y1 + (th + fit.size * 0.8) / 2;
        ctx.fillText(address, textX, textY);
    }

    // ------------------------------------------------------------------
    // 5. Timestamp — vertical text at bottom-right corner
    // ------------------------------------------------------------------
    {
        const now = new Date();
        const timestamp = now.getUTCFullYear() + '-' +
            String(now.getUTCMonth() + 1).padStart(2, '0') + '-' +
            String(now.getUTCDate()).padStart(2, '0') + ' ' +
            String(now.getUTCHours()).padStart(2, '0') + ':' +
            String(now.getUTCMinutes()).padStart(2, '0') + ':' +
            String(now.getUTCSeconds()).padStart(2, '0') + ' UTC';

        const tsFontSize = 14;
        ctx.font = `${tsFontSize}px "Courier New", monospace`;
        ctx.fillStyle = 'rgb(0, 0, 0)';

        const tsMetrics = ctx.measureText(timestamp);
        const tsW = tsMetrics.width;

        // Rotate 90° counter-clockwise for vertical text
        ctx.save();
        const edgeMargin = 8;
        const px = BILL_WIDTH - tsFontSize - edgeMargin;
        const py = BILL_HEIGHT - edgeMargin;
        ctx.translate(px, py);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(timestamp, 0, tsFontSize * 0.8);
        ctx.restore();
    }
}

/**
 * Generate a bill and return it as a data URL (PNG).
 *
 * @param {HTMLImageElement} templateImg - Pre-loaded bill template image
 * @param {string} address - Bitcoin address
 * @param {string} privateKeyWif - WIF-encoded private key
 * @param {string} addressType - "segwit" or "taproot"
 * @param {boolean} isTweaked - If true, add "(tweaked)" labels
 * @param {string} network - "mainnet", "testnet4", or "regtest"
 * @returns {string} Data URL of the bill image (PNG)
 */
function generateBillDataURL(templateImg, address, privateKeyWif, addressType, isTweaked, network) {
    const canvas = document.createElement('canvas');
    generateBillOnCanvas(canvas, templateImg, address, privateKeyWif, addressType, isTweaked, network);
    return canvas.toDataURL('image/png');
}

/**
 * Pre-load the bill template image.
 *
 * @param {string} templatePath - URL or path to bill_template.png
 * @returns {Promise<HTMLImageElement>} Loaded image element
 */
function loadBillTemplate(templatePath) {
    if (templatePath === undefined) templatePath = 'bill_template.png';
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('Failed to load bill template: ' + templatePath));
        img.src = templatePath;
    });
}


// ============================================================
// Exports
// ============================================================

const BillGenerator = {
    generateBillOnCanvas,
    generateBillDataURL,
    loadBillTemplate,
    BILL_WIDTH,
    BILL_HEIGHT,
};

if (typeof window !== 'undefined') {
    window.BillGenerator = BillGenerator;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = BillGenerator;
}
