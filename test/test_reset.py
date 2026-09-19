# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Reset-behavior tests for tt_um_benpayne_sound_chip.

Covers tasks T009 and T010 from specs/001-psg-sound-chip/tasks.md.

These tests are written against the current placeholder in src/project.v,
which drives uo_out = uio_out = uio_oe = 8'h00 unconditionally. That
placeholder does NOT implement DTACK_n release, so test_reset_outputs_safe
is EXPECTED TO FAIL until real reset/bus logic lands (see tasks T012/T014).
Do not weaken these assertions to make the placeholder pass.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly

CLK_PERIOD_PS = 40690  # 24.576 MHz, per contracts/pinout-and-bus-timing.md

# ui_in bit positions, per contracts/pinout-and-bus-timing.md
CS_N = 1 << 0
AS_N = 1 << 1
R_W = 1 << 2
DS_N = 1 << 3

# Idle bus: all active-low strobes deasserted (high), R_W high (read/idle),
# A1-A3 and the unused bit don't matter while unselected -- drive them 0.
BUS_IDLE = CS_N | AS_N | R_W | DS_N


async def _start_clock(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())


def _binstr_has_xz(binstr):
    return any(c in ("x", "X", "z", "Z") for c in binstr)


@cocotb.test()
async def test_reset_outputs_safe(dut):
    """T009 / FR-014 / FR-003.

    On reset, with the bus idle, the chip must not acknowledge (DTACK_n
    must be deasserted/high) and must not drive the shared data bus
    (uio_oe must be 0x00). FR-014 requires acknowledge deasserted and the
    data bus released on reset; FR-003 requires DTACK to only ever be
    generated for a cycle actually addressed to this chip.

    The current placeholder drives uo_out = 8'h00, i.e. uo_out[0]
    (DTACK_n) = 0 = permanently ASSERTED. On a real 68000 bus with
    multiple peripherals ANDed together (per 68k-bus-interface.md §3.1),
    a peripheral that holds DTACK_n low while unselected jams every bus
    cycle in the system, including ones addressed to other devices --
    the CPU never sees its wait states satisfied and hangs forever. This
    test is expected to FAIL against the placeholder for exactly that
    reason.
    """
    await _start_clock(dut)

    dut.ena.value = 1
    dut.ui_in.value = BUS_IDLE
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    dtack_n = (int(dut.uo_out.value) >> 0) & 1
    assert dtack_n == 1, (
        "uo_out[0] (DTACK_n) is asserted (0) after reset while idle/unselected. "
        "DTACK_n is push-pull and ANDed with every other peripheral's DTACK_n "
        "on the shared 68k bus (68k-bus-interface.md sec 3.1) -- a peripheral that "
        "holds it low while unaddressed jams EVERY bus cycle in the system, not "
        "just its own, hanging the CPU indefinitely. FR-014 requires acknowledge "
        "deasserted on reset; FR-003 requires DTACK only for cycles addressed to "
        "this chip."
    )

    assert int(dut.uio_oe.value) == 0x00, (
        "uio_oe is nonzero after reset while unselected -- the chip must never "
        "drive the shared 68k data bus (D7-D0) except during a read cycle "
        "actually addressed to it (FR-005, FR-014)."
    )

    # Hold idle for a further ~100 clocks and confirm DTACK_n never glitches
    # low with no bus activity.
    for _ in range(100):
        await ClockCycles(dut.clk, 1)
        dtack_n = (int(dut.uo_out.value) >> 0) & 1
        assert dtack_n == 1, (
            "uo_out[0] (DTACK_n) glitched low during idle bus with no cycle in "
            "progress -- this would spuriously acknowledge a cycle addressed to "
            "another peripheral and jam the shared 68k bus."
        )


@cocotb.test()
async def test_no_x_after_reset(dut):
    """T010 / Constitution Principle IV (Explicit Reset and No Assumed State).

    Every top-level output bit must be a resolved 0/1 (never X or Z) from
    reset release onward. Simulation starts every register at X (Icarus
    default, no initial blocks / initializers per Principle IV); an FPGA
    bitstream would zero those flops for you, but real ASIC flops power up
    to random, unpredictable values. Any flop that feeds a top-level output,
    directly or through combinational logic, must therefore have an
    explicit reset -- if it doesn't, that output stays X in simulation
    forever, which is exactly the bug this test exists to catch before
    fabrication.

    Honesty note: against the current src/project.v placeholder (which
    drives uo_out/uio_out/uio_oe from constant 8'h00 assigns with no
    registers at all) this test passes trivially -- there is no state to
    leave unresolved. It only becomes a meaningful check once real
    registers exist (post T012/T013/T014). It is still written now,
    test-first, per Principle I, and must not be skipped or deleted.
    """
    await _start_clock(dut)

    dut.ena.value = 1
    dut.ui_in.value = BUS_IDLE
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    async def _assert_resolved(when):
        for name in ("uo_out", "uio_out", "uio_oe"):
            sig = getattr(dut, name)
            binstr = str(sig.value)
            assert not _binstr_has_xz(binstr), (
                f"{name} = 0b{binstr} contains X/Z {when}. Every flop feeding a "
                "top-level output must have an explicit reset (Principle IV) -- "
                "ASIC flops power up random, unlike an FPGA bitstream's zeroed "
                "flops, so an unresolved bit here is a real silicon hazard, not "
                "a simulation artifact."
            )

    # Immediately after reset release.
    await _assert_resolved("immediately after reset release")

    # After 10 clocks.
    await ClockCycles(dut.clk, 10)
    await _assert_resolved("10 clocks after reset release")

    # After ~1000 clocks of continued idle operation.
    await ClockCycles(dut.clk, 990)
    await _assert_resolved("~1000 clocks after reset release")


@cocotb.test()
async def test_ui_in7_unused(dut):
    """Constitution "tt08 lesson" (CLAUDE.md): an unused input must not be
    able to perturb any output.

    ui_in[7] is the project's one genuinely-unused input pin -- project.v
    consumes it only through the `_unused` reduction at the bottom of the
    module, never as a real signal. An unused input that turns out to be
    accidentally wired to something is exactly the failure mode CLAUDE.md
    records: a sibling chip shipped with an unwired status signal and
    failed on the real board. This is measured here, not assumed.

    A static "nothing may change while I wiggle ui_in[7]" assertion would
    be false regardless of ui_in[7], because several outputs free-run
    from the reset-relative clock count alone: HEARTBEAT (uo_out[6]) is
    only the most obvious one -- with the reset-default ENABLE (0x70:
    pwm_en/i2s_en/spdif_en all on, every channel silent), the PWM
    carrier, I2S BCLK/LRCLK and the S/PDIF line all keep toggling their
    own framing too, bus idle and no register writes notwithstanding.
    Excluding each of those pins by name would be fragile (it silently
    stops checking anything the day a new free-running output is added)
    and still wouldn't prove the property for the pins it does check.

    Instead, this compares two full-length traces of
    (uo_out, uio_out, uio_oe), sampled once per clock from an identical
    reset release: one with ui_in[7] held low throughout (control), one
    with ui_in[7] driven through several rising/falling edges at varied
    hold lengths (perturbed). ui_in[7] is not read by anything downstream,
    so -- unlike cs_n/as_n/ds_n/r_w -- there is no synchronizer to race
    and no off-grid timing discipline needed here, only variety of
    edges/hold times. Both runs start from the same deterministic reset
    and differ ONLY in ui_in[7], so every free-running signal (HEARTBEAT
    included) must land on the exact same value at the exact same clock
    index in both traces if, and only if, ui_in[7] truly has no effect --
    no per-signal exclusion list required.
    """
    # > one full HEARTBEAT period (512 clk) and several PWM-carrier
    # periods (128 clk each), with room for edges near the end too.
    WINDOW_CLOCKS = 700

    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())

    async def _reset_and_idle():
        dut.ena.value = 1
        dut.uio_in.value = 0
        dut.ui_in.value = BUS_IDLE
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 10)
        dut.rst_n.value = 1
        await ClockCycles(dut.clk, 2)

    async def _capture_trace(ui_in7_high_windows):
        """Reset fresh, then run WINDOW_CLOCKS clocks with ui_in[7] high
        during any clock index inside a (start, end) range (end
        exclusive) in `ui_in7_high_windows` and low otherwise, sampling
        (uo_out, uio_out, uio_oe) every clock."""
        await _reset_and_idle()
        trace = []
        for i in range(WINDOW_CLOCKS):
            high = any(start <= i < end for start, end in ui_in7_high_windows)
            dut.ui_in.value = BUS_IDLE | (0x80 if high else 0x00)
            await ClockCycles(dut.clk, 1)
            await ReadOnly()
            trace.append((int(dut.uo_out.value), int(dut.uio_out.value), int(dut.uio_oe.value)))
            await NextTimeStep()  # leave the read-only phase before driving ui_in again
        return trace

    control_trace = await _capture_trace(ui_in7_high_windows=[])

    # Several rising and falling edges, mixing long holds and single-clock
    # pulses, spread across the whole window.
    toggle_windows = [(37, 91), (150, 151), (240, 320), (410, 412), (500, 560), (620, 680)]
    perturbed_trace = await _capture_trace(ui_in7_high_windows=toggle_windows)

    mismatches = [i for i in range(WINDOW_CLOCKS) if control_trace[i] != perturbed_trace[i]]
    assert not mismatches, (
        f"{len(mismatches)}/{WINDOW_CLOCKS} clock(s) differ between the ui_in[7]-low "
        f"control trace and the ui_in[7]-toggled trace (first mismatch at clock "
        f"{mismatches[0]}: control (uo_out, uio_out, uio_oe)={control_trace[mismatches[0]]} "
        f"vs perturbed={perturbed_trace[mismatches[0]]}) -- ui_in[7] is documented as "
        "unused (project.v's `_unused` reduction) and must not be able to perturb any "
        "output; see CLAUDE.md's tt08 lesson."
    )
