# Pipeline Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pipeline/run_pipeline.py` — a single command that runs all three DXFPipeline stages in sequence with clean console output, timing, and proper exit codes.

**Architecture:** Thin subprocess wrapper that calls `render_svg.py`, `rasterise_tiles.py`, and `extract_manifest.py` sequentially via `subprocess.run`. Intermediate files (`drawing.svg`, `transform.json`) are kept in `<out-dir>/.work/`; they are deleted after a successful full run unless `--keep-work` is set. A `--from-stage` flag resumes from a given stage using cached intermediates.

**Tech Stack:** Python 3.9+ stdlib only (`subprocess`, `argparse`, `pathlib`, `time`, `shutil`, `sys`). No new dependencies.

---

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `pipeline/run_pipeline.py` | **Create** | CLI entry point — arg parsing, stage orchestration, timing output, work dir lifecycle |
| `requirements.txt` | **Create** | Pip dependency manifest for the pipeline |
| `tests/test_run_pipeline.py` | **Create** | Unit tests for arg validation, command building, work dir logic |

---

## Task 1: requirements.txt

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Create `requirements.txt` at the DXFPipeline root**

```text
# Python package dependencies for DXFPipeline
# Install with: pip install -r requirements.txt
#
# External binary: Inkscape (used by rasterise_tiles.py for SVG→PNG rasterisation)
#   Windows : https://inkscape.org/release/  (add to PATH or pass --inkscape)
#   Linux   : sudo apt install inkscape
#   macOS   : brew install inkscape

ezdxf>=1.3
Pillow>=10.0
lxml>=5.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: add requirements.txt for DXFPipeline"
```

---

## Task 2: Test file skeleton + arg parsing

**Files:**
- Create: `pipeline/run_pipeline.py`
- Create: `tests/test_run_pipeline.py`

- [ ] **Step 1: Write the failing test for arg parsing**

Create `tests/test_run_pipeline.py`:

```python
"""Unit tests for run_pipeline.py"""
import sys
import os
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
        with pytest.raises(SystemExit):
            run_pipeline.parse_args([
                "--dxf", str(dxf),
                "--labels", str(labels),
                "--from-stage", "bad",
            ])
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd c:/Users/thoma/Repos/DViewer/DXFPipeline
python -m pytest tests/test_run_pipeline.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'run_pipeline'`

- [ ] **Step 3: Create `pipeline/run_pipeline.py` with arg parsing only**

```python
#!/usr/bin/env python3
"""
run_pipeline.py
───────────────
Orchestrates all three DXFPipeline stages in sequence:
  Stage 1: render_svg.py      (DXF → SVG + transform.json)
  Stage 2: rasterise_tiles.py (SVG → tile pyramid + tile_meta.json)
  Stage 3: extract_manifest.py (DXF + labels → hitboxes.json + label-manifest.json)

Usage:
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt \\
        --out-dir output/ --max-zoom 4 --keep-work
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt \\
        --from-stage manifest  # requires prior run with --keep-work

Exit codes:
    0  all stages succeeded
    1  a stage failed
    2  argument error
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).parent


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Run the full DXF → tiles + hitboxes pipeline.",
    )
    p.add_argument("--dxf", required=True, type=Path, metavar="FILE",
                   help="Input DXF file")
    p.add_argument("--labels", required=True, type=Path, metavar="FILE",
                   help="Newline-separated target labels file")
    p.add_argument("--out-dir", default=Path("output"), type=Path, metavar="DIR",
                   help="Output directory (default: output/)")
    p.add_argument("--max-zoom", type=int, default=None, metavar="N",
                   help="Maximum tile zoom level (passed to rasterise_tiles.py)")
    p.add_argument("--inkscape", default=None, metavar="PATH",
                   help="Path to Inkscape executable (auto-detected if omitted)")
    p.add_argument("--from-stage", choices=["svg", "tiles", "manifest"],
                   default=None, dest="from_stage", metavar="STAGE",
                   help="Resume from this stage using cached intermediates "
                        "(requires prior run with --keep-work). "
                        "Choices: svg, tiles, manifest")
    p.add_argument("--keep-work", action="store_true",
                   help="Retain <out-dir>/.work/ intermediates after success")
    p.add_argument("--debug-svg", default=None, type=Path, metavar="FILE",
                   help="Write debug hitbox overlay SVG (passed to extract_manifest.py)")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose output (passed to extract_manifest.py)")

    args = p.parse_args(argv)

    # Validate file existence immediately so errors are caught before any work starts
    if not args.dxf.exists():
        p.error(f"DXF file not found: {args.dxf}")
    if not args.labels.exists():
        p.error(f"Labels file not found: {args.labels}")

    return args


if __name__ == "__main__":
    args = parse_args()
    print(args)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_run_pipeline.py::TestArgParsing -v
```

Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/run_pipeline.py tests/test_run_pipeline.py
git commit -m "feat: add run_pipeline.py skeleton with arg parsing"
```

---

## Task 3: Stage command builders

**Files:**
- Modify: `pipeline/run_pipeline.py`
- Modify: `tests/test_run_pipeline.py`

- [ ] **Step 1: Write failing tests for command builders**

Append to `tests/test_run_pipeline.py`:

```python
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
            debug_svg=None,
            verbose=False,
        )
        defaults.update(kwargs)
        import argparse
        return argparse.Namespace(**defaults)

    def test_svg_cmd_basic(self, tmp_path):
        args = self._args(tmp_path)
        work_dir = tmp_path / ".work"
        cmd = run_pipeline.build_svg_cmd(args, work_dir)
        assert cmd[1].endswith("render_svg.py")
        assert str(args.dxf) in cmd
        assert str(work_dir / "drawing.svg") in cmd
        assert str(work_dir / "transform.json") in cmd

    def test_tiles_cmd_basic(self, tmp_path):
        args = self._args(tmp_path)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_tiles_cmd(args, work_dir, out_dir)
        assert cmd[1].endswith("rasterise_tiles.py")
        assert str(work_dir / "drawing.svg") in cmd
        assert str(work_dir / "transform.json") in cmd
        assert str(out_dir / "tiles") in cmd
        assert str(out_dir / "tile_meta.json") in cmd

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
        assert cmd[1].endswith("extract_manifest.py")
        assert "--dxf" in cmd
        assert str(args.dxf) in cmd
        assert "--labels" in cmd
        assert str(args.labels) in cmd
        assert str(out_dir / "label-manifest.json") in cmd

    def test_manifest_cmd_debug_svg(self, tmp_path):
        debug = tmp_path / "debug.svg"
        args = self._args(tmp_path, debug_svg=debug)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_manifest_cmd(args, work_dir, out_dir)
        assert "--debug-svg" in cmd
        assert str(debug) in cmd

    def test_manifest_cmd_verbose(self, tmp_path):
        args = self._args(tmp_path, verbose=True)
        work_dir = tmp_path / ".work"
        out_dir = tmp_path / "output"
        cmd = run_pipeline.build_manifest_cmd(args, work_dir, out_dir)
        assert "--verbose" in cmd
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_run_pipeline.py::TestCommandBuilders -v 2>&1 | head -20
```

Expected: `AttributeError: module 'run_pipeline' has no attribute 'build_svg_cmd'`

- [ ] **Step 3: Add command builders to `run_pipeline.py`**

Add after the `parse_args` function, before `if __name__ == "__main__"`:

```python
def build_svg_cmd(args, work_dir: Path) -> list[str]:
    """Build the subprocess command for render_svg.py (Stage 1)."""
    return [
        sys.executable,
        str(HERE / "render_svg.py"),
        str(args.dxf),
        str(work_dir / "drawing.svg"),
        "--transform-out", str(work_dir / "transform.json"),
    ]


def build_tiles_cmd(args, work_dir: Path, out_dir: Path) -> list[str]:
    """Build the subprocess command for rasterise_tiles.py (Stage 2)."""
    cmd = [
        sys.executable,
        str(HERE / "rasterise_tiles.py"),
        "--svg",       str(work_dir / "drawing.svg"),
        "--transform", str(work_dir / "transform.json"),
        "--tiles-dir", str(out_dir / "tiles"),
        "--tile-meta", str(out_dir / "tile_meta.json"),
    ]
    if args.max_zoom is not None:
        cmd += ["--max-zoom", str(args.max_zoom)]
    if args.inkscape:
        cmd += ["--inkscape", str(args.inkscape)]
    return cmd


def build_manifest_cmd(args, work_dir: Path, out_dir: Path) -> list[str]:
    """Build the subprocess command for extract_manifest.py (Stage 3)."""
    cmd = [
        sys.executable,
        str(HERE / "extract_manifest.py"),
        "--dxf",       str(args.dxf),
        "--labels",    str(args.labels),
        "--svg",       str(work_dir / "drawing.svg"),
        "--transform", str(work_dir / "transform.json"),
        "--tile-meta", str(out_dir / "tile_meta.json"),
        "--out",       str(out_dir / "label-manifest.json"),
    ]
    if args.debug_svg:
        cmd += ["--debug-svg", str(args.debug_svg)]
    if args.verbose:
        cmd.append("--verbose")
    return cmd
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_run_pipeline.py::TestCommandBuilders -v
```

Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/run_pipeline.py tests/test_run_pipeline.py
git commit -m "feat: add stage command builder functions"
```

---

## Task 4: Work directory validation (`--from-stage` prerequisites)

**Files:**
- Modify: `pipeline/run_pipeline.py`
- Modify: `tests/test_run_pipeline.py`

- [ ] **Step 1: Write failing tests for prerequisite checks**

Append to `tests/test_run_pipeline.py`:

```python
class TestPrerequisiteCheck:
    def test_from_stage_tiles_fails_if_no_drawing_svg(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        # transform.json exists but drawing.svg does not
        (work_dir / "transform.json").touch()
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")
        assert exc.value.code == 2

    def test_from_stage_tiles_fails_if_no_transform(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        (work_dir / "drawing.svg").touch()
        # transform.json is missing
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")
        assert exc.value.code == 2

    def test_from_stage_tiles_passes_when_files_exist(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        (work_dir / "drawing.svg").touch()
        (work_dir / "transform.json").touch()
        # Should not raise
        run_pipeline.check_prerequisites("tiles", work_dir, tmp_path / "output")

    def test_from_stage_manifest_fails_if_no_tile_meta(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (work_dir / "transform.json").touch()
        # tile_meta.json is missing from out_dir
        with pytest.raises(SystemExit) as exc:
            run_pipeline.check_prerequisites("manifest", work_dir, out_dir)
        assert exc.value.code == 2

    def test_from_stage_manifest_passes_when_files_exist(self, tmp_path):
        work_dir = tmp_path / ".work"
        work_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (work_dir / "transform.json").touch()
        (out_dir / "tile_meta.json").touch()
        # Should not raise
        run_pipeline.check_prerequisites("manifest", work_dir, out_dir)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_run_pipeline.py::TestPrerequisiteCheck -v 2>&1 | head -15
```

Expected: `AttributeError: module 'run_pipeline' has no attribute 'check_prerequisites'`

- [ ] **Step 3: Add `check_prerequisites` to `run_pipeline.py`**

Add after the command builders:

```python
def check_prerequisites(from_stage: str, work_dir: Path, out_dir: Path) -> None:
    """
    Verify that the cached intermediate files required by `--from-stage` exist.
    Calls sys.exit(2) with a descriptive message if any are missing.
    """
    missing = []

    if from_stage in ("tiles",):
        for f in (work_dir / "drawing.svg", work_dir / "transform.json"):
            if not f.exists():
                missing.append(str(f))

    if from_stage in ("manifest",):
        for f in (work_dir / "transform.json", out_dir / "tile_meta.json"):
            if not f.exists():
                missing.append(str(f))

    if missing:
        files = "\n  ".join(missing)
        print(
            f"Error: --from-stage {from_stage} requires these cached files "
            f"from a prior run with --keep-work:\n  {files}",
            file=sys.stderr,
        )
        sys.exit(2)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_run_pipeline.py::TestPrerequisiteCheck -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/run_pipeline.py tests/test_run_pipeline.py
git commit -m "feat: add --from-stage prerequisite validation"
```

---

## Task 5: Orchestration loop with timing output

**Files:**
- Modify: `pipeline/run_pipeline.py`
- Modify: `tests/test_run_pipeline.py`

- [ ] **Step 1: Write failing tests for the orchestration loop**

Append to `tests/test_run_pipeline.py`:

```python
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
            debug_svg=None,
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
        # Verify stage scripts are called in order
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
        # Stage 3 must NOT have been called
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
        args.keep_work = True  # work dir retained from prior run
        work_dir = args.out_dir / ".work"
        work_dir.mkdir(parents=True)
        out_dir = args.out_dir
        out_dir.mkdir(parents=True)
        (work_dir / "transform.json").touch()
        (out_dir / "tile_meta.json").touch()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            exit_code = run_pipeline.run(args)

        assert exit_code == 0
        assert mock_run.call_count == 1  # only extract_manifest.py
        cmd = mock_run.call_args.args[0]
        assert cmd[1].endswith("extract_manifest.py")
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_run_pipeline.py::TestOrchestrationLoop -v 2>&1 | head -20
```

Expected: `AttributeError: module 'run_pipeline' has no attribute 'run'`

- [ ] **Step 3: Add the `run()` function and update `__main__` block**

First, replace the import block at the top of `run_pipeline.py` with:

```python
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path
```

Then replace the `if __name__ == "__main__":` block with the following (no inline imports needed since all are at the top):

```python
# ── Stage definitions ─────────────────────────────────────────────────────────

STAGES = [
    ("svg",      "render_svg"),
    ("tiles",    "rasterise_tiles"),
    ("manifest", "extract_manifest"),
]


def run(args) -> int:
    """
    Execute pipeline stages in order, starting from args.from_stage if set.
    Returns 0 on full success, 1 if any stage fails.
    """
    out_dir = args.out_dir
    work_dir = out_dir / ".work"

    # Determine which stages to run
    stage_keys = [s[0] for s in STAGES]
    start_idx = stage_keys.index(args.from_stage) if args.from_stage else 0
    stages_to_run = STAGES[start_idx:]

    # Validate cached intermediates if resuming
    if args.from_stage:
        check_prerequisites(args.from_stage, work_dir, out_dir)

    # Create output and work directories
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nDXF Pipeline — {args.dxf.name} → {out_dir}/")

    total = len(STAGES)
    failed = False

    for stage_key, stage_label in stages_to_run:
        stage_num = stage_keys.index(stage_key) + 1

        if stage_key == "svg":
            cmd = build_svg_cmd(args, work_dir)
        elif stage_key == "tiles":
            cmd = build_tiles_cmd(args, work_dir, out_dir)
        else:
            cmd = build_manifest_cmd(args, work_dir, out_dir)

        t0 = time.monotonic()
        result = subprocess.run(cmd)
        elapsed = time.monotonic() - t0

        if result.returncode != 0:
            print(
                f"[Stage {stage_num}/{total}] {stage_label:<20}  ✗  FAILED (exit {result.returncode})"
            )
            print(
                f"Pipeline aborted. Intermediates retained in {work_dir}",
                file=sys.stderr,
            )
            failed = True
            break

        print(f"[Stage {stage_num}/{total}] {stage_label:<20}  ✓  {elapsed:.1f}s")

    if not failed:
        print(f"\nDone. Outputs in {out_dir}/")
        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir)

    return 1 if failed else 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args))
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_run_pipeline.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add pipeline/run_pipeline.py tests/test_run_pipeline.py
git commit -m "feat: add pipeline orchestration loop with timing output"
```

---

## Task 6: Integration test with real test data

**Files:**
- Modify: `tests/test_run_pipeline.py`

This task verifies the end-to-end pipeline actually runs against the existing test DXF. It requires Inkscape to be installed.

- [ ] **Step 1: Append the integration test**

Append to `tests/test_run_pipeline.py`:

```python
import shutil

@pytest.mark.integration
class TestIntegration:
    """End-to-end tests. Require Inkscape. Skip with: pytest -m 'not integration'"""

    def test_full_pipeline_produces_outputs(self, tmp_path):
        tests_dir = Path(__file__).parent
        dxf = tests_dir / "test_diagram.dxf"
        labels = tests_dir / "test_labels.txt"

        if not dxf.exists():
            pytest.skip("test_diagram.dxf not found — run generate_test_dxf.py first")
        if not shutil.which("inkscape"):
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
        assert (out / "label-manifest.json").exists(), "label-manifest.json missing"
        assert any((out / "tiles").rglob("*.png")), "No tile PNGs generated"

    def test_from_stage_manifest_reuses_tiles(self, tmp_path):
        tests_dir = Path(__file__).parent
        dxf = tests_dir / "test_diagram.dxf"
        labels = tests_dir / "test_labels.txt"

        if not dxf.exists():
            pytest.skip("test_diagram.dxf not found — run generate_test_dxf.py first")
        if not shutil.which("inkscape"):
            pytest.skip("Inkscape not found in PATH")

        out_dir = tmp_path / "output"

        # Full run first, keeping work dir
        args = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(out_dir),
            "--keep-work",
        ])
        assert run_pipeline.run(args) == 0

        # Record mtime of tile_meta.json — it must NOT change on resume
        tile_meta_mtime = (out_dir / "tile_meta.json").stat().st_mtime

        # Re-run from manifest only
        args2 = run_pipeline.parse_args([
            "--dxf", str(dxf),
            "--labels", str(labels),
            "--out-dir", str(out_dir),
            "--from-stage", "manifest",
        ])
        assert run_pipeline.run(args2) == 0

        # tile_meta.json should be unchanged (tiles stage skipped)
        assert (out_dir / "tile_meta.json").stat().st_mtime == tile_meta_mtime
```

- [ ] **Step 2: Run unit tests only (integration tests require Inkscape)**

```bash
python -m pytest tests/test_run_pipeline.py -m "not integration" -v
```

Expected: all non-integration tests PASS

- [ ] **Step 3: Run integration tests if Inkscape is available**

```bash
python -m pytest tests/test_run_pipeline.py -m integration -v
```

If Inkscape is installed: all integration tests PASS and you'll see tile PNGs in the tmp output dir.
If Inkscape is not installed: tests are skipped with `"Inkscape not found in PATH"`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_run_pipeline.py
git commit -m "test: add integration tests for full pipeline run"
```

---

## Task 7: Smoke-test the CLI manually

- [ ] **Step 1: Generate test data if not already present**

```bash
cd c:/Users/thoma/Repos/DViewer/DXFPipeline
python tests/generate_test_dxf.py
ls tests/test_diagram.dxf tests/test_labels.txt
```

Expected: both files exist.

- [ ] **Step 2: Run the full pipeline**

```bash
python pipeline/run_pipeline.py \
  --dxf tests/test_diagram.dxf \
  --labels tests/test_labels.txt \
  --out-dir /tmp/dxf_out \
  --keep-work
```

Expected console output:
```
DXF Pipeline — test_diagram.dxf → /tmp/dxf_out/
[Stage 1/3] render_svg             ✓  2.1s
[Stage 2/3] rasterise_tiles        ✓  18.4s
[Stage 3/3] extract_manifest       ✓  0.9s

Done. Outputs in /tmp/dxf_out/
```

- [ ] **Step 3: Verify outputs**

```bash
ls /tmp/dxf_out/
ls /tmp/dxf_out/tiles/
cat /tmp/dxf_out/tile_meta.json
python -m json.tool /tmp/dxf_out/hitboxes.json | head -30
```

Expected: `tiles/`, `hitboxes.json`, `tile_meta.json`, `label-manifest.json` all present. `tiles/` contains subdirectories with `.png` files. `.work/` is present (because `--keep-work` was used).

- [ ] **Step 4: Test `--from-stage manifest`**

```bash
python pipeline/run_pipeline.py \
  --dxf tests/test_diagram.dxf \
  --labels tests/test_labels.txt \
  --out-dir /tmp/dxf_out \
  --from-stage manifest
```

Expected: only Stage 3 runs. `tile_meta.json` mtime unchanged.

- [ ] **Step 5: Test error on bad DXF path**

```bash
python pipeline/run_pipeline.py \
  --dxf nonexistent.dxf \
  --labels tests/test_labels.txt; echo "exit: $?"
```

Expected: `error: DXF file not found: nonexistent.dxf` and `exit: 2`

- [ ] **Step 6: Final commit if any tweaks were needed**

```bash
git add -p
git commit -m "fix: address issues found during manual smoke test"
```

---

## Verification Summary

| Check | Command |
|-------|---------|
| Unit tests pass | `python -m pytest tests/test_run_pipeline.py -m "not integration" -v` |
| Integration tests pass (needs Inkscape) | `python -m pytest tests/test_run_pipeline.py -m integration -v` |
| Full pipeline smoke test | `python pipeline/run_pipeline.py --dxf tests/test_diagram.dxf --labels tests/test_labels.txt --out-dir /tmp/dxf_out --keep-work` |
| `--from-stage` resumes correctly | `python pipeline/run_pipeline.py --dxf tests/test_diagram.dxf --labels tests/test_labels.txt --out-dir /tmp/dxf_out --from-stage manifest` |
| Bad input exits with code 2 | `python pipeline/run_pipeline.py --dxf bad.dxf --labels tests/test_labels.txt; echo "exit: $?"` |
