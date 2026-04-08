"""
rasterise_tiles.py
──────────────────
Converts a DXF-derived SVG → high-res PNG → XYZ tile pyramid for Leaflet.

Reads  : drawing.svg
Writes : tiles/{z}/{x}/{y}.png
         tile_meta.json      ← loaded by DXFViewer.tsx

Rasteriser: Inkscape CLI
    Install : https://inkscape.org/release/
    Windows : add to PATH, or pass --inkscape "C:\\Program Files\\Inkscape\\bin\\inkscape.exe"
    Linux   : sudo apt install inkscape  /  sudo dnf install inkscape
    macOS   : brew install inkscape

Requirements (tiling only):
    pip install pillow

Usage:
    python rasterise_tiles.py --svg drawing.svg
    python rasterise_tiles.py --svg drawing.svg --max-zoom 6
    python rasterise_tiles.py --svg drawing.svg \\
        --inkscape "C:\\Program Files\\Inkscape\\bin\\inkscape.exe"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TILE_SIZE         = 256
MIN_FULL_WIDTH_PX = 4096


# ── Inkscape ──────────────────────────────────────────────────────────────────

def _find_inkscape(hint: str | None) -> str:
    candidates = []
    if hint:
        candidates.append(hint)
    candidates.append(shutil.which("inkscape") or "")
    candidates += [
        r"C:\Program Files\Inkscape\bin\inkscape.exe",
        r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    sys.exit(
        "\nERROR: Inkscape not found.\n\n"
        "  Install from : https://inkscape.org/release/\n"
        "  Then either  : add Inkscape\\bin to your PATH\n"
        "      or pass  : --inkscape \"C:\\Program Files\\Inkscape\\bin\\inkscape.exe\"\n"
    )


def _check_inkscape_version(exe: str):
    try:
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        line = (result.stdout or result.stderr or "").splitlines()[0]
        print(f"  Inkscape     : {line.strip()}")
    except Exception as exc:
        print(f"  Inkscape     : {exe}  (version check failed: {exc})")


def _rasterise_inkscape(svg_path: str, out_png: str, width_px: int, exe: str):
    cmd = [
        exe,
        os.path.abspath(svg_path),
        f"--export-filename={os.path.abspath(out_png)}",
        f"--export-width={width_px}",
        "--export-background=white",
        "--export-background-opacity=1",
    ]
    print(f"  Running      : {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("\n-- Inkscape stdout --", file=sys.stderr)
        print(result.stdout,            file=sys.stderr)
        print("-- Inkscape stderr --",  file=sys.stderr)
        print(result.stderr,            file=sys.stderr)
        sys.exit(f"\nInkscape exited with code {result.returncode}")
    for line in (result.stderr or "").splitlines():
        if line.strip():
            print(f"  [inkscape] {line}")


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
                    full_w: int, full_h: int, tile_sz: int):
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

        canvas = Image.new("RGB", (cols * tile_sz, rows * tile_sz), (255, 255, 255))
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
        print(f"  z={z}  {cols:>3}x{rows:<3} tiles  [{written}/{total_tiles}  {pct:.0f}%]")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rasterise DXF SVG to PNG tile pyramid using Inkscape."
    )
    p.add_argument("--svg",       required=True, metavar="FILE",
                   help="SVG from render_svg.py")
    p.add_argument("--inkscape",  default=None,  metavar="EXE",
                   help="Path to inkscape executable (default: auto-detect)")
    p.add_argument("--max-zoom",  type=int, default=None, metavar="N",
                   help=f"Max tile zoom level (default: auto so drawing is "
                        f">={MIN_FULL_WIDTH_PX}px wide)")
    p.add_argument("--tiles-dir", default="tiles",          metavar="DIR")
    p.add_argument("--tile-meta", default="tile_meta.json", metavar="FILE")
    p.add_argument("--tile-size", type=int, default=TILE_SIZE, metavar="PX")
    return p.parse_args()


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        sys.exit("Pillow not installed.  Run:  pip install pillow")

    args     = parse_args()
    tile_sz  = args.tile_size
    inkscape = _find_inkscape(args.inkscape)
    _check_inkscape_version(inkscape)

    vb_w, vb_h = _read_svg_viewbox(args.svg)

    max_zoom    = args.max_zoom or _auto_max_zoom(vb_w)
    target_w_px = 2 ** max_zoom * tile_sz
    target_h_px = round(vb_h * (target_w_px / vb_w))

    print(f"SVG viewBox  : {vb_w:.2f} x {vb_h:.2f} mm")
    print(f"Max zoom     : {max_zoom}  "
          f"(grid {2**max_zoom}x{2**max_zoom}, "
          f"target {target_w_px} x {target_h_px} px)")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_png = tmp.name

    try:
        print(f"Rasterising  : {args.svg} -> {target_w_px}px wide ...")
        _rasterise_inkscape(args.svg, tmp_png, target_w_px, inkscape)

        from PIL import Image
        full_img = Image.open(tmp_png)
        full_img.load()
        full_img = full_img.convert("RGBA")
    finally:
        try:
            os.unlink(tmp_png)
        except OSError:
            pass

    actual_w, actual_h = full_img.size
    print(f"Rendered     : {actual_w} x {actual_h} px")

    full_w_px = actual_w
    full_h_px = actual_h

    tiles_dir = Path(args.tiles_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    print(f"Tiling into  : {tiles_dir}/")
    _generate_tiles(full_img, tiles_dir, max_zoom, full_w_px, full_h_px, tile_sz)

    px_per_svg_unit = full_w_px / vb_w

    # Coordinate space: normalise by shorter pixel dimension so the shorter axis
    # spans exactly tile_sz CRS.Simple units at zoom 0.
    short_px  = min(full_w_px, full_h_px)
    leaflet_w = round(full_w_px * tile_sz / short_px, 4)
    leaflet_h = round(full_h_px * tile_sz / short_px, 4)

    tile_meta = {
        "max_zoom":           max_zoom,
        "tile_size":          tile_sz,
        "full_width_px":      full_w_px,
        "full_height_px":     full_h_px,
        "svg_viewbox_width":  vb_w,
        "svg_viewbox_height": vb_h,
        "px_per_svg_unit":    round(px_per_svg_unit, 6),
        "leaflet_bounds":     [[-leaflet_h, 0], [0, leaflet_w]],
    }

    with open(args.tile_meta, "w") as f:
        json.dump(tile_meta, f, indent=2)
    print(f"\nTile meta    : {args.tile_meta}")

    total_tiles = sum(
        max(1, math.ceil(full_w_px * (2 ** z) / short_px)) *
        max(1, math.ceil(full_h_px * (2 ** z) / short_px))
        for z in range(max_zoom + 1)
    )
    print()
    print("-- Summary ---------------------------------------------------")
    print(f"  Drawing     : {full_w_px} x {full_h_px} px")
    print(f"  Zoom levels : 0 - {max_zoom}")
    print(f"  Total tiles : {total_tiles}")
    print(f"  Tile dir    : {tiles_dir}/")
    print(f"  tile_meta   : {args.tile_meta}")
    print()
    print("-- Next step -------------------------------------------------")
    print("  python extract_manifest.py \\")
    print("    --dxf drawing.dxf \\")
    print("    --labels labels.txt \\")
    print(f"    --tile-meta {args.tile_meta} \\")
    print("    --hitboxes hitboxes.json")
    print()
    print("  Serve tiles:  python -m http.server 8765")
    print("  Then load tile_meta.json + hitboxes.json in DXFViewer")


if __name__ == "__main__":
    main()
