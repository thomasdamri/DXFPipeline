"""Unit tests for run_pipeline.py"""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add pipeline dir to path so we can import run_pipeline
sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))

import run_pipeline


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


class TestCommandBuilders:
    def _args(self, tmp_path, **kwargs):
        """Helper: build a minimal args namespace."""
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
            from_stage=None,
            keep_work=False,
            verbose=False,
        )
        defaults.update(kwargs)
        import argparse
        return argparse.Namespace(**defaults)

    def test_svg_cmd_basic(self, tmp_path):
        args = self._args(tmp_path)
        work_dir = tmp_path / ".work"
        cmd = run_pipeline.build_svg_cmd(args, work_dir)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("render_svg.py")
        assert str(args.dxf) in cmd
        assert str(work_dir / "drawing.svg") in cmd
        # transform.json is no longer written by render_svg.py
        assert "--transform-out" not in cmd

    def test_tiles_cmd_basic(self, tmp_path):
        args = self._args(tmp_path)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_tiles_cmd(args, work_dir, out_dir)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("rasterise_tiles.py")
        assert str(work_dir / "drawing.svg") in cmd
        assert str(out_dir / "tiles") in cmd
        assert str(out_dir / "tile_meta.json") in cmd
        # transform.json is no longer required by rasterise_tiles.py
        assert "--transform" not in cmd

    def test_tiles_cmd_max_zoom_and_inkscape(self, tmp_path):
        args = self._args(tmp_path, max_zoom=5, inkscape="/usr/bin/inkscape")
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_tiles_cmd(args, work_dir, out_dir)
        assert "--max-zoom" in cmd
        assert "5" in cmd
        assert "--inkscape" in cmd
        assert "/usr/bin/inkscape" in cmd

    def test_manifest_cmd_basic(self, tmp_path):
        args = self._args(tmp_path)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_manifest_cmd(args, work_dir, out_dir)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("extract_manifest.py")
        assert "--dxf" in cmd
        assert str(args.dxf) in cmd
        assert "--labels" in cmd
        assert str(args.labels) in cmd
        assert "--tile-meta" in cmd
        assert str(out_dir / "tile_meta.json") in cmd
        assert "--hitboxes" in cmd
        assert str(out_dir / "hitboxes.json") in cmd
        # Removed: --svg, --transform, label-manifest.json, --debug-svg
        assert "--svg" not in cmd
        assert "--transform" not in cmd
        assert "label-manifest.json" not in cmd
        assert "--debug-svg" not in cmd

    def test_manifest_cmd_verbose(self, tmp_path):
        args = self._args(tmp_path, verbose=True)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_manifest_cmd(args, work_dir, out_dir)
        assert "--verbose" in cmd


class TestPrerequisiteCheck:
    def test_from_stage_tiles_fails_if_no_drawing_svg(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        # drawing.svg does not exist
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")
        assert exc.value.code == 2

    def test_from_stage_tiles_passes_when_svg_exists(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        (work_dir / "drawing.svg").touch()
        # Should not raise — transform.json is no longer required
        run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")

    def test_from_stage_manifest_fails_if_no_tile_meta(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        # tile_meta.json is missing from out_dir
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("manifest", work_dir, out_dir)
        assert exc.value.code == 2

    def test_from_stage_manifest_passes_when_tile_meta_exists(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "tile_meta.json").touch()
        # Should not raise — transform.json is no longer required
        run_pipeline.check_prerequisites("manifest", work_dir, out_dir)

    def test_from_stage_svg_always_passes(self, tmp_path):
        # "svg" is the first stage; no cached files are needed regardless of filesystem state
        run_pipeline.check_prerequisites("svg", tmp_path / ".work", tmp_path / "output")


import subprocess
from unittest.mock import patch, call

class TestOrchestrationLoop:
    def _args(self, tmp_path):
        dxf = tmp_path / "test.dxf"
        dxf.touch()
        labels = tmp_path / "labels.txt"
        labels.touch()
        import argparse
        return argparse.Namespace(
            dxf=dxf,
            labels=labels,
            out_dir=tmp_path / "output",
            max_zoom=None,
            inkscape=None,
            from_stage=None,
            keep_work=False,
            verbose=False,
        )

    def test_all_three_stages_run_on_success(self, tmp_path, capsys):
        args = self._args(tmp_path)
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

    def test_stage_failure_stops_pipeline(self, tmp_path, capsys):
        args = self._args(tmp_path)
        fail_result = MagicMock()
        fail_result.returncode = 1
        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("subprocess.run", side_effect=[ok_result, fail_result, ok_result]) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 1
        assert mock_run.call_count == 2

    def test_timing_printed_per_stage(self, tmp_path, capsys):
        args = self._args(tmp_path)
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
        work_dir.mkdir(parents=True)
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
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert not (args.out_dir / ".work").exists()

    def test_work_dir_retained_with_keep_work(self, tmp_path):
        args = self._args(tmp_path)
        args.keep_work = True
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert (args.out_dir / ".work").exists()

    def test_work_dir_retained_on_failure(self, tmp_path):
        args = self._args(tmp_path)
        fail_result = MagicMock()
        fail_result.returncode = 1

        with patch("subprocess.run", return_value=fail_result):
            exit_code = run_pipeline.run(args)

        assert exit_code == 1
        assert (args.out_dir / ".work").exists()


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
        # label-manifest.json is now opt-in and should NOT be written by default
        assert not (out / "label-manifest.json").exists(), "label-manifest.json should not be written by default"

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
