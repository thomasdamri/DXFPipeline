"""Unit tests for run_pipeline.py"""
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add pipeline dir to path so we can import run_pipeline
sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))

import run_pipeline


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_args(tmp_path, **kwargs):
    """Build a minimal args Namespace. Override any field via kwargs."""
    import argparse
    dxf = tmp_path / "test.dxf"
    dxf.touch()
    labels = tmp_path / "labels.txt"
    labels.touch()
    defaults = dict(
        dxf=dxf,
        labels=labels,
        out_dir=tmp_path / "output",
        max_zoom=None,
        inkscape=None,
        themes_config=None,
        from_stage=None,
        keep_work=False,
        verbose=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


_DEFAULT_MANIFEST = [{"theme": None, "svg": "drawing.svg", "background": "#ffffff"}]


def _seed_manifest(work_dir: Path, entries=None) -> None:
    """Write svg_manifest.json into work_dir so run() can read it after Stage 1."""
    work_dir.mkdir(parents=True, exist_ok=True)
    content = entries if entries is not None else _DEFAULT_MANIFEST
    (work_dir / "svg_manifest.json").write_text(
        json.dumps(content), encoding="utf-8"
    )


# ── arg parsing ───────────────────────────────────────────────────────────────

class TestArgParsing:
    def test_required_args_accepted(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
        ])
        assert args.dxf == dxf
        assert args.labels == labels

    def test_dxf_must_exist(self, tmp_path):
        labels = tmp_path / "labels.txt"
        labels.touch()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.parse_args([
                "--dxf", str(tmp_path / "nonexistent.dxf"),
                "--labels", str(labels),
            ])
        assert exc.value.code == 2

    def test_labels_must_exist(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.parse_args([
                "--dxf", str(dxf),
                "--labels", str(tmp_path / "nonexistent.txt"),
            ])
        assert exc.value.code == 2

    def test_default_out_dir(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
        ])
        assert args.out_dir == Path("output")

    def test_from_stage_valid_values(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        for stage in ("svg", "tiles", "manifest"):
            args = run_pipeline.parse_args([
                "--dxf", str(dxf),
                "--labels", str(labels),
                "--from-stage", stage,
            ])
            assert args.from_stage == stage

    def test_from_stage_invalid_value(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.parse_args([
                "--dxf", str(dxf),
                "--labels", str(labels),
                "--from-stage", "bad",
            ])
        assert exc.value.code == 2

    def test_themes_config_accepted(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        cfg = tmp_path / "themes.json"
        cfg.write_text('{"light": {"background": "#fff", "layers": {}}}')
        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--themes-config", str(cfg),
        ])
        assert args.themes_config == cfg

    def test_themes_config_must_exist(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.parse_args([
                "--dxf", str(dxf),
                "--labels", str(labels),
                "--themes-config", str(tmp_path / "missing.json"),
            ])
        assert exc.value.code == 2


# ── command builders ──────────────────────────────────────────────────────────

class TestCommandBuilders:
    def test_svg_cmd_basic(self, tmp_path):
        args = _make_args(tmp_path)
        work_dir = tmp_path / ".work"
        cmd = run_pipeline.build_svg_cmd(args, work_dir)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("render_svg.py")
        assert str(args.dxf) in cmd
        assert str(work_dir / "drawing.svg") in cmd
        assert "--themes-config" not in cmd

    def test_svg_cmd_includes_themes_config(self, tmp_path):
        cfg = tmp_path / "themes.json"
        cfg.write_text("{}")
        args = _make_args(tmp_path, themes_config=cfg)
        work_dir = tmp_path / ".work"
        cmd = run_pipeline.build_svg_cmd(args, work_dir)
        assert "--themes-config" in cmd
        assert str(cfg) in cmd

    def test_tiles_cmd_default_entry(self, tmp_path):
        """Default (no theme) entry produces flat tiles dir and root tile_meta."""
        args = _make_args(tmp_path)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        entry = {"theme": None, "svg": "drawing.svg", "background": "#ffffff"}
        cmd = run_pipeline.build_tiles_cmd_for_entry(args, work_dir, out_dir, entry)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("rasterise_tiles.py")
        assert str(out_dir / "tiles") in cmd
        assert str(out_dir / "tile_meta.json") in cmd
        assert "--theme" not in cmd
        assert "--bg-color" in cmd
        assert "#ffffff" in cmd

    def test_tiles_cmd_themed_entry(self, tmp_path):
        """Themed entry includes --theme and puts tile_meta under tiles/<theme>/."""
        args = _make_args(tmp_path)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        entry = {"theme": "dark", "svg": "drawing_dark.svg", "background": "#1a1a2e"}
        cmd = run_pipeline.build_tiles_cmd_for_entry(args, work_dir, out_dir, entry)
        assert "--theme" in cmd
        assert "dark" in cmd
        assert str(out_dir / "tiles" / "dark" / "tile_meta.json") in cmd
        assert "--bg-color" in cmd
        assert "#1a1a2e" in cmd

    def test_tiles_cmd_max_zoom_and_inkscape(self, tmp_path):
        args = _make_args(tmp_path, max_zoom=5, inkscape="/usr/bin/inkscape")
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        entry = {"theme": None, "svg": "drawing.svg", "background": "#ffffff"}
        cmd = run_pipeline.build_tiles_cmd_for_entry(args, work_dir, out_dir, entry)
        assert "--max-zoom" in cmd
        assert "5" in cmd
        assert "--inkscape" in cmd
        assert "/usr/bin/inkscape" in cmd

    def test_manifest_cmd_basic(self, tmp_path):
        args = _make_args(tmp_path)
        out_dir = tmp_path / "output"
        tile_meta = out_dir / "tile_meta.json"
        cmd = run_pipeline.build_manifest_cmd(args, out_dir, tile_meta)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("extract_manifest.py")
        assert "--dxf" in cmd
        assert str(args.dxf) in cmd
        assert "--labels" in cmd
        assert str(args.labels) in cmd
        assert "--tile-meta" in cmd
        assert str(tile_meta) in cmd
        assert "--hitboxes" in cmd
        assert str(out_dir / "hitboxes.json") in cmd
        assert "--transform" not in cmd

    def test_manifest_cmd_verbose(self, tmp_path):
        args = _make_args(tmp_path, verbose=True)
        out_dir = tmp_path / "output"
        tile_meta = out_dir / "tile_meta.json"
        cmd = run_pipeline.build_manifest_cmd(args, out_dir, tile_meta)
        assert "--verbose" in cmd

    def test_manifest_cmd_not_verbose_by_default(self, tmp_path):
        args = _make_args(tmp_path, verbose=False)
        out_dir = tmp_path / "output"
        tile_meta = out_dir / "tile_meta.json"
        cmd = run_pipeline.build_manifest_cmd(args, out_dir, tile_meta)
        assert "--verbose" not in cmd


# ── tile_meta path helper ─────────────────────────────────────────────────────

class TestTileMetaPath:
    def test_default_theme_is_top_level(self, tmp_path):
        path = run_pipeline._tile_meta_path(tmp_path, None)
        assert path == tmp_path / "tile_meta.json"

    def test_named_theme_is_nested(self, tmp_path):
        path = run_pipeline._tile_meta_path(tmp_path, "dark")
        assert path == tmp_path / "tiles" / "dark" / "tile_meta.json"

    def test_light_theme_path(self, tmp_path):
        path = run_pipeline._tile_meta_path(tmp_path, "light")
        assert path == tmp_path / "tiles" / "light" / "tile_meta.json"


# ── prerequisite check ────────────────────────────────────────────────────────

class TestPrerequisiteCheck:
    def test_from_stage_tiles_fails_if_no_manifest(self, tmp_path):
        """tiles stage requires svg_manifest.json in work_dir."""
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")
        assert exc.value.code == 2

    def test_from_stage_tiles_passes_when_manifest_exists(self, tmp_path):
        work_dir = tmp_path / ".work"
        _seed_manifest(work_dir)
        # drawing.svg is referenced by the manifest and must exist
        (work_dir / "drawing.svg").write_text("<svg/>")
        # Should not raise
        run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")

    def test_from_stage_tiles_fails_if_manifest_svg_missing(self, tmp_path):
        work_dir = tmp_path / ".work"
        _seed_manifest(work_dir)
        # drawing.svg NOT created → should fail
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")
        assert exc.value.code == 2

    def test_from_stage_manifest_fails_if_no_tile_meta(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("manifest", work_dir, out_dir)
        assert exc.value.code == 2

    def test_from_stage_manifest_passes_with_root_tile_meta(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "tile_meta.json").touch()
        run_pipeline.check_prerequisites("manifest", work_dir, out_dir)

    def test_from_stage_manifest_passes_with_themed_tile_meta(self, tmp_path):
        """tile_meta nested under tiles/<theme>/ is also accepted."""
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        themed = out_dir / "tiles" / "dark"
        themed.mkdir(parents=True)
        (themed / "tile_meta.json").touch()
        run_pipeline.check_prerequisites("manifest", work_dir, out_dir)

    def test_from_stage_svg_always_passes(self, tmp_path):
        run_pipeline.check_prerequisites("svg", tmp_path / ".work", tmp_path / "output")


# ── orchestration loop ────────────────────────────────────────────────────────

class TestOrchestrationLoop:
    def _args(self, tmp_path, **kwargs):
        return _make_args(tmp_path, **kwargs)

    def _seed(self, args, entries=None):
        """Pre-seed manifest so Stage 2 can read it (subprocess.run is mocked)."""
        work_dir = args.out_dir / ".work"
        _seed_manifest(work_dir, entries)

    def test_all_three_stages_run_on_success(self, tmp_path, capsys):
        args = self._args(tmp_path)
        self._seed(args)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert mock_run.call_count == 3
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert calls[0][1].endswith("render_svg.py")
        assert calls[1][1].endswith("rasterise_tiles.py")
        assert calls[2][1].endswith("extract_manifest.py")

    def test_two_themes_produces_four_subprocess_calls(self, tmp_path, capsys):
        """Two themes → Stage 2 runs rasterise_tiles twice → 4 calls total."""
        args = self._args(tmp_path)
        entries = [
            {"theme": "light", "svg": "drawing_light.svg", "background": "#ffffff"},
            {"theme": "dark",  "svg": "drawing_dark.svg",  "background": "#1a1a2e"},
        ]
        self._seed(args, entries)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        # Stage 1 (svg) + Stage 2 light + Stage 2 dark + Stage 3 (manifest) = 4
        assert mock_run.call_count == 4
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert calls[0][1].endswith("render_svg.py")
        assert calls[1][1].endswith("rasterise_tiles.py")
        assert calls[2][1].endswith("rasterise_tiles.py")
        assert calls[3][1].endswith("extract_manifest.py")

    def test_themed_tile_cmd_includes_theme_flag(self, tmp_path):
        """Verify the rasterise_tiles call includes --theme and --bg-color for themed entries."""
        args = self._args(tmp_path)
        entries = [{"theme": "dark", "svg": "drawing_dark.svg", "background": "#1a1a2e"}]
        self._seed(args, entries)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            run_pipeline.run(args)

        tiles_call = mock_run.call_args_list[1].args[0]  # second call = Stage 2
        assert "--theme" in tiles_call
        assert "dark" in tiles_call
        assert "--bg-color" in tiles_call
        assert "#1a1a2e" in tiles_call

    def test_stage_failure_stops_pipeline(self, tmp_path, capsys):
        args = self._args(tmp_path)
        self._seed(args)
        fail_result = MagicMock()
        fail_result.returncode = 1
        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("subprocess.run", side_effect=[ok_result, fail_result, ok_result]) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 1
        assert mock_run.call_count == 2

    def test_themed_stage2_failure_stops_pipeline(self, tmp_path):
        """If the first theme's rasterise_tiles fails, the second is never started."""
        args = self._args(tmp_path)
        entries = [
            {"theme": "light", "svg": "drawing_light.svg", "background": "#ffffff"},
            {"theme": "dark",  "svg": "drawing_dark.svg",  "background": "#1a1a2e"},
        ]
        self._seed(args, entries)
        ok   = MagicMock(returncode=0)
        fail = MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=[ok, fail, ok]) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 1
        # Stage 1 + first Stage 2 (fail) = 2; third Stage 2 and Stage 3 never run
        assert mock_run.call_count == 2

    def test_timing_printed_per_stage(self, tmp_path, capsys):
        args = self._args(tmp_path)
        self._seed(args)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            run_pipeline.run(args)

        out = capsys.readouterr().out
        assert "render_svg" in out
        assert "rasterise_tiles" in out
        assert "extract_manifest" in out

    def test_from_stage_skips_earlier_stages(self, tmp_path):
        args = self._args(tmp_path)
        args.from_stage = "manifest"
        args.keep_work = True
        work_dir = args.out_dir / ".work"
        _seed_manifest(work_dir)
        out_dir = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "tile_meta.json").touch()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert mock_run.call_count == 1
        cmd = mock_run.call_args.args[0]
        assert cmd[1].endswith("extract_manifest.py")

    def test_work_dir_deleted_on_success(self, tmp_path):
        args = self._args(tmp_path)
        self._seed(args)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert not (args.out_dir / ".work").exists()

    def test_work_dir_retained_with_keep_work(self, tmp_path):
        args = self._args(tmp_path)
        self._seed(args)
        args.keep_work = True
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert (args.out_dir / ".work").exists()

    def test_work_dir_retained_on_failure(self, tmp_path):
        args = self._args(tmp_path)
        self._seed(args)
        fail_result = MagicMock()
        fail_result.returncode = 1

        with patch("subprocess.run", return_value=fail_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 1
        assert (args.out_dir / ".work").exists()


# ── integration tests ─────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIntegration:
    """End-to-end tests. Require Inkscape. Skip with: pytest -m 'not integration'"""

    def test_full_pipeline_produces_outputs(self, tmp_path):
        import shutil as _shutil
        tests_dir = Path(__file__).parent
        dxf = tests_dir / "test_diagram.dxf"
        labels = tests_dir / "test_labels.txt"

        if not dxf.exists():
            pytest.skip("test_diagram.dxf not found — run generate_test_dxf.py first")
        if not _shutil.which("inkscape"):
            pytest.skip("Inkscape not found in PATH")

        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(tmp_path / "output"),
            "--keep-work",
        ])
        exit_code = run_pipeline.run(args)

        assert exit_code == 0
        out = tmp_path / "output"
        assert (out / "tile_meta.json").exists(), "tile_meta.json missing"
        assert (out / "hitboxes.json").exists(), "hitboxes.json missing"
        assert any((out / "tiles").rglob("*.png")), "No tile PNGs generated"
        assert not (out / "label-manifest.json").exists()

    def test_full_pipeline_with_two_themes(self, tmp_path):
        import shutil as _shutil
        tests_dir = Path(__file__).parent
        dxf = tests_dir / "test_diagram.dxf"
        labels = tests_dir / "test_labels.txt"

        if not dxf.exists():
            pytest.skip("test_diagram.dxf not found — run generate_test_dxf.py first")
        if not _shutil.which("inkscape"):
            pytest.skip("Inkscape not found in PATH")

        themes_cfg = tmp_path / "themes.json"
        themes_cfg.write_text(json.dumps({
            "light": {"background": "#ffffff", "layers": {}},
            "dark":  {"background": "#1a1a2e", "layers": {}},
        }))

        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(tmp_path / "output"),
            "--themes-config", str(themes_cfg),
        ])
        exit_code = run_pipeline.run(args)

        assert exit_code == 0
        out = tmp_path / "output"
        assert any((out / "tiles" / "light").rglob("*.png")), "No light tiles"
        assert any((out / "tiles" / "dark").rglob("*.png")),  "No dark tiles"
        assert (out / "tiles" / "light" / "tile_meta.json").exists()
        assert (out / "tiles" / "dark"  / "tile_meta.json").exists()
        assert (out / "hitboxes.json").exists()

    def test_from_stage_manifest_reuses_tiles(self, tmp_path):
        import shutil as _shutil
        tests_dir = Path(__file__).parent
        dxf = tests_dir / "test_diagram.dxf"
        labels = tests_dir / "test_labels.txt"

        if not dxf.exists():
            pytest.skip("test_diagram.dxf not found — run generate_test_dxf.py first")
        if not _shutil.which("inkscape"):
            pytest.skip("Inkscape not found in PATH")

        out_dir = tmp_path / "output"

        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(out_dir),
            "--keep-work",
        ])
        assert run_pipeline.run(args) == 0

        tile_meta_mtime = (out_dir / "tile_meta.json").stat().st_mtime

        args2 = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(out_dir),
            "--from-stage", "manifest",
        ])
        assert run_pipeline.run(args2) == 0

        assert (out_dir / "tile_meta.json").stat().st_mtime == tile_meta_mtime
