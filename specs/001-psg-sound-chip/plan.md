# Implementation Plan: 68k PSG Sound Chip

**Branch**: `001-psg-sound-chip` (spec dir; work is on `main`) | **Date**: 2026-09-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-psg-sound-chip/spec.md`

## Summary

Build a Tiny Tapeout ihp-26b 1x1 chip (`tt_um_benpayne_sound_chip`) that sits on a 68000 asynchronous bus as eight byte registers. It generates three square-wave tones and one LFSR noise channel and mixes them into one mono signal, output simultaneously as PWM, I2S, and S/PDIF.

The design is one clock domain (24.576 MHz). Everything runs off clock enables from a single 9-bit free-running counter, and every audio rate is an exact power-of-two division of `clk`.

The 68k interface is a reusable, self-contained `bus68k_if` module:
- It synchronizes only the three strobes and captures address and data at the qualified edge.
- It asserts DTACK synchronously but releases DTACK and the data bus **combinationally** from raw AS_n/CS_n, to meet 68000 hold timing at up to 16 MHz (research R1).

Area is estimated at 57–64% of the tile. It's gated by a yosys estimate, with S/PDIF as the defined cut (FR-051).

## Technical Context

**Language/Version**: Verilog-2005 (synthesizable subset, `default_nettype none`), matching the TT template and sibling projects.

**Primary Dependencies**:
- Tiny Tapeout ihp-26b flow (`TinyTapeout/tt-gds-action@ttihp26b`, LibreLane)
- IHP SG13G2 standard cells

**Storage**: N/A (flip-flop registers only; no SRAM).

**Testing**:
- cocotb 2.0.1 + pytest on Icarus Verilog 11 (RTL and gate level), Python 3.13 venv (`test/venv`), test code kept 3.11-compatible for TT's `gl_test` action
- cocotb-coverage 2.0 for functional coverage and constrained random (constitution v1.1.0)
- Verilator 5.040 for line/toggle code coverage (`SIM=verilator EXTRA_ARGS=--coverage`)
- yosys 0.41 for area estimates
- a standalone cocotb bench for `bus68k_if`
- the gate-level Icarus workaround from `ttihp-ps2-m68k` (research R12)

**Target Platform**: IHP SG13G2 130 nm, TT ihp-26b shuttle, one 1x1 tile (202.08 × 154.98 µm). Runs on the TT demo board (RP2040/RP2350 clock source) on a 68000 retrocomputer bus.

**Project Type**: ASIC digital design (single hardware project).

**Performance Goals**:
- 48 kHz audio exactly at nominal clock
- pitch within ±10 cents (A0–C6) and ±25 cents (to C8)
- DTACK and data release ≤ 50 ns after AS_n
- timing closure at 24.576 MHz (constrained at 40 ns)

**Constraints**:
- 1x1 tile: ≤ ~21,000 µm² synthesized cell area before cutting S/PDIF
- single clock domain
- all 68k inputs asynchronous
- 68000 at ≤ 16 MHz
- `clock_hz` fixed at 24576000
- pin allocation fixed by `info.yaml`

**Scale/Scope**: About 230 flip-flops and ~11 Verilog modules, plus 5 story test modules and 1 bus IP bench.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against **constitution v1.1.2** (`.specify/memory/constitution.md`), ratified after this
plan's first draft. This replaces the earlier ad-hoc G1–G9 table, whose CLAUDE.md-derived rules are
now absorbed into Principles III–VI.

| Principle | Verdict | Evidence / gap |
|---|---|---|
| **I. Test-First (NON-NEGOTIABLE)** | ⚠️ **Plan updated** | Design satisfies it, but the original phasing listed RTL before its bench ("`bus68k_if` and its standalone bench, then `psg_regs` and `test_bus`"). Phasing below is rewritten so each test task precedes its implementation task and the red run is observed. Reference models (`psg_model`) are written from the spec, per R12. |
| **II. Coverage-Driven Completeness** | ⚠️ **Gap → tasks added** | The plan had no coverage flow at all. Added: `make coverage` (Verilator 5.040 line+toggle), cocotb-coverage points for every spec edge case and field boundary, a requirement-traceability matrix (`specs/001-psg-sound-chip/traceability.md`), and a coverage review at every milestone gate. |
| **III. Portable RTL (FPGA + ASIC)** | ⚠️ **Gap → tasks added** | RTL rules already honored by design: single clock domain (R10), clock enables only, no latches/tri-states, `default_nettype none`, parameterized sizes. Missing were the *checks*: added `make lint` (Verilator `--lint-only -Wall`) and `make fpga` (yosys `synth_ice40`, matching the repo's `fpga.yaml` ICE40UP5K target). |
| **IV. Explicit Reset / No Assumed State** | ⚠️ **Gap → test added** | Every register has a documented reset value (data-model.md §1–2), reset is async-assert / sync-release (R10), and IHP has only reset flops. Missing was the X-check: added a test asserting no `X`/`Z` on any output from reset release, with registers X-initialized (Icarus default). Also added a test for the documented reset values (`ENABLE = 0x70`). |
| **V. Explicit Asynchronous Boundaries** | ✅ **PASS** | 2-FF synchronizers on all three strobes; address/data captured only when strobe-qualified with a stability argument (R2). The combinational release and async clear are justified in Complexity Tracking with 68000 timing numbers (R1) and get a dedicated test. Bus stimulus is randomized off the clock grid (R12). |
| **VI. Observability and Safe Defaults** | ✅ **PASS** | HEARTBEAT `uo[6]`, WR_STROBE `uo[7]` (FR-040/041, Story 5). Reset state: DTACK high, `uio` released, outputs silent (FR-014). Unused `ui[7]` explicitly consumed. |
| **VII. Gate-Level and Physical Readiness** | ⚠️ **Partial → tasks added** | GL sim planned with the sibling's Icarus workaround (R12); area budget and thresholds set (R11); timing constrained at 40 ns. Missing were lint and the FPGA synthesis check — both added under Principle III. |

### Verification Standards compliance

| Requirement | Status |
|---|---|
| Python 3.13 venv, deps pinned | ✅ `test/venv`, `test/requirements.txt` |
| cocotb-coverage for functional coverage | ✅ standard tool; no hand-rolled counters |
| Verilator 5.040 for code coverage | ✅ installed; `SIM=verilator EXTRA_ARGS=--coverage` |
| One-command flows: RTL, GL, **lint**, **coverage**, area, **FPGA** | ⚠️ only `make area` was planned; the three bold ones are added below |
| Coverage cadence | ✅ local/milestone check by decision (2026-09-12), not a CI gate |
| Per-story test module + reusable-IP unit bench | ✅ 5 story modules + `test/bus68k_if/` |
| CI fails on any test failure | ✅ existing `test.yaml` greps `results.xml` |

**Result**: PASS with the plan amendments below. No principle requires a waiver; the two entries in
Complexity Tracking are permitted exceptions under Principle V and the `config.json` note.

## Project Structure

### Documentation (this feature)

```text
specs/001-psg-sound-chip/
├── plan.md                         # This file
├── spec.md                         # Feature spec (with clarifications)
├── research.md                     # Phase 0: R1–R12 design decisions
├── data-model.md                   # Phase 1: architectural state + state machines
├── quickstart.md                   # Phase 1: validation guide
├── contracts/
│   ├── register-map.md             # Software ↔ chip
│   ├── pinout-and-bus-timing.md    # Board ↔ chip
│   ├── bus68k_if.md                # Reusable IP port contract
│   └── audio-output-formats.md     # PWM / I2S / S-PDIF wire formats
├── checklists/requirements.md
├── traceability.md                 # FR/SC → test matrix (Principle II)
└── tasks.md                        # Phase 2 (/speckit-tasks, not created here)
```

### Source Code (repository root)

```text
src/
├── project.v          # tt_um_benpayne_sound_chip: pin map, reset sync, instances,
│                      #   output-enable gating, debug pins, ENABLE_SPDIF parameter
├── bus68k_if.v        # Reusable 68000 bus interface (self-contained, incl. synchronizers)
├── psg_regs.v         # 8×8 register file, staged-LO/commit-on-CTRL, readback mux
├── psg_timebase.v     # 9-bit master counter + clock-enable strobes
├── psg_tone.v         # One tone channel (instantiated ×3)
├── psg_noise.v        # 17-bit LFSR + rate prescaler
├── psg_mixer.v        # Serial 4-slot mixer + shared log volume table → 11-bit mix
├── pwm_out.v          # 7-bit PWM, 192 kHz carrier
├── i2s_out.v          # Philips I2S master, counter-indexed (no shift register)
├── spdif_out.v        # IEC 60958 encoder, counter-indexed (cuttable)
└── config.json        # CLOCK_PERIOD 20 → 40 (R11); density raised only if needed

test/
├── Makefile           # PROJECT_SOURCES synced with info.yaml; story modules; GL
│                      #   workaround; targets: area, lint, coverage, fpga
├── tb.v               # TT wrapper (existing)
├── test_bus.py        # Story 1 (full chip)
├── test_tone_pwm.py   # Story 2
├── test_i2s.py        # Story 3
├── test_spdif.py      # Story 4
├── test_debug.py      # Story 5
├── tbutil/
│   ├── __init__.py
│   ├── bus68k_master.py   # 68000 bus-cycle driver, randomized off-grid timing
│   ├── psg_model.py       # Bit-accurate reference model (regs → mix → samples)
│   ├── i2s_decoder.py
│   ├── spdif_decoder.py   # IEC 60958 closed-loop decoder
│   └── pwm_meter.py       # Duty/frequency measurement
├── bus68k_if/             # Standalone bench for the reusable IP (FR-008)
│   ├── Makefile
│   ├── tb_bus68k_if.v     # bus68k_if + trivial 8×8 register payload
│   └── test_bus68k_if.py
├── area.ys                # yosys area-estimate script (IHP typ lib)
├── fpga.ys                # yosys synth_ice40 portability check (Principle III)
├── strip_gl_timing_cells.py  # from ttihp-ps2-m68k e8abe74
└── gl_sim_cell_models.v      # from ttihp-ps2-m68k e8abe74

docs/
├── info.md                        # Datasheet: fill from contracts/
└── design/
    ├── sound-chip.md              # Update: 8-reg map, tone clock, PWM 7-bit, mix, S/PDIF C-bits
    └── 68k-bus-interface.md       # Update: 68000 single-lane A1–A3, async release, IRQ as port
```

**Structure Decision**: This is a single-project TT hardware repo, keeping the template's `src/` + `test/` layout. There's one Verilog module per functional block so each user story maps to a small set of files, and S/PDIF can be removed by a parameter without touching other modules.

`bus68k_if.v` is deliberately a single file with no dependencies (its synchronizers are inline) so the sibling project can copy it together with `test/bus68k_if/`. Helper Python lives in `test/tbutil/` so the story modules stay short and the reference model is shared.

## Implementation phasing (for /speckit-tasks)

Ordered by spec priority, with **Principle I** ordering inside every step: the test is written and
observed failing before the RTL that satisfies it. "Gate" refers to the constitution's milestone
gate (coverage reviewed, traceability updated, area recorded).

1. **Foundation — toolchain and harness before any RTL**
   - `test/venv` (Python 3.13) + pinned `test/requirements.txt` via `scripts/setup-toolchain.sh`
   - `test/Makefile` targets: `lint`, `coverage`, `area`, `fpga` (plus the template's RTL/GL runs)
   - GL workaround files (`strip_gl_timing_cells.py`, `gl_sim_cell_models.v`)
   - `traceability.md` skeleton (FR/SC → test), `tbutil/` package skeleton
   - `config.json` `CLOCK_PERIOD` 20 → 40 ns
   - **Tests first**: reset-state test (DTACK high, `uio` released, `ENABLE` = 0x70) and the
     X-after-reset check — both fail against the placeholder `project.v`.
   - Then: reset synchronizer, `psg_timebase`, `project.v` skeleton with safe reset outputs and
     HEARTBEAT on `uo[6]`. Satisfies part of Story 5 and Principle IV.
2. **Story 1 (P1) — bus**
   - `test/bus68k_if/` bench first (red), then `bus68k_if.v` → green. This is the reusable IP's
     independent verification (FR-008).
   - `test_bus.py` at chip level (red), then `psg_regs.v` → green. WR_STROBE on `uo[7]` finishes
     Story 5.
   - **Gate.**
3. **Story 2 (P1) — sound + PWM (MVP)**
   - `psg_model.py` (from the spec, not the RTL), `pwm_meter.py`, `test_tone_pwm.py` (red)
   - Then `psg_tone.v`, `psg_noise.v`, `psg_mixer.v`, `pwm_out.v` → green
   - First real area measurement against R11 thresholds. **Gate.**
4. **Story 3 (P2) — I2S**: `i2s_decoder.py` + `test_i2s.py` (red) → `i2s_out.v` → green. **Gate.**
5. **Story 4 (P3) — S/PDIF**: `spdif_decoder.py` + `test_spdif.py` (red) → `spdif_out.v` → green.
   **Area gate**: keep S/PDIF or build with `ENABLE_SPDIF = 0` (R11, FR-051). **Gate.**
6. **Release gate (close-out)**
   - full GL run; STA check of the `ui_in[1] → uo_out[0]` / `uio_oe` release paths (SC-002)
   - `make lint` clean; `make fpga` (yosys `synth_ice40`) passes — Principle III/VII proof
   - final `make coverage`: line + toggle + FSM holes closed or waived; traceability complete
   - `docs/info.md` datasheet from the contracts; design-doc updates; `info.yaml` ⇔ Makefile sync

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| **Raw (unsynchronized) AS_n/CS_n used combinationally for DTACK_n/`uio_oe` release, and as an async clear of `dtack_q`** (an explicit Principle V exception) | The 68000 requires DTACK negated within 110 ns and data high-Z within 90 ns of AS negation at 16 MHz. A synchronized release takes 81–163 ns. The async clear also stops a stale DTACK from ending a back-to-back cycle whose AS-high gap (≥ 60 ns) is shorter than the synchronizer latency (R1). | A fully synchronous release violates 68000 #28/#29A at ≥ 12.5 MHz. It isn't a real Principle V violation: the raw signals only *release* outputs and *clear* a flop, and no async data enters the clock domain. The clear is removed while D equals the reset value, so there's no metastability path. |
| **`src/config.json` `CLOCK_PERIOD` 20 → 40 ns** (the file warns against edits) | The real clock is 40.69 ns. Constraining at 20 ns makes the resizer add buffering area we can't spare in a tight 1x1 (R11). | Leaving 20 ns spends area on timing that has no use. `CLOCK_PERIOD` is one of the variables the file itself lists as user-adjustable, and 40 ns still leaves a small margin over the real period. |
| **`src/config.json` `PL_TARGET_DENSITY_PCT` 60 → 75**, S/PDIF retained above the R11 cut line | Full design synthesizes at 21,584 µm² = 69% of the 31,318 µm² tile, over R11's 21,000 µm² heuristic. User decision (2026-09-13): keep all three outputs, raise density, and defer the cut until a real harden either closes or fails. | Cutting S/PDIF mechanically on a synthesis estimate would discard a working, verified encoder over an 804 µm² margin against a threshold I invented; synthesis area is not placed area, and TT reports densities up to 80 working. `ENABLE_SPDIF = 0` stays pre-wired as the escape hatch (FR-051). |
| **Principle II waiver: 3 unreachable `default:` branches excluded from the 100% line-coverage target** — `psg_regs.v:227` (readback mux, 3-bit `reg_addr`, all 8 values enumerated), `bus68k_if.v:157` (FSM, 2-bit `state`, all 4 enumerated), `psg_mixer.v:76` (amp LUT, 4-bit volume, all 16 enumerated) | Principle III **requires** a `default` on every `case`; Principle II requires 100% line coverage. Where the selector is fully enumerated the two rules are in direct conflict — the mandated default is by construction unreachable, so no stimulus can ever cover it. **Re-measured 2026-09-14** on the full 11-module, 51-test suite (`make coverage`, Verilator 5.040, 427/445 = 95.00%): still the ONLY 3 uncovered line points. Branch (102) and expression (107) coverage are at 100% with zero holes. | Deleting the defaults to gain coverage would violate Principle III and remove the defensive recovery that makes an illegal state self-correcting — precisely the safety property the rule exists for. Waived, not closed. Toggle coverage is handled in its own row below. |
| **Principle II waiver: 48 toggle points that are constant by construction** (of 1752 toggle points; measured 2026-09-14 on the full suite, 52 zero-count, 48 waived here and 4 closed with a test) | Each is a bit that provably cannot change, with the proof in the RTL itself rather than in an argument about stimulus: **(a) 30 points** — `sample[4:0]` at `i2s_out.v:30`, `spdif_out.v:47` and `audio_sample[4:0]` at `project.v:248`: `project.v:254` assigns `audio_sample <= {mix, 5'b0}`, so the low five bits are hardwired zero. **(b) 8 points** — `pwm_out.v:33` `duty_signed[10:7]`: `mix` is `signed [10:0]` bounded ±1020, so `(mix>>>4)+64` lands in [0,127] and only bits [6:0] are reachable (stated at `pwm_out.v:46`). **(c) 7 points** — `spdif_out.v:146` `pre_pat_nxt` bits 0,4,5,6,7: the only three values are `PREAMBLE_B=1110_1000`, `PREAMBLE_M=1110_0010`, `PREAMBLE_W=1110_0100`, which share bits 7,6,5=1 and bits 4,0=0; only bits 3,2,1 differ. **(d) 1 point** — `psg_noise.v:30` `presc_target[0]:1->0`: `{rate,1'b0}+5'd1` is always odd. **(e) 2 points** — `ena:1->0` at `project.v:37` and `tb.v:19`: the TT harness holds `ena` high whenever the design is powered. | Not waived on the grounds that the DUT "should" behave a certain way — each proof is arithmetic or structural, and each predicted the measurement before it was read: (c) in particular predicts bits 0 and 4 dead in *both* directions while bits 5/6/7 lack only their `1->0` edge, which is exactly what Verilator reported. The remaining 4 zero-count toggle points (`ui_in[7]` at `project.v:32` and `tb.v:20`) are **not** waived: that pin is stimulable, and an unused input that turns out to perturb an output is the tt08 failure mode this project exists to avoid, so it is closed with a real test in `test_reset.py` instead. |
