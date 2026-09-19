# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Bring-up observability tests: HEARTBEAT and WR_STROBE.

Covers task T037 from specs/001-psg-sound-chip/tasks.md (User Story 5,
"Observe the chip before any software works"). Requirements verified:

  - FR-040: uo_out[6] (HEARTBEAT) carries a heartbeat at the audio sample
    rate whenever the chip is clocked and out of reset, independent of
    register contents.
  - FR-041: uo_out[7] (WR_STROBE) pulses exactly once, one clock wide, per
    completed register write, and never on a read or an unselected cycle.
  - SC-007: on first-silicon bring-up, a frequency counter on HEARTBEAT
    reads 48 kHz (+/-0.5%) with only power, clock, and reset applied --
    no software, no register writes.

Why this story exists (see spec.md Sec "Why this priority" / CLAUDE.md "the
tt08 lesson"): a prior sibling chip (tt08 PS/2) shipped with a meaningful
status signal computed internally but never wired to a visible pin, and it
failed on the real board as a result with no way to tell why. HEARTBEAT and
WR_STROBE are this chip's fix -- a scope on two spare pins must be able to
confirm "the chip is alive" and "the bus decode is landing writes" before
any driver software exists.

`src/project.v` is currently the all-zero placeholder from the Tiny Tapeout
template (drives uo_out = uio_out = uio_oe = 0 unconditionally and never
toggles anything). Per the test-first requirement for this story, every
test below is EXPECTED TO FAIL against that placeholder. Do not weaken
these assertions and do not modify src/project.v from this file -- T038
(fixing/confirming the HEARTBEAT and WR_STROBE drivers) is a separate task.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from tbutil.bus68k_master import Bus68kMaster, ChipLevelSignals

# Chip clock: 24.576 MHz nominal (contracts/pinout-and-bus-timing.md).
CLK_PERIOD_PS = 40690
CLK_HZ = 1e12 / CLK_PERIOD_PS

# ui_in bit positions (contracts/pinout-and-bus-timing.md):
#   ui[0]=CS_n ui[1]=AS_n ui[2]=R_W ui[3]=DS_n ui[6:4]=A3:A1 ui[7]=unused
CS_N_BIT = 0
AS_N_BIT = 1
RW_BIT = 2
DS_N_BIT = 3

# Fully idle bus: CS_n=1, AS_n=1, DS_n=1 (all deasserted, active-low) and
# R_W=1 (read direction, the safe idle default). No cycle in progress and
# no register is ever written -- this is the SC-007 bring-up state.
IDLE_UI_IN = (1 << CS_N_BIT) | (1 << AS_N_BIT) | (1 << RW_BIT) | (1 << DS_N_BIT)

HEARTBEAT_BIT = 6
WR_STROBE_BIT = 7

# R10 (research.md): HEARTBEAT is a registered copy of count[8] of a 9-bit
# free-running counter, so one full HEARTBEAT period is 512 clk cycles
# (FR-034: Fs = clk/512 = 48 kHz nominal at 24.576 MHz).
HEARTBEAT_PERIOD_CLOCKS = 512
FS_HZ = CLK_HZ / HEARTBEAT_PERIOD_CLOCKS  # ~48 kHz at the nominal clock

FREQ_TOLERANCE = 0.005  # SC-007: +/-0.5%

# ENABLE register index (register-map.md): channel/output-format enables.
ENABLE_REG_IDX = 7

# Fixed seeds for reproducible bus timing randomization (Bus68kMaster).
SEED_WRITE_ONE = 0xDEB035
SEED_WRITE_MANY = 0xDEB036
SEED_READ = 0xDEB037
SEED_ENABLES = 0xDEB038

# If HEARTBEAT hasn't toggled at all within this many clocks, fail cleanly
# instead of hanging or burning the whole collection budget -- this is what
# catches the current all-zero placeholder.
NO_EDGE_GUARD_CLOCKS = 3 * HEARTBEAT_PERIOD_CLOCKS

# How many HEARTBEAT periods to observe for a frequency measurement.
HEARTBEAT_PERIODS_TO_MEASURE = 4

# Settle margin (in clocks) added after a bus operation completes before a
# WR_STROBE pulse monitor is stopped, in case the strobe is registered one
# clock behind the cycle's completion.
PULSE_SETTLE_CLOCKS = 10


async def _start_clock(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())


async def _bring_up_idle(dut):
    """Start the clock, hold the bus idle, and release reset."""
    await _start_clock(dut)

    dut.ena.value = 1
    dut.ui_in.value = IDLE_UI_IN
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def _collect_heartbeat_transitions(dut, min_transitions, max_clocks):
    """
    Sample uo_out[HEARTBEAT_BIT] on every clk rising edge and record every
    level change as (clock_index, new_level), clock_index counting clk
    rising edges from the start of this call (1-based).

    Stops once `min_transitions` changes have been seen or `max_clocks`
    clocks have elapsed, whichever comes first. Fails immediately (instead
    of hanging or silently running out the budget) if the pin has not
    toggled even once within NO_EDGE_GUARD_CLOCKS.
    """
    transitions = []
    prev = (int(dut.uo_out.value) >> HEARTBEAT_BIT) & 1
    clocks = 0
    while len(transitions) < min_transitions and clocks < max_clocks:
        await RisingEdge(dut.clk)
        clocks += 1
        cur = (int(dut.uo_out.value) >> HEARTBEAT_BIT) & 1
        if cur != prev:
            transitions.append((clocks, cur))
            prev = cur
        elif not transitions and clocks >= NO_EDGE_GUARD_CLOCKS:
            assert False, (
                f"HEARTBEAT (uo_out[{HEARTBEAT_BIT}]) has not toggled at all "
                f"in {clocks} clk cycles (~{clocks / HEARTBEAT_PERIOD_CLOCKS:.1f} "
                "heartbeat periods). Per FR-040/SC-007, a scope on this pin "
                "must read 48 kHz using only power, clock, and reset."
            )
    return transitions, clocks


def _assert_heartbeat_frequency(transitions, context):
    """Given >=2 recorded transitions, assert the toggle rate is 48 kHz +/-0.5%."""
    assert len(transitions) >= 2, (
        f"Only {len(transitions)} HEARTBEAT transition(s) observed {context} -- "
        "need at least 2 to measure a period."
    )
    # A full period is two transitions (rise + fall).
    first_clock = transitions[0][0]
    last_clock = transitions[-1][0]
    n_half_periods = len(transitions) - 1
    measured_half_period_clocks = (last_clock - first_clock) / n_half_periods
    measured_period_clocks = measured_half_period_clocks * 2
    measured_hz = CLK_HZ / measured_period_clocks

    rel_err = abs(measured_hz - FS_HZ) / FS_HZ
    assert rel_err <= FREQ_TOLERANCE, (
        f"HEARTBEAT frequency {measured_hz:.1f} Hz {context} is off from the "
        f"expected {FS_HZ:.1f} Hz (clk/512) by {rel_err * 100:.3f}%, exceeding "
        f"the SC-007 +/-0.5% tolerance."
    )


async def _count_pulses_during(dut, bit, coro):
    """
    Run `coro` (an awaitable bus operation) while counting rising edges of
    uo_out[bit], sampled once per clk. Returns (pulse_count, total_high_clocks).

    A monitor task samples continuously from before `coro` starts until
    PULSE_SETTLE_CLOCKS after it completes, so a strobe registered a cycle
    or two behind the bus cycle's completion is still captured. Comparing
    pulse_count to total_high_clocks lets callers additionally assert that
    every pulse was exactly one clock wide (FR-041).
    """
    state = {"prev": (int(dut.uo_out.value) >> bit) & 1, "pulses": 0, "high_clocks": 0}

    async def _monitor():
        while True:
            await RisingEdge(dut.clk)
            cur = (int(dut.uo_out.value) >> bit) & 1
            if cur:
                state["high_clocks"] += 1
            if cur == 1 and state["prev"] == 0:
                state["pulses"] += 1
            state["prev"] = cur

    mon_task = cocotb.start_soon(_monitor())
    try:
        await coro
        await ClockCycles(dut.clk, PULSE_SETTLE_CLOCKS)
    finally:
        mon_task.kill()

    return state["pulses"], state["high_clocks"]


@cocotb.test()
async def test_heartbeat_runs_without_software(dut):
    """
    T037 / FR-040 / SC-007.

    With the bus completely idle (CS_n=AS_n=DS_n=1, R_W=1) and reset
    released, and with no register ever written, uo_out[6] (HEARTBEAT)
    must toggle at 48 kHz within +/-0.5%. This is exactly the SC-007
    bring-up measurement: power, clock, and reset only -- no software.
    """
    await _bring_up_idle(dut)

    max_clocks = HEARTBEAT_PERIODS_TO_MEASURE * 2 * HEARTBEAT_PERIOD_CLOCKS + NO_EDGE_GUARD_CLOCKS
    transitions, _ = await _collect_heartbeat_transitions(
        dut,
        min_transitions=2 * HEARTBEAT_PERIODS_TO_MEASURE,
        max_clocks=max_clocks,
    )
    _assert_heartbeat_frequency(transitions, context="with an idle bus and no register writes")


@cocotb.test()
async def test_heartbeat_independent_of_enables(dut):
    """
    T037 / FR-040.

    Writing ENABLE = 0x00 (register index 7) disables every channel and
    every output format. HEARTBEAT must keep running at 48 kHz regardless
    -- FR-040 deliberately does not gate it on the enables, since it must
    remain useful for bring-up even before any output format is turned on.
    """
    await _bring_up_idle(dut)

    bus = Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=SEED_ENABLES)
    await bus.write(ENABLE_REG_IDX, 0x00)

    # Return the bus to idle before measuring -- HEARTBEAT should already
    # have been running through the write above, but the requirement under
    # test here is "after the enables are cleared", not "during the write".
    dut.ui_in.value = IDLE_UI_IN

    max_clocks = HEARTBEAT_PERIODS_TO_MEASURE * 2 * HEARTBEAT_PERIOD_CLOCKS + NO_EDGE_GUARD_CLOCKS
    transitions, _ = await _collect_heartbeat_transitions(
        dut,
        min_transitions=2 * HEARTBEAT_PERIODS_TO_MEASURE,
        max_clocks=max_clocks,
    )
    _assert_heartbeat_frequency(
        transitions, context="after ENABLE=0x00 disabled every channel and output format"
    )


@cocotb.test()
async def test_wr_strobe_one_pulse_per_write(dut):
    """
    T037 / FR-041.

    A single completed register write must produce exactly one pulse on
    uo_out[7] (WR_STROBE), and that pulse must be exactly one clock wide.
    Five writes back-to-back must produce exactly five pulses, one per
    write -- WR_STROBE must not coalesce, drop, or double-count.
    """
    await _bring_up_idle(dut)

    bus = Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=SEED_WRITE_ONE)

    pulses, high_clocks = await _count_pulses_during(dut, WR_STROBE_BIT, bus.write(0, 0x3C))
    assert pulses == 1, (
        f"Expected exactly 1 WR_STROBE pulse (uo_out[{WR_STROBE_BIT}]) for a single "
        f"completed register write, observed {pulses}."
    )
    assert high_clocks == pulses, (
        f"WR_STROBE was high for {high_clocks} clk cycle(s) across {pulses} pulse(s) -- "
        "FR-041 requires a single clk-wide pulse per write, not a level or a wider strobe."
    )

    bus_many = Bus68kMaster(
        ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=SEED_WRITE_MANY
    )
    n_writes = 5
    reg_values = [(i % 8, (0x10 * (i + 1)) & 0xFF) for i in range(n_writes)]

    async def _do_writes():
        for idx, value in reg_values:
            await bus_many.write(idx, value)

    pulses, high_clocks = await _count_pulses_during(dut, WR_STROBE_BIT, _do_writes())
    assert pulses == n_writes, (
        f"Expected exactly {n_writes} WR_STROBE pulses for {n_writes} completed register "
        f"writes, observed {pulses}."
    )
    assert high_clocks == pulses, (
        f"WR_STROBE was high for {high_clocks} clk cycle(s) across {pulses} pulse(s) over "
        f"{n_writes} writes -- each pulse must be exactly one clk wide (FR-041)."
    )


@cocotb.test()
async def test_wr_strobe_silent_on_read(dut):
    """
    T037 / FR-041.

    A read cycle must produce NO WR_STROBE pulse (uo_out[7] pulses only on
    a completed *write*, per FR-041). An unselected cycle (CS_n held high
    throughout) must likewise produce no pulse, since no register access --
    read or write -- ever reaches this chip.
    """
    await _bring_up_idle(dut)

    bus = Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=SEED_READ)

    pulses, _ = await _count_pulses_during(dut, WR_STROBE_BIT, bus.read(0))
    assert pulses == 0, (
        f"Observed {pulses} WR_STROBE pulse(s) (uo_out[{WR_STROBE_BIT}]) during a read "
        "cycle -- FR-041 requires WR_STROBE to pulse only for a completed write, never "
        "for a read."
    )

    pulses, _ = await _count_pulses_during(dut, WR_STROBE_BIT, bus.unselected_cycle())
    assert pulses == 0, (
        f"Observed {pulses} WR_STROBE pulse(s) (uo_out[{WR_STROBE_BIT}]) during an "
        "unselected cycle (CS_n held high throughout) -- a cycle never addressed to this "
        "chip must never produce a write strobe."
    )
