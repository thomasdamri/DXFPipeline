"""
generate_test_dxf.py
────────────────────
Generates a synthetic P&ID-style DXF that exercises all the matching scenarios
in extract_manifest.py:

  • Exact matches        – full label text in a single TEXT entity
  • Split-label clusters – label split across two TEXT entities nearby each other
  • Duplicates           – same label appears on two different layers
  • Unmatched            – labels in the target list with NO DXF text at all
  • Noise text           – annotation text that should NOT match anything
  • 3-fragment clusters  – top token + two tokens side-by-side below (inverted-T layout)
                           e.g. "FV" above, then "12" and "54" on the row below

Run:
    pip install ezdxf
    python generate_test_dxf.py
    # Outputs: test_diagram.dxf  and  test_labels.txt

Then test the extractor:
    python extract_manifest.py \
        --dxf test_diagram.dxf \
        --labels test_labels.txt \
        --cluster-radius 60 \
        --out test_manifest.json \
        --verbose
"""

import math
import random
import ezdxf
from ezdxf.enums import TextEntityAlignment

# ── Reproducibility ──────────────────────────────────────────────────────────
random.seed(42)

# ── DXF setup ────────────────────────────────────────────────────────────────
doc = ezdxf.new("R2010")
msp = doc.modelspace()

# Layer definitions
LAYERS = {
    "SYS-HVAC":   {"color": 3},   # green   – HVAC system icons
    "SYS-PIPING": {"color": 5},   # blue    – piping system icons
    "SYS-ELEC":   {"color": 2},   # yellow  – electrical system icons
    "TEXT-ALL":   {"color": 7},   # white   – all label text (separate layer)
    "ANNO":       {"color": 8},   # grey    – general annotation
}
for name, props in LAYERS.items():
    doc.layers.new(name=name, dxfattribs={"color": props["color"]})

TEXT_HEIGHT = 2.5   # model-space units
SPLIT_OFFSET = TEXT_HEIGHT * 1.2   # vertical gap between split fragments


# ── Helper: add a simple rectangular "fitting" icon ──────────────────────────
def add_fitting_icon(msp, x, y, layer, size=5.0):
    """Draw a small rectangle to represent a CAD fitting on a system layer."""
    pts = [
        (x - size/2, y - size/2),
        (x + size/2, y - size/2),
        (x + size/2, y + size/2),
        (x - size/2, y + size/2),
        (x - size/2, y - size/2),
    ]
    msp.add_lwpolyline(pts, dxfattribs={"layer": layer})


def add_text(msp, text, x, y, layer, height=TEXT_HEIGHT):
    msp.add_text(
        text,
        dxfattribs={
            "layer":  layer,
            "height": height,
            "insert": (x, y),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO A – EXACT MATCH
# 10 fittings whose label exists as a single TEXT entity on TEXT-ALL
# ─────────────────────────────────────────────────────────────────────────────
exact_fittings = [
    ("FV101", "SYS-PIPING",   100,  100),
    ("FV102", "SYS-PIPING",   200,  100),
    ("FV103", "SYS-PIPING",   300,  100),
    ("HV201", "SYS-HVAC",     100,  200),
    ("HV202", "SYS-HVAC",     200,  200),
    ("HV203", "SYS-HVAC",     300,  200),
    ("EV301", "SYS-ELEC",     100,  300),
    ("EV302", "SYS-ELEC",     200,  300),
    ("EV303", "SYS-ELEC",     300,  300),
    ("CV401", "SYS-PIPING",   400,  100),
]

for label, layer, x, y in exact_fittings:
    add_fitting_icon(msp, x, y, layer)
    # Label text slightly above the icon, on the shared TEXT-ALL layer
    add_text(msp, label, x, y + 8, "TEXT-ALL")


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO B – SPLIT LABELS  (two TEXT entities nearby → cluster should join them)
# Label is split as "PREFIX" on top row and "NUMBER" on row below
# ─────────────────────────────────────────────────────────────────────────────
split_fittings = [
    ("FV", "501", "SYS-PIPING",   500,  100),
    ("FV", "502", "SYS-PIPING",   600,  100),
    ("HV", "601", "SYS-HVAC",     500,  200),
    ("HV", "602", "SYS-HVAC",     600,  200),
    ("EV", "701", "SYS-ELEC",     500,  300),
    ("EV", "702", "SYS-ELEC",     600,  300),
    ("PSV","801", "SYS-PIPING",   700,  100),   # 3-char prefix
    ("PSV","802", "SYS-PIPING",   800,  100),
    ("TCV","901", "SYS-PIPING",   700,  200),
    ("TCV","902", "SYS-PIPING",   800,  200),
]

split_labels = []  # collect the full labels so we can write the label file
for prefix, number, layer, x, y in split_fittings:
    full_label = f"{prefix}{number}"
    split_labels.append(full_label)
    add_fitting_icon(msp, x, y, layer)
    # Top fragment: prefix, slight jitter so it's not pixel-perfect aligned
    jitter_x = random.uniform(-2.0, 2.0)
    add_text(msp, prefix, x + jitter_x,          y + 8,                "TEXT-ALL")
    add_text(msp, number, x + jitter_x + 1.0,    y + 8 - SPLIT_OFFSET, "TEXT-ALL")


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO C – DUPLICATE LABELS  (same label on two different layers)
# ─────────────────────────────────────────────────────────────────────────────
duplicate_fittings = [
    ("DV001", "SYS-PIPING", 100, 400),
    ("DV002", "SYS-HVAC",   200, 400),
    ("DV003", "SYS-ELEC",   300, 400),
]

duplicate_labels = []
for label, layer, x, y in duplicate_fittings:
    duplicate_labels.append(label)
    # Icon on primary layer
    add_fitting_icon(msp, x, y, layer)
    # Label appears on TEXT-ALL …
    add_text(msp, label, x, y + 8, "TEXT-ALL")
    # … and also accidentally on ANNO (stale copy from a revision)
    add_text(msp, label, x + 400, y + 8, "ANNO")


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO D – INTENTIONALLY UNMATCHED
# Labels in the target list that simply don't appear in the DXF at all
# ─────────────────────────────────────────────────────────────────────────────
unmatched_labels = [
    "XX999", "YY888", "ZZ777", "AA001", "BB002",
]


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO E – NOISE / ANNOTATION TEXT  (not in target list, should be ignored)
# ─────────────────────────────────────────────────────────────────────────────
noise_texts = [
    ("NORTH",          50,  50,  "ANNO"),
    ("REV A",          50,  30,  "ANNO"),
    ("DO NOT SCALE",   50,  10,  "ANNO"),
    ("Sheet 1 of 4",  900,  10,  "ANNO"),
    ("P&ID - AREA 5", 450, 450,  "ANNO"),
    ("SEE DWG 12345", 200, 450,  "ANNO"),
    ("ISSUED FOR REVIEW", 600, 450, "ANNO"),
]

for text, x, y, layer in noise_texts:
    add_text(msp, text, x, y, layer, height=TEXT_HEIGHT * 1.5)


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO F – CASE MISMATCH (fuzzy match test)
# Label in DXF is lowercase; target list has uppercase
# ─────────────────────────────────────────────────────────────────────────────
case_fittings = [
    ("fv111", "FV111", "SYS-PIPING", 900, 100),
    ("hv211", "HV211", "SYS-HVAC",   900, 200),
]
case_labels = []
for dxf_text, target_label, layer, x, y in case_fittings:
    case_labels.append(target_label)
    add_fitting_icon(msp, x, y, layer)
    add_text(msp, dxf_text, x, y + 8, "TEXT-ALL")  # lowercase in DXF


# ── Centre-alignment helpers for multi-part clusters ─────────────────────────

def estimate_text_width(text, height=TEXT_HEIGHT):
    """Rough advance width in model units (matches extractor glyph table)."""
    DEFAULT = 0.74
    glyph = {"I":0.34,"i":0.32,"l":0.32,"1":0.46," ":0.38,"f":0.50,"r":0.52,"t":0.54}
    return sum(glyph.get(c, DEFAULT) for c in text) * height


def add_text_centred(msp, text, cx, y, layer, height=TEXT_HEIGHT):
    """Place TEXT so its visual centre lands on cx (left-align with insert offset)."""
    insert_x = cx - estimate_text_width(text, height) / 2
    add_text(msp, text, insert_x, y, layer, height)


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO G – 3-FRAGMENT "INVERTED-T" CLUSTERS
#
# Layout (model space, y increases upward):
#
#         [  top  ]            ← 1 TEXT entity: the shared prefix/type code
#     [ left ] [ right ]       ← 2 TEXT entities side-by-side one row below
#
# The two bottom tokens together form a compound suffix that, combined with
# the top token, produce two distinct target labels:
#
#   top="FV",  left="12", right="54"  →  targets "FV12" and "FV54"
#
# This exercises a matcher that must:
#   1. Cluster all three fragments into one spatial group.
#   2. Recognise that the group represents *two* labels sharing a prefix.
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (top_text, left_num, right_num, layer, anchor_x, anchor_y)
# The two generated target labels are  f"{top}{left}"  and  f"{top}{right}".
triple_fittings = [
    ("FV",  "12", "54", "SYS-PIPING",  100,  550),
    ("HV",  "21", "63", "SYS-HVAC",    250,  550),
    ("EV",  "30", "71", "SYS-ELEC",    400,  550),
    ("PSV", "14", "28", "SYS-PIPING",  550,  550),
    ("TCV", "05", "99", "SYS-PIPING",  700,  550),
]

# Horizontal gap between the two bottom tokens (centre-to-centre)
BOTTOM_SPREAD = TEXT_HEIGHT * 4.0
# Vertical drop from the top token baseline to the bottom-row baseline
TOP_TO_BOTTOM = SPLIT_OFFSET           # reuse the same spacing as 2-fragment splits

triple_labels = []
for top, left_num, right_num, layer, ax, ay in triple_fittings:
    label_left  = f"{top}{left_num}"
    label_right = f"{top}{right_num}"
    triple_labels.append(label_left)
    triple_labels.append(label_right)

    # Draw two small fitting icons side-by-side to match the two labels
    add_fitting_icon(msp, ax - BOTTOM_SPREAD / 2, ay, layer)
    add_fitting_icon(msp, ax + BOTTOM_SPREAD / 2, ay, layer)

    # Top fragment – visually centred over the midpoint of the two bottom tokens
    add_text_centred(msp, top,      ax,                    ay + 8,                "TEXT-ALL")
    # Bottom-left and bottom-right – each centred over its own icon
    add_text_centred(msp, left_num, ax - BOTTOM_SPREAD / 2, ay + 8 - TOP_TO_BOTTOM, "TEXT-ALL")
    add_text_centred(msp, right_num,ax + BOTTOM_SPREAD / 2, ay + 8 - TOP_TO_BOTTOM, "TEXT-ALL")


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO H – RANGE CLUSTERS  (top token + single "N TO M" bottom entity)
#
# Layout:
#           "FV"
#       "18M TO 24M"
#
# The matcher should expand this to FV18M, FV19M, ... FV24M,
# plus "FV" itself as a standalone label.
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (top, start_n, end_n, suffix, layer, anchor_x, anchor_y)
range_fittings = [
    ("FV",  18, 24, "M",  "SYS-PIPING",  100,  650),
    ("HV",   3,  7, "M",  "SYS-HVAC",    300,  650),
    ("EV",   1,  4, "",   "SYS-ELEC",    500,  650),   # no suffix — bare numbers
    ("PSV",  5,  9, "A",  "SYS-PIPING",  700,  650),
]

range_labels = []
for top, start_n, end_n, sfx, layer, ax, ay in range_fittings:
    range_labels.append(top)                            # standalone top label
    for n in range(start_n, end_n + 1):
        range_labels.append(f"{top}{n}{sfx}")

    add_fitting_icon(msp, ax, ay, layer)
    # Top token and range string both visually centred on ax
    range_str = f"{start_n}{sfx} TO {end_n}{sfx}"
    add_text_centred(msp, top,       ax, ay + 8,                "TEXT-ALL")
    add_text_centred(msp, range_str, ax, ay + 8 - SPLIT_OFFSET, "TEXT-ALL")


# ─────────────────────────────────────────────────────────────────────────────
# Assemble full target label list and write files
# ─────────────────────────────────────────────────────────────────────────────
all_labels = (
    [label for label, _, __, ___ in exact_fittings]
    + split_labels
    + duplicate_labels
    + unmatched_labels
    + case_labels
    + triple_labels
    + range_labels
)

# Write DXF
dxf_path = "test_diagram.dxf"
doc.saveas(dxf_path)
print(f"✓  DXF written: {dxf_path}")

# Write label list
labels_path = "test_labels.txt"
with open(labels_path, "w", encoding="utf-8") as f:
    f.write("# Test label list for extract_manifest.py\n")
    f.write("# Scenarios: exact, split-cluster, duplicate, unmatched, case-fuzzy, triple-fragment\n\n")
    f.write("# --- Exact matches ---\n")
    for label, _, __, ___ in exact_fittings:
        f.write(f"{label}\n")
    f.write("\n# --- Split-label cluster matches (need proximity grouping) ---\n")
    for lbl in split_labels:
        f.write(f"{lbl}\n")
    f.write("\n# --- Duplicates ---\n")
    for lbl in duplicate_labels:
        f.write(f"{lbl}\n")
    f.write("\n# --- Intentionally unmatched (no DXF entity exists) ---\n")
    for lbl in unmatched_labels:
        f.write(f"{lbl}\n")
    f.write("\n# --- Case-mismatch fuzzy matches ---\n")
    for lbl in case_labels:
        f.write(f"{lbl}\n")
    f.write("\n# --- 3-fragment inverted-T clusters (top + two bottom tokens → two labels) ---\n")
    for lbl in triple_labels:
        f.write(f"{lbl}\n")
    f.write("\n# --- Range clusters (top + 'N TO M' → expanded labels + bare top) ---\n")
    for lbl in range_labels:
        f.write(f"{lbl}\n")

print(f"✓  Label list written: {labels_path}")
print(f"\nLabel counts:")
print(f"  Exact match candidates : {len(exact_fittings)}")
print(f"  Split cluster candidates: {len(split_labels)}")
print(f"  Duplicate candidates   : {len(duplicate_labels)}")
print(f"  Intentionally unmatched: {len(unmatched_labels)}")
print(f"  Case-mismatch (fuzzy)  : {len(case_labels)}")
print(f"  3-fragment clusters    : {len(triple_labels)}  ({len(triple_fittings)} groups × 2 labels)")
print(f"  Range clusters         : {len(range_labels)}  ({len(range_fittings)} groups, expanded)")
print(f"  Total labels           : {len(all_labels)}")
print(f"\nNext step:")
print(f"  python extract_manifest.py \\")
print(f"      --dxf {dxf_path} \\")
print(f"      --labels {labels_path} \\")
print(f"      --cluster-gap 3.5 \\")
print(f"      --out test_manifest.json")
