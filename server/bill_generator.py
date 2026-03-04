"""
Bill image generator.
Takes the template bill image and overlays Bitcoin address/key text and QR codes.
"""

import io
import os
import base64
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
from qr_generator import generate_qr_image, EC_M, EC_Q

# Template bill image path (resolve relative to project root)
_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_DIR)
TEMPLATE_PATH = os.path.join(_PROJECT_ROOT, "assets", "bill_template.png")

# ============================================================
# Overlay positions (measured from pixel-level scan of the template)
# Bill image: 1843 x 784 pixels
#
# There are FOUR white regions on the template:
#   1. Left QR box:       x=35..319,   y=469..752  (284x284)  — Address QR
#   2. Right QR box:      x=1525..1808, y=40..324   (284x285)  — Private Key QR
#   3. Dollar-sign box:   x=1167..1494, y=40..107    (328x68)   — LEFT EMPTY (handwritten)
#   4. Bottom text band:  x=348..959,  y=700..753   (612x54)   — Address text
#   5. Orange strip above boxes: y≈5..38, x=1100..1808          — Private Key text
#
# Yellow "VIRES IN NUMERIS" banner with blue text:
#   Left segment (text):  x=1082..1227, y=305..338  — "VIRES IN NUMERIS"
#   Medallion gap:        x≈1228..1325
#   Right segment (year): x=1326..1424, y=305..338  — "2023" (to be replaced)
#   Banner background:    rgb(253, 229, 167)
#   Text color:           rgb(0, 161, 210)
# ============================================================

ADDRESS_QR_BOX    = (35,   469, 319,  752)   # 284×284 px
PRIVKEY_QR_BOX    = (1525,  40, 1808, 324)   # 284×285 px
PRIVKEY_TEXT_AREA  = (1100,   2, 1808,  30)   # Orange strip, closer to top edge
ADDRESS_TEXT_BOX  = (348,  694, 1148, 751)   # 801×58 px, shifted 2px up

# Banner properties
BANNER_COLOR       = (253, 229, 167)
BANNER_TEXT_COLOR   = (0, 161, 210)
BANNER_LEFT_BOX    = (1082, 305, 1228, 339)   # "VIRES IN NUMERIS" area (full extent)
BANNER_RIGHT_BOX   = (1326, 305, 1425, 339)   # year area


# ============================================================
# Font helpers
# ============================================================

_MONO_FONT_PATHS = [
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    # macOS
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFMono-Regular.otf",
    "/Library/Fonts/Courier New.ttf",
    # Windows
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
]

_SANS_FONT_PATHS = [
    # Linux (bold first)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/Helvetica-Bold.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSText.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/calibri.ttf",
]

# Narrow / condensed font for long addresses (e.g. Taproot 62-char)
# so they fill the text box with a similar visual weight to shorter addresses.
_NARROW_FONT_PATHS = [
    # Linux
    "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
    # macOS
    "/System/Library/Fonts/HelveticaNeue-Condensed.ttc",
    "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
    # Windows
    "C:/Windows/Fonts/arialn.ttf",
    "C:/Windows/Fonts/arialnb.ttf",
]


def _load_font(size, font_paths=None):
    """Load a font at the requested point size, with fallback."""
    if font_paths is None:
        font_paths = _MONO_FONT_PATHS
    for fp in font_paths:
        try:
            return ImageFont.truetype(fp, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _fit_font_to_box(draw, text, box_w, box_h, start_size=36, font_paths=None):
    """Find the largest font size where `text` fits within box_w x box_h."""
    for size in range(start_size, 5, -1):
        font = _load_font(size, font_paths)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        if tw <= box_w and th <= box_h:
            return font, tw, th
    font = _load_font(6, font_paths)
    bbox = draw.textbbox((0, 0), text, font=font)
    return font, bbox[2] - bbox[0], bbox[3] - bbox[1]


# ============================================================
# Main generator
# ============================================================

def generate_bill_image(address, private_key_wif, address_type="taproot",
                        is_tweaked=False, sweep_url=None):
    """Generate a bill image with the given address and private key.

    Args:
        address: Bitcoin address string (bc1q... or bc1p...)
        private_key_wif: WIF-encoded private key
        address_type: "segwit" or "taproot" for display purposes
        is_tweaked: If True, render "(tweaked)" labels next to the private
                    key text and below the private key QR code.  Used for
                    Taproot addresses that have a backup key (script tree).
        sweep_url: If provided, the private key QR code encodes this URL
                   instead of the raw WIF (e.g. sweep page URL with WIF
                   and network query parameters).

    Returns:
        PIL Image of the completed bill
    """
    template = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(template)

    # ------------------------------------------------------------------
    # 0. Redraw "VIRES IN NUMERIS" and year on the banner
    #    Instead of covering with a rectangle (which would overlap the
    #    medallion circle), replace only the old blue text pixels with
    #    the banner background color, then redraw with the new font.
    # ------------------------------------------------------------------
    # Cover with solid rectangles — sized to NOT overlap the medallion.
    # Banner: x=1082..1225, medallion starts at ~x=1226.
    draw.rectangle((1082, 305, 1225, 339), fill=BANNER_COLOR)
    draw.rectangle(BANNER_RIGHT_BOX, fill=BANNER_COLOR)

    # Left segment: "VIRES IN NUMERIS"
    # Banner spans x=1082..1225 (144px). Add equal margins on both sides
    # so the text is centered between the banner left edge and medallion.
    lx1, ly1 = 1082 + 4, 301
    lx2_text, ly2 = 1225 - 2, 343
    lw, lh = lx2_text - lx1, ly2 - ly1
    motto = "VIRES IN NUMERIS"
    font, tw, th = _fit_font_to_box(draw, motto, lw, lh,
                                     start_size=24, font_paths=_SANS_FONT_PATHS)
    draw.text((lx1 + (lw - tw) // 2, ly1 + (lh - th) // 2),
              motto, fill=BANNER_TEXT_COLOR, font=font)

    # Right segment: current year — fit to the right box independently
    # so it appears visually the same weight/height as the motto text.
    rx1, ry1, rx2, ry2 = BANNER_RIGHT_BOX
    rw, rh = rx2 - rx1, ry2 - ry1
    year_str = str(datetime.now(timezone.utc).year)
    yr_font, tw, th = _fit_font_to_box(draw, year_str, rw, rh,
                                        start_size=24, font_paths=_SANS_FONT_PATHS)
    draw.text((rx1 + (rw - tw) // 2, ry1 + (rh - th) // 2),
              year_str, fill=BANNER_TEXT_COLOR, font=yr_font)

    # ------------------------------------------------------------------
    # 1. Address QR code — fill the left white box (284×284)
    # ------------------------------------------------------------------
    ax1, ay1, ax2, ay2 = ADDRESS_QR_BOX
    box_w = ax2 - ax1
    box_h = ay2 - ay1
    qr_side = min(box_w, box_h)
    addr_qr = generate_qr_image(address, size=qr_side, ec_level=EC_M)
    # Centre the square QR in the box
    paste_x = ax1 + (box_w - qr_side) // 2
    paste_y = ay1 + (box_h - qr_side) // 2
    template.paste(addr_qr, (paste_x, paste_y))

    # ------------------------------------------------------------------
    # 2. Private Key QR code — fill the right white box (284×285)
    #    Keep it square using the smaller dimension, centred in the box.
    #    If sweep_url is provided, encode the URL instead of the raw WIF.
    # ------------------------------------------------------------------
    px1, py1, px2, py2 = PRIVKEY_QR_BOX
    box_w = px2 - px1
    box_h = py2 - py1
    qr_side = min(box_w, box_h)
    qr_content = sweep_url if sweep_url else private_key_wif
    priv_qr = generate_qr_image(qr_content, size=qr_side, ec_level=EC_M)
    paste_x = px1 + (box_w - qr_side) // 2
    paste_y = py1 + (box_h - qr_side) // 2
    template.paste(priv_qr, (paste_x, paste_y))

    # ------------------------------------------------------------------
    # 2b. "(tweaked)" label inside the private key QR white box,
    #     bottom-right corner (overlaps the QR quiet zone).
    # ------------------------------------------------------------------
    if is_tweaked:
        tweaked_label = "(tweaked)"
        tw_font = _load_font(12, _SANS_FONT_PATHS)
        tw_bbox = draw.textbbox((0, 0), tweaked_label, font=tw_font)
        tw_w = tw_bbox[2] - tw_bbox[0]
        tw_h = tw_bbox[3] - tw_bbox[1]
        # Bottom-right of the QR white box with small margin
        tw_x = px2 - tw_w - 3
        tw_y = py2 - tw_h - 3
        draw.text((tw_x, tw_y), tweaked_label, fill=(30, 30, 30), font=tw_font)

    # ------------------------------------------------------------------
    # 3. Private key text — orange strip ABOVE the dollar box and QR
    #    (the dollar-sign box is left empty for handwriting)
    #    When is_tweaked, append " (tweaked)" to the right of the WIF.
    # ------------------------------------------------------------------
    tx1, ty1, tx2, ty2 = PRIVKEY_TEXT_AREA
    tw = tx2 - tx1   # ~708
    th = ty2 - ty1   # ~33

    if is_tweaked:
        # Measure the "(tweaked)" suffix in a smaller font
        suffix = " (tweaked)"
        suffix_font = _load_font(12, _SANS_FONT_PATHS)
        sb = draw.textbbox((0, 0), suffix, font=suffix_font)
        suffix_w = sb[2] - sb[0]
        suffix_h = sb[3] - sb[1]
        # Fit the WIF into a reduced-width area
        wif_avail_w = tw - suffix_w - 2
        font, text_w, text_h = _fit_font_to_box(
            draw, private_key_wif, wif_avail_w, th, start_size=24)
        # Right-align: suffix flush to x=1808, WIF just before it
        suffix_x = 1808 - suffix_w
        text_x = suffix_x - text_w - 2
        text_y = ty1 + (th - text_h) // 2
        draw.text((text_x, text_y), private_key_wif,
                  fill=(30, 30, 30), font=font)
        # Vertically centre the suffix relative to the WIF text
        suffix_y = ty1 + (th - suffix_h) // 2
        draw.text((suffix_x, suffix_y), suffix,
                  fill=(30, 30, 30), font=suffix_font)
    else:
        font, text_w, text_h = _fit_font_to_box(
            draw, private_key_wif, tw, th, start_size=24)
        # Right-align to the right edge of the QR box (x=1808)
        text_x = 1808 - text_w
        text_y = ty1 + (th - text_h) // 2
        draw.text((text_x, text_y), private_key_wif,
                  fill=(30, 30, 30), font=font)

    # ------------------------------------------------------------------
    # 4. Address text — bottom white band (801×58)
    #    Taproot addresses are 62 chars vs SegWit's 42 chars.
    #    Use a narrow/condensed font for Taproot so the text fills the
    #    box with similar visual weight to SegWit.
    # ------------------------------------------------------------------
    tx1, ty1, tx2, ty2 = ADDRESS_TEXT_BOX
    tw = tx2 - tx1
    th = ty2 - ty1
    addr_font_paths = _NARROW_FONT_PATHS if address_type == "taproot" else None
    font, text_w, text_h = _fit_font_to_box(draw, address, tw, th,
                                              start_size=36, font_paths=addr_font_paths)
    text_x = tx1 + (tw - text_w) // 2
    text_y = ty1 + (th - text_h) // 2
    draw.text((text_x, text_y), address, fill=(30, 30, 30), font=font)

    # ------------------------------------------------------------------
    # 5. Timestamp — vertical text at the bottom-right corner
    #    Readable when the bill is flipped 90° to the left (rotated CCW).
    #    Rendered with generous padding so it doesn't clip at the edge.
    # ------------------------------------------------------------------
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ts_font = _load_font(14)
    # Measure the text
    ts_bbox = draw.textbbox((0, 0), timestamp, font=ts_font)
    ts_w = ts_bbox[2] - ts_bbox[0]
    ts_h = ts_bbox[3] - ts_bbox[1]
    # Render onto a transparent image with padding, then rotate
    pad = 6
    ts_img = Image.new("RGBA", (ts_w + pad * 2, ts_h + pad * 2), (0, 0, 0, 0))
    ts_draw = ImageDraw.Draw(ts_img)
    ts_draw.text((pad, pad), timestamp, fill=(0, 0, 0, 255), font=ts_font)
    # Rotate 90° counter-clockwise so it reads bottom-to-top
    ts_rotated = ts_img.rotate(90, expand=True)
    # Position closer to the right edge of the bill
    img_w, img_h = template.size
    edge_margin = 8
    paste_x = img_w - ts_rotated.width - edge_margin
    paste_y = img_h - ts_rotated.height - edge_margin
    template.paste(ts_rotated, (paste_x, paste_y), ts_rotated)

    return template


def bill_to_png_bytes(bill_image):
    """Convert a bill PIL Image to PNG bytes."""
    buf = io.BytesIO()
    bill_image.save(buf, format='PNG', quality=95)
    return buf.getvalue()


def bill_to_base64(bill_image):
    """Convert a bill PIL Image to base64-encoded PNG string."""
    png_bytes = bill_to_png_bytes(bill_image)
    return base64.b64encode(png_bytes).decode('ascii')


if __name__ == "__main__":
    from bitcoin_crypto import generate_segwit_address, generate_taproot_address

    sw = generate_segwit_address(mainnet=True)
    bill = generate_bill_image(sw["address"], sw["private_key_wif"], "segwit")
    bill.save("test_bill_segwit.png")
    print(f"SegWit bill: {sw['address']}")

    tr = generate_taproot_address(mainnet=True, backup_key=False)
    bill = generate_bill_image(tr["address"], tr["private_key_wif"], "taproot")
    bill.save("test_bill_taproot.png")
    print(f"Taproot bill (no backup): {tr['address']}")

    from bitcoin_crypto import private_key_to_wif
    tr2 = generate_taproot_address(mainnet=True, backup_key=True)
    tweaked_wif = private_key_to_wif(
        bytes.fromhex(tr2["tweaked_private_key_hex"]), mainnet=True)
    bill = generate_bill_image(tr2["address"], tweaked_wif, "taproot",
                               is_tweaked=True)
    bill.save("test_bill_taproot_tweaked.png")
    print(f"Taproot bill (tweaked/backup): {tr2['address']}")
