# DXFPipeline — Claude Code Session Notes

This folder contains the POC pipeline for converting a DXF drawing into a tiled raster map
with clickable label hitboxes. It is a throwaway spike feeding the main viewer.

---

## Pipeline Overview

Three scripts run in sequence, orchestrated by `pipeline/run_pipeline.py`.

```text
Stage 1  render_svg.py
         input.dxf → drawing.svg  [+ transform.json (partial)]

Stage 2  rasterise_tiles.py
         drawing.svg → tiles/{z}/{x}/{y}.png + tile_meta.json
         (also updates transform.json in-place with PNG scale + leaflet_bounds)

Stage 3  extract_manifest.py
         input.dxf + labels.txt [+ transform.json] → label-manifest.json + hitboxes.json
```

Stages 1 and 3 both read the DXF directly and are otherwise independent.
Stage 3 depends on Stage 2's PNG scale data (via transform.json or tile_meta.json).

---

## Outputs That Matter

| File | Consumer |
| --- | --- |
| `tiles/{z}/{x}/{y}.png` | Leaflet tile layer (background map) |
| `hitboxes.json` | Leaflet interaction layer (clickable label hitboxes) |
| `tile_meta.json` | Viewer bootstrap — zoom levels, pixel dimensions, leaflet_bounds |

Everything else (`transform.json`, `debug_labels.svg`, SVG coords in the manifest) is
intermediate/dev tooling, not consumed by the viewer.

---

## Scripts

### `render_svg.py`

- Reads DXF via `ezdxf`, scans entity bbox (never trust `$EXTMIN/$EXTMAX`).
- Renders to SVG via `ezdxf.addons.drawing.SVGBackend` (outputs in mm units).
- Writes `transform.json` with DXF extents + SVG viewBox.
- `--text-to-path` flag converts text to filled paths (font-independent tiles,
  but then extract_manifest falls back to DXF-space coords for label matching).

### `rasterise_tiles.py`

- Shells out to **Inkscape** to rasterise SVG → high-res PNG.
- Generates XYZ tile pyramid via PIL: for each zoom z, scales full image to
  `2^z × 2^z` grid of 256×256 tiles.
- Reads DXF extents from `transform.json` to compute `px_per_dxf_unit` (scale).
- Writes `tile_meta.json` and updates `transform.json` in-place with PNG dimensions,
  scale factors, and `leaflet_bounds`.
- Inkscape is the only external binary dependency. Auto-detected from PATH or
  hardcoded Windows fallback paths.

### `extract_manifest.py` (~1400 lines)

- Extracts all TEXT/MTEXT entities from DXF modelspace.
- Matches target labels (from `labels.txt`) using:
  1. Exact match
  2. Spatial cluster match — union-find groups nearby text fragments (e.g. "FV" + "501" → "FV501")
  3. Inverted-T pattern — one top token + N bottom tokens → N labels per cluster
  4. Range expansion — "FV" + "18M TO 24M" → FV18M, FV19M, ..., FV24M
  5. Case-insensitive fallback
- Computes per-label bounding boxes in DXF space (rotation + alignment aware,
  using a ~200-entry glyph width lookup table for STANDARD font).
- Transforms DXF coords → Leaflet coords using scale from `transform.json` or `tile_meta.json`.
- Writes `label-manifest.json` (full data) and `hitboxes.json` (flat list for Leaflet).
- `--tile-meta` flag: can read PNG scale from `tile_meta.json` instead of transform.json.
- `--debug-svg`: injects green outlines into SVG at matched label positions (dev only).

### `generate_test_dxf.py`

- Generates `test_diagram.dxf` + `test_labels.txt` covering 8 edge case scenarios.
- Not part of the active pipeline — utility/test data generator only.

---

## Coordinate Transform Chain

```text
DXF (Y-up, arbitrary units)
  ↓  normalize to 0..1, flip Y (DXF is Y-up, raster is Y-down)
  ↓  scale by PNG pixel dimensions
PNG pixel space
  ↓  negate Y (Leaflet CRS.Simple: lat = -y_px)
Leaflet {lat, lng}
```

DXF x_min/y_min/width/height come from entity bbox scan (not DXF header).
Scale factors (`scale_x = png_w / dxf_w`) are in `tile_meta.json` as `px_per_dxf_unit`.

---

## Known Redundancy / Simplification Notes

- **`transform.json` is a handshake artifact** that could be eliminated:
  - DXF extents: `extract_manifest.py` already has `extract_dxf_extents()` to read directly from DXF.
  - PNG scale + leaflet_bounds: already in `tile_meta.json` (use `--tile-meta` flag).
  - SVG viewBox block: only used for debug SVG scaling — not needed for tiles or hitboxes.

- **SVG coordinate space** (DXF → SVG transform) is dead weight for the viewer —
  the viewer only uses Leaflet coords. The `dxf_to_svg()` branch and viewBox tracking
  can be dropped when simplifying.

- **Inkscape** is the fragile point. A pure-Python replacement:
  - `cairosvg` — lightweight, no external binary
  - The SVG output from ezdxf is straightforward enough that cairosvg handles it well.

- `build_dxf_index()` and `build_svg_index()` in extract_manifest.py are identical functions.
- `dxf_bbox_to_svg()`, `dxf_bbox_to_png()`, `dxf_bbox_to_leaflet()` have near-identical
  corner-projection logic that could be a single parameterised method.

---

## Dependencies

| Package | Used by | Purpose |
| --- | --- | --- |
| `ezdxf` | render_svg, extract_manifest | DXF parsing + SVG rendering |
| `pillow` | rasterise_tiles | PNG I/O + tile generation |
| `lxml` | extract_manifest | SVG text extraction (optional) |
| Inkscape (binary) | rasterise_tiles | SVG → PNG rasterisation |

---

## Tile Coordinate Convention

Tiles follow XYZ (slippy map) convention: `tiles/{z}/{x}/{y}.png`.
Leaflet is configured with `CRS.Simple` — no geographic projection.
Leaflet coordinate convention: `lat = -y_px`, `lng = x_px`.
`leaflet_bounds = [[-full_h_px, 0], [0, full_w_px]]`.
