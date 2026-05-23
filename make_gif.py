"""One-off script to generate the animated README GIF."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ring_renderer import render_ring
from PIL import Image

SIZE = 120
BG   = (28, 28, 30, 255)   # macOS dark menu-bar colour

def frame(pct):
    ring = render_ring(pct, size=SIZE, dark_mode=True)
    bg   = Image.new("RGBA", (SIZE, SIZE), BG)
    bg.paste(ring, mask=ring)
    return bg.convert("RGB")

imgs, ms = [], []

# Fill 0 → 100 % with smoothstep easing (80 steps, 40 ms each ≈ 3.2 s)
N = 80
for i in range(N + 1):
    t = i / N
    imgs.append(frame(t * t * (3 - 2 * t)))
    ms.append(40)

# Hold at 100 % (15 frames × 60 ms ≈ 0.9 s)
for _ in range(15):
    imgs.append(frame(1.0))
    ms.append(60)

# Instant reset (1 frame, 80 ms pause)
imgs.append(frame(0.0))
ms.append(80)

# Hold at 0 % (8 frames × 60 ms ≈ 0.5 s)
for _ in range(8):
    imgs.append(frame(0.0))
    ms.append(60)

out = os.path.join(os.path.dirname(__file__), "screenshots", "ring.gif")
os.makedirs(os.path.dirname(out), exist_ok=True)
imgs[0].save(out, save_all=True, append_images=imgs[1:],
             loop=0, duration=ms, optimize=False)

print(f"saved {out}  ({len(imgs)} frames, {os.path.getsize(out)//1024} KB)")
