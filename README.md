# DXFPipeline

Converts a DXF engineering drawing into a tiled raster map with clickable label hitboxes,
ready for the P&ID Leaflet viewer.

## Quick start

```bash
pip install -r requirements.txt          # ezdxf, Pillow, lxml
# also install Inkscape: https://inkscape.org/release/

python pipeline/run_pipeline.py \
  --dxf input.dxf \
  --labels labels.txt \
  --out-dir output/
```

Outputs written to `output/`:

| File | Purpose |
| ---- | ------- |
| `tiles/{z}/{x}/{y}.png` | XYZ tile pyramid for Leaflet |
| `hitboxes.json` | Clickable label hitboxes (Leaflet coords) |
| `tile_meta.json` | Viewer bootstrap — zoom levels, bounds |
| `label-manifest.json` | Full label data |

## Options

```text
--out-dir DIR          Output directory (default: output/)
--max-zoom N           Override max tile zoom level (auto-calculated if omitted)
--inkscape PATH        Path to Inkscape binary (auto-detected if on PATH)
--keep-work            Retain intermediate files (.work/) after success
--from-stage STAGE     Resume from svg / tiles / manifest (requires prior --keep-work run)
--debug-svg FILE       Write SVG with hitbox overlays for inspection
--verbose              Verbose label matching output
```

## Resuming from a stage

Re-run just the label extraction step without re-tiling (saves ~20s):

```bash
# First run — keep intermediates
python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt --keep-work

# Edit labels.txt, then re-run only Stage 3
python pipeline/run_pipeline.py --dxf input.dxf --labels labels.txt --from-stage manifest
```

## Running individual stages

The three underlying scripts can still be called directly if needed:

```bash
# Stage 1: DXF -> SVG
python pipeline/render_svg.py input.dxf drawing.svg

# Stage 2: SVG -> tile pyramid
python pipeline/rasterise_tiles.py --svg drawing.svg --transform transform.json

# Stage 3: label extraction
python pipeline/extract_manifest.py \
  --dxf input.dxf \
  --labels labels.txt \
  --svg drawing.svg \
  --transform transform.json \
  --out label-manifest.json
```

## Tests

```bash
# Unit tests (no Inkscape required)
python -m pytest tests/ -m "not integration" -v

# Integration tests (require Inkscape in PATH)
python -m pytest tests/ -m integration -v

# Generate test DXF data
python tests/generate_test_dxf.py
```
