"""
generate_mock_viewer_dxf.py
────────────────────────────
Generates mock_viewer.dxf + mock_viewer_labels.txt that mirror the hierarchy
and seq-tag assignment from the combined-viewer mock data:
    TestUIImplementations/combined-viewer/src/mockData.ts

The 20 systems are laid out in a 4-column × 5-row grid. Within each system
cell, leaf fittings are drawn as small square icons with their seq-tag TEXT
entity (e.g. "001") placed above. Subsystem bands are separated by a divider
line so the visual grouping matches the tree panel.

The seq tags are the connection point:
    viewer FittingNode.seq  ←→  DXF TEXT entity  ←→  hitboxes.json key

Run:
    cd DXFPipeline/tests
    python generate_mock_viewer_dxf.py
    # Outputs: mock_viewer.dxf   mock_viewer_labels.txt

Then run the full pipeline:
    python ../pipeline/run_pipeline.py \\
        --dxf  mock_viewer.dxf \\
        --labels mock_viewer_labels.txt \\
        --out ../../TestUIImplementations/combined-viewer/public/tiles/mock
"""

import math
import ezdxf

# ---------------------------------------------------------------------------
# Deterministic RNG — exact port of mockData.ts rng() / rngInt()
# ---------------------------------------------------------------------------

def rng(seed: int) -> float:
    """Deterministic float in [0, 1) — mirrors TS rng(seed)."""
    h = seed & 0xFFFFFFFF
    h = ((h ^ (h >> 16)) * 0x45d9f3b) & 0xFFFFFFFF
    h = ((h ^ (h >> 16)) * 0x45d9f3b) & 0xFFFFFFFF
    h = (h ^ (h >> 16)) & 0xFFFFFFFF
    return h / 0x100000000


def rng_int(seed: int, lo: int, hi: int) -> int:
    """Integer in [lo, hi] — mirrors TS rngInt(seed, min, max)."""
    return lo + int(rng(seed) * (hi - lo + 1))


# ---------------------------------------------------------------------------
# Tier assignment — mirrors mockData.ts SHALLOW / MEDIUM / DEEP sets
# ---------------------------------------------------------------------------

SHALLOW = {1, 4, 8, 13, 17}       # system → leaves directly
MEDIUM  = {0, 2, 3, 6, 10, 15, 18}  # system → subsystem → leaves
# Deep  = everything else           # system → subsystem → component → leaves

SYSTEM_NAMES = [
    "Vantor Engine Core",        "Stratus Power Unit",      "Helion Thrust Module",
    "Cryogen Feed Assembly",     "Oxidiser Regulation Bank","Fuel Conditioning Loop",
    "Turbopump Drive System",    "Ignition & Torch Sub",    "Gimbal Control Actuators",
    "Nozzle Extension Group",    "Hydraulic Power Unit",    "Pneumatic Control Network",
    "Electrical Power Dist.",    "Thermal Protection Sys.", "Avionics Bay",
    "Separation Mechanism",      "Ullage Pressurisation",   "Tank Pressurisation Loop",
    "Main Propulsion Lines",     "Recovery Subsystem",
]

SYSTEM_TIERS = [
    "medium", "shallow", "medium", "medium", "shallow",   # 0-4
    "deep",   "medium",  "deep",   "shallow", "deep",     # 5-9
    "medium", "deep",    "deep",   "shallow", "deep",     # 10-14
    "medium", "deep",    "shallow","medium",  "deep",     # 15-19
]

# ---------------------------------------------------------------------------
# Build the hierarchy — reproduce exact seq-tag order from mockData.ts
# ---------------------------------------------------------------------------

def seq_tag(n: int) -> str:
    return str(n).zfill(3)


def build_systems():
    """
    Returns a list of 20 system dicts:
        {
            name:   str,
            tier:   "shallow" | "medium" | "deep",
            subsystems: [
                { leaves: ["001", "002", ...] },
                ...
            ]
        }

    Seq counter starts at 1 and increments globally in generation order,
    matching the TypeScript module-level seqCounter variable.
    """
    seq = [1]  # mutable counter shared by nested closure

    def next_seq():
        tag = seq_tag(seq[0])
        seq[0] += 1
        return tag

    systems = []
    for i in range(20):
        name = SYSTEM_NAMES[i]

        if i in SHALLOW:
            leaf_count = rng_int(i * 17, 6, 18)
            leaves = [next_seq() for _ in range(leaf_count)]
            systems.append({
                "name": name,
                "tier": "shallow",
                "subsystems": [{"leaves": leaves}],
            })

        elif i in MEDIUM:
            sub_count = rng_int(i * 13, 2, 5)
            subsystems = []
            for s in range(sub_count):
                leaf_count = rng_int(i * 7 + s * 11, 3, 9)
                subsystems.append({"leaves": [next_seq() for _ in range(leaf_count)]})
            systems.append({
                "name": name,
                "tier": "medium",
                "subsystems": subsystems,
            })

        else:  # deep
            sub_count = rng_int(i * 19, 2, 4)
            subsystems = []
            for s in range(sub_count):
                comp_count = rng_int(i * 11 + s * 7, 2, 5)
                all_leaves: list[str] = []
                for c in range(comp_count):
                    leaf_count = rng_int(i * 5 + s * 13, 3, 8)
                    all_leaves.extend(next_seq() for _ in range(leaf_count))
                subsystems.append({"leaves": all_leaves})
            systems.append({
                "name": name,
                "tier": "deep",
                "subsystems": subsystems,
            })

    return systems


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

GRID_COLS    = 4        # systems per row
CELL_W       = 340.0   # DXF units per cell (horizontal)
CELL_H       = 550.0   # DXF units per cell (vertical) — generous for deep systems
PAD          = 14.0    # inner padding inside cell border
HEADER_H     = 22.0    # reserved height for system name
FIT_ICON     = 4.0     # fitting icon half-size
FIT_STEP     = 30.0    # centre-to-centre spacing in fitting sub-grid
TEXT_H_NAME  = 4.5     # system name text height
TEXT_H_SEQ   = 2.0     # seq tag text height


def cell_origin(sys_idx: int):
    """
    Returns (ox, oy) = top-left corner of the system cell in DXF space.
    DXF Y increases upward, so rows go downward (negative Y direction).
    """
    col = sys_idx % GRID_COLS
    row = sys_idx // GRID_COLS
    return col * CELL_W, -(row * CELL_H)


# ---------------------------------------------------------------------------
# DXF construction
# ---------------------------------------------------------------------------

doc = ezdxf.new("R2010")
msp = doc.modelspace()

doc.layers.new("SYS-BORDER",  dxfattribs={"color": 8})   # grey  — cell borders
doc.layers.new("SYS-NAME",    dxfattribs={"color": 7})   # white — system name
doc.layers.new("SYS-DIVIDER", dxfattribs={"color": 253}) # light-grey — subsystem divider
doc.layers.new("FITTING",     dxfattribs={"color": 5})   # blue  — fitting icon
doc.layers.new("TEXT-SEQ",    dxfattribs={"color": 3})   # green — seq tag label (matchable)


def draw_fitting(x: float, y: float, seq: str):
    """Square icon centred on (x, y) with seq tag text above."""
    s = FIT_ICON
    pts = [
        (x - s, y - s), (x + s, y - s),
        (x + s, y + s), (x - s, y + s),
        (x - s, y - s),
    ]
    msp.add_lwpolyline(pts, dxfattribs={"layer": "FITTING"})
    msp.add_text(seq, dxfattribs={
        "layer":  "TEXT-SEQ",
        "height": TEXT_H_SEQ,
        "insert": (x - TEXT_H_SEQ * 0.9, y + FIT_ICON + 1.5),
    })


systems = build_systems()
all_seq_tags: list[str] = []

for sys_idx, system in enumerate(systems):
    ox, oy = cell_origin(sys_idx)
    # Cell top-left = (ox, oy); bottom-right = (ox + CELL_W, oy - CELL_H)

    # --- Cell border ---
    border = [
        (ox,          oy),
        (ox + CELL_W, oy),
        (ox + CELL_W, oy - CELL_H),
        (ox,          oy - CELL_H),
        (ox,          oy),
    ]
    msp.add_lwpolyline(border, dxfattribs={"layer": "SYS-BORDER"})

    # --- System name (top of cell) ---
    msp.add_text(
        f"{sys_idx + 1:02d} · {system['name']}",
        dxfattribs={
            "layer":  "SYS-NAME",
            "height": TEXT_H_NAME,
            "insert": (ox + PAD, oy - PAD - TEXT_H_NAME),
        },
    )

    # Tier badge (small text, top-right corner)
    msp.add_text(
        system["tier"],
        dxfattribs={
            "layer":  "SYS-DIVIDER",
            "height": 2.0,
            "insert": (ox + CELL_W - PAD - 30, oy - PAD - 2.0),
        },
    )

    # --- Fitting area starts below the header ---
    max_cols_fit = max(1, int((CELL_W - 2 * PAD) / FIT_STEP))
    cursor_y = oy - PAD - HEADER_H - PAD / 2

    for sub_idx, sub in enumerate(system["subsystems"]):
        leaves = sub["leaves"]
        all_seq_tags.extend(leaves)

        n_rows = math.ceil(len(leaves) / max_cols_fit)
        for i, seq in enumerate(leaves):
            col_i = i % max_cols_fit
            row_i = i // max_cols_fit
            fx = ox + PAD + col_i * FIT_STEP + FIT_STEP / 2
            fy = cursor_y - row_i * FIT_STEP - FIT_STEP / 2
            draw_fitting(fx, fy, seq)

        # Advance cursor below this subsystem's rows
        cursor_y -= n_rows * FIT_STEP + PAD * 0.6

        # Divider between subsystems (skip after the last one)
        if sub_idx < len(system["subsystems"]) - 1:
            div_y = cursor_y + PAD * 0.3
            msp.add_line(
                (ox + PAD / 2, div_y),
                (ox + CELL_W - PAD / 2, div_y),
                dxfattribs={"layer": "SYS-DIVIDER"},
            )


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------

dxf_path    = "mock_viewer.dxf"
labels_path = "mock_viewer_labels.txt"

doc.saveas(dxf_path)
print(f"✓  DXF written:       {dxf_path}")

with open(labels_path, "w", encoding="utf-8") as f:
    f.write("# Seq-tag label list generated by generate_mock_viewer_dxf.py\n")
    f.write("# Matches TEXT-SEQ entities in mock_viewer.dxf.\n")
    f.write("# One label per line — feed to extract_manifest.py --labels.\n\n")
    for tag in all_seq_tags:
        f.write(f"{tag}\n")

print(f"✓  Label list written: {labels_path}  ({len(all_seq_tags)} labels)")

# Summary
shallow_count = sum(1 for s in systems if s["tier"] == "shallow")
medium_count  = sum(1 for s in systems if s["tier"] == "medium")
deep_count    = sum(1 for s in systems if s["tier"] == "deep")
print(f"\nSystems: {shallow_count} shallow  {medium_count} medium  {deep_count} deep")
print(f"Total seq tags (leaf fittings): {len(all_seq_tags)}")
print(f"\nNext steps:")
print(f"  # Run the full pipeline:")
print(f"  python pipeline/run_pipeline.py \\")
print(f"      --dxf  tests/{dxf_path} \\")
print(f"      --labels tests/{labels_path} \\")
print(f"      --out  output/mock_viewer")
print(f"\n  # Or test just the label extractor:")
print(f"  python pipeline/extract_manifest.py \\")
print(f"      --dxf  tests/{dxf_path} \\")
print(f"      --labels tests/{labels_path} \\")
print(f"      --cluster-gap 3.5 \\")
print(f"      --out  output/mock_manifest.json")
