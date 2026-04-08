# Pipeline Orchestrator Design

**Date:** 2026-04-05  
**Status:** Approved

## Context

The DXFPipeline has three standalone Python CLI scripts with no integrated orchestrator:

1. `pipeline/render_svg.py` — DXF → SVG + `transform.json`
2. `pipeline/rasterise_tiles.py` — SVG → XYZ tile pyramid + `tile_meta.json`
3. `pipeline/extract_manifest.py` — DXF text entities → `hitboxes.json` + `label-manifest.json`

Running the pipeline currently requires manually sequencing three commands with the right arguments in the right order. The goal is a single command that runs all three stages end-to-end, with production-ready ergonomics (timing output, clean error messages, exit codes, stage resumption).

## Approach

**Thin subprocess wrapper.** A new `pipeline/run_pipeline.py` script calls each existing stage script via `subprocess.run`. No refactoring of the existing scripts is required. The subprocess boundary gives clean failure isolation — each stage fails independently with its own stderr.

This approach was chosen over in-process imports because the existing scripts use top-level `argparse.parse_args()` which doesn't compose without refactoring.

## File Locations

| File | Action |
|------|--------|
| `pipeline/run_pipeline.py` | **New** — orchestrator script |
| `requirements.txt` | **New** — at DXFPipeline root |

## CLI Interface

```
python pipeline/run_pipeline.py \
  --dxf input.dxf \
  --labels labels.txt \
  [--out-dir output/]        # default: ./output
  [--max-zoom N]             # passed to rasterise_tiles.py
  [--inkscape PATH]          # passed to rasterise_tiles.py
  [--from-stage {svg,tiles,manifest}]  # skip earlier stages
  [--keep-work]              # retain .work/ directory after success
  [--debug-svg FILE]         # passed to extract_manifest.py
  [--verbose]                # passed to extract_manifest.py
```

### Stage names for `--from-stage`

| Stage name | Script | Skips |
|------------|--------|-------|
| `svg` | `render_svg.py` | nothing (start from beginning) |
| `tiles` | `rasterise_tiles.py` | render_svg |
| `manifest` | `extract_manifest.py` | render_svg + rasterise_tiles |

`--from-stage` requires that a prior run used `--keep-work` so that intermediates exist in `<out-dir>/.work/`.

## Directory Layout

```
<out-dir>/
  .work/               # intermediates (deleted unless --keep-work or failure)
    drawing.svg        # produced by render_svg.py, consumed by rasterise_tiles.py
    transform.json     # produced by render_svg.py, updated by rasterise_tiles.py, read by extract_manifest.py
  tiles/               # final: XYZ tile pyramid
  hitboxes.json        # final: Leaflet-ready label hitboxes
  tile_meta.json       # final: Leaflet bounds + zoom config
  label-manifest.json  # final: full label manifest
  debug_labels.svg     # final: only if --debug-svg passed
```

## Console Output

```
DXF Pipeline — input.dxf → output/
[Stage 1/3] render_svg       ✓  2.1s
[Stage 2/3] rasterise_tiles  ✓  18.4s
[Stage 3/3] extract_manifest ✓  0.9s
Done. Outputs in output/
```

On failure:
```
[Stage 2/3] rasterise_tiles  ✗  FAILED (exit 1)
  Inkscape not found. Install Inkscape or pass --inkscape PATH.
Pipeline aborted. Intermediates retained in output/.work/
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All stages succeeded |
| 1 | A stage failed (stage name printed to stderr) |
| 2 | Argument error (bad flags, missing required files) |

## Work Directory Lifecycle

- **Full run (no `--keep-work`):** `.work/` is created at the start, populated during stages 1–2, then **deleted** after stage 3 succeeds.
- **Full run with `--keep-work`):** `.work/` is kept for inspection.
- **`--from-stage` run:** `.work/` must already exist from a prior `--keep-work` run. The orchestrator checks for required files before invoking a stage and fails early with a clear message if they're missing.
- **Any run that fails:** `.work/` is always retained so the user can inspect the partial output.

## requirements.txt

```
ezdxf>=1.3
Pillow>=10.0
lxml>=5.0
```

Inkscape is an OS-level dependency — noted as a comment in requirements.txt rather than a pip package.

## Verification

```bash
# Generate test data
python tests/generate_test_dxf.py

# Full pipeline run
python pipeline/run_pipeline.py \
  --dxf tests/test_diagram.dxf \
  --labels tests/test_labels.txt \
  --out-dir /tmp/dxf_test_out \
  --keep-work

# Verify outputs
ls /tmp/dxf_test_out/tiles/
cat /tmp/dxf_test_out/hitboxes.json | python -m json.tool | head -30
cat /tmp/dxf_test_out/tile_meta.json

# Re-run only manifest stage
python pipeline/run_pipeline.py \
  --dxf tests/test_diagram.dxf \
  --labels tests/test_labels.txt \
  --out-dir /tmp/dxf_test_out \
  --from-stage manifest

# Confirm non-zero exit on bad input
python pipeline/run_pipeline.py --dxf nonexistent.dxf --labels tests/test_labels.txt; echo "exit: $?"
```
