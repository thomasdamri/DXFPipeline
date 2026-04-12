# render_svg.py
# Renders a DXF to SVG, with optional per-theme colour overrides.
#
# Typical pipeline:
#   1. python render_svg.py    input.dxf              → drawing.svg
#   2. python rasterise_tiles.py --svg drawing.svg    → tiles/ + tile_meta.json
#   3. python extract_manifest.py --dxf input.dxf \  → hitboxes.json
#                                  --labels labels.txt \
#                                  --tile-meta tile_meta.json
#
# Usage:
#   python render_svg.py input.dxf [output.svg] [--text-to-path]
#                                  [--themes-config themes.json]
#
#   --text-to-path      Convert all DXF text/MTEXT to filled outline paths in the
#                       SVG rather than <text> elements.  Use this when your DXF
#                       fonts are not available on the viewing machine, or when you
#                       need pixel-accurate glyph rendering.  Note: extract_manifest
#                       cannot read paths as text, so it will fall back to DXF
#                       coordinate matching (unaffected by this flag).
#
#   --themes-config     JSON file defining one or more named themes.  Each theme
#                       specifies a background colour and optional per-layer colour
#                       overrides.  When provided, one SVG is rendered per theme
#                       and named <output_stem>_<theme>.svg.  If omitted, a single
#                       SVG is rendered with default DXF colours.
#
# themes.json example:
#   {
#     "light": { "background": "#FFFFFF", "layers": { "Pipes": "#000000" } },
#     "dark":  { "background": "#1A1A2E", "layers": { "Pipes": "#E0E0E0" } }
#   }
#
# Outputs:
#   output.svg (or output_<theme>.svg per theme)  -- vector SVG via ezdxf SVGBackend
#   svg_manifest.json                             -- list of rendered (theme, svg, background)

import argparse
import json
import sys
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.layout import Margins, Page, Settings, Units
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.bbox import extents as bbox_extents

# ---- CLI --------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Render DXF to SVG with optional theme colour overrides."
)
parser.add_argument("dxf", nargs="?", default="test_diagram.dxf",
                    help="Input DXF file (default: test_diagram.dxf)")
parser.add_argument("svg", nargs="?", default=None,
                    help="Output SVG file (default: <dxf_stem>.svg)")
parser.add_argument("--text-to-path", action="store_true",
                    help="Convert text/MTEXT to outline paths instead of "
                         "<text> elements (font-independent, path-accurate)")
parser.add_argument("--themes-config", default=None, metavar="FILE",
                    help="JSON file with per-theme background + layer colours. "
                         "Renders one SVG per theme when provided.")
args = parser.parse_args()

dxf_path     = args.dxf
svg_path     = args.svg or (dxf_path.rsplit(".", 1)[0] + ".svg")
text_to_path = args.text_to_path

print(f"DXF          : {dxf_path}")
print(f"SVG base     : {svg_path}")
print(f"text-to-path : {text_to_path}")
if args.themes_config:
    print(f"themes-config: {args.themes_config}")


# ---- Helpers ----------------------------------------------------------------

def _hex_to_rgb(hex_str: str) -> tuple:
    """Convert '#RRGGBB' (or 'RRGGBB') to an (R, G, B) int tuple."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _apply_theme(doc, theme_cfg: dict) -> None:
    """Mutate *doc* in-place to apply background and layer colour overrides."""
    import ezdxf.addons.drawing.properties as _props
    if "background" in theme_cfg:
        _props.MODEL_SPACE_BG_COLOR = theme_cfg["background"]
    for layer_name, hex_color in theme_cfg.get("layers", {}).items():
        if doc.layers.has_entry(layer_name):
            doc.layers.get(layer_name).rgb = _hex_to_rgb(hex_color)
        else:
            print(
                f"WARNING: layer '{layer_name}' not found in DXF "
                "— colour override skipped",
                file=sys.stderr,
            )


def _make_settings(text_to_path: bool) -> Settings:
    """Build render Settings, handling older ezdxf versions gracefully."""
    if not text_to_path:
        return Settings()
    # Modern API (ezdxf >= 1.1): Settings(text_policy=TextPolicy.FILLING)
    try:
        from ezdxf.addons.drawing.properties import TextPolicy
        print("text-to-path : using TextPolicy.FILLING")
        return Settings(text_policy=TextPolicy.FILLING)
    except (ImportError, AttributeError, TypeError):
        pass
    # Older API: Settings(text_as_paths=True)
    try:
        s = Settings(text_as_paths=True)
        print("text-to-path : using text_as_paths=True")
        return s
    except TypeError:
        pass
    # Last resort: show_text attribute
    s = Settings()
    if hasattr(s, "show_text"):
        s.show_text = False
        print("text-to-path : using show_text=False (fallback)")
    else:
        print("WARNING: text-to-path not supported by this ezdxf version -- "
              "text will remain as <text> elements.", file=sys.stderr)
    return s


def _render_one(dxf_path: str, svg_out: str, theme_cfg, text_to_path: bool) -> None:
    """Load DXF, optionally apply theme colours, render to SVG, write file."""
    doc = ezdxf.readfile(dxf_path)   # fresh load ensures no cross-theme mutation
    msp = doc.modelspace()

    if theme_cfg is not None:
        _apply_theme(doc, theme_cfg)

    # DXF extents via entity bbox scan (never trust $EXTMIN/$EXTMAX)
    print("  Scanning entity extents...")
    bbox = bbox_extents(msp)
    if bbox is None or not bbox.has_data:
        sys.exit("ERROR: Could not determine drawing extents")

    dxf_x_min, dxf_y_min = bbox.extmin.x, bbox.extmin.y
    dxf_x_max, dxf_y_max = bbox.extmax.x, bbox.extmax.y
    dxf_w = dxf_x_max - dxf_x_min
    dxf_h = dxf_y_max - dxf_y_min
    print(f"  DXF extents  : x=[{dxf_x_min:.4f}, {dxf_x_max:.4f}]  "
          f"y=[{dxf_y_min:.4f}, {dxf_y_max:.4f}]")
    print(f"  DXF size     : {dxf_w:.4f} x {dxf_h:.4f} units")

    settings = _make_settings(text_to_path)

    print("  Rendering SVG...")
    ctx     = RenderContext(doc)
    backend = SVGBackend()
    Frontend(ctx, backend).draw_layout(msp)

    page       = Page(0, 0, Units.mm, Margins(0, 0, 0, 0))
    svg_string = backend.get_string(page, settings=settings)

    with open(svg_out, "w", encoding="utf-8") as f:
        f.write(svg_string)
    print(f"  SVG written  : {svg_out}")


# ---- Theme loading ----------------------------------------------------------

themes_config = None
if args.themes_config:
    with open(args.themes_config, encoding="utf-8") as f:
        themes_config = json.load(f)

# ---- Render pass(es) --------------------------------------------------------

manifest = []

if themes_config:
    base = svg_path.rsplit(".", 1)[0]
    ext  = "." + svg_path.rsplit(".", 1)[1] if "." in svg_path else ".svg"
    for theme_name, theme_cfg in themes_config.items():
        if theme_name.startswith("_"):
            continue   # skip metadata/comment keys
        theme_svg = f"{base}_{theme_name}{ext}"
        print(f"\n── Theme: {theme_name} ───────────────────────────────────────────")
        _render_one(dxf_path, theme_svg, theme_cfg, text_to_path)
        manifest.append({
            "theme":      theme_name,
            "svg":        str(Path(theme_svg).resolve()),
            "background": theme_cfg.get("background", "#ffffff"),
        })
else:
    print()
    _render_one(dxf_path, svg_path, None, text_to_path)
    manifest = [{"theme": None, "svg": str(Path(svg_path).resolve()), "background": "#ffffff"}]

# ---- Write svg_manifest.json ------------------------------------------------

manifest_path = Path(svg_path).parent / "svg_manifest.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print(f"\nSVG manifest : {manifest_path}")

# ---- Next step hint ---------------------------------------------------------

print()
print("── Next step ─────────────────────────────────────────────────────────")
if len(manifest) == 1 and manifest[0]["theme"] is None:
    print(f"  python rasterise_tiles.py --svg {manifest[0]['svg']}")
else:
    for entry in manifest:
        print(f"  python rasterise_tiles.py --svg {entry['svg']} "
              f"--theme {entry['theme']} --bg-color \"{entry['background']}\"")
print()
print("  rasterise_tiles.py will:")
print("    • rasterise the SVG to a high-res PNG tile pyramid (tiles/)")
print("    • write tile_meta.json for DXFViewer")
