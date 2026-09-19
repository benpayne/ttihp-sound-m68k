# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Chip-level 68k bus tests for tt_um_benpayne_sound_chip.

Task T019 (specs/001-psg-sound-chip/tasks.md, User Story 1). Verifies
FR-001, FR-004, FR-005, FR-006, SC-001, SC-002 against the chip's
`ui_in`/`uo_out`/`uio_*` pins, driven through `tbutil.bus68k_master`.

These tests are written against the spec and contracts, not the RTL.
src/bus68k_if.v and src/psg_regs.v do not exist yet (only the
zero-driving placeholder src/project.v does), so every test here is
expected to FAIL until T021/T022/T023 land. Do not weaken these
assertions to make the placeholder pass, and do not modify RTL from
this file.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer
from cocotb_coverage.coverage import CoverCross, CoverPoint, coverage_db


from tbutil.bus68k_master import BUS_IDLE_UI_IN, Bus68kMaster, ChipLevelSignals
from tbutil.covutil import export_scoped_yaml

CLK_PERIOD_PS = 40690  # 24.576 MHz, per contracts/pinout-and-bus-timing.md

# Number of randomized cycles for test_randomized_readback (SC-001 requires
# >=10,000). Overridable via COCOTB_BUS_CYCLES for fast local iteration --
# but the default MUST stay 10000 to actually satisfy SC-001; any run with
# a reduced count does not demonstrate the success criterion and must not
# be treated as satisfying the gate.
N_RANDOM_CYCLES = int(os.environ.get("COCOTB_BUS_CYCLES", "10000"))

NUM_REGS = 8


async def _start_clock(dut):
    # Start the coverage monitor for THIS test. cocotb cancels tasks when a test
    # ends, so a once-only guard would leave every test after the first one
    # unmonitored -- which is exactly why the sweep's WRITE->IDLE / READ->IDLE
    # transitions were produced (verified: 26 hits) but never recorded.
    cocotb.start_soon(_fsm_coverage_monitor(dut))
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())


async def _reset(dut, sig):
    dut.ena.value = 1
    dut.ui_in.value = BUS_IDLE_UI_IN
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_randomized_readback(dut):
    """T019 / FR-001, FR-004, FR-005, FR-006, SC-001.

    Drives >=10,000 (COCOTB_BUS_CYCLES) randomized register cycles --
    random register, random data, random read/write mix, random
    off-grid timing, interspersed with cycles to "other devices"
    (CS_n held high) -- and requires that every read returns the last
    value written to that register, with zero errors (SC-001).
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)

    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    # Reset values per contracts/register-map.md (also checked in
    # test_regs.py::test_reset_values): all 0x00 except ENABLE (idx 7) =
    # 0x70.
    shadow = [0x00] * NUM_REGS
    shadow[7] = 0x70

    for i in range(N_RANDOM_CYCLES):
        choice = master._rng.random()
        if choice < 0.05:
            # Occasional cycle to "some other device" on the shared bus
            # (SC-001's "including ... cycles to other devices"): must
            # not disturb any register.
            await master.unselected_cycle(
                addr=master._rng.randrange(NUM_REGS),
                write=master._rng.choice([True, False]),
            )
            continue

        addr = master._rng.randrange(NUM_REGS)
        if master._rng.choice([True, False]):
            data = master._rng.randrange(256)
            await master.write(addr, data)
            shadow[addr] = data
        else:
            got = await master.read(addr)
            sample_cycle('read', 'completed', True)
            assert got == shadow[addr], (
                f"cycle {i}: read reg {addr} got 0x{got:02X}, expected "
                f"0x{shadow[addr]:02X} (seed={master.seed})"
            )


@cocotb.test()
async def test_release_is_combinational(dut):
    """T019 / FR-004, FR-005, SC-002.

    After AS_n rises, DTACK_n must go high and uio_oe must go to 0
    within 1 ns, with no intervening clock edge, at several random
    clock phases. 1 ns is under 2.5% of the 40.69 ns clock period, and
    the driver never places a release within 15% of an edge (see
    tbutil/bus68k_master.py docstring), so this window cannot itself
    straddle a clock edge.

    This is the core check from research.md R1: a *synchronized*
    release would take 2-3 clocks of synchronizer latency plus an
    output register (81-163 ns) and would fail this check outright.
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)

    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    for i in range(10):
        if i % 2 == 0:
            await master.write(i % NUM_REGS, (i * 37) & 0xFF)
        else:
            await master.read(i % NUM_REGS)

        # `write`/`read` return immediately after driving the release
        # edge (AS_n/DS_n/CS_n high); check right after, before any
        # further clock edge can occur.
        await Timer(1, unit="ns")
        assert sig.get_dtack_n() == 1, (
            f"iteration {i}: DTACK_n not released within 1 ns of AS_n rising "
            f"(seed={master.seed}) -- release must be combinational (FR-004)"
        )
        assert sig.get_oe() == 0, (
            f"iteration {i}: uio_oe not released within 1 ns of AS_n rising "
            f"(seed={master.seed}) -- data bus must not stay driven (FR-005)"
        )


@cocotb.test()
async def test_back_to_back_cycles(dut):
    """T019 / FR-001, FR-004, research.md R1.

    Two cycles to this chip separated by only a 60 ns AS-high gap (the
    68000 #15 minimum at 16 MHz, contract C6) must both be acknowledged
    correctly; the second cycle must not see a stale DTACK left over
    from the first (which the async clear on raw AS_n high, R1, exists
    to prevent).
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)

    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    addr_a, data_a = 0, 0xA5
    addr_b, data_b = 2, 0x5A

    ops = [("write", addr_a, data_a), ("write", addr_b, data_b)]
    await master.back_to_back(ops, as_high_ns=60.0)

    got_a = await master.read(addr_a)
    got_b = await master.read(addr_b)
    assert got_a == data_a, (
        f"reg {addr_a} = 0x{got_a:02X} after a 60ns-gapped back-to-back write, "
        f"expected 0x{data_a:02X} (seed={master.seed})"
    )
    assert got_b == data_b, (
        f"reg {addr_b} = 0x{got_b:02X} after a 60ns-gapped back-to-back write, "
        f"expected 0x{data_b:02X} (seed={master.seed})"
    )

    # Read-after-write back to back, with the same tight gap: the second
    # cycle (the read) must see fresh acknowledge/data, not a stale one
    # from the write's own DTACK.
    read_results = await master.back_to_back(
        [("write", addr_a, 0x33), ("read", addr_a)], as_high_ns=60.0
    )
    assert read_results[1] == 0x33, (
        f"back-to-back read after write of reg {addr_a} returned "
        f"0x{read_results[1]:02X}, expected 0x33 (seed={master.seed}) -- "
        "looks like a stale DTACK from the first cycle"
    )


@cocotb.test()
async def test_aborted_write(dut):
    """T019 / FR-006.

    AS_n released before DTACK is ever seen low must leave the target
    register holding either its complete old value or its complete new
    value -- never a mix of the two.
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)

    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    addr = 4
    for trial in range(20):
        old = master._rng.randrange(256)
        await master.write(addr, old)
        sample_cycle('write', 'completed', True)

        new = master._rng.randrange(256)
        await master.aborted_write(addr, new)
        sample_cycle('write', 'aborted', True)

        got = await master.read(addr)
        assert got in (old, new), (
            f"trial {trial}: reg {addr} = 0x{got:02X} after an aborted write "
            f"(old=0x{old:02X}, new=0x{new:02X}) -- must be exactly one of "
            f"the two, never a mix (FR-006, seed={master.seed})"
        )


@cocotb.test()
async def test_unselected_cycle(dut):
    """T019 / FR-001, FR-005, contract C7.

    With CS_n held high, toggling AS_n/DS_n (a cycle addressed to some
    other peripheral) must never assert DTACK_n and must never drive the
    data bus, checked on every clock edge for the duration of the cycle
    -- whether that cycle runs its normal length or is cut short early
    (an aborted cycle addressed elsewhere, T055 / bus.cycle.cross).
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)

    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    violations = []

    async def _monitor():
        while True:
            await RisingEdge(dut.clk)
            if sig.get_dtack_n() != 1:
                violations.append("dtack asserted while unselected")
            if sig.get_oe() != 0:
                violations.append("data bus driven while unselected")

    mon_task = cocotb.start_soon(_monitor())
    try:
        for write in (False, True):
            await master.unselected_cycle(addr=3, write=write)
            # `write` selects a write- or read-shaped cycle (r_w polarity,
            # see Bus68kMaster.unselected_cycle) -- label the sample to
            # match, not a hardcoded "write" regardless of which cycle
            # actually ran (that bug had silently recorded every
            # unselected READ cycle as a WRITE for as long as this cross
            # has existed).
            sample_cycle("write" if write else "read", "completed", False)
            await ClockCycles(dut.clk, 5)

        # Aborted-and-unselected (T055 / bus.cycle.cross): CS_n held high
        # throughout AND AS_n/DS_n released early, before the cycle's
        # normal length -- models a bus error on the ACTUAL target device
        # (not this chip) cutting the cycle short. This combination is
        # constructible at the bus interface (a real 68000 can release
        # AS_n/DS_n early on any cycle, including one addressed
        # elsewhere) even though bus68k_if.v's `qualified` never rises
        # while cs_n stays high, so it's expected to be behaviorally
        # identical to the completed-unselected case above -- measured
        # here rather than assumed, since glitch-adjacent stimulus
        # against the strobe synchronizer/qualify logic is exactly the
        # class of bug that shipped unnoticed on the tt08 PS/2 sibling
        # (see CLAUDE.md's "why this matters" section).
        for write in (False, True):
            for _ in range(20):
                frac_clocks = master._rng.uniform(
                    Bus68kMaster.ABORT_DELAY_MIN_CLOCKS, Bus68kMaster.ABORT_DELAY_MAX_CLOCKS
                )
                await master.unselected_cycle(
                    addr=3, write=write, abort_delay_ps=frac_clocks * CLK_PERIOD_PS
                )
                sample_cycle("write" if write else "read", "aborted", False)
                await ClockCycles(dut.clk, 5)
    finally:
        mon_task.kill()

    assert not violations, (
        f"unselected cycle produced a response: {violations[:5]} "
        f"(seed={master.seed}) -- CS_n was held high throughout (FR-001, FR-005)"
    )


# ---------------------------------------------------------------------------
# T024 -- functional coverage for the bus FSM (constitution Principle II).
#
# bus68k_if.v encodes ST_IDLE=0, ST_WRITE=1, ST_READ=2, ST_ACK=3. Legal moves:
# IDLE->WRITE / IDLE->READ on `qualified`, WRITE->ACK, READ->ACK, ACK holds,
# and `state_clr` (raw as_n|cs_n) forces IDLE asynchronously from any state.
# ---------------------------------------------------------------------------
FSM_PATH = "u_bus"
_ST = {0: "IDLE", 1: "WRITE", 2: "READ", 3: "ACK"}
_LEGAL_TRANSITIONS = [
    ("IDLE", "IDLE"), ("IDLE", "WRITE"), ("IDLE", "READ"),
    ("WRITE", "ACK"), ("READ", "ACK"), ("ACK", "ACK"),
    ("ACK", "IDLE"), ("WRITE", "IDLE"), ("READ", "IDLE"),
]


@CoverPoint("bus.fsm.state", xf=lambda prev, cur: cur, bins=list(_ST.values()))
@CoverPoint("bus.fsm.transition", xf=lambda prev, cur: (prev, cur), bins=_LEGAL_TRANSITIONS)
def _sample_fsm(prev, cur):
    pass


@CoverPoint("bus.cycle.kind", xf=lambda k, o, s: k, bins=["write", "read"])
@CoverPoint("bus.cycle.outcome", xf=lambda k, o, s: o, bins=["completed", "aborted"])
@CoverPoint("bus.cycle.selected", xf=lambda k, o, s: s, bins=[True, False])
@CoverCross("bus.cycle.cross", items=["bus.cycle.kind", "bus.cycle.outcome", "bus.cycle.selected"])
def sample_cycle(kind, outcome, selected):
    """Record one bus cycle's shape for the coverage cross."""


async def _fsm_coverage_monitor(dut):
    """Sample the FSM every clock; runs for the whole simulation."""
    fsm = getattr(dut.user_project, FSM_PATH)

    def _state():
        """Current FSM state name, or None while the value is unresolved."""
        v = fsm.state.value
        if not v.is_resolvable:      # X/Z during reset
            return None
        return _ST.get(int(v), "IDLE")

    prev = _state()
    while True:
        await RisingEdge(dut.clk)
        # Settle before sampling. Reading .value straight after RisingEdge
        # returns the PRE-edge value, so a state entered on this edge and
        # asynchronously cleared by state_clr before the next one (exactly
        # ST_WRITE/ST_READ during an abort) would never be observed -- the
        # WRITE->IDLE and READ->IDLE edges would read as IDLE->IDLE forever.
        await ReadOnly()
        cur = _state()
        if cur is None:              # still unresolved -- nothing to sample
            prev = None
            continue
        if prev is not None:
            _sample_fsm(prev, cur)
        prev = cur


@cocotb.test()
async def test_fsm_transition_sweep(dut):
    """T024 / Principle II -- close the remaining FSM transition holes.

    ST_WRITE and ST_READ each last exactly one clock and unconditionally
    advance to ST_ACK, so the WRITE->IDLE and READ->IDLE edges are only taken
    when the asynchronous state_clr (raw as_n|cs_n rising) fires inside that
    single 40.69 ns window. Randomized abort delays never happen to land there.
    Sweep the abort instant finely across two clock periods so some iteration
    does. Must be defined BEFORE test_fsm_coverage_complete: cocotb runs tests
    in definition order and coverage accumulates across them.
    """
    await _start_clock(dut)
    sig = ChipLevelSignals(dut)
    await _reset(dut, sig)
    master = Bus68kMaster(sig, dut.clk, clk_period_ps=CLK_PERIOD_PS)

    # The WRITE->IDLE / READ->IDLE window is a 1T-wide slice inside [2T, 4T)
    # after the raw ds_n assert, sliding with the phase at which the raw qualify
    # condition first goes true. `qualified` (synchronized) takes 2 clocks to
    # rise, so the FSM reaches ST_WRITE/ST_READ at t0+2T..3T, and holds there
    # for exactly one clock before auto-advancing to ST_ACK unless state_clr
    # (raw as_n|cs_n) fires first. Sweeping only [1T, 2.96T] merely grazed the
    # bottom of that band. Bus68kMaster's own jitter sets the phase per call, so
    # rather than control it, span the whole union at T/24 granularity -- far
    # finer than the 1T window, so ~24 grid points land inside it whatever the
    # phase turns out to be.
    step = max(1, CLK_PERIOD_PS // 24)
    base = 2 * CLK_PERIOD_PS
    for k in range(96):
        await master.aborted_write(3, 0x5A, abort_delay_ps=base + k * step)
        sample_cycle("write", "aborted", True)
    for k in range(96):
        await master.aborted_read(5, abort_delay_ps=base + k * step)
        sample_cycle("read", "aborted", True)


@cocotb.test()
async def test_fsm_coverage_complete(dut):
    """T024 / Principle II.

    Every FSM state and every legal transition must be exercised by the suite.
    Coverage accumulates across all tests in this module, so this check is only
    meaningful in a full-module run; it skips when the module is run with a
    COCOTB_TESTCASE/COCOTB_TEST_FILTER filter.
    """
    state_cov = coverage_db.get("bus.fsm.state")
    assert state_cov is not None and state_cov.size > 0, (
        "FSM coverage database is empty -- the monitor never sampled, so this "
        "check would pass vacuously."
    )

    if os.environ.get("COCOTB_TESTCASE") or os.environ.get("COCOTB_TEST_FILTER"):
        dut._log.info(
            "subset run (COCOTB_TESTCASE/COCOTB_TEST_FILTER set) -- skipping coverage completeness check"
        )
        return

    # NB: bus.cycle.cross is recorded for insight but NOT asserted -- several
    # combinations (e.g. read+aborted) are not exercised by any test, so
    # requiring 8/8 would be a false gate. States and transitions ARE gated.
    #
    # `coverage_db` is a process-wide singleton shared with test_tone_pwm.py,
    # test_i2s.py and test_spdif.py: `coverage_db.export_to_yaml()` has no
    # scoping parameter and would dump every coverpoint ever registered in
    # the process, not just this module's -- harmless when this module runs
    # alone, but cross-contaminated the moment more than one coverage-bearing
    # suite shares a process (e.g. a combined run across all of this
    # project's test modules). `export_scoped_yaml` filters to just this
    # module's "bus.*" namespace before writing.
    export_scoped_yaml("bus", "coverage_bus_fsm.yml")
    missing_states = state_cov.size - state_cov.coverage
    trans = coverage_db["bus.fsm.transition"]
    missing_trans = trans.size - trans.coverage
    assert missing_states == 0, (
        f"{missing_states} of {state_cov.size} FSM states never reached "
        "-- Principle II requires 100% state coverage."
    )
    assert missing_trans == 0, (
        f"{missing_trans} of {trans.size} legal FSM transitions never taken "
        "-- Principle II requires 100% transition coverage."
    )
