# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Standalone unit tests for the reusable `bus68k_if` 68000 bus IP.

Covers task T018 from specs/001-psg-sound-chip/tasks.md (User Story 1),
checking contract guarantees 1-7 in
specs/001-psg-sound-chip/contracts/bus68k_if.md against the trivial 8x8
register payload in tb_bus68k_if.v. Satisfies FR-008 ("the bus-interface
function MUST be ... verifiable on its own").

THIS IS TEST-FIRST (constitution Principle I): src/bus68k_if.v does not
exist yet, so this bench is expected to fail to elaborate. Do not weaken
these assertions to make a stub pass.

Reuses, rather than reinvents, the shared 68000 bus-cycle driver at
test/tbutil/bus68k_master.py (constitution Verification Standards
"Organization": shared drivers live in a helper package, not copied
between tests).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from tbutil.bus68k_master import Bus68kMaster, IpLevelSignals

# 24.576 MHz, per info.yaml clock_hz / research.md (all research.md timing
# numbers, e.g. C1-C7 in pinout-and-bus-timing.md, assume this period).
CLK_PERIOD_PS = 40690


async def _start_clock(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())


async def _reset(dut):
    """Assert rst_n asynchronously, release synchronously (Principle IV)."""
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


def _new_bus(dut, seed):
    signals = IpLevelSignals(dut)
    return Bus68kMaster(signals, dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=seed)


class _PulseMonitor:
    """Counts clock-synchronous reg_write/reg_read pulses until stopped.

    Used to check contract guarantee 1 (exactly one pulse per qualified
    cycle, never both together) without racing the bus driver's own
    awaits.
    """

    def __init__(self, dut):
        self._dut = dut
        self.write_pulses = 0
        self.read_pulses = 0
        self.both_high_seen = False
        self._task = None

    def start(self):
        self._task = cocotb.start_soon(self._run())

    def stop(self):
        if self._task is not None:
            self._task.kill()
            self._task = None

    async def _run(self):
        while True:
            await RisingEdge(self._dut.clk)
            w = int(self._dut.reg_write.value)
            r = int(self._dut.reg_read.value)
            if w:
                self.write_pulses += 1
            if r:
                self.read_pulses += 1
            if w and r:
                self.both_high_seen = True


@cocotb.test()
async def test_reg_write_pulses_once_never_on_read(dut):
    """T018 - contract guarantee 1 (FR-008).

    `reg_write` pulses exactly once per qualified write cycle and never
    during a read cycle; `reg_read` is the mirror image for reads. The
    two must never be high on the same clock.
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=1)
    await _reset(dut)

    mon = _PulseMonitor(dut)
    mon.start()
    await bus.write(0x3, 0xA5)
    await ClockCycles(dut.clk, 2)
    mon.stop()

    assert mon.write_pulses == 1, f"expected exactly one reg_write pulse for a write, saw {mon.write_pulses}"
    assert mon.read_pulses == 0, "reg_read must stay low for the whole write cycle"
    assert not mon.both_high_seen, "reg_write and reg_read must never be high together"

    mon = _PulseMonitor(dut)
    mon.start()
    await bus.read(0x3)
    await ClockCycles(dut.clk, 2)
    mon.stop()

    assert mon.read_pulses == 1, f"expected exactly one reg_read pulse for a read, saw {mon.read_pulses}"
    assert mon.write_pulses == 0, "reg_write must stay low for the whole read cycle"
    assert not mon.both_high_seen, "reg_write and reg_read must never be high together"


@cocotb.test()
async def test_reg_addr_wdata_stable_during_write(dut):
    """T018 - contract guarantee 2 (FR-008).

    reg_addr and reg_wdata must be stable for the ENTIRE clock period in
    which reg_write is high, not only at the moment the payload happens
    to sample them.
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=2)
    await _reset(dut)

    addr, data = 0x5, 0x3C
    write_task = cocotb.start_soon(bus.write(addr, data))

    # Find the clock edge on which reg_write asserts.
    while True:
        await RisingEdge(dut.clk)
        # Settle first: reg_write is combinational off `state`, which is
        # NBA-updated on this same edge. Without this the poll can notice
        # reg_write one cycle late and then sample reg_addr/reg_wdata during
        # ST_ACK instead of ST_WRITE -- the assertions still pass, but not for
        # the reason the test claims.
        await ReadOnly()
        if int(dut.reg_write.value) == 1:
            break

    addr_at_edge = int(dut.reg_addr.value)
    data_at_edge = int(dut.reg_wdata.value)
    assert addr_at_edge == addr
    assert data_at_edge == data

    # Re-sample just before the next edge -- still the same clock period.
    await Timer(CLK_PERIOD_PS * 0.9, unit="ps")
    assert int(dut.reg_addr.value) == addr_at_edge, "reg_addr moved within the write's clock period"
    assert int(dut.reg_wdata.value) == data_at_edge, "reg_wdata moved within the write's clock period"

    await write_task


@cocotb.test()
async def test_read_data_valid_before_dtack_falls(dut):
    """T018 - contract guarantee 3 (FR-008), C3 (>=1T data-before-DTACK).

    On a read, data_oe is asserted and the read data is already valid on
    data_out before dtack_n falls, matching 68000 timing #31 (DTACK
    asserted -> data valid), which this chip satisfies by driving data a
    full clock ahead of DTACK.
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=3)
    await _reset(dut)

    addr, data = 0x2, 0x7E
    await bus.write(addr, data)

    oe_time_ps = None

    async def _watch_oe():
        nonlocal oe_time_ps
        while True:
            await RisingEdge(dut.clk)
            # Settle before sampling: data_oe is a wire driven by a reg that
            # updates on this same edge, so reading .value straight after
            # RisingEdge returns the pre-update value on Icarus. The DTACK
            # measurement below uses a real FallingEdge trigger, so without
            # this the two timestamps are not on equal footing.
            await ReadOnly()
            if int(dut.data_oe.value) == 0xFF and oe_time_ps is None:
                oe_time_ps = get_sim_time(unit="ps")

    watcher = cocotb.start_soon(_watch_oe())
    read_task = cocotb.start_soon(bus.read(addr))

    await FallingEdge(dut.dtack_n)
    dtack_time_ps = get_sim_time(unit="ps")
    watcher.kill()

    assert oe_time_ps is not None, "data_oe never asserted during the read"
    assert oe_time_ps <= dtack_time_ps - CLK_PERIOD_PS, (
        "data_oe (and therefore data_out) must be valid at least one full "
        "clock before dtack_n falls (contract guarantee 3 / C3)"
    )
    assert int(dut.data_out.value) == data, "data_out must already equal the stored register value"

    result = await read_task
    assert result == data


@cocotb.test()
async def test_release_is_combinational_random_phase(dut):
    """T018 - contract guarantee 4 (FR-008), research R1, C1/C2.

    dtack_n and data_oe must return to their inactive state within
    combinational delay of raw as_n/cs_n going high -- no clock edge in
    between. Checked directly on the raw pins (not through the driver's
    higher-level read()/write() helpers) so the deassertion can be placed
    at several random, non-clock-grid phases per constitution Principle V.
    """
    await _start_clock(dut)
    signals = IpLevelSignals(dut)
    await _reset(dut)

    # Pre-load a register so a real read cycle has something to drive.
    bus = Bus68kMaster(signals, dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=40)
    addr, data = 0x1, 0x66
    await bus.write(addr, data)

    # Random, non-clock-aligned phases (ps) at which we will drop as_n/cs_n
    # mid-cycle, per Principle V ("stimulus that lands exactly on clock
    # edges is forbidden for async inputs").
    # Every phase must leave > the 1 ns observation window below before the next
    # clock edge, otherwise an edge lands inside the window no matter how the DUT
    # behaves and the check cannot distinguish pass from fail. 40001 ps left only
    # 689 ps of margin against CLK_PERIOD_PS=40690, so it always failed.
    phases_ps = [3701, 17123, 29999, 39001, 11111]

    for phase_ps in phases_ps:
        await RisingEdge(dut.clk)

        # Drive a minimal qualified read cycle directly on the raw pins.
        dut.addr.value = addr
        dut.r_w.value = 1
        dut.cs_n.value = 0
        dut.as_n.value = 0
        dut.ds_n.value = 0

        await FallingEdge(dut.dtack_n)
        assert int(dut.data_oe.value) == 0xFF, "data_oe must be asserted once dtack_n falls"

        # Hold the cycle open a little longer, then release at a
        # deliberately sub-clock-period phase.
        await ClockCycles(dut.clk, 2)
        await Timer(phase_ps % CLK_PERIOD_PS, unit="ps")

        edges_before = [0]

        async def _count_edges():
            while True:
                await RisingEdge(dut.clk)
                edges_before[0] += 1

        edge_counter = cocotb.start_soon(_count_edges())

        dut.as_n.value = 1
        dut.cs_n.value = 1
        dut.ds_n.value = 1

        # Sample well within one combinational-delay budget (1 ns) of the
        # release, before any clock edge could have occurred.
        await Timer(1, unit="ns")
        edge_counter.kill()

        assert edges_before[0] == 0, "a clock edge occurred between as_n/cs_n rising and the release check (not combinational)"
        assert int(dut.dtack_n.value) == 1, f"dtack_n did not release combinationally at phase {phase_ps} ps"
        assert int(dut.data_oe.value) == 0, f"data_oe did not release combinationally at phase {phase_ps} ps"

        await ClockCycles(dut.clk, 3)


@cocotb.test()
async def test_data_oe_never_during_write_or_deselect(dut):
    """T018 - contract guarantee 5 (FR-008).

    data_oe must never be asserted while raw r_w is low (a write cycle),
    nor while raw cs_n is high (unselected).
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=5)
    await _reset(dut)

    violations = []

    async def _monitor():
        while True:
            await RisingEdge(dut.clk)
            oe = int(dut.data_oe.value)
            if oe != 0 and (int(dut.r_w.value) == 0 or int(dut.cs_n.value) == 1):
                violations.append((get_sim_time(unit="ns"), oe, int(dut.r_w.value), int(dut.cs_n.value)))

    mon = cocotb.start_soon(_monitor())

    for i in range(20):
        await bus.write(i & 0x7, (0x30 + i) & 0xFF)
    for _ in range(10):
        await bus.unselected_cycle()

    mon.kill()
    assert not violations, f"data_oe asserted during a write or while deselected: {violations}"


@cocotb.test()
async def test_reset_state(dut):
    """T018 - contract guarantee 6 (FR-008), constitution Principle IV.

    After reset, with the bus idle: dtack_n = 1, data_oe = 0, and no
    reg_write/reg_read pulses occur.
    """
    await _start_clock(dut)
    # Construct the driver only to park the bus pins at their idle
    # (deselected) values; reset itself is this test's responsibility.
    _new_bus(dut, seed=6)

    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)

    mon = _PulseMonitor(dut)
    mon.start()
    await ClockCycles(dut.clk, 3)

    assert int(dut.dtack_n.value) == 1, "dtack_n must be inactive (1) during reset"
    assert int(dut.data_oe.value) == 0, "data_oe must be inactive (0) during reset"

    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    mon.stop()

    assert int(dut.dtack_n.value) == 1, "dtack_n must stay inactive after reset release with an idle bus"
    assert int(dut.data_oe.value) == 0, "data_oe must stay inactive after reset release with an idle bus"
    assert mon.write_pulses == 0 and mon.read_pulses == 0, "no spurious reg_write/reg_read pulses around reset"


@cocotb.test()
async def test_aborted_cycle_no_effect_qualified_atomic(dut):
    """T018 - contract guarantee 7 (FR-008), research R2, FR-006.

    A cycle aborted before qualification has no effect on the payload
    register. Once qualified, the access completes atomically: the
    payload never observes a partial/mixed write value, only the old
    value or the complete new one.
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=7)
    await _reset(dut)

    addr = 0x4
    await bus.write(addr, 0x55)
    assert await bus.read(addr) == 0x55

    # Guarantee 7 has TWO clauses; test each with stimulus that actually
    # establishes its precondition.
    #
    # Clause 1 -- a cycle aborted BEFORE qualification has no effect. This
    # cannot be built with aborted_write(): it always asserts DS, and any
    # non-zero DS window may contain a clock edge, so the cycle may
    # legitimately qualify (FR-006 permits the write to complete then). Build
    # the precondition directly instead: assert CS/AS but NEVER assert DS, so
    # raw_qualified never rises and no capture can occur.
    dut.cs_n.value = 0
    dut.as_n.value = 0
    dut.addr.value = addr
    dut.r_w.value = 0
    dut.data_in.value = 0xAA
    await ClockCycles(dut.clk, 4)
    dut.cs_n.value = 1
    dut.as_n.value = 1
    dut.r_w.value = 1
    dut.data_in.value = 0
    await ClockCycles(dut.clk, 4)
    assert await bus.read(addr) == 0x55, (
        "a cycle that never asserted DS must never qualify, so it must not "
        "modify the register (contract guarantee 7, clause 1)"
    )

    # Clause 2 -- once qualified, the access is atomic: the payload sees the
    # old value or the complete new one, never a mix (FR-006).
    await bus.aborted_write(addr, 0xAA)
    got = await bus.read(addr)
    assert got in (0x55, 0xAA), (
        f"aborted write left 0x{got:02X}: must be exactly the old value or the "
        "complete new one, never a mix (FR-006, contract guarantee 7, clause 2)"
    )

    # A qualified write always lands the complete byte in a single clock
    # (research R2's capture-on-one-edge design) -- never a mix of old and
    # new bits.
    seen_values = set()

    async def _sample_write_data():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.reg_write.value):
                seen_values.add(int(dut.reg_wdata.value))

    mon = cocotb.start_soon(_sample_write_data())
    await bus.write(addr, 0x99)
    mon.kill()

    assert await bus.read(addr) == 0x99
    assert seen_values == {0x99}, f"write must be atomic -- observed intermediate reg_wdata values {seen_values}"


@cocotb.test()
async def test_all_registers_addressable(dut):
    """T018 - parameterization smoke test (contracts/bus68k_if.md Parameters).

    ADDR_BITS is configurable (the sibling ttihp-spi-m68k project may use
    a different width). This bench instantiates ADDR_BITS=3, so at
    minimum, confirm all 2**3 = 8 registers are independently
    addressable, readable and writable.
    """
    await _start_clock(dut)
    bus = _new_bus(dut, seed=8)
    await _reset(dut)

    addr_bits = len(dut.addr.value)
    num_regs = 1 << addr_bits
    assert num_regs == 8, f"this bench instantiates ADDR_BITS=3 (8 registers), got {num_regs}"

    for addr in range(num_regs):
        await bus.write(addr, (0xC0 + addr) & 0xFF)

    for addr in range(num_regs):
        expected = (0xC0 + addr) & 0xFF
        got = await bus.read(addr)
        assert got == expected, f"register {addr}: expected {expected:#04x}, got {got:#04x}"
