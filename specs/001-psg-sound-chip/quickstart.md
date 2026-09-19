# Quickstart: Validating the 68k PSG Sound Chip

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

How to prove each user story works: in simulation first, then on silicon. Formats and timing are defined in [contracts/](contracts/). This guide only says how to check them.

## Prerequisites

- Icarus Verilog ≥ 11, yosys ≥ 0.41, and **Python 3.13** (required by `cocotb-coverage` 2.0;
  the system `python3` may be older).
- One-command setup: `scripts/setup-toolchain.sh` — creates the `test/venv` virtualenv and installs
  the pinned deps (cocotb 2.0.1, cocotb-coverage 2.0, pytest). It is idempotent and never uses sudo.
- **Activate the venv before any `make` below**: `source test/venv/bin/activate`. Skipping this is
  not a subtle failure but it is a confusing one: the system `python3` may have its own cocotb
  without `cocotb-coverage`, and the run dies at `import cocotb_coverage` inside `test_bus.py`
  before a single test starts.
- The suite has **two halves**: eleven cocotb modules driven by `make`, and one pytest file
  (`test_volume_curve.py`) driven by `make unit`. `make all-tests` runs both; CI runs both as
  separate steps. Neither half runs the other.
- Verilator ≥ 5.036 for `make coverage` (5.040 is what cocotb 2.0.1's own CI tests; apt ships 4.038
  on jammy / 5.020 on noble, both too old — the setup script builds it into `~/.local`).
- For gate-level runs: `PDK_ROOT` pointing at an ihp-sg13g2 install containing `ihp-sg13g2/libs.ref/sg13g2_stdcell/`. On this machine there's a copy under `/tmp/pdk/ciel/ihp-sg13g2/versions/<hash>`, which is temporary. Re-fetch it with `ciel`/`volare` if it's gone.

## 1. Bus interface on its own (Story 1, FR-008)

```bash
cd test/bus68k_if
make -B
```
**Expect**: 8 tests pass, one per contract guarantee in
[contracts/bus68k_if.md](contracts/bus68k_if.md) plus a parameterization smoke test:

| Test | Covers |
|---|---|
| `test_reg_write_pulses_once_never_on_read` | guarantee 1 — exactly one `reg_write` pulse, never on a read |
| `test_reg_addr_wdata_stable_during_write` | guarantee 2 — address/data stable for the whole write |
| `test_read_data_valid_before_dtack_falls` | guarantee 3 — read data valid ≥1T before DTACK falls |
| `test_release_is_combinational_random_phase` | guarantee 4 — DTACK_n and `uio_oe` release combinationally at random clock phases (research R1, SC-002 structural) |
| `test_data_oe_never_during_write_or_deselect` | guarantee 5 — data bus driven only during an addressed read |
| `test_reset_state` | guarantee 6 — reset state (Principle IV) |
| `test_aborted_cycle_no_effect_qualified_atomic` | guarantee 7 — aborted cycle leaves the register untouched (research R2, FR-006) |
| `test_all_registers_addressable` | parameterization smoke test over `NUM_REGS` |

> **Scope note.** This bench verifies the *reusable IP contract* only. The randomized
> ≥10,000-cycle readback (SC-001), unselected cycles, and back-to-back cycles with a 60 ns
> AS-high gap are **chip-level** checks and live in `test/test_bus.py`, not here — an earlier
> version of this section listed them under this bench, which was wrong.

## 2. Full chip, RTL (Stories 1–5)

```bash
cd test
source venv/bin/activate

# A bare `make -B` runs the ELEVEN cocotb suites (what TT CI does). ~40 min;
# test_noise alone is ~26 min of that. It does NOT run test_volume_curve.py,
# which holds pytest tests rather than cocotb tests -- see `make unit`.
make -B

# Both halves. Unit tests run first (~0.02 s), so an AMP-table regression
# fails in seconds instead of after the full cocotb suite.
make all-tests

# Pure-Python unit tests only: AMP table / FR-023, no simulator needed.
make unit

# To iterate on one cocotb suite:
make -B COCOTB_TEST_MODULES=test_bus
make -B COCOTB_TEST_MODULES=test_tone_pwm

# Running several agents/shells at once? Give each run its own build dir.
# NOTE: SIM_BUILD must be a MAKE ARGUMENT, not an environment prefix. The
# Makefile assigns it with plain `=`, which beats an env var, so
# `SIM_BUILD=x make ...` silently builds in the shared sim_build/rtl and
# collides with concurrent runs. This form is correct:
make -B SIM_BUILD=sim_build/mine COCOTB_RESULTS_FILE=results_mine.xml \
     COCOTB_TEST_MODULES=test_bus
```

| Test module | Story | Pass criteria |
|---|---|---|
| `test_bus` | 1 | Register readback over randomized timing, including the commit semantics of `x_LO`/`x_CTRL` (SC-001) |
| `test_tone_pwm` | 2 | Measured PWM-filtered pitch within FR-021 limits for A0, A4, C6, C8 (SC-003). Silence = 50% duty. All channels at max volume don't wrap. No intermediate pitch on a LO→CTRL update. |
| `test_i2s` | 3 | 64 BCLK/frame, one-BCLK delay, L = R. ≥1,000 frames match the Python model sample-for-sample (SC-005). Silence = 0 (SC-004). Zero mean over whole tone periods. |
| `test_spdif` | 4 | ≥2 blocks decoded: preambles, parity, V = 0, channel status = 48 kHz consumer PCM. Audio equals I2S (SC-006). Skipped automatically if built with `ENABLE_SPDIF = 0`. |
| `test_debug` | 5 | Heartbeat = Fs with no bus activity. Exactly one WR_STROBE pulse per write. |
| `test_reset` | — | After reset DTACK_n = 1, `uio_oe` = 0x00, audio idle; no X/Z on any output from reset release (FR-003, FR-014, Principle IV). |
| `test_timebase` | — | `uo[6]` toggles at Fs = clk/512; tick cadences clk/128 and clk/512 (FR-034, FR-040). |
| `test_regs` | 1 | All eight registers read back; `x_LO` staging is atomic; reads have no side effects; reset values correct (FR-010…FR-014). |
| `test_channels` | 2 | B and C pitch within ±10 cents at A4 and C4; each channel alone shows dwell == its own N exactly, catching a cross-wired register; PWM disable holds the pin low for a whole window (FR-020, FR-035, SC-003). |
| `test_noise` | 2 | Rate 0–15 sweep; every dwell a whole multiple of 2*(rate+1) ticks; LFSR never locks up; bit-exact vs `PsgModel` (FR-022). **Slowest suite, ~26 min.** |
| `test_output_consistency` | 3–4 | PWM and I2S reconciled in the mix domain over 300 frames — the three outputs carry the same signal (FR-030). |

Plus one **pytest** file, which `make` does **not** run:

| Test file | Story | Pass criteria |
|---|---|---|
| `test_volume_curve.py` | 2 | AMP table shape, distinct and increasing levels, logarithmic step, ~40 dB AY-3-8910 span (FR-023). Pure Python, no simulator, ~0.02 s. Run via `make unit` / `make all-tests`, and by CI's "Run pure-Python unit tests" step. Listing it in `COCOTB_TEST_MODULES` does **not** work: cocotb finds no `@cocotb.test` in it, warns, and exits 0 — green while running nothing. |

Waveforms: `gtkwave tb.fst tb.gtkw` or `surfer tb.fst`.

## 3. Area, lint, FPGA portability and coverage (SC-009, FR-051, Principles II & III)

```bash
cd test
make area      # synthesized cell area vs the 1x1 budget
make lint      # verilator --lint-only -Wall (must be clean)
make fpga      # yosys synth_ice40 — the FPGA/ASIC portability proof (Principle III)
make coverage  # Verilator line+toggle coverage; local/milestone check, not a CI gate
```
**Expect**: `make lint` clean (any unwaived warning is fatal), `make fpga` synthesizing for the
iCE40UP5K without error, and a yosys cell area for the typ corner.

The original thresholds from [research.md R11](research.md#r11-area-budget-and-hardening-configuration)
were: ≤ 17,500 µm² proceed | 17,500 – 21,000 raise `PL_TARGET_DENSITY_PCT` | above that consider
cutting S/PDIF. **These have been superseded by the decision below — do not act on them.** They are
kept only so the reasoning that led there stays legible.

**Current status**: the full design measures ~21,584 µm² (69% of the 31,318 µm² 1x1 tile) with all
three outputs built. `PL_TARGET_DENSITY_PCT` is already raised to 75 in `src/config.json` (TT reports
densities up to 80 working). The 21,000 figure is a heuristic from R11, not a physical limit —
synthesis area is not placed area.

**Decision (2026-09-14, project owner)**: this area is **accepted** and **S/PDIF is retained**. T049
is closed; do not rebuild with `ENABLE_SPDIF = 0` as a planned step. It stays wired purely as an
escape hatch should a real LibreLane harden fail to close.

## 4. Gate level (FR-060)

After the GDS GitHub Action runs (or a local LibreLane harden):
```bash
cp ../runs/wokwi/results/final/verilog/gl/tt_um_benpayne_sound_chip.v test/gate_level_netlist.v
cd test && make -B GATES=yes
```
**Expect**: the same pass set as RTL. `strip_gl_timing_cells.py` and `gl_sim_cell_models.v` are applied automatically; without them every flip-flop reads X under Icarus.

**SC-002 timing evidence**: in the harden's STA report, check that the `ui_in[1] → uo_out[0]` and `ui_in[1] → uio_oe[*]` paths are ≤ 50 ns.

## 5. First silicon bring-up (SC-007, SC-008)

Bring up in this order. Each step needs only what the previous steps proved.

1. **Clocked and alive**: apply power, a 24.576 MHz clock, and release reset, with no CPU activity. `uo[6]` should read **48 kHz ± 0.5%** on a frequency counter. `uo[0]` (DTACK_n) should be high, and `uio` not driven.
2. **Bus decode**: have the CPU write any register. `uo[7]` should pulse once per write, and the CPU shouldn't hang (DTACK arrives).
3. **Readback**: write `0x5A` to `NOISE`, read it back, then restore it. This is the only presence check (there's no ID register).
4. **Audible tone**: program A4 on channel A (the example in [register-map.md](contracts/register-map.md#example-68k-assembly-chip-on-lds-at-psg)). Check it on whichever output is wired:
   - **PWM**: RC-filtered `uo[1]` into an amp. You should hear 440 Hz.
   - **I2S**: a PCM5102A module (SCK tied low, FMT = I2S) on `uo[2..4]`.
   - **S/PDIF**: `uo[5]` through a divider to 0.5 Vpp / 75 Ω coax, or a TOSLINK transmitter, into a receiver. The receiver should lock and report 48 kHz.
5. **Tuning**: measure A4 on a tuner. It should read within ±10 cents; if the board clock isn't exactly 24.576 MHz, allow for that proportionally.
