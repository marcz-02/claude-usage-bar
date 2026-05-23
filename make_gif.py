"""Generate ring-dark.gif and ring-light.gif for the README.

Both use **transparent backgrounds** so they blend with any viewer background
and avoid a solid-colour box artefact with browser dark-mode extensions.

Dark variant:  white ring on transparent  — matches GitHub dark (#0d1117).
Light variant: dark ring on transparent   — matches GitHub light (#ffffff).

Transparent GIFs use 1-bit palette transparency (GIF limitation).
Ring edges are alpha-thresholded at 64 — slight aliasing, barely visible
at 88 px given the thick brushstroke stroke style.
"""
from pathlib import Path
from PIL import Image

SRC  = Path('/tmp/claude_ring_icons')
OUT  = Path(__file__).parent / 'screenshots'
SIZE = 88            # 2× the real 44 px icon — crisp at "right-float" size in README
ALPHA_THRESHOLD = 64 # pixels with alpha > this → ring colour; rest → transparent


def make_frame(n: int, ring_rgb: tuple) -> Image.Image:
    """Return a palette-mode (P) frame with index 0 = transparent, index 1 = ring colour."""
    icon = Image.open(SRC / f'r{n:03d}.png').convert('RGBA')
    icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    _, _, _, alpha = icon.split()

    # Build a 2-slot palette: slot 0 = background (transparent), slot 1 = ring colour
    palette = [0] * (256 * 3)
    palette[0], palette[1], palette[2] = 0, 0, 0           # slot 0: transparent
    palette[3], palette[4], palette[5] = ring_rgb           # slot 1: ring

    pal_img = Image.new('P', (SIZE, SIZE), 0)   # start fully transparent
    pal_img.putpalette(palette)

    alpha_data = alpha.load()
    pal_data   = pal_img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            if alpha_data[x, y] > ALPHA_THRESHOLD:
                pal_data[x, y] = 1   # ring pixel

    return pal_img


def build(path: Path, ring_rgb: tuple) -> None:
    frames, ms = [], []
    for n in range(0, 101, 2):        # 0 → 100 % in steps of 2
        frames.append(make_frame(n, ring_rgb))
        ms.append(50)
    for _ in range(12):               # hold at 100 %
        frames.append(frames[-1])
        ms.append(60)
    zero = make_frame(0, ring_rgb)
    frames.append(zero); ms.append(80)   # instant reset
    for _ in range(6):                # hold at 0 %
        frames.append(zero)
        ms.append(60)

    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=ms,
        optimize=False,
        transparency=0,   # palette index 0 is the transparent colour
        disposal=2,        # restore to transparent between frames
    )
    print(f'{path.name}: {len(frames)} frames  {path.stat().st_size // 1024} KB')


OUT.mkdir(exist_ok=True)
build(OUT / 'ring-dark.gif',  (255, 255, 255))   # white ring → GitHub dark mode
build(OUT / 'ring-light.gif', (28,  28,  30))    # dark ring  → GitHub light mode
print('done')
