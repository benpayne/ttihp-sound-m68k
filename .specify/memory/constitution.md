<!--
SYNC IMPACT REPORT (temporary — remove before committing)
Version change: 1.1.1 → 1.1.2
Bump rationale: PATCH. Names the concrete code-coverage toolchain inside the existing
  Verification Standards bullet, replacing a generic "e.g. Verilator >= 5.036". No principle or
  section added, removed, or redefined.

Modified sections:
  Verification Standards → Code coverage: Verilator 5.040 with the exact run and report commands.

Templates/commands reviewed (not modified — scope is constitution only):
  ⚠ .specify/templates/tasks-template.md: still frames tests as OPTIONAL (see v1.0.0 note).
  ⚠ specs/001-psg-sound-chip/plan.md: still needs a full re-check against Principles I–VII.
    Its Testing list was updated for Verilator 5.040 alongside this amendment.

History:
  v1.1.2 (2026-09-12): code-coverage toolchain pinned to Verilator 5.040.
  v1.1.1 (2026-09-10): Python 3.13 dev/CI, 3.11 compatibility floor for TT gl_test.
  v1.1.0 (2026-09-10): standardized cocotb-coverage for functional coverage; pinned Python venv.
  v1.0.0 (2026-09-10): initial ratification, Principles I–VII.

Deferred TODOs:
  TODO(COVERAGE_TOOLING) — RESOLVED. Functional coverage: cocotb-coverage 2.0. Code coverage:
    Verilator 5.040 built from source into ~/.local (apt only ships 4.038, which it shadows).
    Both verified on Python 3.13 + cocotb 2.0.1 against a sample design.
  TODO(CI_COVERAGE) — RESOLVED 2026-09-12 by decision: coverage is a LOCAL / MILESTONE check, not
    a CI gate. CI keeps running the test suite only. Coverage is measured and reviewed at each
    milestone gate and before release, per Development Workflow & Quality Gates. A ready-to-enable
    CI recipe stays in test/README.md if that decision is ever revisited.
-->

# 68k Retrocomputer RTL Constitution

Applies to this repository (`ttihp-sound-m68k`) and is intended to be shared unchanged with sibling
68k peripheral projects (e.g. `ttihp-spi-m68k`) so they are held to the same rules.

## Core Principles

### I. Test-First Verification (NON-NEGOTIABLE)

- Every RTL behavior MUST have a self-checking test written **before** the RTL that implements it.
  The test MUST be run and observed to fail against the stub or current RTL before
  implementation begins (red → green → refactor).
- Tests MUST decide pass/fail by assertion. Inspecting waveforms is a debugging aid, never a
  verification result.
- Every functional requirement and success criterion in a feature spec (FR-###, SC-###) MUST be
  covered by at least one named test, and each test MUST cite the IDs it verifies (in its
  docstring or name).
- Data-path behavior (samples, encodings, arithmetic) MUST be checked against an independent
  reference model written from the spec or contract, never derived from the RTL under test.
- Every bug fix MUST start with a regression test that reproduces the bug and fails before the
  fix.
- Randomized tests MUST log their seed and MUST be reproducible from it.

**Rationale**: Silicon can't be patched. A test written after the RTL tends to encode what the RTL
does rather than what the spec requires. Seeing the test fail first proves it can detect the defect.

### II. Coverage-Driven Completeness

Verification is complete only when every one of these holds, or has a written waiver
(see Governance):

- **Requirement coverage**: 100% of FR/SC IDs, spec edge cases, and contract clauses map to
  passing tests. This traceability is reviewed, not assumed.
- **Code coverage** of synthesizable RTL, measured by a coverage tool:
  - 100% line/statement coverage
  - 100% of FSM states and legal transitions
  - 100% toggle coverage of every flip-flop bit and every top-level port bit
- **Functional coverage**: explicit coverage points for each spec edge case and each boundary
  value (min/max/zero of every programmable field). Every point MUST be hit.
- A coverage hole is a defect. It MUST be closed with a test, or removed as dead logic, or waived
  with a written reason (e.g. an unreachable defensive default).
- Coverage MUST be re-measured and reviewed at every feature milestone and before any tapeout or
  FPGA release.

**Rationale**: Passing tests only prove what they exercise. Coverage shows what was never
exercised, and hardware corner cases (reset, wrap-around, back-to-back events) hide exactly there.

### III. Portable RTL: FPGA- and ASIC-Neutral

RTL MUST synthesize and behave identically under the ASIC flow and FPGA flows:

- Synthesizable Verilog-2005 subset, accepted without errors by Icarus Verilog, Verilator lint,
  and yosys (both ASIC and FPGA targets). Every file begins with `` `default_nettype none ``.
- **Fully synchronous**: one clock edge (posedge) per domain. Clock enables, never gated or
  logic-derived clocks. No latches: any inferred latch is a build failure. No combinational loops.
- No vendor primitives or technology cells in RTL. If one is unavoidable, it MUST sit behind a
  wrapper module that has a behavioral model.
- No internal tri-states. Bidirectional behavior exists only at top-level pads via explicit
  output-enable signals.
- Every `case` has a `default`, and every FSM recovers from an illegal state to a defined state.
- Explicit widths on all constants and ports. No implicit truncation or extension: lint width
  warnings are errors unless waived.
- Non-blocking assignments (`<=`) in clocked blocks, blocking (`=`) in `always @*` combinational
  blocks, never mixed within one block.
- No `#` delays, and no `$display`/`$finish` outside `` `ifdef SIM `` blocks in synthesizable files.
- Sizes and magic numbers are named `parameter`/`localparam` values.

**Rationale**: FPGAs are the fast, cheap way to prove a design before tapeout, and the same RTL
must be trustworthy on both. The constructs banned here are the ones that behave differently
between the two, or between simulation and synthesis.

### IV. Explicit Reset and No Assumed State

- Synthesizable RTL MUST NOT rely on initial values. `initial` blocks and declaration initializers
  (`reg x = 0;`) are forbidden. An FPGA bitstream zeroes flops, but ASIC flops power up random.
- Every flip-flop whose value can influence an output or control decision MUST have an explicit
  reset to a documented value. An exception is allowed only for pure data/pipeline registers that
  are provably overwritten before being read, and each one MUST be marked with an inline comment
  giving that argument.
- **Reset style**: active-low, asserted asynchronously, released synchronously through a 2-FF reset
  synchronizer. This is used consistently across the design.
- Simulation MUST start every register at X (not zero). Tests MUST check that every top-level output
  is free of X/Z from reset release onward. An X at an output after reset is a test failure.
- Documented reset values (register maps, datasheets) MUST be verified by a test that reads them
  back or observes them.

**Rationale**: "Works in simulation, dead on silicon" is most often an un-reset flop that simulation
or the FPGA quietly initialized. X-propagation checks make that failure visible before fabrication.

### V. Explicit Asynchronous Boundaries

- Every signal asynchronous to the clock MUST either pass through a ≥ 2-FF synchronizer before it
  controls anything, or be sampled only while qualified by a synchronized strobe, with a documented
  stability argument (setup/hold relative to that strobe).
- Multi-bit asynchronous values MUST NOT be synchronized bit-by-bit. Use strobe-qualified capture,
  Gray codes, or handshakes.
- A single clock domain is the default. Adding a second clock domain requires a written CDC design
  and review before RTL is written.
- Any intentional use of an unsynchronized signal (e.g. a combinational output release, or an async
  clear from a pin) MUST be justified in the plan's Complexity Tracking with a timing and
  metastability argument, and MUST be covered by a dedicated test.
- Tests MUST drive asynchronous inputs at randomized, non-clock-aligned phases. Stimulus that lands
  exactly on clock edges is forbidden for async inputs.

**Rationale**: The tt08 PS/2 chip failed on the board because its async inputs had no
synchronizers and no glitch filtering. Stimulus aligned to clock edges hid a race in its successor.

### VI. Observability and Safe Defaults

- Every status signal with meaning (FIFO state, busy, error, heartbeat) MUST be observable: on a pin
  or in a readable register. Nothing meaningful is left dangling.
- Spare output pins MUST first be considered for bring-up visibility (heartbeat, activity strobes)
  before being left unused.
- Every output MUST be in a documented, safe state from reset. Bus-facing outputs MUST be inactive
  (e.g. acknowledges deasserted, shared data lines not driven).
- Unused outputs are driven to constants. Unused inputs are explicitly consumed (e.g. `_unused`)
  so lint stays clean.

**Rationale**: At first-silicon bring-up, a scope on a pin is the only debugger. The tt08 failure
included a status signal that was computed but never wired out.

### VII. Gate-Level and Physical Readiness

- The synthesized netlist MUST pass the same test suite as the RTL (gate-level simulation) before
  any tapeout. A test excluded from GL runs needs a written reason (e.g. it depends on internal
  signal names).
- Lint MUST be clean (Verilator `--lint-only -Wall` or equivalent), or have explicit per-line
  waivers.
- The RTL MUST also synthesize for at least one FPGA target without errors (yosys `synth_ice40`,
  i.e. the TT ICE40UP5K demo target). This is the proof of Principle III.
- Area/utilization MUST be estimated at every milestone against a budget recorded in the plan, and
  timing MUST close at the target clock with positive slack.

**Rationale**: RTL simulation hides synthesis mismatches, and a design that doesn't fit or close
timing can't ship. Checking these at every milestone is far cheaper than finding them at tapeout.

## Verification Standards

- **Framework**: cocotb (Python) testbenches, run through the project Makefile. The simulator is
  Icarus Verilog for RTL and GL runs.
- **Python environment**: Python 3.13 in a project virtualenv (`test/venv`, not committed). 3.13 is
  the newest version cocotb 2.0.1 supports and the version `cocotb-coverage` 2.0 is tested on. Every
  Python dependency is pinned in `test/requirements.txt`, and tests are run from that venv.
  Project CI (`test.yaml`) uses 3.13 too. Test code MUST stay compatible with Python 3.11, the
  version TinyTapeout's `gl_test` action pins.
- **Functional coverage and constrained random**: `cocotb-coverage` is the standard tool
  (`CoverPoint`, `CoverCross`, `CoverCheck`, `coverage_section`). Every Principle II functional
  coverage point is declared with it. Each test module exports its results with
  `coverage_db.export_to_yaml`/`export_to_xml`, and they are combined with `merge_coverage` into one
  report. Constrained-random stimulus uses its `crv` module or plain seeded `random` (Principle I).
  Hand-rolled coverage counters are not allowed.
- **Code coverage** (line/toggle, which `cocotb-coverage` does not measure) uses **Verilator
  5.040** (cocotb supports 5.036+; 5.040 is the newest release cocotb 2.0.1's own CI tests).
  Tests run under `SIM=verilator EXTRA_ARGS=--coverage`, and the report comes from
  `verilator_coverage --annotate <dir> coverage.dat`, which prints the total percentage and marks
  uncovered lines. `verilator_coverage --write-info` emits lcov format for HTML or CI upload.
  Verilator runs alongside Icarus, which stays the primary RTL/GL simulator.
- **One-command flows**: each of these MUST be runnable locally with a single documented command:
  - RTL tests
  - gate-level tests
  - lint
  - code and functional coverage report
  - area estimate
  - FPGA synthesis check
- **Organization**:
  - One test module per user story, plus a standalone unit bench for each reusable IP block
    (e.g. `bus68k_if`).
  - Shared drivers, decoders, and reference models live in a helper package, not copied between
    tests.
- **Reference models**: bit-accurate, written from the spec and contracts, and kept alongside the
  tests.
- **Coverage cadence**: coverage is a local and milestone check, NOT a CI gate. It MUST be
  measured and reviewed at every milestone gate and before release (Principle II); CI runs the
  test suite only.
- **Results**: CI MUST fail on any test failure (e.g. inspect `results.xml`; the make exit code
  alone is not enough). Coverage reports are kept as CI artifacts.

## Development Workflow & Quality Gates

- **Flow**: spec → clarify → plan → tasks → implement, using Spec Kit. In `tasks.md`, test tasks
  are mandatory and each one precedes the implementation task it verifies.
- **Per-task definition of done**:
  1. The test existed and was observed failing.
  2. The test passes.
  3. Lint is clean.
  4. No new X at outputs.
  5. `info.yaml` `source_files` and `test/Makefile` `PROJECT_SOURCES` are in sync.
- **Milestone gate** (end of each user story), in addition to the per-task items:
  - The requirement-coverage traceability for that story is reviewed.
  - A code-coverage report is produced and every hole is closed or waived.
  - The area estimate is recorded against the budget.
- **Release gate** (tapeout or FPGA release), in addition to all milestone gates:
  - The full GL suite passes.
  - The FPGA synthesis check passes.
  - Timing closes.
  - The datasheet (`docs/info.md`) matches the contracts.
  - Design docs are updated for every deviation.
- **Reviews**: every change is reviewed against this constitution. The reviewer checks that tests
  came first and that coverage didn't regress.

## Governance

- This constitution supersedes other process guidance. Where `CLAUDE.md` or design docs conflict
  with it, this document wins, and the conflict MUST be fixed in the other document.
- **Amendments** are made by a change to this file that includes a Sync Impact Report, a version
  bump, and an update to Last Amended. Dependent plans that rely on an amended principle MUST be
  re-checked.
- **Versioning** (semantic):
  - MAJOR: a principle is removed or redefined incompatibly.
  - MINOR: a principle or section is added, or guidance is materially expanded.
  - PATCH: clarifications and wording only.
- **Waivers**: any deviation from a MUST (a coverage hole, an unsynchronized signal, a non-reset
  flop, a GL-excluded test) MUST be recorded with its rationale in the feature plan's Complexity
  Tracking table, or in an inline waiver comment where the principle allows it. Unrecorded
  deviations are defects.
- **Compliance review** happens at:
  - `/speckit-plan` (Constitution Check gate)
  - `/speckit-analyze`
  - every milestone gate
  - the release gate

  Runtime development guidance for agents lives in `CLAUDE.md`.

**Version**: 1.1.2 | **Ratified**: 2026-09-10 | **Last Amended**: 2026-09-12
