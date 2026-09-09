# Sound Chip — Design Doc (ttihp-26b proposal)

Status: brainstorm / pre-RTL. Builds directly on
[68k-bus-interface.md](68k-bus-interface.md) — read that first.

## 1. Motivation

A fun chiptune-style PSG (programmable sound generator) for the 68k
retrocomputer, exposed as a memory-mapped peripheral via the 68k bus
interface. Three simultaneous output formats — PWM, I2S, and S/PDIF — to
maximize the odds at least one comes up clean on real silicon, and because
each is independently interesting to get working.

## 2. Pin Budget (combined with the bus interface)

| Pin | Signal | Direction | Notes |
|---|---|---|---|
| ui_in[7:0] | 68k bus control/address | in | identical to bus-interface doc §3 (CS_n, AS_n, R_W, DS_n, A1-3, spare) |
| uio[7:0] | D0-D7 | bidir | shared register data bus |
| uo_out[0] | DTACK_n | out | self-generated, see [68k-bus-interface.md §3.1](68k-bus-interface.md#31-dtack-self-generated-plain-push-pull-output) — needed, not optional |
| uo_out[1] | PWM_OUT | out | single-ended PWM audio, external RC filter |
| uo_out[2] | I2S_BCLK | out | bit clock |
| uo_out[3] | I2S_LRCLK | out | word select (L/R) |
| uo_out[4] | I2S_SDATA | out | serial audio data |
| uo_out[5] | SPDIF_OUT | out | biphase-mark encoded S/PDIF |
| uo_out[6:7] | spare | out | test/debug, or future use |

**No IRQ, mono confirmed** (resolved — see §8): a pure tone generator with
no host-driven double-buffering doesn't need to interrupt the CPU, and
real stereo isn't worth doubling channel resources for a 1x1 tile. That
leaves DTACK_n plus the three audio outputs taking only 4 of 8 `uo_out`
pins, with 2 spare.

## 3. Audio Engine

Classic PSG architecture (AY-3-8910 / SN76489 inspired):

- 3 square/pulse tone channels, each with a 12-bit frequency divider
  (counts down from system clock to produce the tone) and a 4-bit
  volume/amplitude.
- 1 noise channel (LFSR-based), also with volume control.
- Per-channel enable bits.
- Mixer: sum the enabled channels (with saturation) into a PCM sample
  register, updated at a fixed internal sample-tick rate.

## 4. Clock & Sample Rate Strategy — RESOLVED

The TT demo board's clock generator (RP2040/RP2350, PWM/PIO-divided) is
genuinely programmable via `clock_hz` in `info.yaml`, from 1 Hz up to
66.5 MHz — confirmed against [tinytapeout.com/specs/clock](https://tinytapeout.com/specs/clock/).
So we get to pick a clock that makes the audio math clean, rather than
being stuck with tt08's 25 MHz.

**Chosen: `clock_hz = 24576000` (24.576 MHz), Fs = 48 kHz.**

`24.576 MHz / 512 = 48 kHz` exactly, and 512 = 2⁹ — so every clock this
chip needs falls out as a bit-tap of one free-running 9-bit counter, with
no fractional dividers or DDS anywhere:

| Counter bit | Frequency | Use |
|---|---|---|
| bit1 | 6.144 MHz (128×Fs) | S/PDIF half-cell clock (biphase-mark encoder tick) |
| bit2 | 3.072 MHz (64×Fs) | I2S `BCLK`, 32-bit slots per channel (compatible with common off-the-shelf I2S DACs, e.g. PCM5102-style) |
| bit5 | 384 kHz | PWM carrier @ 6-bit resolution (recommended, see §5) |
| bit7 | 96 kHz | PWM carrier @ 8-bit resolution (alternative — worse carrier margin, see §5) |
| bit8 | 48 kHz | Fs / `LRCLK` / mixer sample-tick |

**Single clock domain, no CDC.** `BCLK`, `LRCLK`, and the S/PDIF output are
not separate clock trees — they're `clk`-synchronous toggle *outputs*
driven from specific counter bits, and the I2S/S/PDIF encoder logic runs
every `clk` cycle, gated by edge-detecting the relevant counter bit as a
clock-enable rather than crossing into a second clock domain. This sidesteps
the entire class of bug that caused the tt08 board failure
([[project_tt08_board_failure]]) — there's simply no async boundary inside
the audio engine to get wrong.

This also resolves the earlier open question about bit-exact 44.1/48 kHz:
we're not chasing it, we're getting a real 48 kHz exactly (limited only by
however accurate the RP2040/RP2350's own system clock is), which is more
than good enough for chiptune audio and a consistent, jitter-free clock
for any S/PDIF receiver.

## 5. Output-Format-Specific Notes

### PWM
Simplest option. Recommend 6-bit PWM (64 levels) comparator against the
mixed sample value, giving a 384 kHz carrier (`clk`/64, see §4) — 8x
headroom over the 48 kHz audio band for the external RC low-pass filter.
8-bit PWM was considered but only yields a 96 kHz carrier (2 cycles per
audio sample), too little margin; the 4-channel mixer's 4-bit-per-channel
volume resolution doesn't need more than ~6 bits of real dynamic range
anyway. Most forgiving output of the three — treat as the "must work"
fallback.

### I2S
Standard 3-wire digital serializer (`BCLK` = 3.072 MHz, `LRCLK`/`WS` =
48 kHz, `SDATA`, per §4). A shift register loads L/R sample words on each
`LRCLK` edge and shifts MSB-first on `BCLK` edges, 32-bit slots per
channel (top 16-18 bits meaningful, rest zero-padded — matches what most
off-the-shelf I2S DAC breakout boards expect). Mono confirmed — duplicate
the single mixed sample to both L and R slots rather than building any
pseudo-stereo split.

### S/PDIF
Biphase-mark-coded serial output at `64 × Fs`, framed in 32-bit subframes
with B/M/W preamble patterns per IEC 60958. Meaningfully more logic than
I2S (encoder plus frame/channel-status bit sequencing), but a lot of
retro/DIY audio gear can consume it directly through a simple transformer
or even a resistor into coax — no external DAC needed. Treat as the
highest-effort, highest-payoff of the three; first candidate to cut if
area or timing gets tight.

## 6. Register Map

Only 8 directly addressable registers (`A1-A3`) but 4 channels' worth of
state (12-bit freq + 4-bit volume + enable, ×4) won't fit directly. Adopt
the indirect-addressing option from the bus-interface doc:

- `REG0` (address latch): selects one of up to 16 internal registers.
- `REG1` (data port): read/write the selected internal register.

Sketch of internal registers (indices, not final):
| Idx | Register |
|---|---|
| 0x0-0x1 | Channel 0 freq (lo/hi) |
| 0x2 | Channel 0 volume/enable |
| 0x3-0x4 | Channel 1 freq (lo/hi) |
| 0x5 | Channel 1 volume/enable |
| 0x6-0x7 | Channel 2 freq (lo/hi) |
| 0x8 | Channel 2 volume/enable |
| 0x9 | Noise control (period + volume) |
| 0xA | Mixer/enable (per-channel + per-output-format enable bits) |

## 7. Test Plan

- cocotb: program each channel through the register interface, then:
  - Verify PWM output period/duty cycle matches the programmed
    frequency/volume.
  - Verify I2S frame structure — `LRCLK` period, bit count, MSB-first
    ordering — against known sample values.
  - Verify S/PDIF preamble patterns and biphase-mark encoding against a
    known test pattern.
- Consider a small Python IEC 60958 decoder in the testbench so S/PDIF
  correctness can be closed-loop verified (encode on-chip → decode in
  testbench → compare).

## 8. Open Questions / Risks

- Clock plan is resolved (§4) — confirm `clock_hz = 24576000` is
  accepted/achievable on the actual ttihp-26b board before treating the
  divider table as final; the 66.5 MHz ceiling and RP2040/RP2350 PWM/PIO
  divide chain are documented for sky130-era boards, ttihp specifics not
  independently verified.
- S/PDIF is the most likely feature to get cut under area/timing pressure;
  PWM is the fallback that should work no matter what.
- **Resolved: mono only, no IRQ.** Real stereo would roughly double channel
  resources for a 1x1 tile and isn't worth it; a pure tone generator with
  no host-driven double-buffering doesn't need to interrupt the CPU either.
  Both `uo_out[6]` and `uo_out[7]` are spare as a result.

## 9. Dependency

Built entirely on top of the 68k Bus Interface's `reg_addr / reg_wdata /
reg_rdata / reg_write / reg_read` interface — no 68k bus signal handling is
duplicated here. See [68k-bus-interface.md](68k-bus-interface.md).
