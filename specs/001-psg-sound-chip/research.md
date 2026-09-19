# Phase 0 Research: 68k PSG Sound Chip

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-09-10

Each entry resolves a design question the spec left to the plan. Format: Decision / Rationale / Alternatives considered. All clock-derived numbers assume the nominal 24.576 MHz clock (period 40.69 ns); every rate scales proportionally with the real clock (FR-034).

---

## R1. 68000 bus timing budget and bus-release mechanism

**Decision**: Assert DTACK and the data-bus drive synchronously (after 2-FF synchronization of the strobes), but **release both combinationally from the raw `AS_n`/`CS_n` pins**, and clear the internal DTACK flop **asynchronously** whenever raw `AS_n` is high.

- `dtack_n = ~dtack_q | as_n_raw | cs_n_raw`
- `uio_oe  = {8{oe_q & ~as_n_raw & ~cs_n_raw & r_w_raw}}`
- `dtack_q` is a flop with async clear on `(as_n_raw | ~rst_n)`.

**Rationale**: MC68000 User's Manual §10.10, read/write AC specifications:

| Parameter | 8 MHz | 10 MHz | 12.5 MHz | 16 MHz |
|---|---|---|---|---|
| #28 AS/DS negated → DTACK negated (max) | 240 ns | 190 ns | 150 ns | 110 ns |
| #29A AS/DS negated → data-in high-Z (max) | 187 ns | 150 ns | 120 ns | 90 ns |
| #15 AS/DS width negated (min) | 150 ns | 105 ns | 65 ns | 60 ns |
| #31 DTACK asserted → data-in valid (max) | 90 ns | 65 ns | 50 ns | 50 ns |

A fully synchronized release takes 2–3 clocks for the synchronizer plus an output register: 81–163 ns. That meets 8 MHz and fails at 12.5 MHz and above. The combinational release is a few gate delays plus TT mux and pad delay, so FR-004's 50 ns is easy, leaving ≥40 ns of the 16 MHz budget for board wiring.

The async clear on `dtack_q` fixes a second hazard: in a back-to-back cycle to this chip, AS can be high for as little as 60 ns (#15). That's about 1.5 of our clocks, so the synchronizer might not yet have deasserted `dtack_q` when the next AS falls. Without the clear, the stale DTACK would end the new cycle before its data was captured.

A 60 ns minimum AS-high pulse is always wider than one clock period (40.69 ns), so the synchronized `AS_n` is guaranteed at least one clean high sample. The FSM therefore always sees the release and returns to IDLE, and no extra sticky "release seen" flag is needed.

**Metastability of the async clear**: The clear is removed when `AS_n` falls, which happens asynchronously to `clk`. At that moment `dtack_q`'s D input equals its reset value (0), because the FSM can't be in its acknowledge state until the new cycle's strobes pass the synchronizer, at least 2 clocks later. A flop whose D equals its reset value can't go metastable on reset removal. See the Complexity Tracking entry in plan.md.

**Alternatives considered**:
- Fully synchronous release: fails 68000 timing at ≥12.5 MHz (table above).
- Open-drain / tri-state DTACK: not available on `uo_out`, and made unnecessary by the board-level AND combiner (bus-interface doc §3.1).

Source: [NXP MC68000 User's Manual (MC68000UM), §10.10](https://www.nxp.com/docs/en/reference-manual/MC68000UM.pdf).

---

## R2. Bus-cycle qualification and input capture

**Decision**: Synchronize **only the strobes**: `CS_n`, `AS_n`, and `DS_n` each get a 2-FF synchronizer. Qualify a cycle as `~cs_s & ~as_s & ~ds_s`. On the first qualified clock, capture `R_W`, `A3:A1`, and (for writes) `D7:D0` straight from the pins, without synchronizers.

**Rationale**: The 68000 guarantees address and R/W are valid before AS asserts (#11, #20A), and that write data is valid before DS asserts (#26: data-out valid → DS asserted ≥15 ns). By the time a synchronized DS is seen (≥2 clocks = 81 ns after the pin edge), those signals have been stable for over 80 ns, so capturing them is safe. This is the standard "synchronize the strobe, sample the qualified bus" technique and satisfies FR-002 without 14 extra synchronizer flops.

On write cycles DS asserts a full CPU clock after AS (#22). Qualifying on DS as well as AS is what guarantees the data is valid. On reads, AS and DS assert together.

Capturing on a single clock edge makes FR-006 hold by construction. Register writes happen in one clock, so an aborted cycle leaves either the old value (abort before qualification) or the complete new value (abort after).

**Glitch filtering**: Requiring all three of `CS_n`, `AS_n`, `DS_n` to be low in the synchronized domain is the qualification. No debounce counter is needed, because these are clean logic-level lines (bus-interface doc §5).

**Alternatives considered**: Synchronizing every bus input costs about 14 extra FFs (~900 µm²), doesn't protect any better, and still needs a coherent capture point.

---

## R3. Tone generator clock and pitch

**Decision**: Tone counters advance on a **192 kHz** enable (clk/128). Each channel's output toggles every `max(N,1)` ticks, so f = 96000 / max(N,1) Hz for 12-bit period N. The counter compares with `>=` against the active period, so shrinking the period mid-count takes effect on the next tick with no 4096-tick wrap.

**Rationale**: Verified numerically across all equal-tempered notes:

| Tone enable | Range (N=4095…1) | Worst error A0–C6 | Worst error A0–C7 | Worst error A0–C8 |
|---|---|---|---|---|
| **clk/128 (192 kHz)** | **23.4 Hz – 96 kHz** | **6.4 cents** | **16.4 cents** | **23.3 cents** |
| clk/256 (96 kHz) | 11.7 Hz – 48 kHz | 16.4 cents | 23.3 cents | 71.9 cents |
| AY-3-8910 @ 1.79 MHz | 13.7 Hz – 55.9 kHz | 14.5 cents | 19.1 cents | 47.4 cents |

clk/128 meets FR-021 (A0–C6 within ±10 cents, C6–C8 within ±25 cents) and beats a real AY. A440 is N=218 → 440.37 Hz (+1.5 cents).

**N = 0 (FR-027)**: Behaves as N = 1 (96 kHz toggle), matching AY-3-8910 behavior. Documented in the register contract.

**Alternatives considered**: A 48 kHz tone clock gives ~30-cent errors at A440. A 384 kHz clock needs a 13-bit divider to reach A0, which breaks the 12-bit period / 4-bit volume packing.

---

## R4. Volume curve, per-channel amplitude, and mix width

**Decision**:
- **Amplitude**: a 16-entry logarithmic table with 8-bit amplitude, derived from the measured AY-3-8910 DAC curve (normalized, per MAME's AY model) and scaled to 255: `0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255`. All 15 non-zero levels are distinct.
- **Bipolar output**: each enabled channel contributes `+amp` when its square/noise bit is high and `−amp` when low. Disabled or volume-0 channels contribute 0.
- **Mix**: a signed sum of 4 channels, range ±1020, held in an **11-bit signed** word. It can't overflow, so no saturation is needed (FR-025).
- **Serial mixer**: a single shared volume table and adder accumulate the 4 channels over 4 consecutive clocks, once per 192 kHz tick, instead of 4 parallel tables and a 4-input adder tree.

**Rationale**:
- Bipolar output makes each 50%-duty square wave zero-mean (no DC offset, FR-026), and silence is exactly 0.
- An 8-bit amplitude table is the narrowest width where the AY curve keeps all 15 steps distinct. At 6 bits, the three lowest levels collapse to 1.
- The mixer has 128 clocks per tick and needs 4, so serializing is free in time and saves three copies of the table (~60–80 cells each) and an adder tree.

**Alternatives considered**:
- Linear 4-bit volume: fails FR-023, because the low steps sound too loud.
- 5-bit log table: bottom steps collapse.
- A 32-level YM2149-style curve: the register map only has 4 volume bits.

---

## R5. PWM output

**Decision**: 7-bit PWM with a carrier at clk/128 = **192 kHz**, driven from the master counter's low 7 bits (no separate PWM counter). The duty value is `(mix >>> 4) + 64`, an offset-binary 0…127, latched at the start of each PWM period so the duty never changes mid-period. `pwm_out = (count[6:0] < duty)`. Silence gives duty 64/128 = 50%.

**Rationale**: 128 levels ≥ 64 and 192 kHz ≥ 150 kHz, so FR-031 passes with margin. The 192 kHz carrier is almost 10× above the 20 kHz audio band. Because PWM updates at the tone tick rate (192 kHz), not 48 kHz, the must-work output also gets the best time resolution of the three.

Dropping 4 LSBs pushes the quietest volume levels (1–4) below PWM resolution. That's acceptable for the lo-fi fallback, and I2S/S-PDIF keep all 11 bits.

**Alternatives considered**:
- 6-bit / 384 kHz (the design doc's recommendation): too few levels for a log volume curve summed over 4 channels.
- 8-bit / 96 kHz: fails FR-031's 150 kHz carrier floor.
- First-order noise-shaped PWM: worth ~2 extra effective bits for ~12 FFs. Deferred as an optional improvement if area allows.

---

## R6. I2S output format

**Decision**: Philips I2S, master mode, driven from master counter bits:
- **BCLK** = `count[2]`: 3.072 MHz, 64 per frame, registered.
- **LRCLK** = `count[8]`: 48 kHz, low = left, registered.
- **Slot**: `s = count[7:3]` (0…31) within each half-frame. SDATA carries sample bit `15 − (s−1)` for s = 1…16 and 0 otherwise. Slot 0 carries the previous word's (zero) LSB position, which gives the standard one-BCLK delay after each LRCLK edge.
- **Sample**: 16-bit signed = `mix << 5` (±32640), latched once per frame at `count == 511`. Left and right carry the same latched value.
- **Timing**: all three outputs come from flops updated on the same `clk` edge. SDATA and LRCLK change on the BCLK falling edge (`count[2:0]` wraps 7→0), and the receiver samples on the rising edge 4 clocks (163 ns) later.

**Rationale**: PCM5102-class DACs accept 64×Fs BCLK with no MCLK (internal PLL), satisfying FR-032. Indexing the latched sample by the counter needs no shift register, saving about 16 FFs. Registered outputs guarantee glitch-free, mutually aligned edges.

**Alternatives considered**:
- Left-justified format: not "standard I2S", and PCM5102 needs its FMT pin set.
- A 16-bit shift register: costs area for nothing.

---

## R7. S/PDIF output format

**Decision**: IEC 60958 consumer format, generated directly from the master counter with no subframe shift register.
- **Half-cell**: 4 clocks (6.144 MHz). **Bit cell** = `count[8:3]` (64 per frame), aligned exactly with I2S BCLK slots. Subframe = `count[8]` (0 = A/left, 1 = B/right). Bit-in-subframe `b = count[7:3]`.
- **Preambles** (bits 0–3, 8 half-cells), emitted as fixed level patterns:
  - B `11101000`: subframe A of frame 0
  - M `11100010`: subframe A of other frames
  - W `11100100`: subframe B
- **Bits 4–7 (aux)**: 0. **Bits 8–11**: 0. **Bits 12–27**: 16-bit sample, LSB first (same latched sample as I2S).
- **Bit 28 V** = 0 (valid). **Bit 29 U** = 0. **Bit 31 P** = even parity over bits 4–30, accumulated serially in one flop.
- **Bit 30 C (channel status)**: the bit for frame `f` is 1 only at `f = 2` (copy permitted, so SCMS receivers don't refuse) and `f = 25` (byte 3 bit 1: sample frequency 48 kHz, i.e. byte 3 = `0x02`). Everything else is 0: consumer, linear PCM, no pre-emphasis, general category, word length not indicated. Both subframes carry the same C bit.
- **Frame counter**: 0…191, advanced at each frame boundary.
- **Biphase-mark**: the output level toggles at every bit-cell boundary, and again mid-cell for 1 bits.
- **Preamble polarity**: never needs inverting. Even parity over bits 4–31 guarantees an even number of transitions per subframe, and each preamble also has an even number, so the line level at every subframe boundary equals the reset level (0). Receivers are polarity-agnostic anyway.

**Rationale**: No shift register saves about 32 FFs. The whole encoder is a frame counter (8 FFs), a line-level flop, and a parity flop, plus combinational bit selection.

**Cut path (FR-051)**: the encoder is its own module behind an `ENABLE_SPDIF` parameter. When set to 0, the pin is tied low and the module isn't built.

**Alternatives considered**: Full 192-bit channel-status storage is unnecessary, since only two bits are non-zero.

---

## R8. Noise channel

**Decision**: A **17-bit LFSR** with feedback taps at bits 17 and 14 (x¹⁷ + x¹⁴ + 1, the AY-3-8910 polynomial), reset to 1 so it can never lock at all-zeros. It shifts at 96 kHz / (R+1) for 4-bit rate R, which gives 96 kHz (R=0) down to 6 kHz (R=15), using a 5-bit prescale counter on the 192 kHz tick (one shift every 2(R+1) ticks). LFSR bit 0 drives the channel bipolar ±amp.

**Rationale**: AY-compatible timbre. 16 settings (FR-022) cover the useful range, from bright hiss to low rumble.

**Alternatives considered**: A 15-bit LFSR (SN76489-like) saves 2 FFs but gives a slightly more tonal, periodic noise.

---

## R9. Atomic pitch update and register semantics

**Decision**: Each tone channel has a **staged low byte** (the register at index 2c) and a **committed 12-bit active period**. Writing `PERIOD_LO` only updates the staged byte. Writing the channel's `CTRL` register (volume + period[11:8]) commits `{CTRL[3:0], PERIOD_LO}` into the active period on the same clock. The tone counter only ever reads the active period. Reads return the register contents as last written, including the staged low byte.

**Rationale**: FR-012 requires that no mixed old/new pitch is ever heard. Committing on the CTRL write makes the pitch change atomic and costs 12 FFs per channel. The driver rule is simple: **write LO, then CTRL**. A fine-tune that only changes LO must rewrite CTRL; rewriting it with an unchanged value is harmless. Volume, which lives in CTRL, changes immediately.

**Alternatives considered**:
- A single shared staging byte: saves 16 FFs, but reading back a staged LO before its commit would return the wrong value, which violates FR-011.
- Committing only at the tone counter's wrap: still allows one period of mixed pitch if the wrap falls between the two writes.

---

## R10. Time base and clock-enable schedule

**Decision**: A single free-running 9-bit master counter `count[8:0]`, reset to 0. Every function is a clock enable derived from it (single clock domain, no CDC):

| Event | Condition | Rate |
|---|---|---|
| S/PDIF half-cell boundary | `count[1:0] == 3` | 6.144 MHz |
| I2S / S-PDIF bit-cell boundary | `count[2:0] == 7` | 3.072 MHz |
| Mixer accumulate slots | `count[6:0] ∈ {120,121,122,123}` | 4 clks per 192 kHz |
| Tone/noise tick, PWM period start, PWM duty latch | `count[6:0] == 127` | 192 kHz |
| Frame boundary: latch I2S/S-PDIF sample, advance S/PDIF frame counter | `count == 511` | 48 kHz |
| Heartbeat pin (FR-040) | registered `count[8]` | 48 kHz square |

**Rationale**: Directly implements the resolved clock plan (sound-chip.md §4) with exact ratios.

**Reset**: `rst_n` is asserted asynchronously and released through a 2-FF reset synchronizer. On the TT demo board `rst_n` comes from the RP2040/RP2350 and is asynchronous to `clk`.

---

## R11. Area budget and hardening configuration

**Findings**:
- The ihp-sg13g2 1x1 tile is **202.08 × 154.98 µm = 31,318 µm²** (`tt/tech/ihp-sg13g2/tile_sizes.yaml`). The "167x108 µm" comment in `info.yaml` is left over from the sky130 template.
- IHP only has reset flops. `sg13g2_dfrbpq_1` is **49.0 µm²**, and with an enable mux (`mux2_1`, 18.1 µm²) it's about 67 µm².
- **Calibration point**: the sibling `ttihp-ps2-m68k` synthesizes (yosys 0.41, typ lib) to 170 FFs / 582 cells / **12,620 µm²** (40% of the tile) and hardens in 1x1 at `PL_TARGET_DENSITY_PCT = 60`.

**Estimate for this design**: about 230 FFs (≈ 8 sync/reset + 16 bus FSM + 64 registers + 36 active periods + 39 tone + 9 time base + 22 noise + 27 mixer/samples + 3 I2S + 12 S/PDIF + 2 debug). That's roughly 14k µm² of flops plus 4–6k µm² of logic, so **~18–20k µm² (57–64% of the tile)**. That's tight but feasible.

**Decision**: gate area with a yosys estimate script (`make area`) at three thresholds, measured on the typ-corner synthesis:

| Synth area | Action |
|---|---|
| ≤ 17,500 µm² (≤ 56%) | Proceed with the default config. |
| 17,500 – 21,000 µm² | Raise `PL_TARGET_DENSITY_PCT` to 70–75 (TT reports up to 80 working). |
| > 21,000 µm², or hardening fails | Build with `ENABLE_SPDIF = 0` (FR-051). |

**Also**: change `CLOCK_PERIOD` in `src/config.json` from 20 ns to **40 ns**. The real clock is 40.69 ns; constraining at 20 ns makes the resizer spend area on buffering for timing we don't need. 40 ns keeps a small margin. The config file explicitly lists `CLOCK_PERIOD` as user-adjustable.

---

## R12. Verification approach

**Decision**:
- **Framework**: cocotb 2.0.1 on Icarus Verilog 11 (TT template standard, installed locally). One test module per user story, listed in `COCOTB_TEST_MODULES`.
- **Helpers** (`test/tbutil/`):
  - a 68000 bus-master driver with randomized timing, whose offsets are never multiples of the clock period (see "Clock-grid race" below)
  - a bit-accurate Python PSG reference model
  - I2S and IEC 60958 decoders
  - a PWM duty meter
- **Bus IP unit bench**: `test/bus68k_if/` has its own Makefile and toplevel, pairing `bus68k_if` with a trivial 8-register payload. This is the independent verification FR-008 requires, and the sibling SPI project can copy it.
- **Gate-level sim**: reuse the sibling's workaround (`strip_gl_timing_cells.py` + `gl_sim_cell_models.v`, commit `e8abe74` in ttihp-ps2-m68k). Icarus can't drive the IHP sequential cells' specify-block `delayed_*` nets, so without it every FF reads X in GL sim.
- **Clock-grid race** (also from `e8abe74`): bus stimulus that lands exactly on a clock edge races the sampling flops. The bus driver uses non-grid-aligned delays by design, which also exercises the asynchronous-input requirement (FR-002).
- **FR-004 / SC-002 (50 ns release)**: Icarus GL sim is zero-delay, so it can't measure nanoseconds. The requirement is verified two ways:
  - *Structurally, in RTL and GL sim*: `DTACK_n` and `uio_oe` release within 1 ns of `AS_n` rising, at random clock phases, with no clock edge in between. This proves the path is combinational.
  - *By timing report*: the hardened design's `ui_in[1] → uo_out[0]` and `ui_in[1] → uio_oe[*]` combinational paths are ≤ 50 ns (expected ≈ 1–5 ns inside the project, plus TT mux/pad delay).
- **Runtime**: sim cost is driven by Python-side awaits. Decoders await edges of the output signals rather than every `clk`, so 1,000 I2S frames is about 128k awaits (a few minutes).

**Alternatives considered**: Verilator is faster, but its version here (4.038) is older than cocotb 2.x supports well, and it isn't what TT CI uses.
