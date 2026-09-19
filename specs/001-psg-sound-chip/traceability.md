# Requirement Traceability Matrix

Constitution v1.1.2 Principle II requires 100% of the functional
requirements and success criteria below to map to a passing test before
release. Every row starts unmapped (`Test = —`, `Status = unmapped`);
each user story's closing task updates the rows it covers as tests are
written and pass. This file must reach 41 / 41 passing before release; the
gate-deferred rows below close at the area/STA/GL gates, not in cocotb.

**Current: 36 passing, 0 partial, 4 gate-deferred, 1 unmapped (41 total).**

| ID | Requirement (short summary from spec.md) | Test | Status |
|----|-------------------------------------------|------|--------|
| FR-001 | Responds as byte-wide mapped peripheral, only when CS+AS asserted | test_bus.py::test_unselected_cycle | passing |
| FR-002 | Treats all bus inputs as async; never corrupts state | test_bus.py (all: randomized off-grid stimulus, Principle V) | passing |
| FR-003 | Self-generates DTACK after data captured/valid | test_bus.py::test_randomized_readback; bus68k_if bench guarantee 3 | passing |
| FR-004 | Deasserts ack/releases data bus within 50ns of AS release | test_bus.py::test_release_is_combinational | passing |
| FR-005 | Drives data bus only during addressed read cycle | test_bus.py::test_release_is_combinational; bench guarantee 5 | passing |
| FR-006 | Aborted write leaves register at old or complete new value | test_bus.py::test_aborted_write | passing |
| FR-007 | Targets 68000 single byte-lane strobe; A1-A3 register select | test_regs.py::test_readback_all_registers (A1-A3 select) | passing |
| FR-008 | Bus interface separable, generic, configurable, independently verifiable | test/bus68k_if/ standalone bench (8 guarantees) | passing |
| FR-010 | Eight directly-addressed byte registers control all channels/outputs | test_regs.py::test_readback_all_registers | passing |
| FR-011 | Every writable register reads back last written value | test_regs.py::test_readback_all_registers | passing |
| FR-012 | Multi-byte pitch updates take effect atomically | test_regs.py::test_lo_staging_is_atomic; test_tone_pwm.py::test_atomic_pitch_update | passing |
| FR-013 | Reads have no side effects | test_regs.py::test_reads_have_no_side_effects | passing |
| FR-014 | Reset disables channels, zeroes volumes, enables outputs silent | test_regs.py::test_reset_values; test_reset.py::test_reset_outputs_safe | passing |
| FR-020 | Three independent tone channels, 12-bit pitch | test_tone_pwm.py::test_a4_pitch (A); test_channels.py::test_channel_b_pitch, test_channel_c_pitch, test_channel_independence (exact dwell == N per channel, catches a cross-wired/swapped register) | passing |
| FR-021 | Pitch spans A0 to C8 within cent tolerances | test_tone_pwm.py::test_a4_pitch, test_pitch_range | passing |
| FR-022 | One noise channel, 16 rate settings | test_noise.py (4/4: rate 0-15 sweep, dwell = whole multiple of 2*(rate+1), no LFSR lock-up, bit-exact vs PsgModel at rates 3/11) | passing |
| FR-023 | Each channel has 16-level logarithmic volume | test_tone_pwm.py::test_volume_curve (in-simulation, part of the 11-module cocotb suite); plus test_volume_curve.py (4/4: AMP table shape, distinct+increasing levels, logarithmic step, ~40 dB AY-3-8910 span) — **pytest, NOT cocotb**: run by `make unit` and CI's "Run pure-Python unit tests" step, never by `make`/COCOTB_TEST_MODULES (see T056 note 3) | passing |
| FR-024 | Each of four channels has independent enable | test_tone_pwm.py::test_no_overflow_at_max, test_silence_is_50_percent | passing |
| FR-025 | Mixes all channels into mono signal without overflow/clipping | test_tone_pwm.py::test_no_overflow_at_max | passing |
| FR-026 | No sustained DC offset; silence is exact midpoint/zero | test_i2s.py::test_no_dc_offset (zero mean over whole tone periods); test_tone_pwm.py::test_silence_is_50_percent | passing |
| FR-027 | Zero tone period produces defined, documented behavior | test_tone_pwm.py::test_period_zero | passing |
| FR-030 | Same mixed mono signal presented on all three outputs | test_output_consistency.py (2/2: PWM vs I2S reconciled in the mix domain, 300 frames) | passing |
| FR-031 | PWM offers >=64 levels, >=150kHz carrier, 50% duty silence | test_tone_pwm.py::test_silence_is_50_percent, test_a4_pitch | passing |
| FR-032 | I2S follows standard Philips format, 48kHz, 64 BCLK/frame | test_i2s.py (6/6: framing, 1-BCLK delay, L=R, silence, disable) | passing |
| FR-033 | S/PDIF follows IEC 60958 consumer format at 48kHz | test_spdif.py (5/5: preambles, parity, V=0, 48kHz channel status) | passing |
| FR-034 | Sample rate exactly clock/512, all timing derives from it | test_timebase.py::test_heartbeat_frequency; test_debug.py heartbeat tests | passing |
| FR-035 | Each output individually disable-able, idle state | test_i2s.py::test_i2s_disable; test_spdif.py::test_spdif_disable; test_channels.py::test_pwm_disable_holds_low (PWM gate covered ONLY here -- no test_pwm_disable exists in test_tone_pwm.py, so this row depends on test_channels being in COCOTB_TEST_MODULES) | passing |
| FR-040 | Spare pin carries 48kHz heartbeat when clocked and out of reset | test_debug.py::test_heartbeat_runs_without_software, test_heartbeat_independent_of_enables | passing |
| FR-041 | Second spare pin pulses once per completed register write | test_debug.py::test_wr_strobe_one_pulse_per_write, test_wr_strobe_silent_on_read | passing |
| FR-050 | Runs from single 24.576MHz clock, standard TT pin allocation | `info.yaml` (`clock_hz: 24576000`, pinout) + the TT flow itself; `test_timebase.py` and `test_debug.py` verify every derived cadence *given* that clock (Fs = clk/512 = 48 kHz, ticks clk/128) | by construction |
| FR-051 | Must fit 1x1 tile; cut S/PDIF first if needed | area gate (make area at release gate) -- not a cocotb test | deferred: area gate |
| FR-060 | Each user story covered by automated sim tests, behavioral+GL | requires the GL suite (make -B GATES=yes) after a harden | deferred: GL run |
| SC-001 | 10,000+ randomized bus cycles, zero read errors | test_bus.py::test_randomized_readback (10,000 cycles, zero errors) | passing |
| SC-002 | Ack/data released within 50ns, post-synthesis | test_bus.py::test_release_is_combinational (structural; STA check is T052) | passing |
| SC-003 | Every note A0-C6 within ±10 cents, C6-C8 within ±25 cents | test_tone_pwm.py::test_a4_pitch, test_pitch_range (A, full A0-C8 range); test_channels.py::test_channel_b_pitch, test_channel_c_pitch (B and C at A4 and C4, ±10 cents vs both the equal-tempered target and PsgModel) | passing |
| SC-004 | All channels disabled: I2S/S-PDIF zero, PWM 50% duty | test_i2s.py::test_silence_is_zero; test_tone_pwm.py::test_silence_is_50_percent | passing |
| SC-005 | 100% I2S samples match reference model over 1000+ frames | test_i2s.py::test_matches_reference_model (6/6; exact match, best-offset aligned) | passing |
| SC-006 | S/PDIF subframes valid preamble/parity, correct channel status | test_spdif.py::test_audio_matches_model (5/5; exact match over 2+ blocks) | passing |
| SC-007 | Heartbeat pin reads 48kHz ±0.5% on bring-up | test_debug.py::test_heartbeat_runs_without_software | passing |
| SC-008 | At least one output produces correct tone on first silicon | first-silicon bring-up (quickstart section 5) | deferred: silicon |
| SC-009 | Design fits allocated area, meets timing with margin | area + STA reports at release gate -- not a cocotb test | deferred: area/timing gate |

**36 / 41 passing, 4 gate-deferred, 1 by construction — all 41 rows dispositioned, 0 unmapped, 0 partial.**

- **Partial**: none. FR-020 and SC-003 closed once `test_channels.py` passed 4/4 on an
  independent run of my own, matching test-gaps' `gaps_channels4` test-for-test.
- **CI-backed**: FR-020, FR-026, FR-035 and SC-003 all cite `test_channels.py`. As of commit
  `f494406` it **is** in `COCOTB_TEST_MODULES` (eleven cocotb modules, 51 tests), so these
  rows are backed by CI rather than only by local runs. FR-023 additionally cites
  `test_volume_curve.py`, which is pytest and runs in CI as its own step — see finding 1.
- **By construction**: FR-050 is an environmental constraint, not chip behaviour. `info.yaml`
  declares `clock_hz: 24576000` and the pin allocation; the TT harness supplies the clock and
  the flow enforces the pinout. `test_timebase.py`/`test_debug.py` verify every *derived*
  cadence given that clock. Recorded as satisfied by construction rather than cited to a test
  that merely has a plausible name — the bench's `CLK_PERIOD_PS = 40690` is a stimulus
  constant, so pointing FR-050 at it would be circular.
- **Gate-deferred**: FR-051, FR-060, SC-008, SC-009 — area, GL sim, first silicon, STA.
  FR-060 and SC-009 close from the `gds` workflow's netlist and STA report (T051/T052).

## Process findings (T056)

Four defects found while closing this feature, each of which made a **green result mean less
than it appeared to**. Recorded because the failure mode is shared: the signal looked fine.

1. **A whole suite ran nowhere.** `test_volume_curve.py` holds pytest tests, not cocotb tests.
   Listing it in `COCOTB_TEST_MODULES` made cocotb print `UserWarning: No tests were
   discovered in module` and exit **0** — green CI, suite never executed. Its four tests were
   the sole evidence for FR-023. Same shape as the earlier obsolete `test.py` stub that
   asserted `uo_out == 50`. Fixed with a `make unit` target, `make all-tests`, and a dedicated
   CI step. *Lesson: a module count that doesn't match the wired list is worth chasing.*
2. **Coverage artifacts collide across concurrent runs.** The four `coverage_*.yml` paths are
   relative to the process CWD, so `SIM_BUILD` isolation — the discipline used all session to
   run agents concurrently — does **not** isolate them. Last writer wins, silently. Harmless
   in CI (single job, no matrix) but a real hazard locally.
3. **`make clean` does not remove `coverage_*.yml`.** Stale coverage files survived a clean and
   were nearly read as a fresh run's output; only their timestamps gave it away.
4. **A coverage bin was mislabeled, not missing.** `test_bus.py`'s `sample_cycle` hardcoded
   `"write"` inside a loop over `write in (False, True)`, so every unselected *read* cycle was
   recorded as a write. The 88.89% figure was wrong in **both** directions: one bin falsely
   empty, another credited with hits it never earned. A coverage number that misreports which
   stimulus ran survives review in a way a low number does not.
