"""
Unit and integration tests for render_svg.py (Stage 1).

render_svg.py has module-level side effects (arg parsing + DXF reading),
so ALL tests invoke it as a subprocess rather than importing it.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).parent.parent / "pipeline"
RENDER_SVG   = PIPELINE_DIR / "render_svg.py"

# render_svg.py prints box-drawing Unicode chars (─) which fail on Windows
# cp1252 console without an explicit UTF-8 override.
_SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run_render(args: list, **kwargs):
    """Helper: run render_svg.py as a subprocess, return CompletedProcess."""
    kwargs.setdefault("env", _SUBPROCESS_ENV)
    return subprocess.run(
        [sys.executable, str(RENDER_SVG)] + args,
        capture_output=True,
        text=True,
        **kwargs,
    )


def _read_viewbox(svg_path: Path) -> tuple[float, float] | None:
    """Parse viewBox dimensions from SVG file; return (w, h) or None."""
    text = svg_path.read_text(encoding="utf-8")[:4096]
    m = re.search(r'viewBox="([^"]+)"', text)
    if m:
        parts = m.group(1).split()
        if len(parts) == 4:
            return float(parts[2]), float(parts[3])
    return None


def _make_dxf_with_entities(tmp_path: Path, entities: list[dict]) -> Path:
    """Build a DXF using ezdxf and return its path."""
    import ezdxf

    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()
    for ent in entities:
        if ent["type"] == "text":
            msp.add_text(
                ent["text"],
                dxfattribs={"insert": ent["insert"], "height": ent.get("height", 2.5)},
            )
        elif ent["type"] == "lwpolyline":
            msp.add_lwpolyline(ent["points"])
    path = tmp_path / "test.dxf"
    doc.saveas(str(path))
    return path


# ─────────────────────────────────────────────────────────────
# Basic rendering
# ─────────────────────────────────────────────────────────────

class TestRenderSvgBasic:
    def test_svg_file_created(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(minimal_dxf), str(svg_out)])
        assert result.returncode == 0, result.stderr
        assert svg_out.exists()

    def test_svg_is_non_empty(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        _run_render([str(minimal_dxf), str(svg_out)])
        assert svg_out.stat().st_size > 0

    def test_viewbox_reflects_entity_extents(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(minimal_dxf), str(svg_out)])
        assert result.returncode == 0, result.stderr

        vb = _read_viewbox(svg_out)
        assert vb is not None, "No viewBox found in SVG output"
        vb_w, vb_h = vb
        # Extents should be positive and well below any sentinel (~1e20)
        assert vb_w > 0
        assert vb_h > 0
        assert vb_w < 1e10
        assert vb_h < 1e10

    def test_svg_contains_geometry(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        _run_render([str(minimal_dxf), str(svg_out)])
        content = svg_out.read_text(encoding="utf-8")
        # SVG should contain at least some elements beyond the root tag
        assert "<path" in content or "<rect" in content or "<polyline" in content or "<line" in content

    def test_default_output_filename(self, tmp_path, minimal_dxf):
        # When no output path is given, SVG is written next to the DXF
        result = _run_render([str(minimal_dxf)], cwd=str(tmp_path))
        assert result.returncode == 0, result.stderr
        expected_svg = minimal_dxf.with_suffix(".svg")
        assert expected_svg.exists()


# ─────────────────────────────────────────────────────────────
# --text-to-path flag
# ─────────────────────────────────────────────────────────────

class TestTextToPath:
    def test_without_flag_has_text_or_path_elements(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(minimal_dxf), str(svg_out)])
        assert result.returncode == 0, result.stderr
        content = svg_out.read_text(encoding="utf-8")
        # At minimum the SVG should contain some content derived from the DXF
        assert len(content) > 200

    def test_with_flag_exits_zero(self, tmp_path, minimal_dxf):
        # --text-to-path should either succeed or gracefully fall back
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(minimal_dxf), str(svg_out), "--text-to-path"])
        assert result.returncode == 0, result.stderr

    def test_with_flag_svg_produced(self, tmp_path, minimal_dxf):
        svg_out = tmp_path / "out.svg"
        _run_render([str(minimal_dxf), str(svg_out), "--text-to-path"])
        assert svg_out.exists()
        assert svg_out.stat().st_size > 0


# ─────────────────────────────────────────────────────────────
# Degenerate inputs
# ─────────────────────────────────────────────────────────────

class TestDegenerateInputs:
    def test_empty_drawing_exits_nonzero(self, tmp_path):
        import ezdxf

        # DXF with no entities at all
        doc = ezdxf.new(dxfversion="R2010")
        dxf_path = tmp_path / "empty.dxf"
        doc.saveas(str(dxf_path))

        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(dxf_path), str(svg_out)])
        assert result.returncode != 0

    def test_single_entity_drawing_succeeds(self, tmp_path):
        dxf_path = _make_dxf_with_entities(
            tmp_path,
            [{"type": "text", "text": "FV101", "insert": (10.0, 20.0)}],
        )
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(dxf_path), str(svg_out)])
        assert result.returncode == 0, result.stderr
        assert svg_out.exists()

    def test_missing_dxf_exits_nonzero(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist.dxf"
        svg_out     = tmp_path / "out.svg"
        result      = _run_render([str(nonexistent), str(svg_out)])
        assert result.returncode != 0

    def test_geometry_only_no_text_succeeds(self, tmp_path):
        dxf_path = _make_dxf_with_entities(
            tmp_path,
            [{"type": "lwpolyline", "points": [(0, 0), (10, 0), (10, 10), (0, 10)]}],
        )
        svg_out = tmp_path / "out.svg"
        result  = _run_render([str(dxf_path), str(svg_out)])
        assert result.returncode == 0, result.stderr
        assert svg_out.exists()


# ─────────────────────────────────────────────────────────────
# --themes-config
# ─────────────────────────────────────────────────────────────

class TestThemesConfig:
    def _make_themes_file(self, tmp_path: Path, themes: dict) -> Path:
        import json
        p = tmp_path / "themes.json"
        p.write_text(json.dumps(themes), encoding="utf-8")
        return p

    def test_single_theme_produces_named_svg(self, tmp_path, minimal_dxf):
        themes = {"dark": {"background": "#1a1a2e", "layers": {}}}
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "out.svg"
        result = _run_render([str(minimal_dxf), str(svg_base),
                              "--themes-config", str(cfg)])
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "out_dark.svg").exists()
        assert not (tmp_path / "out.svg").exists()

    def test_two_themes_produce_two_svgs(self, tmp_path, minimal_dxf):
        themes = {
            "light": {"background": "#ffffff", "layers": {}},
            "dark":  {"background": "#1a1a2e", "layers": {}},
        }
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "drawing.svg"
        result = _run_render([str(minimal_dxf), str(svg_base),
                              "--themes-config", str(cfg)])
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "drawing_light.svg").exists()
        assert (tmp_path / "drawing_dark.svg").exists()

    def test_manifest_written_with_themes(self, tmp_path, minimal_dxf):
        import json
        themes = {
            "light": {"background": "#ffffff", "layers": {}},
            "dark":  {"background": "#1a1a2e", "layers": {}},
        }
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "drawing.svg"
        result = _run_render([str(minimal_dxf), str(svg_base),
                              "--themes-config", str(cfg)])
        assert result.returncode == 0, result.stderr

        manifest_path = tmp_path / "svg_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest) == 2
        themes_in_manifest = {e["theme"] for e in manifest}
        assert themes_in_manifest == {"light", "dark"}

    def test_manifest_written_without_themes(self, tmp_path, minimal_dxf):
        """Default run (no --themes-config) still writes svg_manifest.json."""
        import json
        svg_out = tmp_path / "out.svg"
        result = _run_render([str(minimal_dxf), str(svg_out)])
        assert result.returncode == 0, result.stderr

        manifest_path = tmp_path / "svg_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest) == 1
        assert manifest[0]["theme"] is None
        assert manifest[0]["svg"] == str(svg_out)

    def test_manifest_background_matches_config(self, tmp_path, minimal_dxf):
        import json
        themes = {"dark": {"background": "#1a1a2e", "layers": {}}}
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "drawing.svg"
        _run_render([str(minimal_dxf), str(svg_base), "--themes-config", str(cfg)])

        manifest = json.loads(
            (tmp_path / "svg_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest[0]["background"] == "#1a1a2e"

    def test_unknown_layer_does_not_crash(self, tmp_path, minimal_dxf):
        """A layer name in themes.json that doesn't exist in the DXF is a warning, not a crash."""
        themes = {"dark": {"background": "#1a1a2e", "layers": {"NONEXISTENT": "#ffffff"}}}
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "out.svg"
        result = _run_render([str(minimal_dxf), str(svg_base),
                              "--themes-config", str(cfg)])
        assert result.returncode == 0, result.stderr
        assert "WARNING" in result.stderr

    def test_layer_colour_override_applied(self, tmp_path, minimal_dxf):
        """Rendering with a layer colour override should still produce valid SVG."""
        import ezdxf
        # minimal_dxf has layers "TAGS", "EQUIP", "OUTLINE" (from conftest.py)
        themes = {"light": {"background": "#ffffff", "layers": {"TAGS": "#0000ff"}}}
        cfg = self._make_themes_file(tmp_path, themes)
        svg_base = tmp_path / "drawing.svg"
        result = _run_render([str(minimal_dxf), str(svg_base),
                              "--themes-config", str(cfg)])
        assert result.returncode == 0, result.stderr
        svg_out = tmp_path / "drawing_light.svg"
        assert svg_out.exists()
        assert svg_out.stat().st_size > 0
