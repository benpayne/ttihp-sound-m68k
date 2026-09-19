#!/usr/bin/env python3
"""Strip vendor definitions of sequential IHP SG13G2 cells whose Icarus-Verilog
gate-level simulation is broken by unsupported specify-block timing checks.

See gl_sim_cell_models.v for the full explanation and the replacement models.
Used only by test/Makefile's GATES=yes flow; never touches the real PDK cache.
"""
import re
import sys

# Cells whose functional D/CLK/RESET_B path is routed through specify-block-
# driven "delayed_*" nets that Icarus never assigns (see gl_sim_cell_models.v)
STRIPPED_CELLS = {
    "sg13g2_dfrbp_1", "sg13g2_dfrbp_2",
    "sg13g2_dfrbpq_1", "sg13g2_dfrbpq_2",
    "sg13g2_sdfrbp_1", "sg13g2_sdfrbp_2",
    "sg13g2_sdfrbpq_1", "sg13g2_sdfrbpq_2",
    "sg13g2_sdfbbp_1",
    "sg13g2_dlhq_1", "sg13g2_dlhr_1", "sg13g2_dlhrq_1",
    "sg13g2_dllr_1", "sg13g2_dllrq_1",
    "sg13g2_lgcp_1", "sg13g2_slgcp_1",
}

MODULE_NAME_RE = re.compile(r"\bmodule\s+(\w+)\b")


def strip(text: str) -> str:
    chunks = text.split("`celldefine")
    out = [chunks[0]]
    dropped = set()
    for chunk in chunks[1:]:
        m = MODULE_NAME_RE.search(chunk)
        name = m.group(1) if m else None
        if name in STRIPPED_CELLS:
            dropped.add(name)
            continue
        out.append("`celldefine" + chunk)
    missing = STRIPPED_CELLS - dropped
    if missing:
        print(f"warning: expected cells not found in source (already absent?): {sorted(missing)}", file=sys.stderr)
    return "".join(out)


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <vendor_stdcell.v> <output.v>", file=sys.stderr)
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    with open(src) as f:
        text = f.read()
    with open(dst, "w") as f:
        f.write(strip(text))


if __name__ == "__main__":
    main()
