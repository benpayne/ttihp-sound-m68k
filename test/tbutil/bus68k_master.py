# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""68000 bus-cycle driver for the PSG sound chip's memory-mapped register bus.

Task T016 (specs/001-psg-sound-chip/tasks.md, User Story 1).

This module provides a `Bus68kMaster` that drives full 68000 asynchronous
bus cycles (read / write / aborted / unselected / back-to-back) against
either:

- the whole chip (`ChipLevelSignals`, packing signals into `dut.ui_in` /
  `dut.uio_in` and reading `dut.uo_out` / `dut.uio_out` / `dut.uio_oe`), or
- a standalone `bus68k_if` instance (`IpLevelSignals`, using its named
  ports directly).

`Bus68kMaster` only talks to a `BusSignals` adapter, so the same cycle
logic (and the same randomized-timing discipline) is shared by both the
chip-level tests in this file's siblings (test_bus.py, test_regs.py) and
the standalone `bus68k_if` unit bench (test/bus68k_if/), per
contracts/bus68k_if.md's verification note.

**Timing discipline (constitution Principle V, research.md R12
"clock-grid race")**: every stimulus edge this driver produces is placed
at a randomized offset that is deliberately never a whole multiple of the
DUT clock period, and never within 15% of a period boundary either
(offsets are drawn from the *middle* 70% of a clock period: fraction in
[0.15, 0.85]). Stimulus that lands exactly on -- or immediately next to
-- a clock edge races the sampling flops and can hide a real bug. Keeping
a comfortable margin from the edges also makes it safe to check
"combinational, no intervening clock edge" properties with a plain
`Timer(1, unit="ns")`: 1 ns is under 2.5% of the 40.69 ns nominal clock
period, so a release placed mid-cycle by this driver cannot have a clock
edge fall inside that 1 ns observation window.
"""

import logging
import os
import random

from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer

_LOG = logging.getLogger(__name__)

# ui_in bit positions, per contracts/pinout-and-bus-timing.md (matches
# test_reset.py's convention).
CS_N = 1 << 0
AS_N = 1 << 1
R_W = 1 << 2
DS_N = 1 << 3
A1_SHIFT = 4  # ui_in[4:6] = A1..A3, register select

# Idle bus encoding for ui_in: every active-low strobe deasserted (1),
# R_W=1 (read/idle), address 0, bit 7 (unused) tied low.
BUS_IDLE_UI_IN = CS_N | AS_N | R_W | DS_N

# uo_out[0] = DTACK_n, per contracts/pinout-and-bus-timing.md.
DTACK_N_BIT = 1 << 0


class BusSignals:
    """Abstract adapter between `Bus68kMaster` and a concrete DUT's pins.

    A concrete adapter packs/unpacks whatever the DUT actually exposes
    (a single `ui_in`/`uio_*` vector at chip level, or named ports at
    `bus68k_if` unit-bench level) behind this fixed, DUT-agnostic API.
    """

    def set_cs_n(self, v):
        raise NotImplementedError

    def set_as_n(self, v):
        raise NotImplementedError

    def set_ds_n(self, v):
        raise NotImplementedError

    def set_rw(self, v):
        raise NotImplementedError

    def set_addr(self, v):
        raise NotImplementedError

    def drive_data(self, v):
        """Drive the shared data bus with byte `v` (as the bus master)."""
        raise NotImplementedError

    def release_data(self):
        """Stop driving the shared data bus (as the bus master)."""
        raise NotImplementedError

    def get_data(self):
        """Sample whatever the DUT is currently driving onto the data bus."""
        raise NotImplementedError

    def get_dtack_n(self):
        raise NotImplementedError

    def get_oe(self):
        """Return the DUT's data-bus output-enable, as a raw int (0 = released)."""
        raise NotImplementedError


class ChipLevelSignals(BusSignals):
    """Packs bus signals into `dut.ui_in` / `dut.uio_in`; reads `dut.uo_out`,
    `dut.uio_out`, `dut.uio_oe`.

    Bit layout (contracts/pinout-and-bus-timing.md):
        ui_in[0] = CS_n, [1] = AS_n, [2] = R_W, [3] = DS_n,
        ui_in[4:6] = A1..A3, ui_in[7] = unused (tied low).
        uo_out[0] = DTACK_n.
    """

    def __init__(self, dut):
        self._dut = dut
        self._ui_in = BUS_IDLE_UI_IN
        self._uio_in = 0
        dut.ui_in.value = self._ui_in
        dut.uio_in.value = self._uio_in

    def _set_ui_in_bit(self, mask, v):
        if v:
            self._ui_in |= mask
        else:
            self._ui_in &= ~mask & 0xFF
        self._dut.ui_in.value = self._ui_in

    def set_cs_n(self, v):
        self._set_ui_in_bit(CS_N, v)

    def set_as_n(self, v):
        self._set_ui_in_bit(AS_N, v)

    def set_ds_n(self, v):
        self._set_ui_in_bit(DS_N, v)

    def set_rw(self, v):
        self._set_ui_in_bit(R_W, v)

    def set_addr(self, v):
        self._ui_in = (self._ui_in & ~(0x7 << A1_SHIFT)) & 0xFF
        self._ui_in |= (v & 0x7) << A1_SHIFT
        self._dut.ui_in.value = self._ui_in

    def drive_data(self, v):
        self._uio_in = v & 0xFF
        self._dut.uio_in.value = self._uio_in

    def release_data(self):
        self._uio_in = 0
        self._dut.uio_in.value = self._uio_in

    def get_data(self):
        return int(self._dut.uio_out.value) & 0xFF

    def get_dtack_n(self):
        return (int(self._dut.uo_out.value) >> 0) & 1

    def get_oe(self):
        return int(self._dut.uio_oe.value) & 0xFF


class IpLevelSignals(BusSignals):
    """Named ports on a standalone `bus68k_if` instance, per
    contracts/bus68k_if.md: `cs_n`, `as_n`, `ds_n`, `r_w`, `addr`,
    `data_in`, `data_out`, `data_oe`, `dtack_n`.
    """

    def __init__(self, dut):
        self._dut = dut
        dut.cs_n.value = 1
        dut.as_n.value = 1
        dut.ds_n.value = 1
        dut.r_w.value = 1
        dut.addr.value = 0
        dut.data_in.value = 0

    def set_cs_n(self, v):
        self._dut.cs_n.value = int(bool(v))

    def set_as_n(self, v):
        self._dut.as_n.value = int(bool(v))

    def set_ds_n(self, v):
        self._dut.ds_n.value = int(bool(v))

    def set_rw(self, v):
        self._dut.r_w.value = int(bool(v))

    def set_addr(self, v):
        self._dut.addr.value = v

    def drive_data(self, v):
        self._dut.data_in.value = v & 0xFF

    def release_data(self):
        self._dut.data_in.value = 0

    def get_data(self):
        return int(self._dut.data_out.value) & 0xFF

    def get_dtack_n(self):
        return int(self._dut.dtack_n.value) & 1

    def get_oe(self):
        return int(self._dut.data_oe.value)


class Bus68kMaster:
    """Drives 68000-style asynchronous bus cycles against a `BusSignals`
    adapter.

    All signals are active-low except `R_W` (1 = read), matching the
    68000 and contracts/pinout-and-bus-timing.md. Cycle shapes follow
    research.md R1/R2:

    - **Write**: assert CS_n+AS_n with addr/R_W=0/data driven, then
      assert DS_n one CPU clock later (68000 #22), wait for DTACK_n low,
      then release AS_n/DS_n/CS_n and stop driving data.
    - **Read**: assert CS_n+AS_n+DS_n together with addr and R_W=1, wait
      for DTACK_n low, sample data, then release.

    Every stimulus edge is placed at a randomized, non-clock-aligned
    offset (see module docstring); the seed is logged for reproducibility
    (Principle I).
    """

    #: One 68000 CPU clock at the 16 MHz target (contract C5/#22): DS
    #: asserts one CPU clock after AS on a write. This value is not a
    #: multiple of the 40.69 ns chip clock, so it is off-grid even before
    #: the extra per-edge jitter below is added.
    # Contract C6 / 68000 #15: AS_n must stay HIGH at least 1.25 clk (51 ns)
    # between cycles; the datasheet minimum at 16 MHz is 60 ns. Without this the
    # bench drives back-to-back cycles no real 68000 can produce, and the chip's
    # strobe synchronizer can legitimately miss the AS-high pulse entirely.
    MIN_AS_HIGH_NS = 60.0
    DS_AFTER_AS_NS = 62.5

    #: Guarantees at least ~4 chip clocks of headroom below the typical
    #: 4-5 clock DTACK latency (research.md R1), so an "aborted" cycle
    #: usually really does abort before acknowledge, while still varying.
    ABORT_DELAY_MIN_CLOCKS = 0.5
    ABORT_DELAY_MAX_CLOCKS = 3.0

    DEFAULT_DTACK_TIMEOUT_CLOCKS = 100

    def __init__(self, signals, clk, clk_period_ps=40690, seed=None):
        self.signals = signals
        self.clk = clk
        self.clk_period_ps = clk_period_ps
        self.seed = seed if seed is not None else int(os.environ["COCOTB_BUS_SEED"]) if os.environ.get("COCOTB_BUS_SEED") else random.SystemRandom().randrange(2**32)
        self._rng = random.Random(self.seed)
        self.log_seed()

    def log_seed(self):
        """Log the RNG seed so a failing randomized run can be reproduced."""
        _LOG.info("Bus68kMaster seed=%d", self.seed)

    # -- timing helpers -----------------------------------------------

    def _offgrid_ps(self, min_frac=0.15, max_frac=0.85):
        """A randomized delay, in ps, that is a mid-cycle fraction of one
        clock period -- never near 0% or 100% of a period, so it never
        lands on (or immediately beside) a clock edge."""
        frac = self._rng.uniform(min_frac, max_frac)
        return frac * self.clk_period_ps

    async def _delay_ps(self, ps):
        await Timer(max(1, round(ps)), unit="ps")

    async def _offgrid_delay(self):
        await self._delay_ps(self._offgrid_ps())

    async def _inter_cycle_gap(self):
        """Idle before starting a cycle: guarantees the contract C6 / 68000 #15
        minimum AS-high time, plus off-grid jitter so no edge lands on a clock
        edge (constitution Principle V)."""
        await self._delay_ps(self.MIN_AS_HIGH_NS * 1000.0 + self._offgrid_ps())

    async def _ds_after_as_delay(self):
        """~one CPU clock (68000 #22), plus off-grid jitter."""
        await self._delay_ps(self.DS_AFTER_AS_NS * 1000.0 + self._offgrid_ps())

    async def _wait_dtack_low(self, timeout_clocks=None):
        timeout_clocks = timeout_clocks or self.DEFAULT_DTACK_TIMEOUT_CLOCKS
        for _ in range(timeout_clocks):
            await RisingEdge(self.clk)
            if self.signals.get_dtack_n() == 0:
                return
        raise TimeoutError(
            f"DTACK_n not asserted within {timeout_clocks} clocks "
            f"(seed={self.seed})"
        )

    # -- low-level cycle primitives (no leading random pre-delay; used
    #    by back_to_back() to get an exact, controlled AS-high gap) ----

    async def _do_write(self, addr, data):
        s = self.signals
        s.set_addr(addr)
        s.set_rw(0)
        s.drive_data(data)
        s.set_cs_n(0)
        s.set_as_n(0)
        await self._ds_after_as_delay()
        s.set_ds_n(0)
        await self._wait_dtack_low()
        await self._offgrid_delay()
        s.set_as_n(1)
        s.set_ds_n(1)
        s.set_cs_n(1)
        s.release_data()
        s.set_rw(1)

    async def _do_read(self, addr):
        s = self.signals
        s.set_addr(addr)
        s.set_rw(1)
        s.release_data()
        s.set_cs_n(0)
        s.set_as_n(0)
        s.set_ds_n(0)
        await self._wait_dtack_low()
        # Settle before sampling. _wait_dtack_low() returns ON a clock edge, and
        # uio_out is a wire driven by registers that update on that same edge, so
        # reading .value immediately can capture a pre-settle mix of the old and
        # new drive -- seen as a read returning a third value that is neither the
        # old nor the new register contents (FR-006 abort test, seed 886289433).
        # This is the same cocotb/Icarus race as the IP bench's _watch_oe helper.
        await ReadOnly()
        data = s.get_data() & 0xFF
        await NextTimeStep()   # leave the read-only phase before driving again
        await self._offgrid_delay()
        s.set_as_n(1)
        s.set_ds_n(1)
        s.set_cs_n(1)
        return data

    # -- public API -----------------------------------------------------

    async def write(self, addr, data):
        """Drive a full 68000 write cycle; returns once AS_n/DS_n/CS_n
        have been released."""
        await self._inter_cycle_gap()
        await self._do_write(addr, data)

    async def read(self, addr):
        """Drive a full 68000 read cycle; returns the byte read."""
        await self._inter_cycle_gap()
        return await self._do_read(addr)

    async def aborted_write(self, addr, data, abort_delay_ps=None):
        """Like `write`, but releases AS_n (and DS_n/CS_n) before DTACK_n
        is ever observed low -- models a bus error / retry abort."""
        s = self.signals
        await self._inter_cycle_gap()
        s.set_addr(addr)
        s.set_rw(0)
        s.drive_data(data)
        s.set_cs_n(0)
        s.set_as_n(0)
        await self._ds_after_as_delay()
        s.set_ds_n(0)
        if abort_delay_ps is None:
            frac_clocks = self._rng.uniform(
                self.ABORT_DELAY_MIN_CLOCKS, self.ABORT_DELAY_MAX_CLOCKS
            )
            abort_delay_ps = frac_clocks * self.clk_period_ps
        await self._delay_ps(abort_delay_ps)
        s.set_as_n(1)
        s.set_ds_n(1)
        s.set_cs_n(1)
        s.release_data()
        s.set_rw(1)

    async def aborted_read(self, addr, abort_delay_ps=None):
        """Like `read`, but releases the strobes before DTACK_n is ever seen.

        The read-side mirror of `aborted_write`. Needed because that helper
        drives r_w=0 and so can only ever produce the FSM's WRITE-side
        transitions -- READ->IDLE has no stimulus without this.
        """
        s = self.signals
        await self._inter_cycle_gap()
        s.set_addr(addr)
        s.set_rw(1)
        s.release_data()
        s.set_cs_n(0)
        s.set_as_n(0)
        s.set_ds_n(0)
        if abort_delay_ps is None:
            frac_clocks = self._rng.uniform(
                self.ABORT_DELAY_MIN_CLOCKS, self.ABORT_DELAY_MAX_CLOCKS
            )
            abort_delay_ps = frac_clocks * self.clk_period_ps
        await self._delay_ps(abort_delay_ps)
        s.set_as_n(1)
        s.set_ds_n(1)
        s.set_cs_n(1)

    async def unselected_cycle(self, addr=0, write=False, abort_delay_ps=None):
        """Toggle AS_n/DS_n (and drive an address/data pattern) with CS_n
        held HIGH throughout -- a cycle addressed to some other
        peripheral on the shared bus.

        By default (`abort_delay_ps=None`) this runs its normal length:
        AS_n asserted, DS_n asserted `_ds_after_as_delay()` later, held
        for one more off-grid-jittered clock, then every strobe releases
        -- byte-for-byte what this method did before `abort_delay_ps`
        existed, so every caller that doesn't pass it is unaffected.

        Passing `abort_delay_ps` instead releases AS_n/DS_n/CS_n that
        many picoseconds after DS_n asserts, in place of the normal
        jittered hold -- modeling the ACTUAL target device (not this
        chip; CS_n never goes low) hitting its own bus error/retry and
        cutting the cycle short, the unselected-cycle counterpart of what
        `aborted_write`/`aborted_read` model for a cycle addressed to
        this chip. Unlike those two, `None` here means "run the normal,
        non-aborted length," not "pick a random abort delay" -- this
        method has both a completed and an aborted shape, and the
        parameter itself is what selects between them, so it can't also
        double as an on/off switch the way it does for aborted_write/
        aborted_read (which are never anything but an abort).
        """
        s = self.signals
        await self._inter_cycle_gap()
        s.set_cs_n(1)
        s.set_addr(addr)
        s.set_rw(0 if write else 1)
        if write:
            s.drive_data(self._rng.randrange(256))
        else:
            s.release_data()
        s.set_as_n(0)
        await self._ds_after_as_delay()
        s.set_ds_n(0)
        if abort_delay_ps is None:
            await self._offgrid_delay()
        else:
            await self._delay_ps(abort_delay_ps)
        s.set_as_n(1)
        s.set_ds_n(1)
        s.set_cs_n(1)
        s.release_data()
        s.set_rw(1)

    async def back_to_back(self, ops, as_high_ns=60.0):
        """Run consecutive cycles separated by a fixed AS-high gap.

        `ops` is a sequence of `("write", addr, data)` / `("read", addr)`
        tuples. Only the first op gets a randomized off-grid lead-in;
        subsequent ops are started exactly `as_high_ns` after the
        previous op released AS_n, to exercise the 68000 #15 minimum
        (contract C6). Returns a list with `None` for each write and the
        byte read for each read.
        """
        results = []
        for i, op in enumerate(ops):
            if i == 0:
                # The LEADING gap must honour C6 too: this call may follow a
                # previous cycle immediately (test_back_to_back_cycles invokes
                # back_to_back() right after a plain read()), and a 6-35 ns
                # _offgrid_delay() can leave an AS-high excursion that no clock
                # edge ever samples -- a genuinely missed pulse the chip cannot
                # recover from. Only the i>0 gaps are the deliberate exact-60 ns
                # ones below.
                await self._inter_cycle_gap()
            else:
                await Timer(round(as_high_ns * 1000.0), unit="ps")
            kind = op[0]
            if kind == "write":
                _, addr, data = op
                await self._do_write(addr, data)
                results.append(None)
            elif kind == "read":
                _, addr = op
                results.append(await self._do_read(addr))
            else:
                raise ValueError(f"unknown back_to_back op kind {kind!r}")
        return results
