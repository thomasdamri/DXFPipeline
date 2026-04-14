"""
rasterise_tiles.py
──────────────────
Converts a DXF-derived SVG → high-res PNG → XYZ tile pyramid for Leaflet.

Reads  : drawing.svg  (or drawing_<theme>.svg)
Writes : tiles/{z}/{x}/{y}.png              (no --theme)
         tiles/<theme>/{z}/{x}/{y}.png      (with --theme)
         tile_meta.json                     ← loaded by DXFViewer.tsx

Rasteriser: cairosvg (pure-Python, no external binary)
    conda install -c conda-forge cairosvg

Requirements (tiling only):
    conda install -c conda-forge pillow

Usage:
    python rasterise_tiles.py --svg drawing.svg
    python rasterise_tiles.py --svg drawing_dark.svg --theme dark --bg-color "#1A1A2E"
    python rasterise_tiles.py --svg drawing.svg --max-zoom 6
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TILE_SIZE         = 256
MIN_FULL_WIDTH_PX = 4096


# ── Colour helpers ────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert '#RRGGBB' (or 'RRGGBB') to an (R, G, B) int tuple."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ── SVG viewBox reader ────────────────────────────────────────────────────────

def _read_svg_viewbox(svg_path: str) -> tuple[float, float]:
    """Read viewBox width and height from the SVG file header."""
    # viewBox is always within the first 4 KB of the SVG
    with open(svg_path, "r", encoding="utf-8") as f:
        head = f.read(4096)
    m = re.search(r'viewBox="([^"]+)"', head)
    if m:
        parts = [float(x) for x in m.group(1).split()]
        if len(parts) == 4:
            return parts[2], parts[3]   # width, height
    # Fallback: width/height attributes
    mw = re.search(r'\bwidth="([\d.]+)', head)
    mh = re.search(r'\bheight="([\d.]+)', head)
    if mw and mh:
        return float(mw.group(1)), float(mh.group(1))
    raise ValueError(f"Could not read SVG dimensions from {svg_path}")


# ── tiler ─────────────────────────────────────────────────────────────────────

def _auto_max_zoom(vb_w: float) -> int:
    target_tiles = math.ceil(MIN_FULL_WIDTH_PX / TILE_SIZE)
    z = max(math.ceil(math.log2(target_tiles)), 3)
    return min(z, 8)


def _generate_tiles(img, out_dir: Path, max_zoom: int,
                    full_w: int, full_h: int, tile_sz: int,
                    bg_rgb: tuple[int, int, int] = (255, 255, 255)):
    from PIL import Image

    # Normalise by the shorter dimension so that axis spans exactly tile_sz
    # CRS.Simple coordinate units at zoom 0. This produces a rectangular tile
    # grid (cols × rows) that matches the Leaflet coordinate space.
    short = min(full_w, full_h)
    total_tiles = sum(
        max(1, math.ceil(full_w * (2 ** z) / short)) *
        max(1, math.ceil(full_h * (2 ** z) / short))
        for z in range(max_zoom + 1)
    )
    written = 0

    for z in range(max_zoom + 1):
        cols         = max(1, math.ceil(full_w * (2 ** z) / short))
        rows         = max(1, math.ceil(full_h * (2 ** z) / short))
        scale_factor = (2 ** z) * tile_sz / short
        target_w     = min(round(full_w * scale_factor), cols * tile_sz)
        target_h     = min(round(full_h * scale_factor), rows * tile_sz)

        scaled = img.resize((target_w, target_h), Image.LANCZOS)

        canvas = Image.new("RGB", (cols * tile_sz, rows * tile_sz), bg_rgb)
        if scaled.mode == "RGBA":
            canvas.paste(scaled, (0, 0), mask=scaled.split()[3])
        else:
            canvas.paste(scaled, (0, 0))

        for tx in range(cols):
            for ty in range(rows):
                left  = tx * tile_sz
                upper = ty * tile_sz
                tile  = canvas.crop((left, upper, left + tile_sz, upper + tile_sz))
                tile_dir = out_dir / str(z) / str(tx)
                tile_dir.mkdir(parents=True, exist_ok=True)
                tile.save(tile_dir / f"{ty}.png", "PNG", optimize=True)
                written += 1

        pct = 100 * written / total_tiles
        logger.debug("z=%d  %3dx%-3d tiles  [%d/%d  %.0f%%]", z, cols, rows, written, total_tiles, pct)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rasterise DXF SVG to PNG tile pyramid using cairosvg."
    )
    p.add_argument("--svg",       required=True, metavar="FILE",
                   help="SVG from render_svg.py")
    p.add_argument("--max-zoom",  type=int, default=None, metavar="N",
                   help=f"Max tile zoom level (default: auto so drawing is "
                        f">={MIN_FULL_WIDTH_PX}px wide)")
    p.add_argument("--tiles-dir", default="tiles",          metavar="DIR")
    p.add_argument("--tile-meta", default="tile_meta.json", metavar="FILE")
    p.add_argument("--tile-size", type=int, default=TILE_SIZE, metavar="PX")
    p.add_argument("--theme",     default=None,  metavar="NAME",
                   help="Theme name (e.g. 'dark', 'light'). When given, tiles "
                        "are written to <tiles-dir>/<theme>/")
    p.add_argument("--bg-color",  default="#ffffff", metavar="HEX",
                   help="Background colour as hex (default: #ffffff). Sets the "
                        "cairosvg render background and the tile canvas fill.")
    return p.parse_args()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    try:
        import cairosvg
    except ImportError:
        sys.exit("cairosvg not installed.  Run:  conda install -c conda-forge cairosvg")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        sys.exit("Pillow not installed.  Run:  conda install -c conda-forge pillow")

    args    = parse_args()
    tile_sz = args.tile_size

    bg_color = args.bg_color or "#ffffff"
    bg_rgb   = _hex_to_rgb(bg_color)

    vb_w, vb_h = _read_svg_viewbox(args.svg)

    max_zoom    = args.max_zoom or _auto_max_zoom(vb_w)
    target_w_px = 2 ** max_zoom * tile_sz
    target_h_px = round(vb_h * (target_w_px / vb_w))

    logger.info("SVG viewBox  : %.2f x %.2f mm", vb_w, vb_h)
    logger.info("Max zoom     : %d  (grid %dx%d, target %d x %d px)",
                max_zoom, 2**max_zoom, 2**max_zoom, target_w_px, target_h_px)
    if args.theme:
        logger.info("Theme        : %s", args.theme)
    logger.info("Background   : %s  %s", bg_color, bg_rgb)

    logger.info("Rasterising  : %s -> %dpx wide ...", args.svg, target_w_px)
    png_bytes = cairosvg.svg2png(
        url=str(Path(args.svg).resolve()),
        output_width=target_w_px,
        output_height=target_h_px,
    )

    from PIL import Image
    full_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    actual_w, actual_h = full_img.size
    logger.info("Rendered     : %d x %d px", actual_w, actual_h)

    full_w_px = actual_w
    full_h_px = actual_h

    # When --theme is given, nest tiles under a subdirectory
    tiles_dir = Path(args.tiles_dir)
    if args.theme:
        tiles_dir = tiles_dir / args.theme
    tiles_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Tiling into  : %s/", tiles_dir)
    _generate_tiles(full_img, tiles_dir, max_zoom, full_w_px, full_h_px,
                    tile_sz, bg_rgb=bg_rgb)

    # Coordinate space: normalise by shorter pixel dimension so the shorter axis
    # spans exactly tile_sz CRS.Simple units at zoom 0.
    short_px  = min(full_w_px, full_h_px)
    leaflet_w = round(full_w_px * tile_sz / short_px, 4)
    leaflet_h = round(full_h_px * tile_sz / short_px, 4)

    tile_meta = {
        "max_zoom":       max_zoom,
        "tile_size":      tile_sz,
        "full_width_px":  full_w_px,
        "full_height_px": full_h_px,
        "leaflet_bounds": [[-leaflet_h, 0], [0, leaflet_w]],
    }

    # Ensure tile_meta parent directory exists (it lives inside tiles_dir for
    # themed runs when run_pipeline.py passes --tile-meta tiles/<theme>/tile_meta.json)
    Path(args.tile_meta).parent.mkdir(parents=True, exist_ok=True)
    with open(args.tile_meta, "w") as f:
        json.dump(tile_meta, f, indent=2)
    logger.info("Tile meta    : %s", args.tile_meta)

    total_tiles = sum(
        max(1, math.ceil(full_w_px * (2 ** z) / short_px)) *
        max(1, math.ceil(full_h_px * (2 ** z) / short_px))
        for z in range(max_zoom + 1)
    )
    logger.info("Drawing: %d x %d px  |  zoom 0-%d  |  %d tiles  |  %s/",
                full_w_px, full_h_px, max_zoom, total_tiles, tiles_dir)


if __name__ == "__main__":
    main()
