# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Multi-channel independence and PWM-disable tests for
tt_um_benpayne_sound_chip (audit gap #2).

Covers:
  - FR-020: "three independent square-wave tone channels."
  - SC-003: every equal-tempered note reproduced within +-10 cents (A0-C6).
  - FR-035: "each output format MUST be individually disable-able by
    software," specifically the PWM path.

Before this file, only channel A's pitch had ever been measured
(test_tone_pwm.py exercises A_LO/A_CTRL exclusively) and nothing checked
that B and C are wired to their OWN registers rather than, say, all three
being aliases of the same counter, or B and C being swapped. Nothing
checked that clearing ENABLE's pwm_en bit actually silences the pin (only
test_i2s.py's test_i2s_disable exists, and that is the I2S enable bit, a
different register field entirely).

RTL is frozen and believed correct (this is a coverage-gap-closing file,
not a debugging session); do not weaken these assertions.
"""

import math

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, First, ReadOnly

from tbutil.bus68k_master import Bus68kMaster, ChipLevelSignals
from tbutil.dutbits import uo_bit
from tbutil.psg_model import PsgModel
from tbutil.pwm_meter import measure_tone_freq, sample_pwm_periods

# Chip clock: 24.576 MHz nominal (contracts/pinout-and-bus-timing.md).
CLK_PERIOD_PS = 40690
CLK_HZ = 1e12 / CLK_PERIOD_PS

# PWM carrier: clk/128 = 192 kHz (research.md R5).
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

_A4_HZ = 440.0
_C4_HZ = 261.63


def _pwm_pin(dut):
    return uo_bit(dut, _PWM_BIT)


def _cents(measured_hz: float, target_hz: float) -> float:
    return 1200.0 * math.log2(measured_hz / target_hz)


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
    dut._log.info(f"test_channels: Bus68kMaster seed={seed}")
    return Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=seed)


def _dwell_lengths(codes):
    """Run-length-encode a sequence of duty codes into consecutive equal-
    value run lengths (copied from test_tone_pwm.py's helper of the same
    name -- both files are independent verification)."""
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


async def _check_channel_pitch(dut, bus, lo_idx, ctrl_idx, model_ch, label):
    """Program `lo_idx`/`ctrl_idx` for A4 then C4 and assert the recovered
    PWM tone is within +-10 cents of each (SC-003), the same method
    test_tone_pwm.test_a4_pitch uses for channel A."""
    for note, n, target_hz, min_edges, timeout_ns in (
        ("A4", 218, _A4_HZ, 3500, 60_000_000),
        ("C4", 367, _C4_HZ, 3000, 90_000_000),
    ):
        await bus.write(lo_idx, n & 0xFF)
        await bus.write(ctrl_idx, 0xB0 | (n >> 8))  # vol 11, commits pitch

        freq = await measure_tone_freq(
            _pwm_pin(dut), dut.clk, min_edges=min_edges, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ, timeout_ns=timeout_ns
        )

        model = PsgModel(clk_hz=int(CLK_HZ))
        model.write_reg(lo_idx, n & 0xFF)
        model.write_reg(ctrl_idx, 0xB0 | (n >> 8))
        expected_hz = model.tone_freq_hz(model_ch)

        cents = _cents(freq, target_hz)
        assert abs(cents) <= 10, (
            f"channel {label}, note {note} (N={n}): measured {freq:.4f} Hz on PWM_OUT, "
            f"{cents:+.2f} cents from {target_hz} Hz (reference model predicts "
            f"{expected_hz:.4f} Hz) -- outside the +-10 cent bound FR-021/SC-003 require."
        )

        # Also check hardware against the reference model directly (not just
        # against the equal-tempered target above) -- this is what actually
        # makes `expected_hz` load-bearing rather than a number that only
        # ever appears in a message.
        cents_vs_model = _cents(freq, expected_hz)
        assert abs(cents_vs_model) <= 10, (
            f"channel {label}, note {note} (N={n}): measured {freq:.4f} Hz on PWM_OUT differs "
            f"from PsgModel's predicted {expected_hz:.4f} Hz by {cents_vs_model:+.2f} cents -- "
            "RTL and the reference model disagree on this channel's pitch formula."
        )


@cocotb.test()
async def test_channel_b_pitch(dut):
    """FR-020, SC-003.

    Channel B (B_LO=reg 2, B_CTRL=reg 3) must reproduce A4 and C4 within
    +-10 cents -- until now only channel A's pitch had ever been measured.
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x3B000001)
    await bus.write(ENABLE, 0x70 | B_EN | PWM_EN)
    await _check_channel_pitch(dut, bus, B_LO, B_CTRL, model_ch=1, label="B")


@cocotb.test()
async def test_channel_c_pitch(dut):
    """FR-020, SC-003.

    Channel C (C_LO=reg 4, C_CTRL=reg 5) must reproduce A4 and C4 within
    +-10 cents -- until now only channel A's pitch had ever been measured.
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x3B000002)
    await bus.write(ENABLE, 0x70 | C_EN | PWM_EN)
    await _check_channel_pitch(dut, bus, C_LO, C_CTRL, model_ch=2, label="C")


@cocotb.test()
async def test_channel_independence(dut):
    """FR-020.

    Program all three tone channels to different, easily-distinguishable
    pitches simultaneously, then enable exactly one channel at a time and
    confirm the measured pitch is that channel's OWN programmed period --
    not one of the other two's. This is the check a per-channel-in-
    isolation test (like test_channel_b_pitch/test_channel_c_pitch above)
    cannot catch: a bug that cross-wires B's period into C's tone counter
    (or any other swap) would still pass every single-channel test, since
    each of those only ever has one channel active at a time.

    Method: with a single tone channel enabled (others off), the mixed
    signal takes exactly two values (+-AMP[vol]), so the PWM duty code's
    run-length ("dwell", in carrier periods == 192 kHz ticks) between
    duty-level changes equals the active tone period N exactly (same
    method as test_tone_pwm.test_atomic_pitch_update) -- an exact integer
    check, not a +-10-cent approximation, so a swapped register cannot
    hide behind measurement tolerance.
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x3B000003)

    # Three periods, chosen far enough apart that a mix-up is unmistakable:
    # A4 (218), C4 (367), and a much faster ~662 Hz (145).
    n_a, n_b, n_c = 218, 367, 145
    vol = 15

    await bus.write(A_LO, n_a & 0xFF)
    await bus.write(A_CTRL, (vol << 4) | (n_a >> 8))
    await bus.write(B_LO, n_b & 0xFF)
    await bus.write(B_CTRL, (vol << 4) | (n_b >> 8))
    await bus.write(C_LO, n_c & 0xFF)
    await bus.write(C_CTRL, (vol << 4) | (n_c >> 8))

    for label, en_bit, n_expected in (("A", A_EN, n_a), ("B", B_EN, n_b), ("C", C_EN, n_c)):
        await bus.write(ENABLE, 0x70 | PWM_EN | en_bit)
        # Let the enabled channel's dwell pattern reach steady state; the
        # other two channels' tone counters keep running internally
        # (psg_tone.v has no enable input -- the mixer gates their
        # contribution instead) but do not affect the mix while disabled.
        await ClockCycles(dut.clk, 4 * n_expected * 128)

        codes = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=6 * n_expected, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
        dwells = _dwell_lengths(codes)
        interior = dwells[1:-1]
        assert interior, f"channel {label}: no interior dwells captured (n_expected={n_expected})"
        assert all(d == n_expected for d in interior), (
            f"channel {label} enabled alone (others disabled): measured dwell lengths "
            f"{sorted(set(interior))} carrier periods, expected exactly {n_expected} -- this "
            f"is either channel {label}'s own commanded period ({n_expected}) not reaching the "
            f"output, or a cross-wired/swapped register bug (FR-020 channel independence). "
            f"Programmed periods were A={n_a}, B={n_b}, C={n_c}."
        )


@cocotb.test()
async def test_pwm_disable_holds_low(dut):
    """FR-035.

    With a tone playing, clearing ENABLE's pwm_en bit (bit 4) must hold
    uo_out[1] steady LOW for a sustained window -- not merely land near
    50% average duty by chance, and not merely "not a clean tone" but
    genuinely pinned low, per contracts/register-map.md ("disabled -> pin
    held low") and audio-output-formats.md's PWM row ("Disabled: Pin held
    low"). Nothing before this file tested this specific gate:
    test_i2s.py's test_i2s_disable exercises i2s_en (a different bit), and
    test_tone_pwm.py never touches pwm_en at all. Re-enabling it must
    bring the tone back, proving the disable only gated the output stage
    and did not disturb the tone generator/mixer underneath.

    Method: rather than sampling `pwm_pin.value` once per clock in a Python
    loop (hold_clocks here is 6*218*128 ~= 167k cycles -- by far the
    slowest thing in this file), this checks the equivalent property in
    O(1): confirm the pin is LOW right after the disabling write settles,
    then race a single `ClockCycles(hold_clocks)` against a value-change
    watcher on the pin for the whole window. If the pin is 0 at the start
    and never changes for the entire window, it was 0 throughout --
    including the final cycle, which a per-cycle sampling loop of the same
    length could only ever check up to the second-to-last one.

    Settle window before the initial-level check: `bus.write()` only
    returns once DTACK_n has been observed low, which (per bus68k_if.v)
    happens on the exact same clock edge ENABLE's register commits (both
    are gated by `state==ST_WRITE`) -- so pwm_en is already 0, for real,
    by the time `bus.write()` returns; the one extra `ClockCycles(dut.clk,
    1)` below is margin for the *observation* of DTACK possibly lagging
    the real commit by up to one cycle (the same stale-pre-edge-value
    behavior this suite has hit before), not for the combinational
    pwm_en-gated output mux itself to "settle" (it has zero delay). This
    is not left as an unverified assumption: the `initial_level == 0`
    check right after is the actual proof, read through `ReadOnly()` so
    that single sample can't itself be the stale-pre-edge value being
    guarded against.
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x3B000004)

    n = 218  # A4
    await bus.write(A_LO, n & 0xFF)
    await bus.write(A_CTRL, 0xF0 | (n >> 8))
    await bus.write(ENABLE, 0x70 | A_EN | PWM_EN)

    # Confirm the tone is actually alive before disabling, so a later
    # "always low" observation can't be a false pass from a stuck pin.
    codes_before = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=6 * n, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
    assert len(set(codes_before)) > 1, "PWM was not toggling before disable -- can't test the disable meaningfully"

    # 0x70 already has pwm_en (bit 4) SET (reset default: all outputs on).
    # Clearing pwm_en means ANDing it out, not just OR-ing in a_en -- OR-ing
    # alone would leave every bit of 0x70 (including pwm_en) untouched.
    await bus.write(ENABLE, (0x70 & ~PWM_EN) | A_EN)  # clear pwm_en; tone A keeps running internally

    pwm_pin = _pwm_pin(dut)
    hold_clocks = 6 * n * 128  # same span (in clk cycles) as codes_before's window

    await ClockCycles(dut.clk, 1)  # margin for DTACK-observation lag (see docstring)
    await ReadOnly()  # guarantee this read cannot itself be a stale pre-edge value
    initial_level = int(pwm_pin.value)
    assert initial_level == 0, (
        "PWM_OUT was HIGH immediately after clearing pwm_en (before the hold window even "
        "started) -- FR-035 requires a disabled output to hold a steady idle LOW."
    )

    # value_change (the non-deprecated replacement for Edge()) fires on ANY
    # change of pwm_pin; racing it against the hold window and checking
    # WHICH trigger First() returns (by identity, not truthiness -- a
    # value-change trigger resolves to the fired trigger object itself,
    # never to `timeout_trigger`) is what actually catches a toggle. Since
    # initial_level == 0 was just proven above, "no change for the whole
    # window" is equivalent to "held at 0 for the whole window".
    timeout_trigger = ClockCycles(dut.clk, hold_clocks)
    result = await First(pwm_pin.value_change, timeout_trigger)
    assert result is timeout_trigger, (
        f"PWM_OUT toggled at some point during the {hold_clocks}-cycle hold window while "
        "pwm_en=0 -- FR-035 requires a disabled output to hold a steady idle LOW, not merely "
        "average out to ~50% duty."
    )

    await bus.write(ENABLE, 0x70 | A_EN | PWM_EN)
    await ClockCycles(dut.clk, 4 * n * 128)  # let the pitch settle back in
    codes_after = await sample_pwm_periods(_pwm_pin(dut), dut.clk, count=6 * n, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
    assert len(set(codes_after)) > 1, (
        "PWM did not resume toggling after re-enabling pwm_en -- disabling the output appears "
        "to have disturbed the tone generator/mixer, not just the output stage (FR-035 requires "
        "a disabled output to not affect the others, implying the reverse also holds: disabling "
        "and re-enabling it must not affect itself either)."
    )
