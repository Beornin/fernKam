"""Generate assets/fernkam.ico — an original mirrorless-camera-and-lens icon.

Not a reproduction of any manufacturer's logo/branding; just a flat,
professional-camera-style silhouette (EVF hump, deep grip, large lens with
aperture rings) in fernKam's dark theme, matching launcher.py's splash
screen colors. Draws at high resolution with supersampling for crisp edges,
then exports the standard multi-res Windows .ico.

Run: python scripts/generate_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "fernkam.ico"
S = 1024  # supersampled canvas size

# fernKam splash palette
BG_TOP = (34, 38, 46)
BG_BOTTOM = (18, 20, 25)
BODY = (86, 92, 104)
BODY_EDGE = (128, 135, 150)
BODY_LIGHT = (110, 117, 131)
LENS_BARREL = (44, 47, 55)
LENS_RING = (96, 102, 115)
GLASS_OUTER = (24, 45, 66)
GLASS_INNER = (110, 168, 254)  # accent blue, matches splash spinner
GLASS_HILITE = (210, 230, 255)
ACCENT = (255, 107, 90)  # shutter-button accent, warm not literal-red-logo


def rounded_square_bg(draw: ImageDraw.ImageDraw) -> None:
    for y in range(S):
        t = y / S
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (S, y)], fill=(r, g, b))


def build() -> Image.Image:
    img = Image.new("RGB", (S, S))
    draw = ImageDraw.Draw(img)
    rounded_square_bg(draw)

    # Mask the gradient into a rounded-square badge (Windows 11 icon convention)
    mask = Image.new("L", (S, S), 0)
    mdraw = ImageDraw.Draw(mask)
    pad = int(S * 0.06)
    mdraw.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.22), fill=255)
    badge = Image.composite(img, Image.new("RGB", (S, S), BG_BOTTOM), mask)
    img = badge
    draw = ImageDraw.Draw(img)

    cx, cy = S // 2, int(S * 0.58)

    # Camera body (rounded rect) with an EVF hump on top-left and a grip bulge on the right
    body_w, body_h = int(S * 0.64), int(S * 0.26)
    bx0, by0 = cx - body_w // 2, cy - body_h // 2
    bx1, by1 = cx + body_w // 2, cy + body_h // 2
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=int(S * 0.045), fill=BODY)
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=int(S * 0.045), outline=BODY_EDGE, width=int(S * 0.008))

    # EVF hump (pentaprism-style bump, sits above the body's left-of-center)
    evf_w = int(body_w * 0.26)
    evf_h = int(S * 0.10)
    evf_x0 = bx0 + int(body_w * 0.14)
    evf_box = [evf_x0, by0 - evf_h, evf_x0 + evf_w, by0 + int(S * 0.02)]
    draw.rounded_rectangle(evf_box, radius=int(S * 0.02), fill=BODY)
    draw.rounded_rectangle(evf_box, radius=int(S * 0.02), outline=BODY_EDGE, width=int(S * 0.007))

    # Grip bulge (right edge, lighter for depth/highlight)
    draw.rounded_rectangle(
        [bx1 - int(S * 0.07), by0, bx1 + int(S * 0.02), by1], radius=int(S * 0.035), fill=BODY_LIGHT
    )

    # Shutter-button accent, top-right of body
    sb_r = int(S * 0.022)
    sb_cx, sb_cy = bx1 - int(S * 0.045), by0 - int(S * 0.01)
    draw.ellipse([sb_cx - sb_r, sb_cy - sb_r, sb_cx + sb_r, sb_cy + sb_r], fill=ACCENT)

    # Lens barrel, centered on the body's front, overlapping the bottom edge
    lens_r = int(S * 0.24)
    lens_cy = cy + int(body_h * 0.30)
    draw.ellipse([cx - lens_r, lens_cy - lens_r, cx + lens_r, lens_cy + lens_r], fill=LENS_BARREL)
    draw.ellipse(
        [cx - lens_r, lens_cy - lens_r, cx + lens_r, lens_cy + lens_r],
        outline=LENS_RING,
        width=int(S * 0.012),
    )

    # Aperture ring
    ring_r = int(lens_r * 0.78)
    draw.ellipse(
        [cx - ring_r, lens_cy - ring_r, cx + ring_r, lens_cy + ring_r],
        outline=LENS_RING,
        width=int(S * 0.01),
    )

    # Glass (front element) with a soft highlight
    glass_r = int(lens_r * 0.62)
    draw.ellipse(
        [cx - glass_r, lens_cy - glass_r, cx + glass_r, lens_cy + glass_r],
        fill=GLASS_OUTER,
    )
    inner_r = int(glass_r * 0.72)
    draw.ellipse(
        [cx - inner_r, lens_cy - inner_r, cx + inner_r, lens_cy + inner_r],
        fill=GLASS_INNER,
    )
    # crescent highlight
    hl_r = int(inner_r * 0.55)
    hl_cx, hl_cy = cx - int(inner_r * 0.35), lens_cy - int(inner_r * 0.35)
    draw.ellipse([hl_cx - hl_r, hl_cy - hl_r, hl_cx + hl_r, hl_cy + hl_r], fill=GLASS_HILITE)
    hl_r2 = int(hl_r * 0.78)
    draw.ellipse(
        [hl_cx - hl_r2 + int(S * 0.02), hl_cy - hl_r2 + int(S * 0.02), hl_cx + hl_r2 + int(S * 0.02), hl_cy + hl_r2 + int(S * 0.02)],
        fill=GLASS_INNER,
    )

    return img


def main() -> None:
    img = build()
    img = img.resize((256, 256), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
