# DXF → Leaflet Diagram Viewer Pipeline

## Overview

```
input.dxf + labels.txt
        │
        ├─── [Stage 1] render_svg.py      ──► drawing.svg
        │
        ├─── [Stage 2] rasterise_tiles.py ──► tiles/{z}/{x}/{y}.png
        │                                     tile_meta.json
        │
        └─── [Stage 3] extract_manifest.py ─► hitboxes.json
                                              label-manifest.json  (opt-in)
```

**Orchestrator:** `run_pipeline.py` runs all three stages in sequence.

---

## Inputs

| File | Description |
| --- | --- |
| `input.dxf` | Raw CAD export from engineering team |
| `labels.txt` | Newline-separated list of text labels to locate and create hitboxes for |

---

## Stage 1 — Render SVG

**Script:** `pipeline/render_svg.py`  
**Tool:** `ezdxf` (Python)

**What it does:**

- Scans entity bounding boxes to determine DXF extents (never trusts `$EXTMIN/$EXTMAX`)
- Renders the full drawing to SVG via `ezdxf.addons.drawing.SVGBackend` (output in mm units)

**Command:**
```bash
python pipeline/render_svg.py input.dxf drawing.svg
python pipeline/render_svg.py input.dxf drawing.svg --text-to-path
```

`--text-to-path` converts text entities to filled outline paths (font-independent tiles, but label matching still works via DXF coords).

**Output:**

`drawing.svg` — vector SVG of the full drawing

---

## Stage 2 — Tile

**Script:** `pipeline/rasterise_tiles.py`  
**Tools:** Inkscape CLI (rasterisation) + Pillow (tiling)

**What it does:**

- Reads SVG viewBox dimensions directly from the SVG file
- Shells out to Inkscape to rasterise SVG → full-resolution PNG
- Slices the PNG into a 256×256 XYZ tile pyramid for Leaflet
- Writes `tile_meta.json` consumed by the viewer and Stage 3

**Command:**
```bash
python pipeline/rasterise_tiles.py --svg drawing.svg
python pipeline/rasterise_tiles.py --svg drawing.svg --max-zoom 6
python pipeline/rasterise_tiles.py --svg drawing.svg \
    --inkscape "C:\Program Files\Inkscape\bin\inkscape.exe"
```

**Inkscape install:**

- Windows: [inkscape.org/release](https://inkscape.org/release/) — add to PATH or pass `--inkscape`
- Linux: `sudo apt install inkscape`
- macOS: `brew install inkscape`

**Outputs:**

`tiles/{z}/{x}/{y}.png` — XYZ tile pyramid

- `z=0` — whole drawing in one 256×256 tile
- `z=max_zoom` — maximum detail (auto-calculated so full width ≥ 4096 px)

`tile_meta.json` — viewer bootstrap + coordinate scale data
```json
{
  "max_zoom": 5,
  "tile_size": 256,
  "full_width_px": 8192,
  "full_height_px": 5793,
  "svg_viewbox_width": 1189.0,
  "svg_viewbox_height": 841.0,
  "px_per_svg_unit": 6.889,
  "leaflet_bounds": [[-5793, 0], [0, 8192]]
}
```

---

## Stage 3 — Extract Hitboxes

**Script:** `pipeline/extract_manifest.py`  
**Tool:** `ezdxf` (Python)

**What it does:**

- Extracts all `TEXT` and `MTEXT` entities from DXF modelspace
- Matches target labels using five strategies (in order):
  1. **Exact match** — direct text lookup
  2. **Cluster match** — union-find groups nearby fragments (e.g. `"FV"` + `"501"` → `"FV501"`)
  3. **Inverted-T pattern** — one top token + N bottom tokens → N labels
  4. **Range expansion** — `"FV"` + `"18M TO 24M"` → `FV18M`, `FV19M`, …, `FV24M`
  5. **Case-insensitive fallback**
- Computes per-label bounding boxes in DXF space (rotation + alignment aware, 200-entry glyph-width lookup)
- Transforms DXF coords → PNG pixels → Leaflet CRS.Simple using `tile_meta.json` + DXF extents

**Command:**
```bash
python pipeline/extract_manifest.py \
  --dxf input.dxf \
  --labels labels.txt \
  --tile-meta tile_meta.json \
  --hitboxes hitboxes.json

# Also write full manifest (optional):
python pipeline/extract_manifest.py \
  --dxf input.dxf \
  --labels labels.txt \
  --tile-meta tile_meta.json \
  --hitboxes hitboxes.json \
  --manifest label-manifest.json
```

**Outputs:**

`hitboxes.json` — flat list consumed directly by the Leaflet viewer
```json
[
  {
    "label": "FV501",
    "found": true,
    "dxf":     { "x": 120.4, "y": 88.2 },
    "leaflet": { "lat": -4521.0, "lng": 3840.0 },
    "bbox": {
      "dxf":     { "x": 119.1, "y": 87.0, "width": 12.4, "height": 3.2, "corners": [...] },
      "png":     { "x": 3821.0, "y": 4508.0, "width": 101.0, "height": 26.0, "corners": [...] },
      "leaflet": { "bounds": [[-4534, 3821], [-4508, 3922]], "corners": [...], "center": {...} }
    },
    "meta": { "layer": "TAGS", "type": "TEXT", "handle": "1A2B", "duplicate": false, ... }
  }
]
```

`label-manifest.json` (opt-in) — full per-label data including unmatched labels and stats

---

## Running the Full Pipeline

```bash
# Full run
python pipeline/run_pipeline.py \
  --dxf input.dxf \
  --labels labels.txt \
  --out-dir output/

# Keep intermediates (drawing.svg) for stage resumption
python pipeline/run_pipeline.py \
  --dxf input.dxf \
  --labels labels.txt \
  --out-dir output/ \
  --keep-work

# Re-run label matching only (tiles already generated)
python pipeline/run_pipeline.py \
  --dxf input.dxf \
  --labels labels.txt \
  --out-dir output/ \
  --from-stage manifest
```

---

## Key Files Summary

| File | Produced by | Consumed by |
| --- | --- | --- |
| `input.dxf` | Engineering team | Stages 1 + 3 |
| `labels.txt` | Engineering team | Stage 3 |
| `drawing.svg` | Stage 1 | Stage 2 |
| `tiles/{z}/{x}/{y}.png` | Stage 2 | Leaflet tile layer |
| `tile_meta.json` | Stage 2 | Viewer bootstrap + Stage 3 |
| `hitboxes.json` | Stage 3 | Leaflet interaction layer |
| `label-manifest.json` | Stage 3 (opt-in) | Debugging / inspection |

---

## Coordinate Transform Chain

```
DXF (Y-up, arbitrary units)
  ↓  normalize to [0..1] using DXF extents from entity bbox scan
  ↓  flip Y  (DXF is Y-up; pixels are Y-down)
  ↓  scale by full PNG pixel dimensions
PNG pixel space  (full_width_px × full_height_px)
  ↓  negate Y  (Leaflet CRS.Simple: lat = -y_px)
Leaflet {lat: -png_y, lng: png_x}
```

Scale factors are derived at runtime: `scale_x = full_width_px / dxf_width`.  
DXF extents come from a live entity bbox scan (never from the DXF header — `$EXTMIN/$EXTMAX` are often sentinel values).

**Coordinate spaces reference:**

| Space | Origin | Y direction | Units |
| --- | --- | --- |---|
| DXF | Bottom-left | Up | Unitless CAD |
| PNG | Top-left | Down | Pixels |
| Leaflet CRS.Simple | Top-left | Down (`-y`) | `[lat, lng]` = `[-png_y, png_x]` |

---

## Dependencies

```
pip install ezdxf>=1.3 Pillow>=10.0
```

Plus **Inkscape** (external binary) for Stage 2 rasterisation.
