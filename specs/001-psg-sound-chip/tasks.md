---

description: "Task list for the 68k PSG Sound Chip"
---

# Tasks: 68k PSG Sound Chip

**Input**: Design documents from `/specs/001-psg-sound-chip/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: **MANDATORY, not optional.** Constitution v1.1.2 Principle I (Test-First) is
NON-NEGOTIABLE and spec FR-060 requires automated tests. Every test task below MUST be written and
**observed failing** before the implementation task that satisfies it.

**Organization**: Grouped by user story so each can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US5, mapping to the user stories in spec.md
- Exact file paths are included in every task

## Path Conventions

Tiny Tapeout single-project layout: Verilog in `src/`, cocotb benches in `test/`, shared Python
helpers in `test/tbutil/`, reusable-IP bench in `test/bus68k_if/`. Run everything from the
Python 3.13 venv: `source test/venv/bin/activate`.

**Gate** = constitution milestone gate: coverage measured and reviewed, traceability updated, area
recorded. Coverage is a local/milestone check, not a CI gate (decision 2026-09-12).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Toolchain and harness before any RTL exists

- [X] T001 Run `scripts/setup-toolchain.sh` and confirm Python 3.13 venv, cocotb 2.0.1, cocotb-coverage 2.0, Verilator ≥ 5.036, iverilog, yosys
- [X] T002 [P] Change `CLOCK_PERIOD` from 20 to 40 in `src/config.json` (real clock is 40.69 ns; 20 ns wastes area on buffering — research R11)
- [X] T003 [P] Copy `strip_gl_timing_cells.py` and `gl_sim_cell_models.v` from `ttihp-ps2-m68k` (commit `e8abe74`) into `test/`, and add the GL stdcell-filter block to `test/Makefile`
- [X] T004 [P] Create `test/tbutil/__init__.py` package skeleton for shared drivers, models, and decoders
- [X] T005 Add `test/area.ys` (yosys area estimate against the IHP typ liberty) and a `make area` target in `test/Makefile`
- [X] T006 [P] Add `test/fpga.ys` (yosys `synth_ice40`, ICE40UP5K) and a `make fpga` target — Principle III portability proof
- [X] T007 [P] Add `make lint` (Verilator `--lint-only -Wall`) and `make coverage` (`SIM=verilator EXTRA_ARGS=--coverage` then `verilator_coverage --annotate`) targets to `test/Makefile`
- [X] T008 [P] Create `specs/001-psg-sound-chip/traceability.md` with a FR/SC → test matrix seeded from spec.md (FR-001…FR-060, SC-001…SC-009), all rows initially unmapped

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Time base, reset discipline, and a safe top level. Everything else depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests first (must FAIL against the placeholder `src/project.v`)

- [X] T009 [P] Write `test/test_reset.py::test_reset_outputs_safe` asserting that after reset `uo_out[0]` (DTACK_n) is **1**, `uio_oe` is `0x00`, and audio outputs are idle — the current placeholder drives `uo_out = 8'h00`, i.e. DTACK permanently asserted, which would jam the bus (FR-014, FR-003)
- [X] T010 [P] Write `test/test_reset.py::test_no_x_after_reset` asserting no `X`/`Z` on any bit of `uo_out`/`uio_out`/`uio_oe` from reset release onward, with registers X-initialized (Icarus default) — constitution Principle IV
- [X] T011 [P] Write `test/test_timebase.py` asserting `uo_out[6]` toggles at Fs = clk/512 (48 kHz nominal) with no bus activity, and that tick cadences are clk/128 and clk/512 (FR-034, FR-040)

### Implementation

- [X] T012 Add a 2-FF reset synchronizer (async assert, synchronous release) to `src/project.v` — Principle IV reset style
- [X] T013 [P] Create `src/psg_timebase.v`: free-running 9-bit `count`, with strobes `half_cell` (`count[1:0]==3`), `bit_cell` (`count[2:0]==7`), `tick` (`count[6:0]==127`, 192 kHz), `frame` (`count==511`, 48 kHz) — research R10
- [X] T014 Rewrite `src/project.v` skeleton: pin map per `contracts/pinout-and-bus-timing.md`, DTACK_n driven high, `uio_oe` 0, HEARTBEAT on `uo_out[6]`, `ENABLE_SPDIF` parameter (default 1), and explicit `_unused` consumption of `ui_in[7]`/`ena`
- [X] T015 Sync `info.yaml` `source_files` with `test/Makefile` `PROJECT_SOURCES` (constitution workflow gate; repeat after every module added)

**Checkpoint**: Chip is bus-safe and observably alive. Part of US5 is already delivered.

---

## Phase 3: User Story 1 - Safe, correct bus citizen (Priority: P1) 🎯 MVP part 1

**Goal**: The chip behaves correctly on the 68000 bus and every register reads back what was written.

**Independent Test**: Drive randomized read/write cycles at random clock phases, including aborted and unselected cycles, and confirm readback, release timing, and idle behavior — with no audio logic present.

### Tests for User Story 1 (write first, observe FAIL)

- [X] T016 [P] [US1] Create `test/tbutil/bus68k_master.py`: a 68000 bus-cycle driver (read/write/aborted/unselected) whose stimulus delays are **never** multiples of the clock period, per constitution Principle V and research R12
- [X] T017 [P] [US1] Create `test/bus68k_if/tb_bus68k_if.v` (instantiates `bus68k_if` plus a trivial 8×8 register payload) and `test/bus68k_if/Makefile`
- [X] T018 [US1] Write `test/bus68k_if/test_bus68k_if.py` covering contract guarantees 1–7 in `contracts/bus68k_if.md`: one `reg_write` pulse per write cycle, `reg_read` mirror, stable `reg_addr`/`reg_wdata`, data valid before DTACK, combinational release, `data_oe` never asserted while `r_w`=0, reset state (FR-008)
- [X] T019 [P] [US1] Write `test/test_bus.py` at chip level: ≥10,000 randomized cycles with zero readback errors (SC-001); DTACK_n and `uio_oe` release within 1 ns of AS_n rising at random phases with no clock edge between (SC-002, FR-004, FR-005); back-to-back cycles with a 60 ns AS-high gap (research R1); aborted cycle leaves old-or-new value (FR-006); unselected cycle produces no response (FR-001)
- [X] T020 [P] [US1] Write `test/test_regs.py`: all 8 registers read back as written including `rsvd[7]` (FR-011), reset values all `0x00` except `ENABLE = 0x70` (FR-014), reads have no side effects (FR-013), and `x_LO` staging — a write to `A_LO` alone does not change pitch until `A_CTRL` is written (FR-012)

### Implementation for User Story 1

- [X] T021 [US1] Create `src/bus68k_if.v` per `contracts/bus68k_if.md`: `ADDR_BITS` parameter (default 3), 2-FF synchronizers on `cs_n`/`as_n`/`ds_n` only, strobe-qualified capture of `A3:A1`/`R_W`/`D7:D0` (research R2), 4-state FSM (IDLE/WRITE/READ/ACK) with a `default` branch, `dtack_q` with **async clear on raw `as_n` high or reset**, and combinational release `dtack_n = ~dtack_q | as_n_raw | cs_n_raw`, `uio_oe = {8{oe_q & ~as_n_raw & ~cs_n_raw & r_w_raw}}` (research R1)
- [X] T022 [US1] Create `src/psg_regs.v`: eight 8-bit registers per `contracts/register-map.md` (`A_LO`, `A_CTRL`, `B_LO`, `B_CTRL`, `C_LO`, `C_CTRL`, `NOISE`, `ENABLE`), reset `0x00` except `ENABLE = 0x70`; `x_CTRL` write commits `{wdata[3:0], x_LO}` into that channel's 12-bit `active_period` on the same clock (FR-012); combinational readback mux valid one clock after `reg_addr`
- [X] T023 [US1] Wire `bus68k_if` + `psg_regs` into `src/project.v` and drive WR_STROBE on `uo_out[7]` from `reg_write` (FR-041) — completes the US5 pin set
- [X] T024 [US1] Add cocotb-coverage points in `test/test_bus.py` for the bus FSM: 100% of states and legal transitions, plus read/write × aborted/completed × selected/unselected crosses (Principle II)
- [X] T025 [US1] Update `traceability.md` for FR-001…FR-014, FR-008, SC-001, SC-002 and run `make lint`

**Checkpoint**: US1 fully functional and independently testable. **Gate.**

---

## Phase 4: User Story 2 - Play in-tune music through the PWM output (Priority: P1) 🎯 MVP part 2

**Goal**: Programmed channels produce correctly pitched, glitch-free audio on the PWM pin.

**Independent Test**: Program each channel over the bus and measure the filtered PWM output's frequency and level against the programmed values.

### Tests for User Story 2 (write first, observe FAIL)

- [X] T026 [P] [US2] Create `test/tbutil/psg_model.py`: a bit-accurate reference model written **from spec.md and contracts/, not from the RTL** (Principle I) — tone `f = 96000/max(N,1)`, `AMP = {0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255}`, bipolar per-channel contribution, 11-bit signed mix in [−1020, +1020]
- [X] T027 [P] [US2] Create `test/tbutil/pwm_meter.py` to measure PWM duty cycle and recovered frequency
- [X] T028 [US2] Write `test/test_tone_pwm.py`: A4 (`A_LO=218`, i.e. `0xDA`) is within ±10 cents of 440 Hz (SC-003, FR-021); A0 (N=3491), C6, and C8 (N=23) within FR-021 limits; silence is exactly 50% duty (SC-004, FR-031); all four channels at volume 15 never wrap (FR-025); a LO→CTRL pitch change never produces an intermediate pitch (FR-012); volume steps 15→1 are monotonic and 0 is silent (FR-023); period `N=0` behaves as `N=1` (FR-027)

### Implementation for User Story 2

- [X] T029 [P] [US2] Create `src/psg_tone.v`: 12-bit up-counter enabled by the 192 kHz `tick`, output toggles when `tone_cnt >= max(active_period,1) - 1` then resets, giving `f = clk/(256·max(N,1))` (research R3); `>=` comparison so a shrinking period can't cause a 4096-tick wrap
- [X] T030 [P] [US2] Create `src/psg_noise.v`: 17-bit LFSR, taps x¹⁷+x¹⁴+1, **reset to 1** so it can never lock at all-zeros, shifted at `96 kHz/(rate+1)` via a 5-bit prescaler on `tick` (research R8)
- [X] T031 [US2] Create `src/psg_mixer.v`: serial 4-slot accumulator (slot 120 loads channel A, slots 121–123 add B, C, noise) sharing one 16-entry logarithmic `AMP` table; each enabled channel contributes `+AMP[vol]` when its bit is high and `−AMP[vol]` when low, 0 when disabled or `vol==0`; result is 11-bit signed, no saturation needed (research R4, FR-025, FR-026)
- [X] T032 [US2] Create `src/pwm_out.v`: 7-bit PWM off `count[6:0]` (192 kHz carrier), duty `(mix >>> 4) + 64` latched at each period start, `pwm_out = (count[6:0] < duty)`, silence = 50% (research R5, FR-031)
- [X] T033 [US2] Wire tone×3, noise, mixer, and PWM into `src/project.v` with per-channel and `pwm_en` gating from `ENABLE`; update `info.yaml` + `PROJECT_SOURCES`
- [X] T034 [US2] Add cocotb-coverage points in `test/test_tone_pwm.py` for field boundaries: period {0, 1, 2, 4095}, volume {0, 1, 15}, every channel-enable combination, noise rate {0, 15} (Principle II)
- [X] T035 [US2] Run `make area` and record the result against the research R11 thresholds (≤17,500 µm² proceed; 17,500–21,000 raise density; >21,000 cut S/PDIF)
- [X] T036 [US2] Update `traceability.md` for FR-020…FR-031 and SC-003, and run `make lint`

**Checkpoint**: **MVP complete** — a working, in-tune sound chip on the bus with audio out. **Gate.**

---

## Phase 5: User Story 5 - Observe the chip before any software works (Priority: P2)

**Goal**: Bring-up visibility with only a scope, before any driver exists.

**Independent Test**: With power, clock and reset only, measure the heartbeat pin; then perform one register write and observe a single strobe pulse.

### Tests for User Story 5 (write first, observe FAIL)

- [X] T037 [US5] Write `test/test_debug.py`: `uo_out[6]` toggles at 48 kHz regardless of register contents and with no bus activity (FR-040, SC-007), and `uo_out[7]` pulses **exactly once** per completed register write and never on a read (FR-041)

### Implementation for User Story 5

- [X] T038 [US5] Verify and, if needed, correct the HEARTBEAT (T014) and WR_STROBE (T023) drivers in `src/project.v` so both are single-clock-wide registered outputs independent of the output-format enables
- [X] T039 [US5] Update `traceability.md` for FR-040, FR-041, SC-007

**Checkpoint**: Silicon bring-up is possible without working software. **Gate.**

---

## Phase 6: User Story 3 - Hear the chip through an I2S DAC (Priority: P2)

**Goal**: A standard I2S DAC module plays the same audio, with no DAC configuration.

**Independent Test**: Decode the three I2S lines and compare the sample stream against the reference model.

### Tests for User Story 3 (write first, observe FAIL)

- [X] T040 [P] [US3] Create `test/tbutil/i2s_decoder.py` (awaits edges of BCLK/LRCLK/SDATA rather than every clock, to keep runtime sane — research R12)
- [X] T041 [US3] Write `test/test_i2s.py`: LRCLK is 48 kHz with exactly 64 BCLK per frame, MSB-first and delayed one BCLK after each LRCLK transition (FR-032); left and right slots carry the identical mono sample; ≥1,000 consecutive frames match `psg_model` sample-for-sample (SC-005); silence decodes to exactly `0x0000` (SC-004); a single tone averages to zero over whole periods, proving no DC offset (FR-026)

### Implementation for User Story 3

- [X] T042 [US3] Create `src/i2s_out.v`: BCLK = registered `count[2]`, LRCLK = registered `count[8]` (low = left), slot `s = count[7:3]` selecting sample bit `15-(s-1)` for s = 1…16 and 0 otherwise; 16-bit sample `mix << 5` latched once per frame; all outputs registered and changing on the BCLK falling edge (research R6)
- [X] T043 [US3] Wire into `src/project.v` with `i2s_en` gating (disabled → all three pins low, FR-035); update `info.yaml` + `PROJECT_SOURCES`
- [X] T044 [US3] Add coverage points for I2S slot indices and the silence case, update `traceability.md` for FR-032 and SC-005, and run `make lint`

**Checkpoint**: US1, US2, US3, US5 all independently functional. **Gate.**

---

## Phase 7: User Story 4 - Connect directly to consumer audio gear via S/PDIF (Priority: P3)

**Goal**: A consumer S/PDIF receiver locks, reports 48 kHz, and plays the audio with no external DAC.

**Independent Test**: Decode the S/PDIF line with a reference IEC 60958 decoder and check preambles, parity, channel status, and audio.

### Tests for User Story 4 (write first, observe FAIL)

- [X] T045 [P] [US4] Create `test/tbutil/spdif_decoder.py`: a closed-loop IEC 60958 biphase-mark decoder (preamble recognition, parity check, channel-status assembly)
- [X] T046 [US4] Write `test/test_spdif.py`: over ≥2 full 192-frame blocks, 100% of subframes have valid preambles in the correct B/M/W sequence and valid parity (SC-006, FR-033); V=0; assembled channel status is consumer linear PCM at 48 kHz (bit 2 and bit 25 set, i.e. byte 3 = `0x02`); decoded audio matches the I2S output sample-for-sample; the test skips automatically when built with `ENABLE_SPDIF = 0`

### Implementation for User Story 4

- [X] T047 [US4] Create `src/spdif_out.v` behind the `ENABLE_SPDIF` parameter: subframe = `count[8]`, bit `b = count[7:3]`, half-cell = `count[2]`; preambles B `11101000` / M `11100010` / W `11100100` as fixed half-cell patterns; bits 4–11 zero; bits 12–27 the 16-bit sample LSB-first; V=0, U=0; C bit set only at frames 2 and 25; P = even parity over bits 4–30 accumulated in one flop; 8-bit 0…191 frame counter (research R7)
- [X] T048 [US4] Wire into `src/project.v` with `spdif_en` gating and tie the pin low when `ENABLE_SPDIF = 0`; update `info.yaml` + `PROJECT_SOURCES`
- [X] T049 [US4] **Area gate**: run `make area`; if over the R11 threshold, rebuild with `ENABLE_SPDIF = 0` and record the decision in plan.md Complexity Tracking (FR-051) **Status 2026-09-13**: measured 21,584 µm² (69% of tile); `PL_TARGET_DENSITY_PCT` raised to 75. **Closed 2026-09-14 by user decision**: S/PDIF is **retained** and the area figure is accepted; do not rebuild with `ENABLE_SPDIF = 0`. The 21,000 µm² figure in R11 was always a heuristic, not a physical limit, and synthesis area is not placed area. `ENABLE_SPDIF = 0` stays wired as an escape hatch should a real LibreLane harden fail to close, but it is no longer a planned action.
- [X] T050 [US4] Add coverage points for preamble types and block boundaries, update `traceability.md` for FR-033 and SC-006, and run `make lint`

**Checkpoint**: All five user stories independently functional. **Gate.**

---

## Phase 8: Polish & Release Gate

**Purpose**: Cross-cutting checks required before tapeout (constitution release gate)

- [ ] T051 Harden the design, copy `runs/wokwi/results/final/verilog/gl/tt_um_benpayne_sound_chip.v` to `test/gate_level_netlist.v`, and run `make -B GATES=yes` — the full suite must pass at gate level (Principle VII, FR-060)
- [ ] T052 [P] Check the STA report under `runs/wokwi/reports/` (LibreLane timing output): `ui_in[1] → uo_out[0]` and `ui_in[1] → uio_oe[*]` combinational paths ≤ 50 ns (SC-002)
- [X] T053 [P] `make fpga` (yosys `synth_ice40`) passes — the FPGA/ASIC portability proof (Principle III)
- [X] T054 `make lint` clean across all of `src/`, with any waiver commented inline
- [ ] T055 Final `make coverage`: 100% line, 100% FSM state/transition, 100% toggle on registers and ports; every hole closed with a test, removed as dead logic, or waived with a written reason (Principle II)
- [ ] T056 Complete `traceability.md`: every FR-001…FR-060 and SC-001…SC-009 maps to a passing test (Principle II)
- [X] T057 [P] Write `docs/info.md` datasheet from `contracts/register-map.md`, `contracts/pinout-and-bus-timing.md`, and `contracts/audio-output-formats.md`
- [X] T058 [P] Update `docs/design/sound-chip.md` for the 8-register direct map, 192 kHz tone clock, 7-bit PWM, bipolar mix, and S/PDIF channel-status bits (constitution Principle "note the deviation")
- [X] T059 [P] Update `docs/design/68k-bus-interface.md` for 68000 single-byte-lane A1–A3, the combinational release plus async DTACK clear, and IRQ as a module port rather than a fixed pin
- [X] T060 Final `info.yaml` ⇔ `test/Makefile` `PROJECT_SOURCES` sync check
- [ ] T061 Walk through `quickstart.md` end to end and correct any drift

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately
- **Foundational (Phase 2)**: needs Setup — **blocks every user story**
- **US1 (Phase 3)**: needs Foundational
- **US2 (Phase 4)**: needs Foundational; independent of US1 in principle, but the tests program registers over the bus, so US1 first is the practical order
- **US5 (Phase 5)**: heartbeat comes from Foundational, WR_STROBE from US1 — verified once both exist
- **US3 (Phase 6)**: needs Foundational + the mixer from US2 (it serializes `frame_sample`)
- **US4 (Phase 7)**: needs Foundational + the mixer from US2; independent of US3
- **Polish (Phase 8)**: needs every story that will ship

### Within Each User Story

- Tests are written and **observed failing** before implementation (Principle I)
- Reference models and decoders before the tests that use them
- RTL modules before the top-level wiring that instantiates them
- `traceability.md` and `make lint` close out each story

### Parallel Opportunities

- Setup: T002, T003, T004, T006, T007, T008 are all different files
- Foundational tests: T009, T010, T011 in parallel; then T013 alongside T012
- US1: T016, T017 in parallel; T019, T020 in parallel once the driver exists
- US2: T026, T027 in parallel; T029, T030 in parallel (different modules)
- US3 and US4 can be developed by different people once US2 lands
- Polish: T052, T053, T057, T058, T059 are independent

---

## Parallel Example: User Story 2

```bash
# Write the reference model and the meter together (different files):
Task: "Create test/tbutil/psg_model.py bit-accurate reference model"
Task: "Create test/tbutil/pwm_meter.py duty/frequency measurement"

# After test_tone_pwm.py is red, build the two independent generators in parallel:
Task: "Create src/psg_tone.v 12-bit tone channel"
Task: "Create src/psg_noise.v 17-bit LFSR noise channel"
```

---

## Implementation Strategy

### MVP (User Stories 1 + 2)

Both are P1, and the spec treats them as one usable product: a chip that sits safely on the bus and
makes correctly pitched sound.

1. Phase 1 Setup
2. Phase 2 Foundational (blocks everything)
3. Phase 3 US1 → **stop and validate** the bus in isolation
4. Phase 4 US2 → **stop and validate** audio out of PWM
5. First real area measurement — this is where the 1x1 budget gets tested

### Incremental Delivery

1. Foundation → chip is bus-safe and observably alive
2. + US1 → registers work; the driver author can start
3. + US2 → **MVP**, audible music through PWM
4. + US5 → bring-up visibility confirmed
5. + US3 → higher-quality audio through an I2S DAC
6. + US4 → S/PDIF, subject to the area gate (first to cut, FR-051)

### Risk Order

The two highest-risk items are deliberately early: the bus release timing (US1, hardest to fix in
silicon) and the area budget (measured at the end of US2, when there's still time to cut S/PDIF).

---

## Notes

- Every test task cites the FR/SC IDs it verifies, so `traceability.md` stays mechanical
- `[P]` means different files with no incomplete dependencies
- Commit after each task or logical group; the failing run is the evidence Principle I asks for
- Randomized tests log their seed and are reproducible from it
- Re-sync `info.yaml` with `test/Makefile` every time a module is added
- Coverage is reviewed at each **Gate**, not in CI
