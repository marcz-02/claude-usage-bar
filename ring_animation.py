#!/usr/bin/env python3
"""
Standalone tkinter animation of the Claude session ring.
Shows a smooth 0%→100%→0% loop, exactly like the reference image.
Run: python3 ring_animation.py
"""
import tkinter as tk
import math

# ── Layout constants ──────────────────────────────────────────────────────────
WINDOW   = 300
CX, CY   = WINDOW // 2, WINDOW // 2
RADIUS   = 105          # center-to-arc-center radius
STROKE   = 20           # arc line width
BG_COLOR = "#ffffff"    # window background
RING_BG  = "#e0e0e0"    # thin background ring color
RING_FG  = "#111111"    # progress arc color
FONT     = ("Helvetica Neue", 38, "bold")

# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bbox(cx, cy, r):
    return cx - r, cy - r, cx + r, cy + r


def _cap(cx, cy, r, angle_deg, cap_r):
    """Return bbox for a filled circle (round cap) at angle_deg on radius r."""
    a  = math.radians(angle_deg)
    px = cx + r * math.cos(a)
    py = cy + r * math.sin(a)
    return px - cap_r, py - cap_r, px + cap_r, py + cap_r


def _tk_start(pct):
    """
    tkinter arc angles: 0° = 3 o'clock, counter-clockwise positive.
    We want 12 o'clock as start → 90°.
    Returns (start_deg, extent_deg) tuple.
    """
    start  = 90.0
    extent = -(pct / 100.0 * 360.0)
    return start, extent


def _angle_at_end(pct):
    """
    Absolute angle (tkinter convention) of the arc's moving end point.
    Used to place the round cap correctly.
    """
    return 90.0 + (pct / 100.0 * 360.0)

# ── Drawing ───────────────────────────────────────────────────────────────────

def draw_ring(canvas, pct):
    canvas.delete("all")
    pct = max(0.0, min(100.0, pct))

    cap_r  = STROKE / 2
    x0, y0, x1, y1 = _bbox(CX, CY, RADIUS)

    # 1. Thin background ring
    canvas.create_oval(x0, y0, x1, y1,
                       outline=RING_BG, width=2, fill="")

    if pct <= 0:
        # Draw a small dot at 12 o'clock so 0% still shows something subtle
        canvas.create_oval(*_cap(CX, CY, RADIUS, -90, cap_r),
                           fill=RING_BG, outline="")
        canvas.create_text(CX, CY, text="0%", font=FONT, fill=RING_FG)
        return

    start, extent = _tk_start(pct)

    # 2. Progress arc
    canvas.create_arc(x0, y0, x1, y1,
                      start=start, extent=extent,
                      style=tk.ARC, outline=RING_FG, width=STROKE)

    # 3. Round caps (tkinter arcs have flat ends by default)
    #    Start cap: always at 12 o'clock (angle -90 in math convention)
    canvas.create_oval(*_cap(CX, CY, RADIUS, -90, cap_r),
                       fill=RING_FG, outline="")

    #    End cap: travels clockwise from -90 by pct * 3.6 degrees
    end_math_angle = -90 + (pct / 100.0 * 360.0)
    canvas.create_oval(*_cap(CX, CY, RADIUS, end_math_angle, cap_r),
                       fill=RING_FG, outline="")

    # 4. Percentage label
    label = f"{int(pct)}%" if pct == int(pct) else f"{pct:.1f}%"
    canvas.create_text(CX, CY, text=label, font=FONT, fill=RING_FG)

# ── Animation loop ────────────────────────────────────────────────────────────

class RingAnimation:
    STEP = 0.6   # percent per frame  (0.6 × 60fps ≈ 2.7s for full sweep)
    FPS  = 60

    def __init__(self, root, canvas):
        self.root      = root
        self.canvas    = canvas
        self.pct       = 0.0
        self.direction = 1
        self._tick()

    def _tick(self):
        draw_ring(self.canvas, self.pct)

        self.pct += self.direction * self.STEP

        if self.pct >= 100.0:
            self.pct       = 100.0
            self.direction = -1
        elif self.pct <= 0.0:
            self.pct       = 0.0
            self.direction = 1

        self.root.after(1000 // self.FPS, self._tick)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    root   = tk.Tk()
    root.title("Claude Session Ring")
    root.resizable(False, False)

    canvas = tk.Canvas(root, width=WINDOW, height=WINDOW,
                       bg=BG_COLOR, highlightthickness=0)
    canvas.pack()

    RingAnimation(root, canvas)
    root.mainloop()


if __name__ == "__main__":
    main()
