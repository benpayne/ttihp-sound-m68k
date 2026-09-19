# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Bit-accurate software reference model of the PSG sound engine.

Covers task T026 (specs/001-psg-sound-chip/tasks.md, Phase 4 / User Story
2). Per Constitution Principle I, this model is written from spec.md
(FR-020..FR-027, FR-030, FR-031) and the contracts
(register-map.md, audio-output-formats.md) and research.md (R3 tone clock,
R4 volume/mix, R5 PWM, R8 noise) -- **never** from src/*.v, which does not
exist yet at the time this file was written. test_tone_pwm.py (T028) checks
the RTL against this model, not the other way around.

This model is also the normative reference for User Story 3 (I2S, via
`sample16()`) and User Story 4 (S/PDIF, same `sample16()`), so its public
API is intentionally a bit broader than what T028's PWM tests alone need.

Register map (contracts/register-map.md), index = A3:A1:
    0 A_LO    period_lo[7:0], staged
    1 A_CTRL  vol[7:4] | period_hi[3:0], commits channel A's active period
    2 B_LO    (channel B, same shape as A_LO)
    3 B_CTRL  (channel B, same shape as A_CTRL)
    4 C_LO    (channel C, same shape as A_LO)
    5 C_CTRL  (channel C, same shape as A_CTRL)
    6 NOISE   vol[7:4] | rate[3:0]
    7 ENABLE  rsvd[7] spdif_en[6] i2s_en[5] pwm_en[4] noise_en[3] c_en[2]
              b_en[1] a_en[0]

Reset: every register 0x00 except ENABLE = 0x70 (data-model.md Sec 1).
"""

# 16-entry logarithmic volume table (research.md R4), derived from the
# measured AY-3-8910 DAC curve and scaled to 255. All 15 non-zero levels
# are distinct.
AMP = [0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255]

# Register indices (contracts/register-map.md).
A_LO, A_CTRL, B_LO, B_CTRL, C_LO, C_CTRL, NOISE, ENABLE = range(8)

# ENABLE bit positions (contracts/register-map.md).
A_EN_BIT, B_EN_BIT, C_EN_BIT, NOISE_EN_BIT = 0, 1, 2, 3
PWM_EN_BIT, I2S_EN_BIT, SPDIF_EN_BIT = 4, 5, 6

# 17-bit LFSR mask and taps (research.md R8: x^17 + x^14 + 1).
_LFSR_MASK = (1 << 17) - 1
_LFSR_RESET = 1  # never all-zero


class PsgModel:
    """Reference model of the tone/noise/mixer/PWM-duty audio path.

    Advance time one 192 kHz tick at a time with `step_tick()`; every other
    accessor (`mix()`, `sample16()`, `pwm_duty()`) reads the *current*
    state without advancing it.
    """

    def __init__(self, clk_hz: int = 24_576_000) -> None:
        self.clk_hz = clk_hz

        # Software-visible registers, as last written (FR-011).
        self.regs = [0] * 8
        self.regs[ENABLE] = 0x70

        # Internal (non-visible) state (data-model.md Sec 2).
        self._staged_lo = [0, 0, 0]
        self._active_period = [0, 0, 0]
        self._tone_cnt = [0, 0, 0]
        self._tone_out = [0, 0, 0]

        self._noise_pre = 0
        self._lfsr = _LFSR_RESET

    # -- register access -------------------------------------------------

    def write_reg(self, idx: int, value: int) -> None:
        """Write an 8-bit register, applying LO-stage / CTRL-commit
        semantics for the tone channels (research.md R9)."""
        if not 0 <= idx <= 7:
            raise ValueError(f"register index out of range: {idx}")
        value &= 0xFF
        self.regs[idx] = value

        if idx in (A_LO, B_LO, C_LO):
            ch = idx // 2
            self._staged_lo[ch] = value
        elif idx in (A_CTRL, B_CTRL, C_CTRL):
            ch = idx // 2
            period_hi = value & 0x0F
            self._active_period[ch] = (period_hi << 8) | self._staged_lo[ch]
        # NOISE and ENABLE have no commit side effect: their fields are
        # read directly out of self.regs at mix time.

    def read_reg(self, idx: int) -> int:
        if not 0 <= idx <= 7:
            raise ValueError(f"register index out of range: {idx}")
        return self.regs[idx]

    # -- rates -------------------------------------------------------------

    @property
    def fs_hz(self) -> float:
        """Audio frame rate: clk/512 (FR-034)."""
        return self.clk_hz / 512

    @property
    def tick_hz(self) -> float:
        """Tone/noise/PWM tick rate: clk/128 (research.md R3/R5)."""
        return self.clk_hz / 128

    def tone_freq_hz(self, ch: int) -> float:
        """f = clk / (256 * max(N, 1)) for channel `ch` (0=A, 1=B, 2=C)."""
        n = max(self._active_period[ch], 1)
        return self.clk_hz / (256 * n)

    # -- time advance --------------------------------------------------

    def step_tick(self) -> int:
        """Advance tone counters and the noise LFSR by one 192 kHz tick,
        then return the resulting 11-bit signed mix."""
        for ch in range(3):
            n = max(self._active_period[ch], 1)
            if self._tone_cnt[ch] >= n - 1:
                self._tone_out[ch] ^= 1
                self._tone_cnt[ch] = 0
            else:
                self._tone_cnt[ch] += 1

        rate = self.regs[NOISE] & 0x0F
        period_ticks = 2 * (rate + 1)
        if self._noise_pre >= period_ticks - 1:
            self._shift_lfsr()
            self._noise_pre = 0
        else:
            self._noise_pre += 1

        return self.mix()

    def _shift_lfsr(self) -> None:
        feedback = ((self._lfsr >> 16) & 1) ^ ((self._lfsr >> 13) & 1)
        self._lfsr = ((self._lfsr << 1) & _LFSR_MASK) | feedback

    # -- mix / outputs -----------------------------------------------------

    @staticmethod
    def _contrib(enabled: bool, vol: int, bit: int) -> int:
        """+AMP[vol] / -AMP[vol] / 0, per research.md R4."""
        if not enabled or vol == 0:
            return 0
        return AMP[vol] if bit else -AMP[vol]

    def mix(self) -> int:
        """Current 11-bit signed mix, in [-1020, +1020], without advancing
        time (FR-025, FR-026)."""
        enable = self.regs[ENABLE]
        a_en = bool(enable & (1 << A_EN_BIT))
        b_en = bool(enable & (1 << B_EN_BIT))
        c_en = bool(enable & (1 << C_EN_BIT))
        noise_en = bool(enable & (1 << NOISE_EN_BIT))

        vol_a = (self.regs[A_CTRL] >> 4) & 0x0F
        vol_b = (self.regs[B_CTRL] >> 4) & 0x0F
        vol_c = (self.regs[C_CTRL] >> 4) & 0x0F
        vol_noise = (self.regs[NOISE] >> 4) & 0x0F

        total = 0
        total += self._contrib(a_en, vol_a, self._tone_out[0])
        total += self._contrib(b_en, vol_b, self._tone_out[1])
        total += self._contrib(c_en, vol_c, self._tone_out[2])
        total += self._contrib(noise_en, vol_noise, self._lfsr & 1)
        return total

    def sample16(self) -> int:
        """16-bit signed sample carried by I2S/S-PDIF: mix << 5 (research.md
        R6), range +-32640."""
        return self.mix() << 5

    def pwm_duty(self) -> int:
        """7-bit PWM duty code, 0..127: (mix >>> 4) + 64 (research.md R5).
        Silence (mix == 0) is exactly 64 (50%)."""
        return (self.mix() >> 4) + 64


if __name__ == "__main__":
    # Self-check (T026): run with `test/venv/bin/python test/tbutil/psg_model.py`.

    # 1. AMP has 16 distinct entries (research.md R4: "all 15 non-zero
    #    levels are distinct"; level 0 is silence).
    assert len(AMP) == 16, f"AMP has {len(AMP)} entries, expected 16"
    assert len(set(AMP)) == 16, f"AMP has duplicate entries: {AMP}"
    print(f"OK: AMP has {len(set(AMP))} distinct entries: {AMP}")

    # 2. A4 (N=218) gives 440.37 Hz within 0.01 Hz (contracts/register-map.md).
    m = PsgModel()
    m.write_reg(A_LO, 0xDA)  # 218 & 0xFF
    m.write_reg(A_CTRL, 0xC0)  # vol 12, period_hi 0 -> commits N=218
    freq = m.tone_freq_hz(0)
    assert abs(freq - 440.37) < 0.01, f"A4 (N=218) gave {freq!r} Hz, expected ~440.37 Hz"
    print(f"OK: A4 (N=218) -> {freq:.5f} Hz (expected 440.37 Hz)")

    # 3. Mix stays in [-1020, +1020] with all four channels enabled at vol 15.
    m2 = PsgModel()
    m2.write_reg(A_LO, 37)
    m2.write_reg(A_CTRL, 0xF0)
    m2.write_reg(B_LO, 53)
    m2.write_reg(B_CTRL, 0xF0)
    m2.write_reg(C_LO, 19)
    m2.write_reg(C_CTRL, 0xF0)
    m2.write_reg(NOISE, 0xF7)
    m2.write_reg(ENABLE, 0x7F)  # everything on
    seen_min, seen_max = 0, 0
    for _ in range(20_000):
        mix = m2.step_tick()
        assert -1020 <= mix <= 1020, f"mix {mix} out of [-1020, 1020] range"
        seen_min, seen_max = min(seen_min, mix), max(seen_max, mix)
    print(f"OK: mix stayed within [-1020, 1020] over 20000 ticks (observed [{seen_min}, {seen_max}])")

    # 4. Silence (reset state: all channels off, vol 0) is exactly 0 / duty 64.
    m3 = PsgModel()
    assert m3.mix() == 0, f"silent mix is {m3.mix()}, expected 0"
    assert m3.pwm_duty() == 64, f"silent pwm_duty is {m3.pwm_duty()}, expected 64"
    assert m3.sample16() == 0, f"silent sample16 is {m3.sample16()}, expected 0"
    print("OK: silence (reset state) gives mix=0, pwm_duty=64, sample16=0")

    print("All psg_model.py self-checks passed.")
