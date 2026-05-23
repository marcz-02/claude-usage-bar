"""
Pillow-based brushstroke ring renderer for the macOS menu bar icon.

NOTE on Pillow arc geometry:
  draw.arc(bbox, width=w) places the stroke's OUTER edge at the bbox boundary
  and extends inward. So the stroke CENTER is at bbox_radius - w/2.
  We account for this so the background ring and the brushstroke share the
  same visual center radius.
"""
import math
from PIL import Image, ImageDraw

_TAPER = 0.72


def _draw_ring_on(draw: ImageDraw.ImageDraw, pct: float, size: int,
                  fg: tuple, bg_ring: tuple):
    """Draw onto an existing ImageDraw at the given size."""
    max_stroke = max(6, size // 5)

    outer_r = size // 2 - 2
    mid_r   = outer_r - max_stroke // 2
    pad     = size // 2 - outer_r
    bbox    = [pad, pad, size - pad - 1, size - pad - 1]
    bg_pad  = size // 2 - mid_r
    bg_bbox = [bg_pad, bg_pad, size - bg_pad - 1, size - bg_pad - 1]

    draw.arc(bg_bbox, start=0, end=360, fill=bg_ring, width=1)

    if pct <= 0:
        return

    total_deg = pct * 360.0
    N = max(200, int(total_deg * outer_r))

    for i in range(N):
        t0 = i / N
        a0 = -90.0 + t0 * total_deg
        a1 = -90.0 + (i + 1) / N * total_deg
        w  = int(round(max_stroke * (t0 ** _TAPER)))
        if w < 1:
            continue
        draw.arc(bbox, start=a0, end=a1, fill=fg, width=w)

    # Round cap at the thick leading edge
    end_rad = math.radians(-90.0 + total_deg)
    cx = cy = size / 2
    hx = cx + mid_r * math.cos(end_rad)
    hy = cy + mid_r * math.sin(end_rad)
    cap_r = max_stroke / 2 - 0.5
    draw.ellipse([hx - cap_r, hy - cap_r, hx + cap_r, hy + cap_r], fill=fg)


def render_ring(
    pct: float,
    size: int = 44,
    dark_mode: bool = True,
) -> Image.Image:
    pct = max(0.0, min(1.0, pct))

    fg       = (255, 255, 255, 255) if dark_mode else (17, 17, 17, 255)
    bg_alpha = 60 if dark_mode else 50
    bg_ring  = (255, 255, 255, bg_alpha) if dark_mode else (0, 0, 0, bg_alpha)

    # Render at 4× and downscale for smooth antialiasing
    scale  = 4
    hi     = size * scale
    hi_img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    _draw_ring_on(ImageDraw.Draw(hi_img), pct, hi, fg, bg_ring)

    return hi_img.resize((size, size), Image.LANCZOS)
