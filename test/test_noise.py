# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Noise channel rate-sweep tests for tt_um_benpayne_sound_chip (audit gap #1).

Covers FR-022: "one pseudo-random noise channel with 16 programmable rate
settings." Before this file, noise was only ever exercised at rate=3
(test_tone_pwm.test_no_overflow_at_max), so `src/psg_noise.v`'s prescaler
(`presc_target = {rate,1'b0}+1`, i.e. the LFSR shifts once every
`2*(rate+1)` 192 kHz ticks -- research.md R8) was completely unverified
across its 16-value range.

Noise has no clean "frequency" to lock a PLL-style measurement onto (it's
pseudo-random), so this file measures it two ways:

  1. Statistically: with only the noise channel enabled, PWM duty codes
     (`tbutil.pwm_meter.sample_pwm_periods`) are one 192 kHz-tick sample of
     the mixed signal each -- and with a single bipolar channel active, the
     duty code takes exactly two values, so a code-to-code transition
     happens if and only if the LFSR's bit 0 differs from what it was one
     tick ago. A shift happens on schedule every `2*(rate+1)` ticks
     regardless of rate; whether a given shift *changes* bit 0 is
     effectively a coin flip (the feedback tap XORs two other LFSR bits
     that have already been thoroughly mixed by prior shifts), so the
     transition rate should track the shift rate at a roughly constant
     ~50% "did it flip" factor. That makes transition count over a FIXED
     tick window a direct, monotonically-decreasing-with-rate proxy for
     the underlying (unmeasurable-directly) shift rate.

  2. Exactly: the reference model (`tbutil.psg_model.PsgModel`) implements
     the identical prescaler arithmetic. Since both the RTL and the model
     are deterministic finite-state machines seeded identically at reset,
     ticking a model in lock-step with the DUT's own `clk` (the same
     technique test_i2s.py's `_run_model_live` uses for I2S frames) predicts
     the DUT's PWM duty code bit-for-bit, up to a fixed pipeline offset
     that a best-alignment search absorbs -- giving an exact, not
     statistical, check of the prescaler's cadence for a couple of rates.

Also checked directly, independent of both of the above: over a long run at
several rates, the LFSR must not lock up (research.md R8: reset to 17'd1 so
it can never sit at all-zero forever) -- its output must vary.

RTL is frozen and believed correct (this is a coverage-gap-closing file, not
a debugging session); do not weaken these assertions.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tbutil.bus68k_master import Bus68kMaster, ChipLevelSignals
from tbutil.dutbits import uo_bit
from tbutil.psg_model import PsgModel
from tbutil.pwm_meter import sample_pwm_periods

# Chip clock: 24.576 MHz nominal (contracts/pinout-and-bus-timing.md), same
# constant used throughout this suite (test_tone_pwm.py, test_i2s.py, ...).
CLK_PERIOD_PS = 40690
CLK_HZ = 1e12 / CLK_PERIOD_PS

# PWM carrier == tone/noise tick rate: clk/128 = 192 kHz (research.md R5).
CARRIER_HZ = CLK_HZ / 128

# Register indices (contracts/register-map.md).
A_LO, A_CTRL, B_LO, B_CTRL, C_LO, C_CTRL, NOISE, ENABLE = range(8)

# ENABLE bits (contracts/register-map.md). Reset value is 0x70 (outputs on,
# channels off); every write below ORs its channel bit onto 0x70.
A_EN, B_EN, C_EN, NOISE_EN, PWM_EN, I2S_EN, SPDIF_EN = (1 << b for b in range(7))

# ui_in bit positions (contracts/pinout-and-bus-timing.md), for driving an
# idle bus during reset, before a Bus68kMaster exists to own those pins.
_CS_N_BIT, _AS_N_BIT, _RW_BIT, _DS_N_BIT = 0, 1, 2, 3
_IDLE_UI_IN = (1 << _CS_N_BIT) | (1 << _AS_N_BIT) | (1 << _RW_BIT) | (1 << _DS_N_BIT)

# PWM_OUT pin (contracts/pinout-and-bus-timing.md: uo[1]).
_PWM_BIT = 1


def _pwm_pin(dut):
    return uo_bit(dut, _PWM_BIT)


async def _reset_dut(dut):
    """Start the clock, hold the bus idle, and release reset -- same
    pattern as test_tone_pwm.py's `_reset_dut`."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = _IDLE_UI_IN
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


def _make_bus(dut, seed: int) -> "Bus68kMaster":
    dut._log.info(f"test_noise: Bus68kMaster seed={seed}")
    return Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=seed)


def _transitions(codes):
    """Count how many times consecutive duty codes differ."""
    return sum(1 for a, b in zip(codes, codes[1:]) if a != b)


def _dwell_lengths(codes):
    """Run-length-encode a sequence of duty codes into consecutive equal-
    value run lengths (copied from test_tone_pwm.py's helper of the same
    name -- both files are independent verification and this is the whole
    of what's needed here, not worth an inter-test-file import)."""
    lengths = []
    run = 1
    for prev, cur in zip(codes, codes[1:]):
        if cur == prev:
            run += 1
        else:
            lengths.append(run)
            run = 1
    lengths.append(run)
    return lengths


async def _run_model_ticks_live(dut, model, duties):
    """Tick `model` once per 128 `clk` cycles (one 192 kHz tick), appending
    `model.pwm_duty()` *before* each `step_tick()` call.

    Mirrors test_i2s.py's `_run_model_live`, which samples before stepping
    because the RTL's mixer slots (count7 120-123) run and settle before
    the tone/noise toggle that happens at the tick pulse itself (count7==
    127) -- so the duty value that ends up driving the *next* PWM period is
    a function of the *pre-toggle* state for this tick. Sampling before
    `step_tick()` reproduces that same fixed one-tick relationship; any
    remaining constant skew (e.g. from register-write timing before this
    coroutine's first sample) is absorbed by the caller's best-offset
    search rather than reasoned about here.
    """
    while True:
        await ClockCycles(dut.clk, 128)
        duties.append(model.pwm_duty())
        model.step_tick()


async def _crosscheck_rate_against_model(dut, rate, seed, vol=12, n_samples=300, min_offset_headroom=64):
    """Exact (bit-for-bit, up to a fixed alignment offset) cross-check of
    the DUT's PWM duty sequence against PsgModel's, at one noise rate.

    `min_offset_headroom` guarantees the best-offset search below actually
    has room to search: `model_task` starts ticking immediately after
    reset, well before the two register writes below (which take at most a
    few tens of ticks worth of simulated time), so without an explicit
    warm-up wait `len(model_duties)` could end up barely bigger than
    `n_samples` -- e.g. exactly equal, leaving `max_offset == 0` and a
    "search" over exactly one alignment. A passing result from a search
    that was never actually free to try more than one offset would prove
    nothing; the warm-up wait below and the assertion on `max_offset`
    together make sure a green result here reflects a real search.
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed)
    model = PsgModel(clk_hz=int(CLK_HZ))

    model_duties = []
    model_task = cocotb.start_soon(_run_model_ticks_live(dut, model, model_duties))
    try:
        val = (vol << 4) | rate
        await bus.write(NOISE, val)
        model.write_reg(NOISE, val)
        await bus.write(ENABLE, 0x70 | NOISE_EN)
        model.write_reg(ENABLE, 0x70 | NOISE_EN)

        # Warm-up: let the model accumulate enough extra ticks beyond
        # n_samples that the search below has real headroom, not just
        # whatever few ticks elapsed during the two writes above.
        await ClockCycles(dut.clk, min_offset_headroom * 128)

        codes = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=n_samples, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
        await ClockCycles(dut.clk, 1)
    finally:
        model_task.kill()

    max_offset = len(model_duties) - n_samples
    assert max_offset >= min_offset_headroom, (
        f"rate={rate}: model trace only gives {max_offset + 1} alignment offset(s) to search "
        f"({len(model_duties)} model ticks vs {n_samples} DUT samples) -- below the "
        f"{min_offset_headroom}-offset minimum headroom this search needs to be a real search "
        "rather than a lucky single guess. Increase the warm-up window or decrease n_samples."
    )

    best_offset, best_bad = None, None
    for offset in range(0, max_offset + 1):
        window = model_duties[offset : offset + n_samples]
        n_bad = sum(1 for a, b in zip(window, codes) if a != b)
        if best_bad is None or n_bad < best_bad:
            best_offset, best_bad = offset, n_bad
        if n_bad == 0:
            break

    assert best_bad == 0, (
        f"rate={rate}: DUT PWM duty codes never matched PsgModel's predicted duty sequence "
        f"exactly at any of the {max_offset + 1} tick alignments tried (best: offset="
        f"{best_offset}, {best_bad}/{n_samples} mismatches) -- FR-022 prescaler cadence "
        "diverges from the reference model."
    )


@cocotb.test()
async def test_noise_rate_sweep_monotonic(dut):
    """FR-022.

    Sweep rate 0..15 (NOISE register, only the noise channel enabled) and
    show the LFSR shift cadence changes with rate: over a FIXED tick
    window, the PWM duty-code transition count (see module docstring for
    why this is a valid proxy for shift rate) must fall as rate rises, and
    rate 0 vs rate 15 must differ by roughly the ~16x the prescaler formula
    (`2*(rate+1)` ticks/shift) predicts.

    Tolerances:
      - Monotonicity slack (25%): transitions are a statistical count
        (each shift flips bit 0 with roughly, not exactly, 50% probability,
        and the *number* of shifts in a fixed window is itself a Poisson-
        ish random count). At WINDOW=2400 ticks, rate=15 (the noisiest
        comparison, fewest expected shifts: ~75) has an expected
        transition count of ~37 with a binomial standard deviation of
        ~4.3 (~11.5% relative) -- comparable rates differ by only a few
        transitions, so a strictly non-increasing sequence would
        occasionally fail by chance alone. 25% is comfortably above that
        per-step noise floor while still failing hard on a genuinely flat
        or increasing curve (which would need a much bigger deviation to
        hide, since the *true* curve falls by roughly 2*(rate+1)/(2*rate+1)
        each step, i.e. up to 3x at the low end).
      - Ratio window (8x-32x, i.e. within a factor of 2 of the 16x the
        formula predicts for rate 0 vs rate 15's shift-rate ratio,
        `2*16/2*1 = 16`): generous because the ratio of two noisy counts
        compounds both sides' statistical error, and because the "did the
        shift flip bit 0" factor is only *approximately* independent of
        rate (it is exactly 50% only in the limit of a well-mixed LFSR
        state, which the fast, frequently-shifting low-rate end reaches
        more thoroughly than the slow, rarely-shifting high-rate end
        within the same tick window).
    """
    WINDOW = 2400
    churn = []
    for rate in range(16):
        seed = 0x2E010000 + rate
        await _reset_dut(dut)
        bus = _make_bus(dut, seed)
        vol = 12
        await bus.write(NOISE, (vol << 4) | rate)
        await bus.write(ENABLE, 0x70 | NOISE_EN)
        codes = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=WINDOW, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
        transitions = _transitions(codes)
        churn.append(transitions)
        dut._log.info(f"noise rate sweep: rate={rate} transitions={transitions}/{WINDOW} codes")

    for rate in range(16):
        assert churn[rate] > 0, (
            f"rate={rate}: zero PWM duty transitions over {WINDOW} ticks -- the LFSR appears "
            "stuck (or the noise channel produced no audible bit at all)."
        )

    SLACK = 1.25
    for rate in range(15):
        assert churn[rate + 1] <= churn[rate] * SLACK, (
            f"noise transition count did not fall monotonically with rate (within the "
            f"documented {SLACK}x statistical slack): rate={rate} -> {churn[rate]} transitions, "
            f"rate={rate + 1} -> {churn[rate + 1]} transitions. Full sweep: {churn}."
        )

    ratio = churn[0] / churn[15]
    assert 8.0 <= ratio <= 32.0, (
        f"rate 0 vs rate 15 transition-count ratio is {ratio:.2f}x, expected roughly 16x "
        f"(2*(15+1) / 2*(0+1)) within a factor-of-2 window [8x, 32x]. "
        f"churn[0]={churn[0]}, churn[15]={churn[15]}, full sweep: {churn}."
    )


@cocotb.test()
async def test_noise_shift_period_matches_formula(dut):
    """FR-022.

    Directly verifies psg_noise.v's prescaler formula
    (`presc_target = {rate,1'b0}+1`, shift every `2*(rate+1)` ticks,
    research.md R8) rather than inferring it statistically: with a single
    bipolar noise channel active, the PWM duty code only changes when the
    LFSR shifts AND the shift happens to flip bit 0, so every dwell
    (run-length of an unchanging duty code) must be a whole-number multiple
    of the shift period -- a dwell of anything else would mean a shift
    happened off-schedule.

    Checked at several representative rates: 0 (fastest, period=2 ticks),
    5, 11, and 15 (slowest, period=32 ticks). The first and last dwell of
    each captured run are dropped, as in test_tone_pwm.py's dwell-based
    tests: they're truncated by the sampling window's arbitrary start/end
    phase, not evidence of anything.
    """
    for rate in (0, 5, 11, 15):
        period_ticks = 2 * (rate + 1)
        seed = 0x2E020000 + rate
        await _reset_dut(dut)
        bus = _make_bus(dut, seed)
        vol = 12
        await bus.write(NOISE, (vol << 4) | rate)
        await bus.write(ENABLE, 0x70 | NOISE_EN)

        # Enough ticks for ~20 shifts, so several dwells are captured even
        # at the slowest rate.
        count = max(200, 20 * period_ticks)
        codes = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=count, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
        dwells = _dwell_lengths(codes)
        interior = dwells[1:-1]
        assert interior, f"rate={rate}: no interior dwells captured -- window too short (count={count})"

        bad = [d for d in interior if d % period_ticks != 0]
        assert not bad, (
            f"rate={rate}: {len(bad)}/{len(interior)} dwell(s) were not a whole multiple of "
            f"the expected shift period ({period_ticks} ticks = 2*(rate+1)): {bad[:10]} "
            f"(full dwell list: {interior}). This is the observable signature of the "
            "prescaler's cadence not matching research.md R8's formula."
        )


@cocotb.test()
async def test_noise_never_locks_up(dut):
    """FR-022 / research.md R8: the 17-bit LFSR resets to 17'd1 specifically
    so it can never lock at all-zeros. Over a long run at several rates
    (including rate=15, the slowest -- the case most likely to look
    "stuck" over a short window), the PWM duty code must vary, not sit at
    a single constant value forever.
    """
    for rate in (0, 8, 15):
        seed = 0x2E030000 + rate
        await _reset_dut(dut)
        bus = _make_bus(dut, seed)
        vol = 12
        await bus.write(NOISE, (vol << 4) | rate)
        await bus.write(ENABLE, 0x70 | NOISE_EN)

        # >=250 shifts worth of ticks even at the slowest rate (period 32).
        count = 8_000
        codes = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=count, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
        assert len(set(codes)) > 1, (
            f"rate={rate}: PWM duty code held a single constant value across {count} ticks "
            "-- the noise LFSR appears locked up (research.md R8 requires it can never do "
            "this, by resetting to a nonzero seed)."
        )


@cocotb.test()
async def test_noise_matches_reference_model(dut):
    """FR-022.

    Exact (not statistical) cross-check: the DUT's PWM duty sequence, with
    only the noise channel enabled, must match PsgModel's bit-for-bit (up
    to a fixed pipeline alignment offset) at two representative rates.
    Since both the RTL prescaler/LFSR and the model implement the identical
    deterministic arithmetic from the same reset state, any mismatch here
    is a real divergence, not a statistical fluke -- see module docstring.
    """
    for rate in (3, 11):
        await _crosscheck_rate_against_model(dut, rate, seed=0x2E040000 + rate)
