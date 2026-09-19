# Sound Chip — Design Doc (ttihp-26b proposal)

Status: brainstorm, **amended during RTL implementation**. Builds directly
on [68k-bus-interface.md](68k-bus-interface.md) — read that first.

Blocks marked **Deviation (RTL)** are places where the original brainstorm
didn't survive contact with the implementation. The original reasoning is
kept in place rather than deleted, so the "why" of each change stays
readable. The authoritative, final statement of any of these is the
contract set under `specs/001-psg-sound-chip/contracts/`.

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
| uo_out[6] | HEARTBEAT | out | 48 kHz square wave whenever clocked and out of reset |
| uo_out[7] | WR_STROBE | out | one `clk`-wide pulse (40.7 ns) per completed register write |

**No IRQ, mono confirmed** (resolved — see §8): a pure tone generator with
no host-driven double-buffering doesn't need to interrupt the CPU, and
real stereo isn't worth doubling channel resources for a 1x1 tile. That
leaves DTACK_n plus the three audio outputs taking only 4 of 8 `uo_out`
pins, with 2 spare.

> **Deviation (RTL):** the two spare pins are now allocated to bring-up
> observability rather than left free — `uo_out[6]` HEARTBEAT and
> `uo_out[7]` WR_STROBE. *Reason:* the tt08 failure was partly a status
> signal that never reached a pin; these two let the bring-up engineer
> confirm "clock reaches the die and the time base is alive" and "register
> writes are landing" with nothing but a scope, before any driver exists.
> HEARTBEAT is independent of register contents, so it works even with the
> audio engine silent.

## 3. Audio Engine

Classic PSG architecture (AY-3-8910 / SN76489 inspired):

- 3 square/pulse tone channels, each with a 12-bit frequency divider
  (counts down from system clock to produce the tone) and a 4-bit
  volume/amplitude.
- 1 noise channel (LFSR-based), also with volume control.
- Per-channel enable bits.
- Mixer: sum the enabled channels (with saturation) into a PCM sample
  register, updated at a fixed internal sample-tick rate.

The sketch above left three things unspecified that turned out to matter.
All three are now pinned down:

> **Deviation (RTL) — tone counter clock.** The divider's input clock was
> never stated. It is now an explicit **192 kHz enable (clk/128)**, and the
> channel output toggles every `max(N,1)` ticks for 12-bit period `N`:
>
> `f = F_clk / (256 · max(N,1))` = **96000 / N Hz**, i.e. 23.4 Hz (N=4095)
> to 96 kHz (N=1). `N = 0` behaves as `N = 1`, matching the AY-3-8910.
>
> *Reason:* it was the only rate that met the pitch-accuracy target across
> a 12-bit period. Worst-case error A0–C6 is 6.4 cents, which actually
> beats a real AY-3-8910 at 1.79 MHz (14.5 cents) over the same range;
> A440 lands on N=218 → 440.37 Hz (+1.5 cents). A 48 kHz tone clock was
> rejected outright — it gives roughly 30-cent errors around A440. A
> 384 kHz clock would need a 13-bit period to reach A0, which breaks the
> 12-bit-period / 4-bit-volume packing the register map depends on.

> **Deviation (RTL) — volume curve.** Volume is a **16-entry logarithmic
> table** with 8-bit amplitudes, derived from the measured AY-3-8910 DAC
> curve: `0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255`.
> *Reason:* a linear 4-bit volume was considered and rejected — its low
> steps sound disproportionately loud, a standard complaint about naive PSG
> clones. 8 bits is the narrowest amplitude width at which all 15 non-zero
> steps of this curve stay distinct; at 6 bits the three quietest collapse
> into each other.

> **Deviation (RTL) — the mixer is bipolar, and there is no saturation.**
> Each enabled channel contributes `+amp` when its square/noise bit is high
> and `−amp` when it is low (disabled or volume-0 channels contribute 0),
> so the sum of four channels is an **11-bit signed** value bounded by
> ±1020. *Reason:* that range **cannot overflow**, so the "sum with
> saturation" above is replaced by a plain sum with no saturation logic at
> all. The bipolar encoding also makes each 50%-duty square zero-mean, so
> no channel injects a DC offset, and silence is *exactly* 0 rather than
> some mid-scale constant — which is what makes enabling or disabling a
> channel click-free. I2S and S/PDIF carry `mix << 5` as a signed 16-bit
> sample (±32640).
>
> Implementation note: the four channels are accumulated serially over four
> consecutive clocks once per 192 kHz tick, through one shared volume table
> and one adder, rather than four parallel tables and an adder tree. There
> are 128 clocks per tick and only 4 are needed, so this is free in time.

> **Deviation (RTL) — noise generator.** A **17-bit LFSR** with taps
> x¹⁷ + x¹⁴ + 1 (the AY-3-8910 polynomial), **reset to 1** so it can never
> lock up in the all-zeros state, shifted at `96 kHz / (rate + 1)` for the
> 4-bit rate field: 96 kHz (bright hiss) down to 6 kHz (low rumble). Bit 0
> drives the channel bipolar, ±amp, like a tone channel.

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

| Counter tap | Frequency | Use |
|---|---|---|
| bit1 | 6.144 MHz (128×Fs) | S/PDIF half-cell clock (biphase-mark encoder tick) |
| bit2 | 3.072 MHz (64×Fs) | I2S `BCLK`, 32-bit slots per channel (compatible with common off-the-shelf I2S DACs, e.g. PCM5102-style) |
| `count[6:0]` wrap | 192 kHz (4×Fs) | tone/noise tick, mixer accumulate, PWM carrier period + duty latch |
| bit8 | 48 kHz | Fs / `LRCLK` / mixer sample-tick |

> **Deviation (RTL) — the PWM rows.** The original table offered bit5
> (384 kHz, 6-bit PWM) and bit7 (96 kHz, 8-bit PWM) as the two PWM options.
> Both are superseded by a single **192 kHz** carrier at 7-bit resolution,
> which is the `count[6:0]` wrap rather than a bit tap — see §5. *Reason:*
> 6 bits is too coarse once four channels are summed through a logarithmic
> volume table, and the 96 kHz alternative has too little carrier margin.
> 192 kHz keeps ~10× headroom over the audio band and shares its tick with
> the tone generators, so no separate PWM counter is needed.

Every function is a clock enable decoded from this one counter, which is
what keeps the whole audio engine in a single clock domain:

| Event | Condition | Rate |
|---|---|---|
| S/PDIF half-cell boundary | `count[1:0] == 3` | 6.144 MHz |
| I2S / S/PDIF bit-cell boundary | `count[2:0] == 7` | 3.072 MHz |
| Mixer accumulate slots | `count[6:0] ∈ {120,121,122,123}` | 4 clks per tick |
| Tone/noise tick, PWM period start + duty latch | `count[6:0] == 127` | 192 kHz |
| Latch I2S/S-PDIF sample, advance S/PDIF frame counter | `count == 511` | 48 kHz |
| HEARTBEAT pin (§2) | registered `count[8]` | 48 kHz square |

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

> **Deviation (RTL):** **7-bit PWM (128 levels) at a 192 kHz carrier**
> (`clk`/128), not 6-bit at 384 kHz. Duty is `((mix >>> 4) + 64) / 128` —
> offset binary 0…127, latched at the start of each carrier period so it
> never changes mid-period — which puts silence at exactly 50%. *Reason:*
> the paragraph above assumed the mixer's dynamic range was set by 4-bit
> per-channel volume, but volume is a *logarithmic* index into an 8-bit
> amplitude table and four such channels are summed (§3), so the mix
> genuinely needs more than 6 bits. 192 kHz is still nearly 10× above the
> 20 kHz audio band, which is ample for the external RC filter. Dropping
> the mix's 4 LSBs does push the quietest volume levels (1–4) below PWM
> resolution; that is accepted for the lo-fi fallback, and I2S and S/PDIF
> keep all 11 bits. Driving the comparator from the master counter's low 7
> bits means there is no separate PWM counter, and PWM updates at 192 kHz
> rather than 48 kHz — so the must-work output also gets the best time
> resolution of the three.

### I2S
Standard 3-wire digital serializer (`BCLK` = 3.072 MHz, `LRCLK`/`WS` =
48 kHz, `SDATA`, per §4). A shift register loads L/R sample words on each
`LRCLK` edge and shifts MSB-first on `BCLK` edges, 32-bit slots per
channel (top 16-18 bits meaningful, rest zero-padded — matches what most
off-the-shelf I2S DAC breakout boards expect). Mono confirmed — duplicate
the single mixed sample to both L and R slots rather than building any
pseudo-stereo split.

Concretely: the word is **16-bit two's-complement, MSB first, starting one
`BCLK` after the `LRCLK` edge**, followed by 16 zero bits to fill the
32-bit slot. The sample value is `mix << 5` (±32640), latched once per
frame; both slots carry that same value. `LRCLK` is low for left. `SDATA`
and `LRCLK` change on the `BCLK` falling edge and are stable for 4 `clk`
(163 ns) before the receiver's rising-edge sample. Targets PCM5102A-class
DACs in slave mode with **SCK tied low** (internal PLL) and FMT = I2S — no
master clock is generated and none is needed. Rather than a shift register,
the serializer indexes the latched sample by the counter, which saves ~16
flops.

### S/PDIF
Biphase-mark-coded serial output at `64 × Fs`, framed in 32-bit subframes
with B/M/W preamble patterns per IEC 60958. Meaningfully more logic than
I2S (encoder plus frame/channel-status bit sequencing), but a lot of
retro/DIY audio gear can consume it directly through a simple transformer
or even a resistor into coax — no external DAC needed. Treat as the
highest-effort, highest-payoff of the three; first candidate to cut if
area or timing gets tight.

Preambles are B = `11101000` (subframe A, frame 0 of a block), M =
`11100010` (subframe A, other frames), W = `11100100` (subframe B), written
as half-cell levels. Bits 4–11 are 0, bits 12–27 carry the same 16-bit
sample as I2S (LSB first), bit 28 V = 0 (valid audio), bit 29 U = 0, bit 31
P = even parity. Like I2S, the encoder is generated straight from the
master counter with no subframe shift register — a frame counter, a line
level flop and a parity flop — saving ~32 flops.

> **Deviation (RTL) — channel status is not all zeros.** Two bits in the
> 192-frame channel-status block are set: **bit 2** (copy permitted) and
> **bit 25** (byte 3 = `0x02`, sample frequency 48 kHz). Everything else
> stays 0, which means consumer, linear PCM, no pre-emphasis, general
> category, word length not indicated. Both subframes carry the same C bit.
> *Reason:* with an all-zero block, byte 3 reads as "44.1 kHz" and a
> receiver may mislabel — or refuse to lock to — a stream that is actually
> 48 kHz; the copy bit keeps SCMS-aware receivers from rejecting it. Only
> two bits are non-zero, so no 192-bit channel-status memory is needed —
> the C bit is just a compare against the frame counter.

**Cut path:** the encoder is its own module behind an `ENABLE_SPDIF`
parameter. Set it to 0 and the module isn't built and the pin is tied low —
this is the FR-051 escape hatch if the design doesn't fit a 1x1 tile.

## 6. Register Map

The original reasoning here was: only 8 directly addressable registers
(`A1-A3`) but 4 channels' worth of state (12-bit freq + 4-bit volume +
enable, ×4) won't fit directly, so adopt the AY-style indirect-addressing
option from the bus-interface doc — `REG0` as an address latch selecting
one of up to 16 internal registers, `REG1` as the data port, with internal
indices roughly 0x0-0xA covering three channels' freq/volume, noise, and a
mixer/enable register.

> **Deviation (RTL) — the indirect scheme is replaced by eight directly
> addressed registers**, with pitch and volume packed together:
>
> | Idx | Name | Bits 7:4 | Bits 3:0 |
> |---|---|---|---|
> | 0 | `A_LO` | `period[7:0]` — whole byte, staged | |
> | 1 | `A_CTRL` | `vol` | `period[11:8]` (commits pitch) |
> | 2 | `B_LO` | `period[7:0]` — whole byte, staged | |
> | 3 | `B_CTRL` | `vol` | `period[11:8]` (commits pitch) |
> | 4 | `C_LO` | `period[7:0]` — whole byte, staged | |
> | 5 | `C_CTRL` | `vol` | `period[11:8]` (commits pitch) |
> | 6 | `NOISE` | `vol` | `rate` |
> | 7 | `ENABLE` | `rsvd`, `spdif_en`, `i2s_en`, `pwm_en` | `noise_en`, `c_en`, `b_en`, `a_en` |
>
> All registers reset to `0x00` except `ENABLE = 0x70` — every channel off,
> every output format on and emitting silence. Every register is read/write
> and reads have no side effects.
>
> *Reason:* the premise that the state "won't fit directly" was wrong once
> volume and pitch share a byte. It fits **exactly** in eight registers, and
> that buys a lot: one bus cycle per register instead of two, no
> address-latch state to hold, no read-modify-write hazard if two pieces of
> software touch the chip, and no possibility of the latch being left
> pointing somewhere unexpected.
>
> Two honest costs, both accepted:
> - **No room for an ID register.** The map is exactly full, so software
>   detects the chip by writing a pattern to a register (e.g. `NOISE`),
>   reading it back, and restoring it. The HEARTBEAT pin (§2) confirms the
>   chip is alive without software at all.
> - **The noise rate is 4 bits (16 settings), not the AY's 5 (32)**, because
>   noise volume and rate share one byte. 16 settings still span 96 kHz
>   (bright hiss) to 6 kHz (low rumble), which covers the useful range.

### 6.1 Atomic pitch commit — write `x_LO`, then `x_CTRL`

> **Deviation (RTL):** a 12-bit pitch spans two registers, so each tone
> channel keeps a **staged low byte** and a separate **committed 12-bit
> active period**. Writing `x_LO` updates only the staged byte. Writing
> `x_CTRL` commits `{x_CTRL[3:0], x_LO}` into the active period on the same
> clock. The tone counter only ever reads the active period.
>
> *Reason:* without this, a two-byte pitch update briefly sounds a garbage
> pitch assembled from the new low byte and the old high nibble — audible
> as a click or a blip on every note change, which is exactly the artifact
> a music player hits hardest. Committing on the CTRL write makes the change
> atomic for ~12 flops per channel.
>
> Rules this implies for the driver:
> - Change pitch: write `x_LO`, then `x_CTRL`.
> - Change volume only: rewrite `x_CTRL` with the same period nibble.
>   Rewriting an unchanged period is harmless and does not restart the
>   waveform.
> - A write to `x_LO` alone is not heard until `x_CTRL` is written.
> - Reads still return exactly what was last written, *including* a staged
>   `x_LO` that hasn't been committed yet. (This is why each channel gets
>   its own staging byte rather than one shared one — a shared byte would
>   read back wrong.)
>
> Volume lives in `x_CTRL` and takes effect immediately; it is not staged.

The registers sit at **every other byte address** in the decoded block,
because the chip ignores A0 and has a single data strobe — see
[68k-bus-interface.md §2](68k-bus-interface.md#2-68k-bus-primer-just-what-we-need).
Use byte accesses only.

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

> **Deviation (RTL) — the "consider" above was taken up, and the plan grew.**
> `test/tbutil/` holds shared helpers: a 68000 bus master whose stimulus
> delays are deliberately *never* multiples of the clock period, a
> bit-accurate Python PSG reference model, I2S and IEC 60958 decoders, and a
> PWM duty meter. Tests are organized one module per user story, plus a
> standalone bench for the bus IP at `test/bus68k_if/` (see
> [68k-bus-interface.md §7](68k-bus-interface.md#7-test-plan)). The S/PDIF
> decoder is closed-loop as hoped, checked over at least two full 192-frame
> blocks against the I2S samples.
>
> Two things worth knowing before running any of this: Icarus GL sim is
> zero-delay, so the 50 ns bus-release requirement can't be *measured* in
> simulation — it is verified structurally (release within 1 ns of `AS_n`
> rising at random clock phases, with no clock edge in between, which proves
> the path is combinational) and then confirmed on the hardened design's
> timing report. And GL sim needs the sibling project's
> `strip_gl_timing_cells.py` workaround, or every flop reads X.

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
  Both `uo_out[6]` and `uo_out[7]` are spare as a result — **though they no
  longer are**: see the §2 deviation, they now carry HEARTBEAT and
  WR_STROBE.
- **Area is the live risk.** Estimated ~230 flops, roughly 18–20k µm² of the
  1x1 tile's 31,318 µm² (57–64%) — tight but feasible. S/PDIF remains the
  designated cut (`ENABLE_SPDIF = 0`) if synthesis comes in over budget or
  hardening fails; PWM and I2S and all four channels are kept regardless.

## 9. Dependency

Built entirely on top of the 68k Bus Interface's `reg_addr / reg_wdata /
reg_rdata / reg_write / reg_read` interface — no 68k bus signal handling is
duplicated here. See [68k-bus-interface.md](68k-bus-interface.md).
