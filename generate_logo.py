"""Generate the OneClick Backup logo and Windows icon.

Creates:
  assets/logo.png   – 512x512 PNG logo
  assets/icon.ico   – Multi-size Windows icon (16–256px)

Run once, then delete this script if you like.
"""

from PIL import Image, ImageDraw, ImageFont
import math, os

OUT_DIR = os.path.join(os.path.dirname(__file__), "assets")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Palette ──────────────────────────────────────────────
BG       = "#0f1117"
INDIGO   = "#6366f1"
INDIGO_L = "#818cf8"
INDIGO_D = "#4f46e5"
TEAL     = "#34d399"
SLATE    = "#1e293b"
WHITE    = "#f1f5f9"
GRAY     = "#475569"

SZ = 512
CX, CY = SZ // 2, SZ // 2


def _circle(draw, cx, cy, r, **kw):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], **kw)


def _rounded_rect(draw, bbox, r, **kw):
    draw.rounded_rectangle(bbox, radius=r, **kw)


def draw_logo(sz=SZ):
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz // 2
    s = sz / 512  # scale factor

    # ── Background: rounded square ──
    margin = int(16 * s)
    _rounded_rect(d, [margin, margin, sz - margin, sz - margin],
                  r=int(64 * s), fill=BG, outline=SLATE, width=int(3 * s))

    # ── Outer ring (subtle) ──
    ring_r = int(200 * s)
    _circle(d, cx, cy, ring_r, outline=SLATE, width=int(3 * s))

    # ── Disk platter: concentric rings ──
    platter_r = int(165 * s)
    _circle(d, cx, cy - int(10 * s), platter_r, fill=INDIGO_D)

    # Gradient-like rings on the platter
    for i, (r_frac, color) in enumerate([
        (0.95, INDIGO),
        (0.80, INDIGO_L),
        (0.65, INDIGO),
        (0.50, INDIGO_D),
    ]):
        r = int(platter_r * r_frac)
        _circle(d, cx, cy - int(10 * s), r,
                outline=color, width=int(2 * s))

    # ── Spindle center ──
    _circle(d, cx, cy - int(10 * s), int(35 * s), fill=BG, outline=INDIGO_L, width=int(3 * s))
    _circle(d, cx, cy - int(10 * s), int(15 * s), fill=INDIGO_L)

    # ── Read/write arm ──
    arm_start = (cx + int(10 * s), cy + int(100 * s))
    arm_elbow = (cx + int(100 * s), cy + int(40 * s))
    arm_head  = (cx + int(130 * s), cy - int(55 * s))

    d.line([arm_start, arm_elbow, arm_head],
           fill=GRAY, width=int(8 * s), joint="curve")
    # Arm head (triangle-ish)
    head_sz = int(14 * s)
    hx, hy = arm_head
    d.polygon([
        (hx - head_sz, hy - head_sz),
        (hx + head_sz, hy),
        (hx - head_sz, hy + head_sz),
    ], fill=TEAL)

    # ── Shield / checkmark overlay (bottom-right) ──
    shield_cx = cx + int(120 * s)
    shield_cy = cy + int(110 * s)
    shield_r  = int(58 * s)

    # Shield background
    _circle(d, shield_cx, shield_cy, shield_r, fill=INDIGO, outline=BG, width=int(5 * s))

    # Checkmark
    check_s = int(22 * s)
    check_pts = [
        (shield_cx - check_s, shield_cy),
        (shield_cx - int(6 * s), shield_cy + int(16 * s)),
        (shield_cx + check_s, shield_cy - int(14 * s)),
    ]
    d.line(check_pts, fill=WHITE, width=int(7 * s), joint="curve")

    # ── Arrow (bottom-left) — "one click" ──
    arrow_cx = cx - int(120 * s)
    arrow_cy = cy + int(120 * s)
    arr_r = int(42 * s)

    # Circular arrow background
    _circle(d, arrow_cx, arrow_cy, arr_r, fill=TEAL, outline=BG, width=int(4 * s))

    # Curved arrow icon
    arrow_w = int(5 * s)
    arc_bbox = [arrow_cx - int(20*s), arrow_cy - int(20*s),
                arrow_cx + int(20*s), arrow_cy + int(20*s)]
    d.arc(arc_bbox, start=200, end=340, fill=BG, width=arrow_w)
    # Arrow tip
    tip_x = arrow_cx + int(10 * s)
    tip_y = arrow_cy - int(18 * s)
    tip_s = int(8 * s)
    d.polygon([
        (tip_x, tip_y - tip_s),
        (tip_x + tip_s + int(3*s), tip_y + int(2*s)),
        (tip_x - int(2*s), tip_y + int(4*s)),
    ], fill=BG)

    # ── Text: "OneClick" at the bottom ──
    try:
        font_lg = ImageFont.truetype("bahnschrift.ttf", int(48 * s))
        font_sm = ImageFont.truetype("consola.ttf", int(20 * s))
    except OSError:
        try:
            font_lg = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", int(48 * s))
            font_sm = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", int(20 * s))
        except OSError:
            font_lg = ImageFont.load_default()
            font_sm = ImageFont.load_default()

    # "OneClick" text at bottom of the rounded square
    text_y = sz - margin - int(70 * s)
    d.text((cx, text_y), "OneClick", font=font_lg, fill=WHITE, anchor="mm")

    # "BACKUP" subtitle
    sub_y = text_y + int(34 * s)
    d.text((cx, sub_y), "BACKUP", font=font_sm, fill=INDIGO_L, anchor="mm")

    return img


def main():
    print("Generating logo...")
    logo = draw_logo(512)

    # Save PNG
    png_path = os.path.join(OUT_DIR, "logo.png")
    logo.save(png_path, "PNG")
    print(f"  -> {png_path}")

    # Generate icon sizes
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_images = []
    for s in ico_sizes:
        resized = logo.resize((s, s), Image.LANCZOS)
        ico_images.append(resized)

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    ico_images[0].save(
        ico_path, format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_images[1:],
    )
    print(f"  -> {ico_path}")
    print("Done!")


if __name__ == "__main__":
    main()
