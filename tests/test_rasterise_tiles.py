"""
Unit and integration tests for rasterise_tiles.py (Stage 2).

Unit tests have no Inkscape dependency and run in milliseconds.
Integration tests are marked @pytest.mark.integration and require Inkscape.
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Make pipeline importable
sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))
import rasterise_tiles  # noqa: E402


PIPELINE_DIR   = Path(__file__).parent.parent / "pipeline"
REQUIRED_META_KEYS = {
    "max_zoom", "tile_size", "full_width_px", "full_height_px",
    "svg_viewbox_width", "svg_viewbox_height", "px_per_svg_unit", "leaflet_bounds",
}


# ─────────────────────────────────────────────────────────────
# _read_svg_viewbox
# ─────────────────────────────────────────────────────────────

class TestReadSvgViewbox:
    def test_viewbox_four_floats(self, simple_svg):
        w, h = rasterise_tiles._read_svg_viewbox(str(simple_svg))
        assert w == pytest.approx(200.0)
        assert h == pytest.approx(100.0)

    def test_fallback_to_width_height_attrs(self, tmp_path):
        # SVG with no viewBox but explicit width/height attributes
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="300" height="150">'
            '<rect/></svg>'
        )
        p = tmp_path / "no_vb.svg"
        p.write_text(svg, encoding="utf-8")
        w, h = rasterise_tiles._read_svg_viewbox(str(p))
        assert w == pytest.approx(300.0)
        assert h == pytest.approx(150.0)

    def test_no_viewbox_raises_value_error(self, tmp_path):
        # SVG with neither viewBox nor width/height
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        p = tmp_path / "bad.svg"
        p.write_text(svg, encoding="utf-8")
        with pytest.raises(ValueError):
            rasterise_tiles._read_svg_viewbox(str(p))

    def test_reads_only_first_4096_bytes(self, tmp_path):
        # viewBox buried after the 4 KB read limit should NOT be found
        padding = " " * 5000
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg">{padding}'
            'viewBox="0 0 999 999">'
            '</svg>'
        )
        p = tmp_path / "buried.svg"
        p.write_text(svg, encoding="utf-8")
        with pytest.raises(ValueError):
            rasterise_tiles._read_svg_viewbox(str(p))


# ─────────────────────────────────────────────────────────────
# _auto_max_zoom
# ─────────────────────────────────────────────────────────────

class TestAutoMaxZoom:
    """
    _auto_max_zoom ignores the vb_w parameter; it always computes:
      target_tiles = ceil(4096/256) = 16
      z = max(ceil(log2(16)), 3) = max(4, 3) = 4
      capped at 8 → always returns 4.
    """

    def test_returns_4_for_any_viewbox_width(self):
        for vb_w in [0.01, 1.0, 100.0, 10_000.0]:
            assert rasterise_tiles._auto_max_zoom(vb_w) == 4

    def test_result_at_least_3(self):
        assert rasterise_tiles._auto_max_zoom(1.0) >= 3

    def test_result_at_most_8(self):
        assert rasterise_tiles._auto_max_zoom(999_999.0) <= 8

    def test_result_satisfies_min_4096px_wide(self):
        z = rasterise_tiles._auto_max_zoom(100.0)
        full_width_at_zoom = (2 ** z) * rasterise_tiles.TILE_SIZE
        assert full_width_at_zoom >= rasterise_tiles.MIN_FULL_WIDTH_PX


# ─────────────────────────────────────────────────────────────
# _generate_tiles
# ─────────────────────────────────────────────────────────────

# Expected tile counts for the 512×256 minimal_png fixture.
# short = min(512,256) = 256
# z=0: cols=ceil(512*1/256)=2, rows=ceil(256*1/256)=1 → 2
# z=1: cols=4, rows=2 → 8
# z=2: cols=8, rows=4 → 32

class TestGenerateTiles:
    def test_tile_count_zoom_0(self, tmp_path, minimal_png):
        from PIL import Image
        out_dir = tmp_path / "tiles"
        img = Image.open(str(minimal_png)).convert("RGBA")
        rasterise_tiles._generate_tiles(img, out_dir, max_zoom=0,
                                        full_w=512, full_h=256, tile_sz=256)
        tiles = list(out_dir.rglob("*.png"))
        assert len(tiles) == 2

    def test_tile_count_max_zoom_2(self, tmp_path, minimal_png):
        from PIL import Image
        out_dir = tmp_path / "tiles"
        img = Image.open(str(minimal_png)).convert("RGBA")
        rasterise_tiles._generate_tiles(img, out_dir, max_zoom=2,
                                        full_w=512, full_h=256, tile_sz=256)
        tiles = list(out_dir.rglob("*.png"))
        assert len(tiles) == 42   # 2 + 8 + 32

    def test_directory_structure(self, tmp_path, minimal_png):
        from PIL import Image
        out_dir = tmp_path / "tiles"
        img = Image.open(str(minimal_png)).convert("RGBA")
        rasterise_tiles._generate_tiles(img, out_dir, max_zoom=1,
                                        full_w=512, full_h=256, tile_sz=256)
        # z/x/y.png convention
        assert (out_dir / "0" / "0" / "0.png").exists()
        assert (out_dir / "1" / "0" / "0.png").exists()

    def test_all_tiles_are_256x256(self, tmp_path, minimal_png):
        from PIL import Image
        out_dir = tmp_path / "tiles"
        img = Image.open(str(minimal_png)).convert("RGBA")
        rasterise_tiles._generate_tiles(img, out_dir, max_zoom=1,
                                        full_w=512, full_h=256, tile_sz=256)
        for png in out_dir.rglob("*.png"):
            opened = Image.open(str(png))
            assert opened.size == (256, 256), f"Bad size: {png} → {opened.size}"


# ─────────────────────────────────────────────────────────────
# tile_meta fields and coordinate bounds
# ─────────────────────────────────────────────────────────────

class TestTileMetaFields:
    def test_all_required_fields_present(self, minimal_tile_meta):
        for key in REQUIRED_META_KEYS:
            assert key in minimal_tile_meta, f"Missing key: {key}"

    def test_max_zoom_is_int(self, minimal_tile_meta):
        assert isinstance(minimal_tile_meta["max_zoom"], int)

    def test_leaflet_bounds_is_nested_list(self, minimal_tile_meta):
        bounds = minimal_tile_meta["leaflet_bounds"]
        assert isinstance(bounds, list)
        assert len(bounds) == 2
        assert len(bounds[0]) == 2
        assert len(bounds[1]) == 2

    def test_coordinate_bounds_formula(self):
        # Given: 1024×512 image, tile_sz=256
        # short_px = 512; leaflet_w = 1024*256/512 = 512; leaflet_h = 256
        full_w, full_h, tile_sz = 1024, 512, 256
        short_px  = min(full_w, full_h)
        leaflet_w = round(full_w * tile_sz / short_px, 4)
        leaflet_h = round(full_h * tile_sz / short_px, 4)
        bounds = [[-leaflet_h, 0], [0, leaflet_w]]

        assert leaflet_w == pytest.approx(512.0)
        assert leaflet_h == pytest.approx(256.0)
        assert bounds == [[-256.0, 0], [0, 512.0]]


# ─────────────────────────────────────────────────────────────
# Integration tests (require Inkscape)
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestRasteriseTilesIntegration:
    def test_full_stage_produces_tile_pyramid(self, tmp_path, simple_svg):
        if not shutil.which("inkscape"):
            pytest.skip("Inkscape not in PATH")

        result = subprocess.run(
            [
                sys.executable,
                str(PIPELINE_DIR / "rasterise_tiles.py"),
                "--svg", str(simple_svg),
                "--tiles-dir", str(tmp_path / "tiles"),
                "--tile-meta", str(tmp_path / "tile_meta.json"),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        meta_path = tmp_path / "tile_meta.json"
        assert meta_path.exists()

        tiles = list((tmp_path / "tiles").rglob("*.png"))
        assert len(tiles) > 0

    def test_tile_meta_schema_valid(self, tmp_path, simple_svg):
        if not shutil.which("inkscape"):
            pytest.skip("Inkscape not in PATH")

        meta_path = tmp_path / "tile_meta.json"
        subprocess.run(
            [
                sys.executable,
                str(PIPELINE_DIR / "rasterise_tiles.py"),
                "--svg", str(simple_svg),
                "--tiles-dir", str(tmp_path / "tiles"),
                "--tile-meta", str(meta_path),
            ],
            capture_output=True, text=True,
        )
        assert meta_path.exists()
        with meta_path.open() as f:
            meta = json.load(f)
        for key in REQUIRED_META_KEYS:
            assert key in meta, f"tile_meta.json missing key: {key}"
        assert isinstance(meta["max_zoom"], int)
        assert isinstance(meta["leaflet_bounds"], list)
