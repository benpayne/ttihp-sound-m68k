# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Cross-output consistency tests for tt_um_benpayne_sound_chip (audit gap #3).

Covers FR-030: "The chip MUST present the same mixed mono signal on all
three outputs simultaneously." Before this file, nothing cross-checked the
outputs against EACH OTHER -- test_tone_pwm.py only ever looks at PWM,
test_i2s.py only ever looks at I2S (both against the independent
PsgModel reference), and test_spdif.py (owned by another agent) presumably
does the same for S/PDIF. None of them establish that the *same* internal
mix is what all three are simultaneously carrying at any given instant.

This file deliberately does NOT touch S/PDIF (test_spdif.py is owned by
another agent per this task's file-ownership rules) -- I2S vs PWM is
sufficient to establish FR-030's cross-output claim, since both are
derived from the identical `mix` register in psg_mixer.v/pwm_out.v/
i2s_out.v and S/PDIF's `sample` input is shared verbatim with I2S's
(research.md R6/R7: "the previous word's... same latched sample as I2S").

Quantization and the derived tolerance
---------------------------------------
The two formats encode `mix` (an 11-bit signed value, research.md R4) at
different resolutions (audio-output-formats.md):
  - I2S:  sample = mix << 5           (16-bit, LOSSLESS: only appends zero
                                        bits, so `sample >> 5` recovers
                                        `mix` exactly, with a Python `>>`
                                        on a signed int performing the same
                                        floor-toward-negative-infinity
                                        arithmetic shift as Verilog's `>>>`)
  - PWM:  duty  = (mix >>> 4) + 64    (7-bit, LOSSY: drops mix's low 4
                                        bits via an arithmetic right shift,
                                        i.e. floors mix to a multiple of 16)

So from a PWM duty code alone, the true `mix` can only be pinned to a
16-wide bucket: `recon = (duty - 64) << 4` satisfies `recon <= mix <=
recon + 15` (never `recon - 15 <= mix <= recon`, because floor-shift always
rounds toward -inf, not toward zero). That one-sided `[0, 15]` window --
not a symmetric +-N -- is the tightest bound the PWM quantization
mathematically allows; asserting a symmetric tolerance around `recon`
would either be needlessly loose (if wide enough to cover +15) or wrong
half the time (if centered). I2S's exact recovery means all of that width
is PWM's contribution alone.

Method
------
A multi-channel setup (two tones + noise, distinct pitches and volumes) is
played so the mix genuinely varies over the capture window -- not a frozen
edge case. PWM duty codes (one per 192 kHz tick, `sample_pwm_periods`) and
I2S frames (one per 48 kHz frame = 4 ticks, `decode_i2s_frames`) are
captured CONCURRENTLY over the same span of simulated time. Because a PWM
duty sample and the I2S frame that closes the same 4-tick group are both
just registered copies of the identical `mix` value latched on the exact
same clock edge (psg_timebase.v: `tick` and `frame` are simultaneously
true at `count==511`), frame `i`'s sample must fall in the tolerance
window of `codes[4*i + k]` for a FIXED integer `k` -- a small best-alignment
search (like test_i2s.py's SC-005 test uses for a similar reason) finds
that `k`, and every one of the `N_FRAMES` frames must then be consistent
with the corresponding PWM code with zero exceptions (not a statistical
pass rate): FR-030 is a hard requirement, and both formats being exact
recordings of one register leaves no room for a legitimate mismatch.

RTL is frozen and believed correct (this is a coverage-gap-closing file,
not a debugging session); do not weaken these assertions.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tbutil.bus68k_master import Bus68kMaster, ChipLevelSignals
from tbutil.dutbits import uo_bit
from tbutil.i2s_decoder import decode_i2s_frames
from tbutil.pwm_meter import sample_pwm_periods

# Chip clock: 24.576 MHz nominal (contracts/pinout-and-bus-timing.md).
CLK_PERIOD_PS = 40690
CLK_HZ = 1e12 / CLK_PERIOD_PS

# PWM carrier / tone-noise tick rate: clk/128 = 192 kHz (research.md R5).
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

# Pin assignments (contracts/pinout-and-bus-timing.md).
_PWM_BIT = 1
_I2S_BCLK_BIT = 2
_I2S_LRCLK_BIT = 3
_I2S_SDATA_BIT = 4

# Ticks per I2S/S-PDIF frame: Fs = clk/512, tick = clk/128 (research.md R6).
TICKS_PER_FRAME = 4

# How far a fixed alignment offset between the two capture streams could
# plausibly be, in PWM ticks -- generous margin around 0.
_MAX_OFFSET = 16


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
    dut._log.info(f"test_output_consistency: Bus68kMaster seed={seed}")
    return Bus68kMaster(ChipLevelSignals(dut), dut.clk, clk_period_ps=CLK_PERIOD_PS, seed=seed)


def _pwm_to_mix_bounds(duty: int):
    """Invert `duty = (mix >>> 4) + 64`: returns (lo, hi) such that the
    true mix satisfies lo <= mix <= hi, per the module docstring's
    quantization derivation."""
    recon = (duty - 64) << 4
    return recon, recon + 15


def _i2s_sample_to_mix(sample: int) -> int:
    """Invert `sample = mix << 5` exactly (lossless: `>> 5` is an exact
    arithmetic right shift of a value with 5 known-zero low bits)."""
    return sample >> 5


async def _capture_concurrent(dut, n_frames):
    """Capture `n_frames` I2S frames and `4*n_frames + margin` PWM duty
    codes over the SAME span of simulated time, by starting both
    coroutines before awaiting either."""
    n_ticks = TICKS_PER_FRAME * n_frames + _MAX_OFFSET

    pwm_task = cocotb.start_soon(
        sample_pwm_periods(uo_bit(dut, _PWM_BIT), dut.clk, count=n_ticks, carrier_hz=CARRIER_HZ, clk_hz=CLK_HZ)
    )
    i2s_task = cocotb.start_soon(
        decode_i2s_frames(
            uo_bit(dut, _I2S_BCLK_BIT), uo_bit(dut, _I2S_LRCLK_BIT), uo_bit(dut, _I2S_SDATA_BIT), n_frames=n_frames
        )
    )
    codes = await pwm_task
    frames = await i2s_task
    return codes, frames


def _assert_pwm_i2s_consistent(codes, frames, n_frames, context):
    for i, f in enumerate(frames):
        assert f["left"] == f["right"], (
            f"{context}: frame {i}: left={f['left']} right={f['right']} differ -- not even "
            "mono within I2S itself, before comparing to PWM (FR-030)."
        )

    max_offset = len(codes) - TICKS_PER_FRAME * (n_frames - 1) - 1
    assert max_offset >= 0, f"{context}: not enough PWM codes captured ({len(codes)}) for {n_frames} frames"

    best_offset, best_bad, best_detail = None, None, None
    for offset in range(0, max_offset + 1):
        bad = []
        for i in range(n_frames):
            duty = codes[TICKS_PER_FRAME * i + offset]
            lo, hi = _pwm_to_mix_bounds(duty)
            mix_i2s = _i2s_sample_to_mix(frames[i]["left"])
            if not (lo <= mix_i2s <= hi):
                bad.append((i, duty, lo, hi, mix_i2s))
        if best_bad is None or len(bad) < best_bad:
            best_offset, best_bad, best_detail = offset, len(bad), bad
        if not bad:
            break

    assert best_bad == 0, (
        f"{context}: I2S and PWM disagreed on the mixed signal at every tested alignment "
        f"(best: offset={best_offset}, {best_bad}/{n_frames} frames outside the derived "
        f"[recon, recon+15] mix-domain tolerance) -- FR-030 requires all outputs to carry "
        f"the same mixed signal. First mismatches: {best_detail[:5]}."
    )


@cocotb.test()
async def test_i2s_pwm_consistency_multichannel(dut):
    """FR-030.

    Two tones (different pitches/volumes) plus noise, all enabled
    simultaneously -- a genuinely varying mixed signal, not a static edge
    case. Decoded I2S samples and sampled PWM duty codes, converted back
    to the common `mix` domain, must agree within the PWM quantization
    tolerance at every one of 300 frames (see module docstring).
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x4C000001)

    n_a, n_b = 218, 91  # deliberately different periods so the mix churns
    await bus.write(A_LO, n_a & 0xFF)
    await bus.write(A_CTRL, 0xB0 | (n_a >> 8))  # vol 11
    await bus.write(B_LO, n_b & 0xFF)
    await bus.write(B_CTRL, 0x70 | (n_b >> 8))  # vol 7
    await bus.write(NOISE, (6 << 4) | 9)  # vol 6, rate 9
    await bus.write(ENABLE, 0x70 | A_EN | B_EN | NOISE_EN)

    await ClockCycles(dut.clk, 4 * 128)  # settle past register-write transients

    n_frames = 300
    codes, frames = await _capture_concurrent(dut, n_frames)
    _assert_pwm_i2s_consistent(codes, frames, n_frames, context="multichannel setup")


@cocotb.test()
async def test_i2s_pwm_consistency_max_volume(dut):
    """FR-030.

    Same cross-check as test_i2s_pwm_consistency_multichannel, but with
    all three tones plus noise at or near maximum volume -- the extreme
    end of the mix range (research.md R4: +-1020) where PWM's quantization
    tolerance derivation matters most (the biggest `mix` values are the
    ones most likely to expose an off-by-one in the `>>> 4` / `<< 5`
    conversions this test's tolerance is built on).
    """
    await _reset_dut(dut)
    bus = _make_bus(dut, seed=0x4C000002)

    n_a, n_b, n_c = 50, 37, 28  # test_tone_pwm.test_no_overflow_at_max's periods
    await bus.write(A_LO, n_a & 0xFF)
    await bus.write(A_CTRL, 0xF0 | (n_a >> 8))
    await bus.write(B_LO, n_b & 0xFF)
    await bus.write(B_CTRL, 0xF0 | (n_b >> 8))
    await bus.write(C_LO, n_c & 0xFF)
    await bus.write(C_CTRL, 0xF0 | (n_c >> 8))
    await bus.write(NOISE, 0xF3)  # vol 15, rate 3
    await bus.write(ENABLE, 0x70 | A_EN | B_EN | C_EN | NOISE_EN)

    await ClockCycles(dut.clk, 4 * 128)

    n_frames = 300
    codes, frames = await _capture_concurrent(dut, n_frames)
    _assert_pwm_i2s_consistent(codes, frames, n_frames, context="max-volume setup")
