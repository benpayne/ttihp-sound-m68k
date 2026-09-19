<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

An 8-bit PSG (programmable sound generator) in the tradition of the AY-3-8910
and SN76489, built as a memory-mapped peripheral for a 68000-based
retrocomputer.

Three independent square-wave tone channels and one pseudo-random noise
channel are mixed into a single mono signal, which is presented
*simultaneously* on three independent outputs — PWM, I2S, and S/PDIF — so that
at least one analog path works on first silicon. The chip appears to the CPU
as **eight byte-wide registers**, each reachable in a single bus cycle.

### Clocking

The chip runs from one clock input at a nominal **24.576 MHz**. Every internal
rate is an exact power-of-two division of it, taken as a bit-tap of a single
free-running 9-bit counter — there are no fractional dividers, no PLL, and no
second clock domain:

| Division | Rate | Use |
|---|---|---|
| clk / 4 | 6.144 MHz | S/PDIF half-cell |
| clk / 8 | 3.072 MHz | I2S `BCLK` (64 × Fs) |
| clk / 128 | 192 kHz | tone/noise tick, PWM carrier |
| clk / 512 | 48 kHz | Fs, `LRCLK`, heartbeat |

Because all of these are exact ratios of the one input clock, a clock that is
off nominal simply shifts every rate proportionally: pitch and sample rate move
together, and no output format's framing breaks. Rates quoted throughout this
datasheet assume the nominal 24.576 MHz.

### Sound engine

- **Tone channels A, B, C** — each has a 12-bit period `N` and a 4-bit volume.
  The channel output toggles every `max(N,1)` ticks of the 192 kHz tone clock:

  `f = F_clk / (256 · max(N,1))` = **96000 / N Hz** at nominal clock

  That spans 23.4 Hz (N = 4095) to 96 kHz (N = 1). **N = 0 behaves as N = 1**,
  matching the AY-3-8910.

  | Note | N | Actual |
  |---|---|---|
  | A0 27.5 Hz | 3491 | 27.50 Hz |
  | C4 261.63 Hz | 367 | 261.58 Hz |
  | A4 440 Hz | 218 | 440.37 Hz |
  | C8 4186 Hz | 23 | 4173.9 Hz (−5 cents) |

- **Noise channel** — a 17-bit LFSR (taps x¹⁷ + x¹⁴ + 1, the AY polynomial),
  shifted at `F_clk / (256 · (rate + 1))`: 96 kHz (rate 0, bright hiss) down to
  6 kHz (rate 15, low rumble). It has its own 4-bit volume.

- **Volume** — 16 levels on a logarithmic curve derived from the measured
  AY-3-8910 DAC, as 8-bit amplitudes:
  `0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255`.
  Level 0 is silent; each step up is a roughly equal increase in loudness.

- **Mixer** — bipolar and zero-mean: each enabled channel contributes
  `+amplitude` when its square/noise bit is high and `−amplitude` when it is
  low, so no channel adds a DC offset. The sum of all four channels is an
  11-bit signed value in −1020…+1020, which **cannot overflow**, so there is no
  clipping or saturation even with everything at maximum volume. Silence is
  exactly 0 — which is what makes enabling and disabling a channel click-free.

### Registers

Eight byte-wide registers, selected by CPU address lines **A3:A1**. The chip
ignores A0 and has a single data strobe wired to UDS or LDS, so its registers
appear at **every other byte address** in the decoded block. Use byte accesses
(`move.b`) only — a word access puts the other byte lane on a strobe the chip
never sees.

| Idx | Name | Offset (on UDS, D15–D8) | Offset (on LDS, D7–D0) | Reset |
|---|---|---|---|---|
| 0 | `A_LO` | +0x0 | +0x1 | 0x00 |
| 1 | `A_CTRL` | +0x2 | +0x3 | 0x00 |
| 2 | `B_LO` | +0x4 | +0x5 | 0x00 |
| 3 | `B_CTRL` | +0x6 | +0x7 | 0x00 |
| 4 | `C_LO` | +0x8 | +0x9 | 0x00 |
| 5 | `C_CTRL` | +0xA | +0xB | 0x00 |
| 6 | `NOISE` | +0xC | +0xD | 0x00 |
| 7 | `ENABLE` | +0xE | +0xF | **0x70** |

Every register is read/write; a read returns the last value written and has no
side effects.

**`x_LO`** (idx 0, 2, 4) — bits 7:0 are `period[7:0]`, the low 8 bits of the
channel's 12-bit tone period. This byte is **staged**: writing it alone has no
audible effect.

**`x_CTRL`** (idx 1, 3, 5)

| Bits | Field | Meaning |
|---|---|---|
| 7:4 | `vol` | Volume 0–15, logarithmic. Takes effect immediately. |
| 3:0 | `period[11:8]` | High 4 bits of the tone period. **Writing this register commits** `{period[11:8], x_LO}` as the new pitch. |

**`NOISE`** (idx 6)

| Bits | Field | Meaning |
|---|---|---|
| 7:4 | `vol` | Noise volume 0–15, same curve as the tone channels. |
| 3:0 | `rate` | Noise shift rate `F_clk / (256 · (rate + 1))`, 96 kHz to 6 kHz. |

**`ENABLE`** (idx 7), reset value `0x70`

| Bit | Field | Meaning |
|---|---|---|
| 0 | `a_en` | Tone A enable |
| 1 | `b_en` | Tone B enable |
| 2 | `c_en` | Tone C enable |
| 3 | `noise_en` | Noise enable |
| 4 | `pwm_en` | PWM output enable (disabled → pin held low) |
| 5 | `i2s_en` | I2S output enable (disabled → all three I2S pins held low) |
| 6 | `spdif_en` | S/PDIF output enable (disabled → pin held low) |
| 7 | — | Reserved; read/write storage, no function. Write 0. |

At reset every channel is off and silent, and all three output formats are
enabled and emitting silence: PWM sits at 50% duty, and I2S and S/PDIF carry
zero samples.

### Always write `x_LO` first, then `x_CTRL`

A 12-bit pitch does not fit in one byte, so a naive two-byte update would
briefly sound a garbage pitch assembled from the new low byte and the old high
nibble. This chip avoids that: `x_LO` only writes a staging byte, and the
**pitch commits atomically on the `x_CTRL` write**. The tone counter never sees
a mixed old/new value, so no intermediate pitch is ever heard.

Consequences for the driver:

- To change pitch: write `x_LO`, then `x_CTRL`.
- To change only volume: rewrite `x_CTRL` with the same period nibble.
  Rewriting an unchanged period is harmless and does not restart the waveform.
- A write to `x_LO` alone is not heard until `x_CTRL` is written.

### Detecting the chip

There is no ID register — the eight-register map is exactly full. To detect the
chip from software, write a pattern to a register (`NOISE` is a good choice),
read it back, and restore the original value. The heartbeat pin below confirms
the chip is alive without any software at all.

### Audio outputs

All three carry the same mono mix at the same time, and each can be disabled
independently through `ENABLE` without affecting the others.

| Output | Details |
|---|---|
| **PWM** (`uo[1]`) | 192 kHz carrier, 7-bit (128 levels). Duty = `((mix >>> 4) + 64) / 128`, latched at the start of each carrier period, so it never changes mid-period. Silence = exactly 50%. |
| **I2S** (`uo[2:4]`) | Philips I2S, chip is clock master. Fs = 48 kHz, BCLK = 3.072 MHz (64 × Fs), 32 BCLKs per slot, 16-bit two's-complement MSB first starting one BCLK after the LRCLK edge, then 16 zero bits. Sample = `mix << 5` (±32640). Left and right carry the identical mono sample. |
| **S/PDIF** (`uo[5]`) | IEC 60958-3 consumer, biphase-mark at 3.072 Mbit/s, 2 subframes × 32 bits, 192-frame blocks. Channel status marks the stream as consumer linear PCM, copy permitted, 48 kHz. Same 16-bit sample as I2S. |

**S/PDIF may not be present in the fabricated design.** It is the most complex
of the three formats and is built behind an `ENABLE_SPDIF` build parameter; if
the design does not fit the 1x1 tile, S/PDIF is the feature that gets removed
to stay at 1x1 (all four sound channels, PWM, and I2S are always kept). If it
is built out, `uo[5]` holds a steady low level and `ENABLE` bit 6 remains
read/write storage with no effect. Check the `uo[5]` pin against a receiver, or
the shipped configuration, before relying on it.

### Bring-up pins

Two output pins exist purely so the chip can be verified with nothing but a
scope, before any driver has been written:

- **`uo[6]` HEARTBEAT** — a 48 kHz square wave whenever the chip is clocked and
  out of reset, regardless of register contents. If this is toggling, the clock
  reaches the die, reset has released, and the internal time base is running.
- **`uo[7]` WR_STROBE** — one `clk`-wide pulse (40.7 ns) per completed register
  write. If this pulses when the CPU stores to the chip's address, the address
  decode, chip-select, and bus handshake are all working — even if no sound
  comes out yet.

Together they split first-silicon debugging into independent questions: is it
clocked, are writes landing, and is the audio engine responding — rather than
one undifferentiated "the chip is silent, why?".

## How to test

Set `clock_hz` to 24576000 and release reset.

1. **Confirm it is alive.** With only power, clock, and reset applied and no
   bus activity at all, put a frequency counter or scope on `uo[6]`. It must
   read a 48 kHz square wave. Nothing else needs to work for this.
2. **Confirm writes land.** Have the CPU (or the demo board's RP2040) drive one
   write cycle to any register. `uo[7]` must pulse exactly once. If it does
   not, the problem is the address decode, `CS_n`, or the bus handshake — not
   the sound engine.
3. **Make a sound.** With the chip wired to LDS at base address `PSG`, play
   concert A on channel A at volume 12:

   ```asm
   ; A4 = 440 Hz on channel A at volume 12
       move.b  #$DA,PSG+$1      ; A_LO   = 218 & $FF
       move.b  #$C0,PSG+$3      ; A_CTRL = vol 12, period[11:8] = 0  -> pitch commits here
       move.b  #$71,PSG+$F      ; ENABLE = outputs on + tone A
   ```

   (On UDS, the same three registers are at `PSG+$0`, `PSG+$2`, and `PSG+$E`.)

   Through an RC filter on `uo[1]`, that is a 440.37 Hz square wave. Sweep it
   by rewriting `A_LO` then `A_CTRL` — remember the pitch only moves on the
   `A_CTRL` write.
4. **Check the other outputs.** The same note should appear on an I2S DAC
   fed from `uo[2:4]`, and on an S/PDIF receiver fed from `uo[5]`, which should
   lock and report 48 kHz consumer PCM. Turn individual formats off with
   `ENABLE` bits 4–6 and confirm the others keep playing.
5. **Check silence.** Clear `ENABLE` bits 0–3. The PWM pin must sit at a steady
   50% duty and the digital outputs must carry exactly zero samples — no
   click on the transition, because silence is the mixer's exact zero.

RTL and gate-level simulation use the standard Tiny Tapeout cocotb flow; see
`test/README.md`.

## External hardware

Bus connection:

- **Address decode into `CS_n`** — the chip decodes only A3:A1 and relies on
  board glue to decode the rest of the address map into `ui[0]`.
- **DTACK combiner** — `uo[0]` is a plain push-pull output, not open-drain, so
  it must **not** be wire-ORed. Combine it with every other peripheral's
  `DTACK_n` through a single multi-input **AND gate** (one 74HC08-class package
  serves several devices) into the CPU's `DTACK_n`. For active-low signals an
  AND naturally means "asserted if any device asserts". See
  [docs/design/68k-bus-interface.md §3.1](design/68k-bus-interface.md#31-dtack-self-generated-plain-push-pull-output).
- Tie `ui[7]` low.

Audio:

- **PWM** — an RC low-pass filter on `uo[1]` to recover analog audio; e.g.
  1 kΩ + 4.7 nF (≈ 34 kHz corner), or a second-order filter for a cleaner
  result. The 192 kHz carrier sits nearly a decade above the audio band.
- **I2S** — a PCM5102-class DAC module on `uo[2]`/`uo[3]`/`uo[4]`, in slave
  mode with **SCK tied low** so it uses its internal PLL, and FMT set for
  Philips I2S. No master clock is provided by this chip and none is needed.
- **S/PDIF** — a level shift from the 1.8/3.3 V logic pin to 0.5 Vpp into 75 Ω
  for coax (a resistor divider or a small transformer), or a TOSLINK
  transmitter module, on `uo[5]`. Only applicable if S/PDIF is present in the
  fabricated design (see above).

### Pinout

| Pin | Signal | Dir | Active | Notes |
|---|---|---|---|---|
| `clk` | CLK | in | — | 24.576 MHz nominal. All output rates scale with it. |
| `rst_n` | RESET_n | in | low | Asynchronous assert, synchronized release. |
| `ui[0]` | CS_n | in | low | From board address decoder. |
| `ui[1]` | AS_n | in | low | 68000 address strobe. |
| `ui[2]` | R_W | in | — | 1 = read, 0 = write. |
| `ui[3]` | DS_n | in | low | UDS_n **or** LDS_n — board's choice. |
| `ui[4]` | A1 | in | — | Register select bit 0. |
| `ui[5]` | A2 | in | — | Register select bit 1. |
| `ui[6]` | A3 | in | — | Register select bit 2. |
| `ui[7]` | — | in | — | Unused; tie low. |
| `uio[7:0]` | D7–D0 | bidir | — | Driven only during a selected read, otherwise high-Z input. Wire to D15–D8 if on UDS, or D7–D0 if on LDS. |
| `uo[0]` | DTACK_n | out | low | Push-pull; AND-combine, don't wire-OR. High after reset. |
| `uo[1]` | PWM_OUT | out | — | 192 kHz carrier, 7-bit. Needs an external RC low-pass. |
| `uo[2]` | I2S_BCLK | out | — | 3.072 MHz (64 × Fs). |
| `uo[3]` | I2S_LRCLK | out | — | 48 kHz; low = left. |
| `uo[4]` | I2S_SDATA | out | — | Philips I2S. |
| `uo[5]` | SPDIF_OUT | out | — | Biphase-mark, 3.072 Mbit/s. Held low if S/PDIF is built out. |
| `uo[6]` | HEARTBEAT | out | — | 48 kHz square whenever clocked and out of reset. |
| `uo[7]` | WR_STROBE | out | high | One 40.7 ns pulse per completed register write. |

### Bus timing

The chip is a well-behaved asynchronous-bus slave for a 68000 at up to 16 MHz.
It asserts `DTACK_n` synchronously, only once write data is captured or read
data is valid, but **releases `DTACK_n` and the data bus combinationally** from
the raw `AS_n`/`CS_n` pins — within 50 ns, independent of clock phase — which
is what keeps it legal at the higher CPU clocks and safe in back-to-back cycles
to other devices. Expect 1–2 wait states per access from a 68000 at 8 MHz;
that is normal for an asynchronous bus and needs no board logic. Asserting
`rst_n` mid-cycle immediately forces `DTACK_n` high and releases the data bus.

Full numbers are in
[docs/design/68k-bus-interface.md](design/68k-bus-interface.md); the complete
specification is in
[docs/design/sound-chip.md](design/sound-chip.md).
