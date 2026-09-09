# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## Project Overview

This is a Tiny Tapeout ihp-26b project: an 8-bit PSG (programmable sound
generator) chip for a 68k-based retrocomputer. It exposes itself as a
memory-mapped peripheral on the 68k's asynchronous bus and drives three
simultaneous audio outputs — PWM, I2S, and S/PDIF — to maximize the odds
at least one comes up clean on real silicon.

**Status: nothing is implemented yet.** `src/project.v` is a placeholder
that drives all outputs to zero. Your job is to build this from the spec
below.

**Top module**: `tt_um_benpayne_sound_chip`
**Target clock**: 24.576 MHz (set via `clock_hz` in `info.yaml` — do not
change this without re-deriving the divider chain in the spec, the whole
audio clock tree depends on this exact value)
**Design size**: 1x1 tile
**Process**: IHP SG13G2 (130nm BiCMOS), ihp-26b shuttle

## Read the spec first

- [`docs/design/sound-chip.md`](docs/design/sound-chip.md) — the actual
  spec for this chip: pin budget, audio engine architecture, clock/divider
  plan (already resolved — 24.576 MHz gives every needed clock as a
  bit-tap of one free-running counter), output-format details for
  PWM/I2S/S-PDIF, register map, test plan, and open risks.
- [`docs/design/68k-bus-interface.md`](docs/design/68k-bus-interface.md)
  — the shared 68k bus interface this chip is built on
  (`bus68k_if`-shaped register access: `reg_addr`/`reg_wdata`/`reg_rdata`/
  `reg_write`/`reg_read`). This is not implemented anywhere yet either —
  you're the first project building it. A sibling project
  ([ttihp-spi-m68k](https://github.com/benpayne/ttihp-spi-m68k)) will
  reuse the same interface, so keep it generic rather than baking in
  audio-specific assumptions.

Both docs are living design docs from a brainstorming session, not
locked specs — if you find something that doesn't work once you're
actually writing RTL, that's expected; use your judgment and note the
deviation, don't treat every line as gospel.

## Key resolved decisions (don't relitigate these)

- **Mono only, no IRQ.** Both were considered and explicitly dropped —
  see sound-chip.md §8.
- **DTACK is self-generated** on a plain push-pull `uo_out` pin, not
  tri-stated. External board-level glue (a single AND gate) combines
  multiple peripherals' `DTACK_n` lines — this chip does not need to
  worry about bus contention. See 68k-bus-interface.md §3.1.
- **Single clock domain, no CDC** inside the audio engine — `BCLK`,
  `LRCLK`, and the S/PDIF clock are `clk`-synchronous toggle outputs
  derived from specific bits of one free-running counter, not separate
  clock trees. The only asynchronous boundary in this whole design is the
  68k bus signals themselves (`cs_n`, `as_n`, `r_w`, `ds_n`, `a1-3`) —
  those need proper 2-FF synchronization before being used, exactly like
  every async input in [ttihp-ps2-m68k](https://github.com/benpayne/ttihp-ps2-m68k)
  should have from the start (see "Why this matters" below).

## Why this matters: the tt08 lesson

A prior sibling project (the PS/2 decoder that predates this retrocomputer's
ttihp port) was fabricated on tt08 *without* metastability synchronizers on
its async inputs, without CS glitch filtering, and with a `fifo_full`
status signal left unwired — and it failed on the real board as a result.
Don't repeat that: every signal crossing from the 68k bus into this chip's
clock domain needs a synchronizer, and every status flag that says
something meaningful (FIFO state, once you have one) needs to actually be
wired to a visible pin, not left dangling.

## Development Commands

Standard Tiny Tapeout cocotb flow (see `test/README.md` for details):

```bash
cd test
make -B                    # RTL simulation
```

Gate-level simulation, once synthesized: copy
`../runs/wokwi/results/final/verilog/gl/{top_module}.v` to
`test/gate_level_netlist.v`, then:

```bash
make -B GATES=yes
```

Keep `info.yaml`'s `source_files` and `test/Makefile`'s
`PROJECT_SOURCES` in sync as you add files to `src/`.

## File Structure

- `src/` — Verilog source (currently just the placeholder `project.v`)
- `test/` — cocotb testbench (currently the template default, needs
  rewriting per the test plans in the design docs)
- `docs/design/` — the specs described above
- `docs/info.md` — user-facing datasheet (fill in as the design solidifies)
- `info.yaml` — Tiny Tapeout project configuration (pinout, clock, source
  file list)
