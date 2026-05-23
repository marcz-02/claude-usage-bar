"""Generate ring-dark.gif and ring-light.gif for the README.

Light: dark ring on white  (#ffffff) — blends seamlessly with GitHub light mode.
Dark:  white ring on dark  (#1c1c1e) — blends seamlessly with GitHub dark mode.
Both are square (SIZE×SIZE), no menu-bar chrome.
"""
import os
from pathlib import Path
from PIL import Image

SRC  = Path('/tmp/claude_ring_icons')
OUT  = Path(__file__).parent / 'screenshots'
SIZE = 88   # 2× the real 44 px icon — crisp at "right-float" size in README


def load_dark(n):
    """White ring composited on dark background."""
    icon = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    bg   = Image.new('RGB', (SIZE, SIZE), (28, 28, 30))
    bg.paste(icon, mask=icon)
    return bg


def load_light(n):
    """Dark ring on white background — recolor alpha mask, no re-render."""
    icon = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    _, _, _, a = icon.split()
    colored = Image.merge('RGBA', (
        Image.new('L', (SIZE, SIZE), 28),
        Image.new('L', (SIZE, SIZE), 28),
        Image.new('L', (SIZE, SIZE), 30),
        a,
    ))
    bg = Image.new('RGB', (SIZE, SIZE), (255, 255, 255))
    bg.paste(colored, mask=a)
    return bg


def build(path, loader):
    frames, ms = [], []
    for n in range(0, 101, 2):          # 0→100 % in steps of 2
        frames.append(loader(n)); ms.append(50)
    for _ in range(12):                  # hold at 100 %
        frames.append(frames[-1]); ms.append(60)
    zero = loader(0)
    frames.append(zero); ms.append(80)   # instant reset
    for _ in range(6):                   # hold at 0 %
        frames.append(zero); ms.append(60)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   loop=0, duration=ms, optimize=False)
    print(f'{path.name}: {len(frames)} frames  {path.stat().st_size // 1024} KB')


OUT.mkdir(exist_ok=True)
build(OUT / 'ring-dark.gif',  load_dark)
build(OUT / 'ring-light.gif', load_light)
print('done')
