#!/usr/bin/env python3
"""
run_pipeline.py
───────────────
Orchestrates all three DXFPipeline stages in sequence:
  Stage 1: render_svg.py      (DXF -> SVG)
  Stage 2: rasterise_tiles.py (SVG -> tile pyramid + tile_meta.json)
  Stage 3: extract_manifest.py (DXF + labels -> hitboxes.json)

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
import os
import shutil      # used by run() — work dir cleanup
import subprocess  # used by run() — stage execution
import sys         # used by run() — exit codes
import time        # used by run() — stage timing
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
                   default=None, dest="from_stage",
                   help="Resume from this stage using cached intermediates "
                        "(requires prior run with --keep-work)")
    p.add_argument("--keep-work", action="store_true",
                   help="Retain <out-dir>/.work/ intermediates after success")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose output (passed to extract_manifest.py)")

    args = p.parse_args(argv)

    # Validate file existence immediately so errors are caught before any work starts
    if not args.dxf.exists():
        p.error(f"DXF file not found: {args.dxf}")
    if not args.labels.exists():
        p.error(f"Labels file not found: {args.labels}")

    return args


def build_svg_cmd(args, work_dir: Path) -> list[str]:
    """Build the subprocess command for render_svg.py (Stage 1)."""
    return [
        sys.executable,
        str(HERE / "render_svg.py"),
        str(args.dxf),
        str(work_dir / "drawing.svg"),
    ]


def build_tiles_cmd(args, work_dir: Path, out_dir: Path) -> list[str]:
    """Build the subprocess command for rasterise_tiles.py (Stage 2)."""
    cmd = [
        sys.executable,
        str(HERE / "rasterise_tiles.py"),
        "--svg",       str(work_dir / "drawing.svg"),
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
        "--tile-meta", str(out_dir / "tile_meta.json"),
        "--hitboxes",  str(out_dir / "hitboxes.json"),
    ]
    if args.verbose:
        cmd.append("--verbose")
    return cmd


def check_prerequisites(from_stage: str, work_dir: Path, out_dir: Path) -> None:
    """
    Verify that the cached intermediate files required by `--from-stage` exist.
    Calls sys.exit(2) with a descriptive message if any are missing.
    """
    # "svg" is the first stage — no cached intermediates are required.
    if from_stage == "svg":
        return

    missing = []

    if from_stage == "tiles":
        svg = work_dir / "drawing.svg"
        if not svg.exists():
            missing.append(str(svg))

    elif from_stage == "manifest":
        tile_meta = out_dir / "tile_meta.json"
        if not tile_meta.exists():
            missing.append(str(tile_meta))

    if missing:
        files = "\n  ".join(missing)
        print(
            f"Error: --from-stage {from_stage} requires these cached files "
            f"from a prior run with --keep-work:\n  {files}",
            file=sys.stderr,
        )
        sys.exit(2)


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

    print(f"\nDXF Pipeline -- {args.dxf.name} -> {out_dir}/")

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
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(cmd, env=env)
        elapsed = time.monotonic() - t0

        if result.returncode != 0:
            print(
                f"[Stage {stage_num}/{total}] {stage_label:<20}  FAILED (exit {result.returncode})"
            )
            print(
                f"Pipeline aborted. Intermediates retained in {work_dir}",
                file=sys.stderr,
            )
            failed = True
            break

        print(f"[Stage {stage_num}/{total}] {stage_label:<20}  OK  {elapsed:.1f}s")

    if not failed:
        print(f"\nDone. Outputs in {out_dir}/")
        if not args.keep_work and work_dir.exists():
            shutil.rmtree(work_dir)

    return 1 if failed else 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args))
