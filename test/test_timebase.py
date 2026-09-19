# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Timebase / bring-up heartbeat tests.

Covers task T011 (specs/001-psg-sound-chip/tasks.md, Phase 2): with the bus
completely idle, `uo_out[6]` (HEARTBEAT) must be a registered copy of the
free-running time-base counter's `count[8]` bit -- a 48 kHz square wave
that runs whenever the chip is clocked and out of reset, independent of
register contents (research.md R10, contracts/pinout-and-bus-timing.md).

Requirements verified:
  - FR-034: sample rate is exactly clk/512.
  - FR-040: HEARTBEAT toggles at the audio sample rate whenever the chip is
    clocked and out of reset, regardless of register contents.
  - SC-007: on bring-up, a frequency counter on HEARTBEAT reads 48 kHz
    within +/-0.5% using only power, clock, and reset.

`src/project.v` is currently the all-zero placeholder (drives uo_out = 0 and
never toggles anything), so these tests are EXPECTED TO FAIL until T012-T014
land. The collector below is bounded so that failure is a clean assertion
rather than a simulation hang.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

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
# R_W=1 (read direction, the safe idle default). No cycle is in progress.
IDLE_UI_IN = (1 << CS_N_BIT) | (1 << AS_N_BIT) | (1 << RW_BIT) | (1 << DS_N_BIT)

HEARTBEAT_BIT = 6

# R10: HEARTBEAT is a registered copy of count[8], a 9-bit free-running
# counter, so one full HEARTBEAT period is 512 clk cycles (FR-034: Fs =
# clk/512 = 48 kHz nominal).
HEARTBEAT_PERIOD_CLOCKS = 512
FS_HZ = CLK_HZ / HEARTBEAT_PERIOD_CLOCKS  # ~48 kHz at the nominal clock

FREQ_TOLERANCE = 0.005  # SC-007: +/-0.5%
DUTY_TOLERANCE = 0.02  # "~50%" -- a counter bit, allow a couple of percent

# If HEARTBEAT hasn't toggled at all within this many clocks, fail instead
# of burning the full collection budget (or hanging, if the budget were
# unbounded) -- this is what catches the current all-zero placeholder.
NO_EDGE_GUARD_CLOCKS = 3 * HEARTBEAT_PERIOD_CLOCKS


async def _bring_up_idle(dut):
    """Start the clock, hold the bus idle, and release reset."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())

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
    clocks have elapsed, whichever comes first. Guards against an
    infinite hang: if the pin has not toggled even once within
    NO_EDGE_GUARD_CLOCKS, fails immediately with a clear message instead
    of running the full budget (or hanging, on an unbounded wait).
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
                "heartbeat periods) with the bus completely idle and the chip "
                "clocked and out of reset. Per FR-040/SC-007, a scope on this "
                "pin must read 48 kHz using only power, clock, and reset -- "
                "it must never depend on register contents or bus activity."
            )
    return transitions, clocks


@cocotb.test()
async def test_heartbeat_frequency(dut):
    """
    T011 / FR-034 / FR-040 / SC-007.

    With the bus held completely idle (CS_n=AS_n=DS_n=1, R_W=1) and reset
    released, measure the period of uo_out[6] across at least 4 full
    heartbeat periods and assert its frequency is 48 kHz within 0.5%
    (SC-007), with ~50% duty cycle (it's a raw counter bit, not a pulse).
    """
    await _bring_up_idle(dut)

    # Need 5 rising edges to bound 4 full periods. A transition list can
    # start on either edge direction, so collect enough raw transitions
    # (up to 10) to guarantee 5 rising edges are among them, with margin
    # in the clock budget for up to ~1 extra period of phase offset.
    transitions, clocks_used = await _collect_heartbeat_transitions(
        dut, min_transitions=10, max_clocks=7 * HEARTBEAT_PERIOD_CLOCKS
    )

    rising_edges = [c for c, level in transitions if level == 1]
    assert len(rising_edges) >= 5, (
        f"Only observed {len(rising_edges)} HEARTBEAT rising edges "
        f"({len(transitions)} total transitions) in {clocks_used} clk "
        "cycles with the bus idle -- need at least 5 (4 full periods) to "
        "measure frequency for SC-007. A bring-up scope on uo_out[6] must "
        "read a clean 48 kHz."
    )

    periods_clocks = [b - a for a, b in zip(rising_edges, rising_edges[1:])]
    avg_period_clocks = sum(periods_clocks) / len(periods_clocks)
    freq_hz = CLK_HZ / avg_period_clocks
    rel_err = abs(freq_hz - FS_HZ) / FS_HZ
    assert rel_err <= FREQ_TOLERANCE, (
        f"HEARTBEAT frequency is {freq_hz:.1f} Hz (avg period "
        f"{avg_period_clocks:.2f} clk cycles), which is {rel_err * 100:.2f}% "
        f"off the expected {FS_HZ:.1f} Hz -- exceeds SC-007's +/-0.5% "
        "bring-up tolerance. With only power, clock, and reset applied, a "
        "frequency counter on uo_out[6] must read 48 kHz."
    )

    # Duty cycle: for each rising-falling-rising triple, high time / period.
    duties = []
    for (t0, lvl0), (t1, lvl1), (t2, lvl2) in zip(
        transitions, transitions[1:], transitions[2:]
    ):
        if lvl0 == 1 and lvl1 == 0 and lvl2 == 1:
            duties.append((t1 - t0) / (t2 - t0))
    assert duties, (
        "Could not find a complete rising/falling/rising triple to measure "
        f"HEARTBEAT duty cycle from {len(transitions)} transitions."
    )
    avg_duty = sum(duties) / len(duties)
    assert abs(avg_duty - 0.5) <= DUTY_TOLERANCE, (
        f"HEARTBEAT duty cycle is {avg_duty * 100:.1f}%, expected ~50% "
        "since it is a straight counter-bit toggle (count[8]), not a "
        "pulse (research.md R10)."
    )


@cocotb.test()
async def test_heartbeat_independent_of_registers(dut):
    """
    T011 / FR-040.

    With the bus idle and no register accesses whatsoever, HEARTBEAT must
    keep toggling at a uniform rate forever -- never stalling, and never
    producing a missing or doubled edge. Sample over at least 20 periods
    and confirm every rising-to-rising interval is identical.
    """
    await _bring_up_idle(dut)

    periods_needed = 20
    # 21 rising edges bound 20 periods; worst-case phase needs up to 42
    # raw transitions to guarantee that many rising edges are captured.
    transitions, clocks_used = await _collect_heartbeat_transitions(
        dut,
        min_transitions=2 * (periods_needed + 1),
        max_clocks=(periods_needed + 3) * HEARTBEAT_PERIOD_CLOCKS,
    )

    rising_edges = [c for c, level in transitions if level == 1]
    assert len(rising_edges) >= periods_needed + 1, (
        f"Only observed {len(rising_edges)} HEARTBEAT rising edges in "
        f"{clocks_used} clk cycles with the bus idle -- need at least "
        f"{periods_needed + 1} to confirm {periods_needed} uniform periods "
        "(FR-040: the heartbeat must never stall)."
    )

    periods_clocks = [b - a for a, b in zip(rising_edges, rising_edges[1:])]
    distinct_periods = set(periods_clocks)
    assert len(distinct_periods) == 1, (
        f"HEARTBEAT rising-edge intervals were not uniform: saw periods "
        f"{sorted(distinct_periods)} clk cycles across {len(periods_clocks)} "
        "intervals. A missing or doubled edge here would show up as an "
        "interval that isn't a multiple of the true heartbeat period -- "
        "FR-040 requires the heartbeat to run continuously regardless of "
        "register contents or bus activity."
    )
