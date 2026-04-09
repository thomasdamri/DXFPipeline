"""
Unit tests for extract_manifest.py (Stage 3).

Covers every matching strategy, the coordinate transform chain,
bounding-box computation, and hitbox output shape.
No Inkscape required.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))

from extract_manifest import (  # noqa: E402
    CoordTransform,
    build_cluster_index,
    build_clusters,
    build_hitboxes,
    build_text_index,
    compute_dxf_bbox,
    match_labels,
)


# ─────────────────────────────────────────────────────────────
# Helper: synthetic entity dict
# ─────────────────────────────────────────────────────────────

def make_entity(text, x=0.0, y=0.0, height=2.5, halign=0, valign=0,
                rotation=0.0, layer="TEXT-ALL", etype="TEXT",
                width_factor=1.0, handle="A1"):
    return {
        "handle":       handle,
        "type":         etype,
        "text":         text,
        "layer":        layer,
        "insert":       [x, y],
        "rotation":     rotation,
        "height":       height,
        "style":        "STANDARD",
        "halign":       halign,
        "valign":       valign,
        "width_factor": width_factor,
    }


# ─────────────────────────────────────────────────────────────
# compute_dxf_bbox
# ─────────────────────────────────────────────────────────────

class TestComputeDxfBbox:
    def test_zero_height_returns_none(self):
        e = make_entity("ABC", height=0.0)
        assert compute_dxf_bbox(e) is None

    def test_returns_dict_with_expected_keys(self):
        bb = compute_dxf_bbox(make_entity("X", height=5.0))
        assert bb is not None
        for key in ("x", "y", "width", "height", "cx", "cy", "rotation", "corners"):
            assert key in bb

    def test_has_4_corners(self):
        bb = compute_dxf_bbox(make_entity("X", height=5.0))
        assert len(bb["corners"]) == 4

    def test_left_aligned_starts_at_insert(self):
        # halign=0 (Left): bbox starts at or just left of insert x (pad only)
        e = make_entity("FV", x=10.0, y=0.0, height=5.0, halign=0)
        bb = compute_dxf_bbox(e)
        # x should start just before insert (pad = 5.0 * 0.12 = 0.6)
        assert bb["x"] < 10.0
        assert bb["x"] + bb["width"] > 10.0
        assert bb["width"] > 0

    def test_center_aligned_straddles_insert(self):
        # halign=1 (Center): bbox straddles insert x
        e = make_entity("ABC", x=50.0, y=0.0, height=5.0, halign=1)
        bb = compute_dxf_bbox(e)
        assert bb["x"] < 50.0
        assert bb["x"] + bb["width"] > 50.0

    def test_right_aligned_ends_near_insert(self):
        # halign=2 (Right): right edge of bbox should be near insert x + small pad
        e = make_entity("ABC", x=50.0, y=0.0, height=5.0, halign=2)
        bb = compute_dxf_bbox(e)
        pad = 5.0 * 0.12
        assert bb["x"] + bb["width"] <= 50.0 + pad + 0.01
        assert bb["x"] < 50.0

    def test_baseline_valign_has_descender_space(self):
        # valign=0 (Baseline): y_min = insert_y - h*0.2 - pad → below insert y
        e = make_entity("g", x=0.0, y=0.0, height=10.0, valign=0)
        bb = compute_dxf_bbox(e)
        assert bb["y"] < 0.0

    def test_top_valign_top_edge_near_insert(self):
        # valign=3 (Top): bbox lies below insert y (ly_min=-h, ly_max=0)
        e = make_entity("X", x=0.0, y=0.0, height=10.0, valign=3)
        bb = compute_dxf_bbox(e)
        pad = 10.0 * 0.12
        # Top edge should be near 0 + pad
        assert bb["y"] + bb["height"] < pad + 0.01
        assert bb["y"] < 0.0

    def test_rotation_enlarges_axis_aligned_bbox(self):
        # A 45° rotated text string has a larger AABB than the same text upright
        e_0   = make_entity("ABCD", x=0.0, y=0.0, height=5.0, rotation=0)
        e_45  = make_entity("ABCD", x=0.0, y=0.0, height=5.0, rotation=45)
        bb_0  = compute_dxf_bbox(e_0)
        bb_45 = compute_dxf_bbox(e_45)
        area_0  = bb_0["width"]  * bb_0["height"]
        area_45 = bb_45["width"] * bb_45["height"]
        assert area_45 > area_0

    def test_positive_width_and_height(self):
        bb = compute_dxf_bbox(make_entity("Hello", height=3.0))
        assert bb["width"]  > 0
        assert bb["height"] > 0


# ─────────────────────────────────────────────────────────────
# CoordTransform
# ─────────────────────────────────────────────────────────────

DXF_EXTENTS = {
    "x_min": 0.0, "y_min": 0.0,
    "x_max": 200.0, "y_max": 100.0,
    "width": 200.0, "height": 100.0,
}


class TestCoordTransform:
    """
    minimal_tile_meta: full_w=1024, full_h=512, tile_sz=256
      → short_px=512, coord_w=512, coord_h=256
    DXF extents: x=[0,200], y=[0,100]
      → scale_x = 512/200 = 2.56
      → scale_y = 256/100 = 2.56
    """

    @pytest.fixture()
    def tf(self, minimal_tile_meta):
        return CoordTransform.from_tile_meta(DXF_EXTENTS, minimal_tile_meta)

    def test_scale_x(self, tf):
        assert tf.scale_x == pytest.approx(512.0 / 200.0)

    def test_scale_y(self, tf):
        assert tf.scale_y == pytest.approx(256.0 / 100.0)

    def test_dxf_origin_maps_to_png_bottom_left(self, tf):
        px, py = tf.dxf_to_png(0.0, 0.0)
        assert px == pytest.approx(0.0)
        assert py == pytest.approx(256.0)

    def test_dxf_max_corner_maps_to_png_top_right(self, tf):
        px, py = tf.dxf_to_png(200.0, 100.0)
        assert px == pytest.approx(512.0)
        assert py == pytest.approx(0.0)

    def test_dxf_to_leaflet_lat_is_negative_py(self, tf):
        ll = tf.dxf_to_leaflet(0.0, 0.0)
        assert ll["lat"] == pytest.approx(-256.0)
        assert ll["lng"] == pytest.approx(0.0)

    def test_dxf_bbox_to_leaflet_has_bounds_center_corners(self, tf):
        # Minimal axis-aligned bbox (no rotation needed — corners supplied directly)
        dxf_bbox = {"corners": [[0, 0], [10, 0], [10, 5], [0, 5]]}
        result = tf.dxf_bbox_to_leaflet(dxf_bbox)
        assert "bounds"  in result
        assert "center"  in result
        assert "corners" in result
        assert len(result["corners"]) == 4

    def test_leaflet_bounds_matches_png_dimensions(self, tf):
        # leaflet_bounds = [[-png_h, 0], [0, png_w]]
        bounds = tf.leaflet_bounds()
        assert bounds[0][0] == pytest.approx(-256.0)
        assert bounds[0][1] == pytest.approx(0.0)
        assert bounds[1][0] == pytest.approx(0.0)
        assert bounds[1][1] == pytest.approx(512.0)


class TestCoordTransformEndToEnd:
    """Known DXF coord → expected Leaflet {lat, lng} with fixed tile_meta."""

    def test_centre_point(self, minimal_tile_meta):
        # DXF (100, 50) is the centre of the [0,200]×[0,100] extents.
        # px = (100-0)*2.56 = 256;  py = 256 - (50-0)*2.56 = 256-128 = 128
        # lat = -128;  lng = 256
        tf = CoordTransform.from_tile_meta(DXF_EXTENTS, minimal_tile_meta)
        ll = tf.dxf_to_leaflet(100.0, 50.0)
        assert ll["lat"] == pytest.approx(-128.0, abs=0.01)
        assert ll["lng"] == pytest.approx(256.0,  abs=0.01)

    def test_top_left_is_leaflet_origin(self, minimal_tile_meta):
        # DXF top-left = (x_min, y_max) = (0, 100)
        # px=0, py=256-(100)*2.56=0 → lat=0, lng=0
        tf = CoordTransform.from_tile_meta(DXF_EXTENTS, minimal_tile_meta)
        ll = tf.dxf_to_leaflet(0.0, 100.0)
        assert ll["lat"] == pytest.approx(0.0, abs=0.01)
        assert ll["lng"] == pytest.approx(0.0, abs=0.01)

    def test_bottom_right_is_full_extent(self, minimal_tile_meta):
        # DXF bottom-right = (200, 0)
        # px=512, py=256 → lat=-256, lng=512
        tf = CoordTransform.from_tile_meta(DXF_EXTENTS, minimal_tile_meta)
        ll = tf.dxf_to_leaflet(200.0, 0.0)
        assert ll["lat"] == pytest.approx(-256.0, abs=0.01)
        assert ll["lng"] == pytest.approx(512.0,  abs=0.01)


# ─────────────────────────────────────────────────────────────
# build_clusters
# ─────────────────────────────────────────────────────────────

class TestBuildClusters:
    def test_empty_input_returns_empty(self):
        assert build_clusters([]) == []

    def test_two_isolated_entities_not_clustered(self):
        e1 = make_entity("FV",  x=0,    y=0,    height=2.5, handle="h1")
        e2 = make_entity("101", x=1000, y=1000, height=2.5, handle="h2")
        assert build_clusters([e1, e2]) == []

    def test_two_nearby_entities_form_cluster(self):
        # dy=3, height=2.5 → threshold = 3.5×2.5 = 8.75; dy=3 < 8.75 ✓
        # dx=0, h_tolerance threshold = 2.5×2.5 = 6.25; dx=0 < 6.25 ✓
        e1 = make_entity("FV",  x=0, y=3.0, height=2.5, handle="h1")
        e2 = make_entity("101", x=0, y=0.0, height=2.5, handle="h2")
        clusters = build_clusters([e1, e2])
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_cluster_sorted_top_first(self):
        # Higher y → higher on page → should appear first in cluster
        e1 = make_entity("FV",  x=0, y=5.0, height=2.5, handle="h1")
        e2 = make_entity("101", x=0, y=0.0, height=2.5, handle="h2")
        clusters = build_clusters([e1, e2])
        assert clusters[0][0]["text"] == "FV"

    def test_three_entities_transitive_cluster(self):
        # e1-e2 and e2-e3 are near each other → all three in one cluster
        e1 = make_entity("A", x=0, y=6.0, height=2.5, handle="h1")
        e2 = make_entity("B", x=0, y=3.0, height=2.5, handle="h2")
        e3 = make_entity("C", x=0, y=0.0, height=2.5, handle="h3")
        clusters = build_clusters([e1, e2, e3])
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_horizontal_distance_prevents_clustering(self):
        # dy ≈ 2 (within vertical threshold), dx=100 >> h_tolerance threshold
        e1 = make_entity("FV",  x=0,   y=2.0, height=2.5, handle="h1")
        e2 = make_entity("101", x=100, y=0.0, height=2.5, handle="h2")
        assert build_clusters([e1, e2]) == []

    def test_single_entity_not_returned(self):
        # build_clusters only returns clusters with >= 2 members
        e = make_entity("FV101", x=0, y=0, height=2.5)
        assert build_clusters([e]) == []

    def test_vertical_gap_too_large_prevents_clustering(self):
        # dy=20, threshold=8.75 → no cluster
        e1 = make_entity("FV",  x=0, y=20.0, height=2.5, handle="h1")
        e2 = make_entity("101", x=0, y=0.0,  height=2.5, handle="h2")
        assert build_clusters([e1, e2]) == []


# ─────────────────────────────────────────────────────────────
# build_cluster_index
# ─────────────────────────────────────────────────────────────

class TestBuildClusterIndex:
    def test_two_entity_cluster_both_separator_forms(self):
        e1 = make_entity("FV",  x=0, y=3.0, height=2.5, handle="h1")
        e2 = make_entity("501", x=0, y=0.0, height=2.5, handle="h2")
        idx = build_cluster_index([e1, e2])
        assert "FV501"   in idx
        assert "FV 501"  in idx

    def test_two_entity_cluster_uppercase_key_present(self):
        e1 = make_entity("fv",  x=0, y=3.0, height=2.5, handle="h1")
        e2 = make_entity("501", x=0, y=0.0, height=2.5, handle="h2")
        idx = build_cluster_index([e1, e2])
        # Upper-case variant is always indexed
        assert "FV501" in idx or "fv501" in idx   # at least one variant

    def test_inverted_t_indexed_per_leaf(self):
        # top="FV" at y=8, two leaves at y=0
        e_top   = make_entity("FV", x=0,  y=8.0, height=2.5, handle="ht")
        e_left  = make_entity("12", x=-5, y=0.0, height=2.5, handle="hl")
        e_right = make_entity("54", x=5,  y=0.0, height=2.5, handle="hr")
        idx = build_cluster_index([e_top, e_left, e_right])
        assert "FV12" in idx
        assert "FV54" in idx

    def test_inverted_t_spaced_forms_indexed(self):
        e_top   = make_entity("FV", x=0,  y=8.0, height=2.5, handle="ht")
        e_left  = make_entity("12", x=-5, y=0.0, height=2.5, handle="hl")
        e_right = make_entity("54", x=5,  y=0.0, height=2.5, handle="hr")
        idx = build_cluster_index([e_top, e_left, e_right])
        assert "FV 12" in idx
        assert "FV 54" in idx

    def test_range_expression_expanded(self):
        # Use h_tolerance=5.0 because "18M TO 20M" is a wide string whose bbox
        # centre is offset from x=0 further than the default 2.5× threshold allows.
        e_top   = make_entity("FV",         x=0, y=8.0, height=2.5, handle="ht")
        e_range = make_entity("18M TO 20M", x=0, y=0.0, height=2.5, handle="hr")
        idx = build_cluster_index([e_top, e_range], h_tolerance=5.0)
        assert "FV18M" in idx
        assert "FV19M" in idx
        assert "FV20M" in idx

    def test_range_bare_top_token_indexed(self):
        e_top   = make_entity("FV",         x=0, y=8.0, height=2.5, handle="ht")
        e_range = make_entity("18M TO 20M", x=0, y=0.0, height=2.5, handle="hr")
        idx = build_cluster_index([e_top, e_range], h_tolerance=5.0)
        # Bare top token is also a standalone label candidate
        assert "FV" in idx

    def test_empty_entities_returns_empty(self):
        assert build_cluster_index([]) == {}


# ─────────────────────────────────────────────────────────────
# match_labels
# ─────────────────────────────────────────────────────────────

class TestMatchLabels:
    """All tests use transform=None to avoid Inkscape dependency."""

    def test_exact_match_found(self):
        e = make_entity("FV101", x=100, y=200, height=2.5, handle="A1")
        dxf_index = build_text_index([e])
        ci = build_cluster_index([e])
        result = match_labels(["FV101"], dxf_index, ci, [], transform=None)
        assert result["FV101"]["found"] is True
        assert result["FV101"]["fuzzy_match"] is False

    def test_exact_match_coords_none_without_transform(self):
        e = make_entity("FV101", x=100, y=200, height=2.5, handle="A1")
        dxf_index = build_text_index([e])
        ci = build_cluster_index([e])
        result = match_labels(["FV101"], dxf_index, ci, [], transform=None)
        assert result["FV101"]["coords"] is None

    def test_spatial_cluster_match(self):
        e1 = make_entity("FV",  x=0, y=3.0, height=2.5, handle="h1")
        e2 = make_entity("501", x=0, y=0.0, height=2.5, handle="h2")
        dxf_index = build_text_index([e1, e2])
        ci = build_cluster_index([e1, e2])
        result = match_labels(["FV501"], dxf_index, ci, [], transform=None)
        assert result["FV501"]["found"] is True
        assert result["FV501"].get("clustered") is True

    def test_inverted_t_produces_two_hitboxes(self):
        e_top   = make_entity("FV", x=0,  y=8.0, height=2.5, handle="ht")
        e_left  = make_entity("12", x=-5, y=0.0, height=2.5, handle="hl")
        e_right = make_entity("54", x=5,  y=0.0, height=2.5, handle="hr")
        dxf_index = build_text_index([e_top, e_left, e_right])
        ci = build_cluster_index([e_top, e_left, e_right])
        result = match_labels(["FV12", "FV54"], dxf_index, ci, [], transform=None)
        assert result["FV12"]["found"] is True
        assert result["FV54"]["found"] is True

    def test_range_expansion_all_steps_found(self):
        e_top   = make_entity("FV",         x=0, y=8.0, height=2.5, handle="ht")
        e_range = make_entity("18M TO 20M", x=0, y=0.0, height=2.5, handle="hr")
        dxf_index = build_text_index([e_top, e_range])
        # Use h_tolerance=5.0 so the wide range entity clusters with "FV"
        ci = build_cluster_index([e_top, e_range], h_tolerance=5.0)
        result = match_labels(
            ["FV18M", "FV19M", "FV20M"], dxf_index, ci, [], transform=None
        )
        for label in ["FV18M", "FV19M", "FV20M"]:
            assert result[label]["found"] is True, f"{label} not found"

    def test_case_insensitive_fallback(self):
        # DXF entity has lowercase text; target label is uppercase
        e = make_entity("fv111", x=0, y=0, height=2.5, handle="A1")
        dxf_index = build_text_index([e])
        ci = build_cluster_index([e])
        result = match_labels(["FV111"], dxf_index, ci, [], transform=None)
        assert result["FV111"]["found"] is True
        assert result["FV111"]["fuzzy_match"] is True

    def test_no_match_entry_present_not_crash(self):
        dxf_index = build_text_index([])
        ci = build_cluster_index([])
        result = match_labels(["XX999"], dxf_index, ci, [], transform=None)
        assert "XX999" in result
        assert result["XX999"]["found"] is False
        assert result["XX999"]["coords"] is None

    def test_multiple_labels_all_processed(self):
        e1 = make_entity("FV101", x=0,  y=0, height=2.5, handle="h1")
        e2 = make_entity("HV201", x=50, y=0, height=2.5, handle="h2")
        dxf_index = build_text_index([e1, e2])
        ci = build_cluster_index([e1, e2])
        result = match_labels(
            ["FV101", "HV201", "XX999"], dxf_index, ci, [], transform=None
        )
        assert result["FV101"]["found"] is True
        assert result["HV201"]["found"] is True
        assert result["XX999"]["found"] is False


# ─────────────────────────────────────────────────────────────
# build_hitboxes
# ─────────────────────────────────────────────────────────────

def _found_labels_dict():
    """Minimal labels dict with one found entry (non-None coords)."""
    return {
        "FV101": {
            "text":        "FV101",
            "found":       True,
            "duplicate":   False,
            "fuzzy_match": False,
            "clustered":   False,
            "cluster_parts": [],
            "dxf": {
                "handle": "A1", "type": "TEXT",
                "insert": [0.0, 0.0], "rotation": 0.0,
                "height": 2.5, "layer": "TAGS",
                "style": "STANDARD", "halign": 0, "valign": 0,
                "width_factor": 1.0,
            },
            "coords": {
                "dxf":     {"x": 0.0, "y": 0.0},
                "png":     {"x": 0.0, "y": 256.0},
                "leaflet": {"lat": -256.0, "lng": 0.0},
                "bbox":    None,
            },
            "all_dxf_matches": [],
            "meta": {},
        }
    }


class TestBuildHitboxes:
    def test_found_entry_included(self):
        result = build_hitboxes(_found_labels_dict())
        assert len(result) == 1
        assert result[0]["label"] == "FV101"
        assert result[0]["found"] is True

    def test_not_found_entry_excluded(self):
        labels = {
            "XX999": {
                "text": "XX999", "found": False, "duplicate": False,
                "fuzzy_match": False, "dxf": None, "coords": None,
                "all_dxf_matches": [], "meta": {},
            }
        }
        assert build_hitboxes(labels) == []

    def test_found_with_none_coords_excluded(self):
        # build_hitboxes skips entries where coords is None
        labels = {
            "FV101": {
                "text": "FV101", "found": True, "duplicate": False,
                "fuzzy_match": False,
                "dxf": {
                    "handle": "A1", "type": "TEXT",
                    "insert": [0, 0], "rotation": 0, "height": 2.5,
                    "layer": "TAGS", "style": None, "halign": 0, "valign": 0,
                    "width_factor": 1.0,
                },
                "coords": None,
                "all_dxf_matches": [], "meta": {},
            }
        }
        assert build_hitboxes(labels) == []

    def test_hitbox_shape_has_required_keys(self):
        hb = build_hitboxes(_found_labels_dict())[0]
        for key in ("label", "found", "dxf", "leaflet", "bbox", "meta"):
            assert key in hb, f"Missing hitbox key: {key}"

    def test_meta_subkeys(self):
        hb = build_hitboxes(_found_labels_dict())[0]
        meta = hb["meta"]
        for key in ("layer", "type", "handle", "duplicate", "fuzzy_match",
                    "clustered", "cluster_parts"):
            assert key in meta, f"Missing meta key: {key}"
