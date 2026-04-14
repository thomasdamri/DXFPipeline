"""
types.py
────────
Shared TypedDict definitions for the DXFPipeline JSON interchange formats.

These types document and enforce the shapes of the files passed between
pipeline stages and consumed by the viewer:

  tile_meta.json  — written by rasterise_tiles.py, read by extract_manifest.py
  hitboxes.json   — written by extract_manifest.py, read by the Leaflet viewer
"""

from __future__ import annotations

from typing import TypedDict


class TileMeta(TypedDict):
    """Schema for tile_meta.json."""
    max_zoom:       int
    tile_size:      int
    full_width_px:  int
    full_height_px: int
    leaflet_bounds: list[list[float]]  # [[lat_min, lng_min], [lat_max, lng_max]]


class LatLng(TypedDict):
    """A Leaflet CRS.Simple coordinate pair."""
    lat: float
    lng: float


class HitboxBbox(TypedDict):
    """Bounding box for a hitbox entry, expressed as four Leaflet corner points."""
    leaflet: dict[str, list[LatLng]]  # {"corners": [TL, TR, BR, BL]}


class HitboxRecord(TypedDict):
    """One entry in hitboxes.json."""
    label: str
    found: bool
    leaflet: LatLng
    bbox:    HitboxBbox


class ThemeConfig(TypedDict, total=False):
    """One theme entry from themes.json."""
    background: str               # hex colour, e.g. "#FFFFFF"
    layers:     dict[str, str]    # layer name → hex colour override


# The full themes.json structure: theme name → config
ThemesConfig = dict[str, ThemeConfig]
