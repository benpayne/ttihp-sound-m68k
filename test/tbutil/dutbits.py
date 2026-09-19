"""Access a single uo_out bit as an edge-triggerable handle.

cocotb 2.0 refuses to index a packed vector handle (`dut.uo_out[3]` raises
TypeError: "Packed objects, either arrays or structs, cannot be indexed"), but
RisingEdge/FallingEdge need a genuine 1-bit signal. test/tb.v therefore breaks
uo_out out into uo_bit0..uo_bit7; this returns the right one.
"""


def uo_bit(dut, n):
    """Return the standalone 1-bit handle for uo_out[n] (see test/tb.v)."""
    return getattr(dut, f"uo_bit{int(n)}")
