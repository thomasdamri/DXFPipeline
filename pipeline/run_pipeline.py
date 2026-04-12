#!/usr/bin/env python3
"""
run_pipeline.py
───────────────
Orchestrates all three DXFPipeline stages in sequence:
  Stage 1: render_svg.py      (DXF -> SVG, optionally multiple themed SVGs)
  Stage 2: rasterise_tiles.py (SVG -> tile pyramid + tile_meta.json, once per theme)
  Stage 3: extract_manifest.py (DXF + labels -> hitboxes.json)

Usage:
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt \\
        --out-dir output/ --max-zoom 4 --keep-work
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt \\
        --themes-config themes.json
    python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt \\
        --from-stage manifest  # requires prior run with --keep-work

Exit codes:
    0  all stages succeeded
    1  a stage failed
    2  argument error
"""

import argparse
import json
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
    p.add_argument("--themes-config", default=None, type=Path, metavar="FILE",
                   help="JSON file with per-theme background + layer colours. "
                        "When provided, one tile set is generated per theme "
                        "under tiles/<theme>/")
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
    if args.themes_config and not args.themes_config.exists():
        p.error(f"Themes config not found: {args.themes_config}")

    return args


def build_svg_cmd(args, work_dir: Path) -> list[str]:
    """Build the subprocess command for render_svg.py (Stage 1)."""
    cmd = [
        sys.executable,
        str(HERE / "render_svg.py"),
        str(args.dxf),
        str(work_dir / "drawing.svg"),
    ]
    if args.themes_config:
        cmd += ["--themes-config", str(args.themes_config)]
    return cmd


def _tile_meta_path(out_dir: Path, theme: str | None) -> Path:
    """Return the tile_meta.json path for a given theme (or top-level for default)."""
    if theme:
        return out_dir / "tiles" / theme / "tile_meta.json"
    return out_dir / "tile_meta.json"


def build_tiles_cmd_for_entry(args, work_dir: Path, out_dir: Path,
                               entry: dict) -> list[str]:
    """Build the rasterise_tiles.py command for a single svg_manifest entry."""
    theme   = entry["theme"]       # str or None
    bg      = entry.get("background", "#ffffff")
    svg     = entry["svg"]

    # svg paths in the manifest are relative to the work_dir when they come
    # from render_svg.py running as a subprocess; normalise to absolute.
    svg_path = Path(svg)
    if not svg_path.is_absolute():
        svg_path = work_dir / svg_path

    tile_meta = _tile_meta_path(out_dir, theme)

    cmd = [
        sys.executable,
        str(HERE / "rasterise_tiles.py"),
        "--svg",       str(svg_path),
        "--tiles-dir", str(out_dir / "tiles"),
        "--tile-meta", str(tile_meta),
        "--bg-color",  bg,
    ]
    if theme:
        cmd += ["--theme", theme]
    if args.max_zoom is not None:
        cmd += ["--max-zoom", str(args.max_zoom)]
    if args.inkscape:
        cmd += ["--inkscape", str(args.inkscape)]
    return cmd


def build_manifest_cmd(args, out_dir: Path, first_tile_meta: Path) -> list[str]:
    """Build the subprocess command for extract_manifest.py (Stage 3)."""
    cmd = [
        sys.executable,
        str(HERE / "extract_manifest.py"),
        "--dxf",       str(args.dxf),
        "--labels",    str(args.labels),
        "--tile-meta", str(first_tile_meta),
        "--hitboxes",  str(out_dir / "hitboxes.json"),
    ]
    if args.verbose:
        cmd.append("--verbose")
    return cmd


def _load_svg_manifest(work_dir: Path) -> list[dict]:
    """Read svg_manifest.json written by render_svg.py."""
    path = work_dir / "svg_manifest.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
        manifest_file = work_dir / "svg_manifest.json"
        if not manifest_file.exists():
            missing.append(str(manifest_file))
        # Also verify at least one SVG referenced by the manifest exists
        if manifest_file.exists():
            try:
                manifest = _load_svg_manifest(work_dir)
                for entry in manifest:
                    svg = Path(entry["svg"])
                    if not svg.is_absolute():
                        svg = work_dir / svg
                    if not svg.exists():
                        missing.append(str(svg))
            except Exception:
                missing.append(f"{manifest_file} (unreadable)")

    elif from_stage == "manifest":
        # Need at least one tile_meta.json — check default location first,
        # then look for any themed tile_meta under tiles/
        candidates = [out_dir / "tile_meta.json"]
        tiles_dir = out_dir / "tiles"
        if tiles_dir.exists():
            candidates += list(tiles_dir.glob("*/tile_meta.json"))
        if not any(c.exists() for c in candidates):
            missing.append(f"tile_meta.json (none found under {out_dir})")

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


def _run_cmd(cmd: list[str], label: str) -> bool:
    """Run a subprocess command. Returns True on success, False on failure."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(cmd, env=env)
    return result.returncode == 0


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
    if args.themes_config:
        print(f"Themes config: {args.themes_config}")

    total = len(STAGES)
    failed = False

    for stage_key, stage_label in stages_to_run:
        stage_num = stage_keys.index(stage_key) + 1

        if stage_key == "svg":
            cmd = build_svg_cmd(args, work_dir)
            t0 = time.monotonic()
            ok = _run_cmd(cmd, stage_label)
            elapsed = time.monotonic() - t0
            if not ok:
                print(f"[Stage {stage_num}/{total}] {stage_label:<20}  FAILED")
                print(f"Pipeline aborted. Intermediates retained in {work_dir}",
                      file=sys.stderr)
                failed = True
                break
            print(f"[Stage {stage_num}/{total}] {stage_label:<20}  OK  {elapsed:.1f}s")

        elif stage_key == "tiles":
            # Read manifest produced by Stage 1
            manifest = _load_svg_manifest(work_dir)
            theme_failed = False
            for entry in manifest:
                theme = entry.get("theme") or "default"
                label = f"{stage_label} ({theme})"
                cmd = build_tiles_cmd_for_entry(args, work_dir, out_dir, entry)
                t0 = time.monotonic()
                ok = _run_cmd(cmd, label)
                elapsed = time.monotonic() - t0
                if not ok:
                    print(f"[Stage {stage_num}/{total}] {label:<28}  FAILED")
                    print(f"Pipeline aborted. Intermediates retained in {work_dir}",
                          file=sys.stderr)
                    theme_failed = True
                    break
                print(f"[Stage {stage_num}/{total}] {label:<28}  OK  {elapsed:.1f}s")
            if theme_failed:
                failed = True
                break

        else:  # manifest
            # Stage 3 needs a tile_meta for coordinate transforms; all themes
            # share the same pixel dimensions so any one will do.
            manifest = _load_svg_manifest(work_dir)
            first_tile_meta = _tile_meta_path(out_dir, manifest[0].get("theme"))

            cmd = build_manifest_cmd(args, out_dir, first_tile_meta)
            t0 = time.monotonic()
            ok = _run_cmd(cmd, stage_label)
            elapsed = time.monotonic() - t0
            if not ok:
                print(f"[Stage {stage_num}/{total}] {stage_label:<20}  FAILED")
                print(f"Pipeline aborted. Intermediates retained in {work_dir}",
                      file=sys.stderr)
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
