"""Generate ring-dark.gif and ring-light.gif — simulated macOS menu bar."""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SRC = Path('/tmp/claude_ring_icons')
OUT = Path(__file__).parent / 'screenshots'

# Canvas: wide menu bar strip
W, H      = 420, 40
ICON_SIZE = 26   # ring icon size inside the bar

# macOS colour schemes
DARK_BAR  = (30,  30,  32)
LIGHT_BAR = (236, 236, 236)
DARK_TXT  = (255, 255, 255, 160)   # dim white dots
LIGHT_TXT = (0,   0,   0,   100)   # dim black dots


def fake_icons(draw, bar_color):
    """Draw a few placeholder circles/text to the left to simulate other icons."""
    is_dark = bar_color[0] < 128
    col = (255, 255, 255, 80) if is_dark else (0, 0, 0, 60)
    # Three small rounded-rect placeholders
    for x in [16, 52, 82]:
        draw.rounded_rectangle([x, 11, x + 26, 29], radius=3, fill=col)


def load_ring_dark(n):
    """White ring, resized for menu bar."""
    img = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    return img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)


def load_ring_light(n):
    """Recolor alpha mask → dark ring for light bar."""
    img = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    r, g, b, a = img.split()
    dark = Image.merge('RGBA', (
        Image.new('L', (ICON_SIZE, ICON_SIZE), 30),
        Image.new('L', (ICON_SIZE, ICON_SIZE), 30),
        Image.new('L', (ICON_SIZE, ICON_SIZE), 32),
        a,
    ))
    return dark


def make_frame(n, bar_color, ring_loader):
    base = Image.new('RGBA', (W, H), bar_color + (255,))
    draw = ImageDraw.Draw(base, 'RGBA')
    fake_icons(draw, bar_color)
    ring = ring_loader(n)
    # Place ring near right edge
    x = W - ICON_SIZE - 14
    y = (H - ICON_SIZE) // 2
    base.paste(ring, (x, y), mask=ring)
    return base.convert('RGB')


def build(path, bar_color, ring_loader):
    frames, ms = [], []
    for n in range(0, 101, 2):
        frames.append(make_frame(n, bar_color, ring_loader))
        ms.append(50)
    for _ in range(12):          # hold at 100 %
        frames.append(frames[-1]); ms.append(60)
    zero = make_frame(0, bar_color, ring_loader)
    frames.append(zero); ms.append(80)
    for _ in range(6):           # hold at 0 %
        frames.append(zero); ms.append(60)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   loop=0, duration=ms, optimize=False)
    print(f'{path.name}: {len(frames)} frames  {path.stat().st_size // 1024} KB')


OUT.mkdir(exist_ok=True)
build(OUT / 'ring-dark.gif',  DARK_BAR,  load_ring_dark)
build(OUT / 'ring-light.gif', LIGHT_BAR, load_ring_light)
print('done')
