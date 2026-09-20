"""Render the PDFusion app icon from its geometry.

The mark is "Fusion": two overlapping rounded pages where only the overlap
carries the brand accent — source and translation as one document, which is the
premise the whole pipeline is built on.

Why this file exists at all, rather than a checked-in .svg someone edits: the
geometry below is the only definition of the mark, and this script emits *both*
the PNG masters the bundler needs and the .svg used at arbitrary size. Two
hand-maintained copies of the same rectangle drift, and the drift is invisible
until an installer ships with a mark that no longer matches the site.

Why the mark sits on a filled tile instead of the transparent ground the concept
was drawn on: a PNG icon cannot be theme-aware. Near-black strokes on
transparency are legible on a light taskbar and gone on a dark one, so the icon
has to carry its own ground. The ink tile is the same green-biased near-black
used by the UI, and every element on top of it is white or accent.

Run from anywhere:

    python scripts/render_icon.py

then regenerate the bundle set from the master it writes:

    cd desktop && node_modules/.bin/tauri icon ../assets/branding/icon-1024.png
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

# Everything is specified on a 256-unit grid — the grid the concept was drawn on
# — and scaled to whatever the output size is. Keeping the numbers small and
# integral is what makes the SVG and the PNG provably the same drawing.
GRID = 256

# Corner radius as a fraction of the tile, not an absolute: the tile is the only
# shape whose size changes between the two masters.
TILE_RADIUS = 0.175

# The two pages, offset on both axes. The offset is not styling: two sheets of
# equal height sharing a top and bottom edge have a union that is itself one
# rectangle, so with flat fills and no outline they read as one page in three
# colours. Staggering them diagonally gives the silhouette a step, which is what
# says "two sheets" at any size. The pair is centred in the grid (28/28 and
# 30/30 of margin), not either box.
BACK_PAGE = (28, 30, 156, 194)
FRONT_PAGE = (100, 62, 228, 226)
PAGE_RADIUS = 18

INK = (19, 32, 26)
PAPER = (255, 255, 255)
# Both pages are filled, and the front one is not outlined. An outlined front
# page was tried first and fails twice over: at 1024 the stroke reads as a white
# frame rather than a sheet, and at 16px — the size Windows paints in the
# taskbar — a stroke this shape can afford falls below one pixel and the two
# pages collapse into a smudge. Three flat areas (sheet, overlap, sheet) is what
# survives the downsample, and it states the set intersection more plainly.
BACK_PAGE_ALPHA = 115

# The UI's own --primary (desktop/src/index.css). Converted rather than
# hand-picked so the icon and the accent in the app are the same colour by
# construction.
ACCENT_OKLCH = (0.689, 0.179, 142.51)

# Pillow has no path antialiasing: a rounded rectangle drawn at the output size
# has visibly stepped corners. Drawing 4x oversized and downsampling is the
# whole antialiasing strategy, and 4x is where the corners stop improving.
SUPERSAMPLE = 4

# macOS lays its dock icons out expecting roughly a tenth of the canvas as clear
# margin; Windows and Linux paint the icon edge to edge. One composition, two
# crops — scaling the whole tile rather than re-placing the mark inside it.
MACOS_SCALE = 0.8


def oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple[int, int, int]:
    """Convert an OKLCH colour to 8-bit sRGB."""
    hue = math.radians(hue_deg)
    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)

    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long_, med, short = l_**3, m_**3, s_**3

    linear = (
        4.0767416621 * long_ - 3.3077115913 * med + 0.2309699292 * short,
        -1.2684380046 * long_ + 2.6097574011 * med - 0.3413193965 * short,
        -0.0041960863 * long_ - 0.7034186147 * med + 1.7076147010 * short,
    )

    channels = []
    for value in linear:
        value = max(0.0, min(1.0, value))
        encoded = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        channels.append(round(max(0.0, min(1.0, encoded)) * 255))
    return channels[0], channels[1], channels[2]


ACCENT = oklch_to_srgb(*ACCENT_OKLCH)


def _scaled(box: tuple[int, int, int, int], scale: float) -> tuple[float, float, float, float]:
    return (box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale)


def _page_mask(size: int, box: tuple[int, int, int, int], scale: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        _scaled(box, scale), radius=PAGE_RADIUS * scale, fill=255
    )
    return mask


def _tinted(size: int, colour: tuple[int, int, int], mask: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", (size, size), colour + (0,))
    layer.putalpha(mask)
    return layer


def render_mark(size: int) -> Image.Image:
    """Draw the full-bleed composition at `size` px, antialiased."""
    work = size * SUPERSAMPLE
    scale = work / GRID

    canvas = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).rounded_rectangle(
        (0, 0, work - 1, work - 1), radius=TILE_RADIUS * work, fill=INK + (255,)
    )

    back = _page_mask(work, BACK_PAGE, scale)
    front = _page_mask(work, FRONT_PAGE, scale)

    canvas = Image.alpha_composite(
        canvas, _tinted(work, PAPER, back.point(lambda a: a * BACK_PAGE_ALPHA // 255))
    )
    canvas = Image.alpha_composite(canvas, _tinted(work, PAPER, front))

    # The accent region is the intersection of the two page masks, not a third
    # rectangle: at the rounded corners where the pages meet, a hand-placed rect
    # leaves a visible sliver of the wrong colour. It goes on last, over both
    # sheets, because the overlap is the subject of the mark.
    canvas = Image.alpha_composite(
        canvas, _tinted(work, ACCENT, ImageChops.multiply(back, front))
    )

    return canvas.resize((size, size), Image.LANCZOS)


def inset_for_macos(mark: Image.Image) -> Image.Image:
    """Shrink the whole tile onto a transparent canvas of the same size."""
    size = mark.width
    # Even inner size keeps the margins equal to the pixel.
    inner = round(size * MACOS_SCALE / 2) * 2
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(mark.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2,) * 2)
    return canvas


def _hex(colour: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


def build_svg() -> str:
    """Emit the same drawing as SVG, from the same constants."""
    bx0, by0, bx1, by1 = BACK_PAGE
    fx0, fy0, fx1, fy1 = FRONT_PAGE
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" \
width="{GRID}" height="{GRID}" role="img" aria-label="PDFusion">
  <!-- Generated by scripts/render_icon.py — edit the geometry there, not here. -->
  <defs>
    <clipPath id="pdfusion-front">
      <rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" rx="{PAGE_RADIUS}"/>
    </clipPath>
  </defs>
  <rect width="{GRID}" height="{GRID}" rx="{round(TILE_RADIUS * GRID, 1)}" fill="{_hex(INK)}"/>
  <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}" rx="{PAGE_RADIUS}"
        fill="{_hex(PAPER)}" fill-opacity="{round(BACK_PAGE_ALPHA / 255, 3)}"/>
  <rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" rx="{PAGE_RADIUS}"
        fill="{_hex(PAPER)}"/>
  <g clip-path="url(#pdfusion-front)">
    <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}" rx="{PAGE_RADIUS}"
          fill="{_hex(ACCENT)}"/>
  </g>
</svg>
"""


def main() -> None:
    default_out = Path(__file__).resolve().parent.parent / "assets" / "branding"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=default_out)
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    mark = render_mark(args.size)
    master = args.out_dir / f"icon-{args.size}.png"
    macos = args.out_dir / f"icon-{args.size}-macos.png"
    svg = args.out_dir / "pdfusion-icon.svg"

    mark.save(master)
    inset_for_macos(mark).save(macos)
    svg.write_text(build_svg(), encoding="utf-8")

    print(f"accent {_hex(ACCENT)} from oklch{ACCENT_OKLCH}")
    for path in (master, macos, svg):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
