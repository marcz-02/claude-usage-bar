"""Generate ring-dark.gif and ring-light.gif for the README.

Uses the pre-rendered icons in /tmp/claude_ring_icons/ (white ring, RGBA).
Light variant: recolors the alpha mask to a dark ring on GitHub's light bg.
Dark variant:  composites white ring on macOS dark bg.
Run with: python3 make_gif.py
"""
import os
from pathlib import Path
from PIL import Image, ImageOps

SRC  = Path('/tmp/claude_ring_icons')
OUT  = Path(__file__).parent / 'screenshots'
SIZE = 160

DARK_BG  = (28,  28,  30)    # macOS menu-bar dark
LIGHT_BG = (246, 248, 250)   # GitHub light background
DARK_INK = (36,  36,  38)    # near-black ring for light mode


def load_icon(n):
    """Load pre-rendered icon, resize to SIZE. Returns RGBA."""
    img = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def dark_frame(icon):
    """White ring composited on dark background."""
    bg = Image.new('RGB', (SIZE, SIZE), DARK_BG)
    bg.paste(icon, mask=icon)
    return bg


def light_frame(icon):
    """Recolor: use alpha channel to paint a dark ring on a light background."""
    r, g, b, a = icon.split()
    # Paint the ring dark instead of white
    colored = Image.merge('RGBA', (
        Image.new('L', (SIZE, SIZE), DARK_INK[0]),
        Image.new('L', (SIZE, SIZE), DARK_INK[1]),
        Image.new('L', (SIZE, SIZE), DARK_INK[2]),
        a,
    ))
    bg = Image.new('RGB', (SIZE, SIZE), LIGHT_BG)
    bg.paste(colored, mask=a)
    return bg


def build_gif(frame_fn, path):
    frames, ms = [], []
    pcts = list(range(0, 101, 2))          # 51 steps

    for n in pcts:
        frames.append(frame_fn(load_icon(n)))
        ms.append(50)

    for _ in range(12):                    # hold at 100 %
        frames.append(frames[-1])
        ms.append(60)

    zero = frame_fn(load_icon(0))
    frames.append(zero); ms.append(80)    # reset
    for _ in range(6):                    # hold at 0 %
        frames.append(zero); ms.append(60)

    frames[0].save(path, save_all=True, append_images=frames[1:],
                   loop=0, duration=ms, optimize=False)
    print(f'{path.name}: {len(frames)} frames, {path.stat().st_size // 1024} KB')


OUT.mkdir(exist_ok=True)
build_gif(dark_frame,  OUT / 'ring-dark.gif')
build_gif(light_frame, OUT / 'ring-light.gif')
print('done')
