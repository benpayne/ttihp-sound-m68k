# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Register-semantics tests for tt_um_benpayne_sound_chip.

Task T020 (specs/001-psg-sound-chip/tasks.md, User Story 1). Verifies
FR-011, FR-012, FR-013, FR-014 against contracts/register-map.md, driven
through `tbutil.bus68k_master`.

These tests are written against the spec and contracts, not the RTL.
src/psg_regs.v does not exist yet (only the zero-driving placeholder
src/project.v does), so every test here is expected to FAIL until
T021/T022/T023 land. Do not weaken these assertions to make the
placeholder pass, and do not modify RTL from this file.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tbutil.bus68k_master import BUS_IDLE_UI_IN, Bus68kMaster, ChipLevelSignals

CLK_PERIOD_PS = 40690  # 24.576 MHz, per contracts/pinout-and-bus-timing.md

# Register indices, per contracts/register-map.md.
A_LO, A_CTRL = 0, 1
B_LO, B_CTRL = 2, 3
C_LO, C_CTRL = 4, 5
NOISE = 6
ENABLE = 7
NUM_REGS = 8

# Reset values, per contracts/register-map.md "Reset state": all 0x00
# except ENABLE = 0x70.
RESET_VALUES = [0x00] * NUM_REGS
RESET_VALUES[ENABLE] = 0x70


async def _start_clock(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())


async def _reset(dut):
    sig = ChipLevelSignals(dut)
    dut.ena.value = 1
    dut.ui_in.value = BUS_IDLE_UI_IN
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return sig


@cocotb.test()
async def test_readback_all_registers(dut):
    """T020 / FR-011.

    Every writable register -- including the reserved bit 7 of ENABLE --
    reads back the value last written to it.
    """
    await _start_clock(dut)
    sig = await _reset(dut)
    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    # 0xFF exercises ENABLE's reserved bit 7 explicitly ("Write 0" is
    # software's obligation, not a hardware requirement -- the register
    # map documents it as "read/write storage, no function").
    values = [0x00, 0x81, 0x3C, 0xFF, 0x01, 0x7E, 0x99, 0xFF]
    assert len(values) == NUM_REGS

    for addr, value in enumerate(values):
        await master.write(addr, value)

    for addr, value in enumerate(values):
        got = await master.read(addr)
        assert got == value, (
            f"reg {addr}: read back 0x{got:02X}, expected 0x{value:02X} "
            f"(seed={master.seed})"
        )


@cocotb.test()
async def test_reset_values(dut):
    """T020 / FR-014.

    On reset, and before any register write, every register reads
    0x00 except ENABLE, which reads 0x70.
    """
    await _start_clock(dut)
    sig = await _reset(dut)
    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    for addr, expected in enumerate(RESET_VALUES):
        got = await master.read(addr)
        assert got == expected, (
            f"reg {addr} = 0x{got:02X} after reset, expected 0x{expected:02X} "
            f"(seed={master.seed})"
        )


@cocotb.test()
async def test_reads_have_no_side_effects(dut):
    """T020 / FR-013.

    Reading a register repeatedly does not change it, nor any other
    register.
    """
    await _start_clock(dut)
    sig = await _reset(dut)
    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    snapshot = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x08]
    for addr, value in enumerate(snapshot):
        await master.write(addr, value)

    target = B_CTRL
    for _ in range(10):
        got = await master.read(target)
        assert got == snapshot[target], (
            f"reg {target} changed under repeated reads: got 0x{got:02X}, "
            f"expected 0x{snapshot[target]:02X} (seed={master.seed})"
        )

    for addr, expected in enumerate(snapshot):
        got = await master.read(addr)
        assert got == expected, (
            f"reg {addr} = 0x{got:02X} after repeated reads of reg {target}, "
            f"expected 0x{expected:02X} (seed={master.seed}) -- a read must "
            "have no side effects on any register, not just the one read"
        )


@cocotb.test()
async def test_lo_staging_is_atomic(dut):
    """T020 / FR-012.

    Per contracts/register-map.md, `x_LO` is staged: writing it alone
    must not affect the committed pitch, only the CTRL write commits
    `{CTRL[3:0], LO}` as the new active period. At the register level
    (no audio hardware exists yet -- this is verified in
    test/test_tone_pwm.py::User Story 2 once psg_tone.v exists), the
    observable contract is: `A_LO` reads back the staged byte
    immediately, and writing it alone leaves `A_CTRL` (and hence the
    committed period/volume) untouched.
    """
    await _start_clock(dut)
    sig = await _reset(dut)
    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    # Establish a baseline committed pitch/volume: write LO then CTRL,
    # per the register contract's update rule.
    old_lo, old_ctrl = 0x11, 0x82  # vol=8, period[11:8]=2
    await master.write(A_LO, old_lo)
    await master.write(A_CTRL, old_ctrl)

    # Write A_LO alone -- must be readable immediately (FR-011) without
    # touching A_CTRL (FR-012: no audible/committed effect until A_CTRL
    # is written).
    new_lo = 0x99
    await master.write(A_LO, new_lo)

    got_lo = await master.read(A_LO)
    assert got_lo == new_lo, (
        f"A_LO reads 0x{got_lo:02X} immediately after being written, "
        f"expected the staged byte 0x{new_lo:02X} (seed={master.seed})"
    )

    got_ctrl = await master.read(A_CTRL)
    assert got_ctrl == old_ctrl, (
        f"A_CTRL changed to 0x{got_ctrl:02X} from a write to A_LO alone "
        f"(expected unchanged 0x{old_ctrl:02X}, seed={master.seed}) -- "
        "the pitch/volume register must only change when written directly"
    )
