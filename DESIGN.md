# DXFPipeline — Design Document

## Purpose

Converts engineering P&ID drawings (DXF format) into web-ready map tiles and interactive hitbox data for the Leaflet-based defect viewer.

---

## Non-Functional Requirements

- **Resumable.** Stages can be re-run independently so engineers can iterate on label lists without re-rasterising tiles from scratch.
- **Minimal dependencies.** Only two Python packages (`ezdxf`, `Pillow`) plus a single external binary (Inkscape). No databases, no network calls.
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
  SVG vector file
    │
    ▼ Stage 2: rasterise_tiles.py
  PNG tile pyramid  ──►  tile_meta.json
    │
    ▼ Stage 3: extract_manifest.py
  hitboxes.json
```

Stages 1 and 3 both read the source DXF directly; they share no mutable state between them. Stage 3 depends on `tile_meta.json` (written by Stage 2) to convert DXF coordinates into Leaflet pixel space.

#### File Structure

```text
DXFPipeline/
├── pipeline/
│   ├── run_pipeline.py       # Orchestrator — runs all three stages
│   ├── render_svg.py         # Stage 1
│   ├── rasterise_tiles.py    # Stage 2
│   └── extract_manifest.py   # Stage 3
├── tests/
│   ├── test_run_pipeline.py  # Unit tests (no Inkscape required)
│   └── generate_test_dxf.py  # Synthetic test data generator
├── requirements.txt
└── pytest.ini
```

#### Pipeline Steps

**Stage 1 — DXF → SVG (`render_svg.py`)**

Parses the DXF file and emits a vector SVG. Entity bounding boxes are scanned directly; the DXF header extents (`$EXTMIN`/`$EXTMAX`) are not trusted. Text can optionally be converted to filled paths for faithful rasterisation downstream.

**Stage 2 — SVG → tile pyramid (`rasterise_tiles.py`)**

Shells out to Inkscape to render the SVG to a high-resolution PNG, then uses Pillow to slice it into a standard XYZ tile pyramid (256×256 tiles per zoom level). The maximum zoom level is chosen automatically so the drawing is at least 4096 px wide at full zoom. Emits `tile_meta.json` containing zoom range, pixel dimensions, and Leaflet coordinate bounds.

**Stage 3 — DXF + labels → hitboxes (`extract_manifest.py`)**

Re-reads the DXF to locate every TEXT/MTEXT entity. Matches entities against a user-supplied label list using a ranked strategy (exact → spatial cluster → inverted-T → range expansion → case-insensitive fallback). Computes axis-aligned bounding boxes for each matched label, converts them into Leaflet `CRS.Simple` coordinates using the scale from `tile_meta.json`, and writes `hitboxes.json`.

**Orchestrator (`run_pipeline.py`)**

Validates all inputs, then runs Stages 1–3 as subprocesses. Supports `--from-stage` to resume from a cached intermediate, and `--keep-work` to retain intermediates for inspection. Cleans up the `.work/` scratch directory on success.

#### Viewer Outputs

| File | Consumer |
| ---- | -------- |
| `tiles/{z}/{x}/{y}.png` | Leaflet tile layer |
| `hitboxes.json` | Leaflet interaction layer (clickable labels) |
| `tile_meta.json` | Viewer bootstrap (zoom levels, bounds, scale) |

---

### Key Technologies

| Technology | Role |
| ---------- | ---- |
| **ezdxf** | DXF parsing and SVG rendering |
| **Pillow** | PNG tiling (slice full image into XYZ pyramid) |
| **Inkscape CLI** | SVG → high-fidelity raster PNG (sole external binary) |
| **Leaflet CRS.Simple** | Coordinate system consumed by the viewer (no geographic projection) |

Inkscape is the only binary dependency. It is auto-detected from `PATH`; Windows fallback paths are tried if not found.

---

## Testing Methodology

Tests are written with **pytest** and split by whether they require Inkscape.

### Stage 1 — `render_svg.py`

Unit-testable without Inkscape. Use `generate_test_dxf.py` (or similar helpers) to produce minimal synthetic DXF fixtures, then assert on the output SVG:

- Correct `viewBox` values derived from entity extents (not DXF header).
- All expected entities present in the SVG output.
- Text-to-path flag produces `<path>` elements instead of `<text>`.
- Degenerate inputs (empty drawing, single entity) don't crash.

### Stage 2 — `rasterise_tiles.py`

Split into two layers:

- **Unit tests (no Inkscape):** Cover the tiling logic in isolation — zoom level auto-calculation, tile count at each zoom, `tile_meta.json` field values, coordinate bounds computation. Feed a pre-rendered PNG directly; do not call Inkscape.
- **Integration tests (`-m integration`):** Run the full stage against a known SVG, verify the tile pyramid structure (`tiles/{z}/{x}/{y}.png` files exist at every expected address) and that `tile_meta.json` is schema-valid.

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

Unit tests (already present) cover argument parsing, stage-selection logic, and prerequisite validation. These remain unit-only (no Inkscape).

### Fixtures and Helpers

`generate_test_dxf.py` is the canonical source of synthetic test DXF + label data. Expand it as new edge cases are discovered rather than inventing ad-hoc fixtures in individual test files.

### General Principles

- Unit tests must run without Inkscape (`-m "not integration"`) — these are the CI gate.
- Integration tests require Inkscape on `PATH` and are run locally or on a capable CI agent.
- Do not mock Inkscape in integration tests; the rasterisation path is a known failure point and a mock would not catch regressions there.
