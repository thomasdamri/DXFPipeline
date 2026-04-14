# DXFPipeline — Design Document

## Purpose

Converts engineering P&ID drawings (DXF format) into web-ready map tiles and interactive hitbox data for the Leaflet-based defect viewer.

---

## Non-Functional Requirements

- **Resumable.** Stages can be re-run independently so engineers can iterate on label lists without re-rasterising tiles from scratch.
- **Minimal dependencies.** Three Python packages (`ezdxf`, `Pillow`, `cairosvg`). No external binaries, no databases, no network calls.
- **Deterministic output.** Given the same inputs, the pipeline produces byte-identical tile pyramids and JSON. Suitable for CI comparison and caching.
- **Fail-fast.** Invalid inputs (missing files, unrecognised DXF entities) produce a non-zero exit code and a clear error message before any slow work begins.
- **No tile assets in source control.** Generated tile pyramids are runtime artefacts, not committed files.

---

## Design

### Architecture

The pipeline is a linear three-stage process. Each stage is a standalone CLI script; the orchestrator (`run_pipeline.py`) composes them in sequence.

```text
DXF drawing
    │
    ▼ Stage 1: render_svg.py
  SVG vector file(s)  ──►  svg_manifest.json  (.work/)
    │
    ▼ Stage 2: rasterise_tiles.py
  PNG tile pyramid  ──►  tile_meta.json
    │
    ▼ Stage 3: extract_manifest.py
  hitboxes.json
```

Stages 1 and 3 both read the source DXF directly; they share no mutable state between them. Stage 3 depends on `tile_meta.json` (written by Stage 2) to convert DXF coordinates into Leaflet pixel space.

`svg_manifest.json` is the handshake between Stage 1 and Stage 2. Stage 1 writes it to `.work/` listing every SVG it produced (one per theme, or one default). Stage 2 reads it to know which SVGs to rasterise and what background colour to use. Example:

```json
[
  { "theme": null,  "svg": "/abs/path/drawing.svg",       "background": "#ffffff" },
  { "theme": "dark","svg": "/abs/path/drawing_dark.svg",  "background": "#1a1a2e" }
]
```

#### File Structure

```text
DXFPipeline/
├── pipeline/
│   ├── run_pipeline.py       # Orchestrator — runs all three stages
│   ├── render_svg.py         # Stage 1
│   ├── rasterise_tiles.py    # Stage 2
│   ├── extract_manifest.py   # Stage 3
│   └── pipeline_types.py     # Shared TypedDict definitions (TileMeta, HitboxRecord, ThemeConfig)
├── tests/
│   ├── test_run_pipeline.py  # Unit tests
│   └── generate_test_dxf.py  # Synthetic test data generator
├── environment.yml
├── mypy.ini
└── pytest.ini
```

#### Pipeline Steps

**Stage 1 — DXF → SVG (`render_svg.py`)**

Parses the DXF file and emits a vector SVG. Entity bounding boxes are scanned directly; the DXF header extents (`$EXTMIN`/`$EXTMAX`) are not trusted. Text can optionally be converted to filled paths for faithful rasterisation downstream.

**Stage 2 — SVG → tile pyramid (`rasterise_tiles.py`)**

Uses `cairosvg` to render the SVG to a high-resolution PNG in memory, then uses Pillow to slice it into a standard XYZ tile pyramid (256×256 tiles per zoom level). The maximum zoom level is chosen automatically so the drawing is at least 4096 px wide at full zoom. Emits `tile_meta.json` containing zoom range, pixel dimensions, and Leaflet coordinate bounds.

**Stage 3 — DXF + labels → hitboxes (`extract_manifest.py`)**

Re-reads the DXF to locate every TEXT/MTEXT entity. Matches entities against a user-supplied label list using a ranked strategy (exact → spatial cluster → inverted-T → range expansion → case-insensitive fallback). Computes axis-aligned bounding boxes for each matched label, converts them into Leaflet `CRS.Simple` coordinates using the scale from `tile_meta.json`, and writes `hitboxes.json`.

Label matching strategies, in priority order:

| Strategy | When it applies | Example |
| -------- | --------------- | ------- |
| **Exact** | A single DXF text entity matches the label verbatim | Label `FV501` ↔ TEXT entity `FV501` |
| **Spatial cluster** | Multiple nearby text fragments (e.g. two-line tags) are joined | `FV` + `501` close together → matched as `FV501` |
| **Inverted-T** | One top token above N bottom tokens → N independent labels | `FV` above `18M`, `20M`, `22M` → three separate hitboxes |
| **Range expansion** | A range expression is expanded into individual label names | `FV` + `18M TO 20M` → `FV18M`, `FV19M`, `FV20M` |
| **Case-insensitive** | Last-resort re-match ignoring case | Label `fv501` matches entity `FV501` |

**Orchestrator (`run_pipeline.py`)**

Validates all inputs, then runs Stages 1–3 as subprocesses. Supports `--from-stage` to resume from a cached intermediate, and `--keep-work` to retain intermediates for inspection. Cleans up the `.work/` scratch directory on success.

#### Viewer Outputs

| File | Consumer |
| ---- | -------- |
| `tiles/{z}/{x}/{y}.png` | Leaflet tile layer |
| `hitboxes.json` | Leaflet interaction layer (clickable labels) |
| `tile_meta.json` | Viewer bootstrap (zoom levels, bounds, scale) |

#### Coordinate Transform Chain

DXF uses Y-up in arbitrary engineering units. Leaflet `CRS.Simple` uses Y-down with `lat = −y` and `lng = x`. Three steps bridge them:

```text
DXF space  (Y-up, arbitrary units)
    │  1. subtract drawing origin (x_min, y_min)
    │  2. scale so the shorter drawing axis = 256 Leaflet units
    │  3. flip Y: y_leaflet = coord_height − y_scaled
    ▼
Leaflet CRS.Simple  { lat = −y_leaflet,  lng = x_leaflet }
```

`lat` is always ≤ 0 because the drawing occupies the negative-latitude half of the Leaflet space — the standard convention for `CRS.Simple` raster overlays. The `leaflet_bounds` value in `tile_meta.json` encodes the full extent of that space.

#### JSON Interchange Schemas

**`tile_meta.json`** — written by Stage 2, read by Stage 3 and the viewer:

```json
{
  "max_zoom":       5,
  "tile_size":      256,
  "full_width_px":  8192,
  "full_height_px": 16565,
  "leaflet_bounds": [[-517.6562, 0], [0, 256.0]]
}
```

**`hitboxes.json`** — written by Stage 3, consumed by the Leaflet viewer:

```json
[
  {
    "label": "FV501",
    "found": true,
    "leaflet": { "lat": -9.88, "lng": 5.12 },
    "bbox": {
      "leaflet": {
        "corners": [
          { "lat": -10.00, "lng": 5.07 },
          { "lat": -10.00, "lng": 5.90 },
          { "lat": -9.46,  "lng": 5.90 },
          { "lat": -9.46,  "lng": 5.07 }
        ]
      }
    }
  }
]
```

Corners are ordered TL → TR → BR → BL. `found: false` entries are omitted from the output.

---

### Key Technologies

| Technology | Role |
| ---------- | ---- |
| **ezdxf** | DXF parsing and SVG rendering |
| **cairosvg** | SVG → PNG rasterisation (pure Python, no external binary) |
| **Pillow** | PNG tiling (slice full image into XYZ pyramid) |
| **Leaflet CRS.Simple** | Coordinate system consumed by the viewer (no geographic projection) |

---

## Testing Methodology

Tests are written with **pytest**. All tests run without external dependencies — the `integration` marker is no longer used.

### Stage 1 — `render_svg.py`

Use `generate_test_dxf.py` (or similar helpers) to produce minimal synthetic DXF fixtures, then assert on the output SVG:

- Correct `viewBox` values derived from entity extents (not DXF header).
- All expected entities present in the SVG output.
- Text-to-path flag produces `<path>` elements instead of `<text>`.
- Degenerate inputs (empty drawing, single entity) don't crash.

### Stage 2 — `rasterise_tiles.py`

Cover the tiling logic in isolation — zoom level auto-calculation, tile count at each zoom, `tile_meta.json` field values, coordinate bounds computation. Feed a pre-rendered PNG directly to test the tiling path without invoking cairosvg.

### Stage 3 — `extract_manifest.py`

The most complex stage; warrants the most test coverage. Each matching strategy should have its own focused tests:

| Strategy | What to test |
| -------- | ------------ |
| Exact match | Label found verbatim in a single TEXT entity |
| Spatial cluster | Fragments within proximity threshold merge into one label |
| Inverted-T | One root token + N leaf tokens → N separate hitboxes |
| Range expansion | `"FV18M TO 20M"` produces FV18M, FV19M, FV20M hitboxes |
| Case-insensitive fallback | Match fires when casing differs |
| No match | Unmatched label absent from output; no crash |

Additionally test the coordinate transform chain end-to-end: a known DXF coordinate should map to the expected `{lat, lng}` pair in `hitboxes.json` given a fixed `tile_meta.json`.

### Orchestrator — `run_pipeline.py`

Unit tests cover argument parsing, stage-selection logic, and prerequisite validation. Stages are mocked as subprocesses — no real DXF or tile data required.

### Fixtures and Helpers

`generate_test_dxf.py` is the canonical source of synthetic test DXF + label data. Expand it as new edge cases are discovered rather than inventing ad-hoc fixtures in individual test files.

### General Principles

- All tests run with `pytest tests/ -v` — no external dependencies or markers required.
- Do not mock `cairosvg` in rasterisation tests; feed real PNG fixtures to the tiling logic instead.
