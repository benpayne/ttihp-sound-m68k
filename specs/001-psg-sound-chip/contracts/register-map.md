# Contract: Software Register Map

**Audience**: 68k driver and music-player authors. **Satisfies**: FR-007, FR-010–FR-014, FR-020–FR-027.

## Addressing

- Eight byte-wide registers, selected by CPU address lines **A3:A1**. The chip ignores A0; its single data strobe is wired to UDS or LDS.
- The registers sit at **every other byte address** in the chip's decoded block:

| Idx | Name | Offset if on UDS (D15–D8) | Offset if on LDS (D7–D0) |
|---|---|---|---|
| 0 | `A_LO` | +0x0 | +0x1 |
| 1 | `A_CTRL` | +0x2 | +0x3 |
| 2 | `B_LO` | +0x4 | +0x5 |
| 3 | `B_CTRL` | +0x6 | +0x7 |
| 4 | `C_LO` | +0x8 | +0x9 |
| 5 | `C_CTRL` | +0xA | +0xB |
| 6 | `NOISE` | +0xC | +0xD |
| 7 | `ENABLE` | +0xE | +0xF |

- Byte accesses only (`move.b`). A word access would put the other byte lane on a strobe the chip doesn't see.
- Every register is read/write. A read returns the last value written. Reads have no side effects.

## Registers

### `x_LO`: tone period, low byte (idx 0, 2, 4)
| Bits | Field | Meaning |
|---|---|---|
| 7:0 | `period[7:0]` | Low 8 bits of the channel's 12-bit tone period. **Staged**: has no audible effect until `x_CTRL` is written. |

### `x_CTRL`: volume + tone period, high nibble (idx 1, 3, 5)
| Bits | Field | Meaning |
|---|---|---|
| 7:4 | `vol` | Volume 0–15. 0 = silent. Logarithmic steps (~3 dB, AY-3-8910 curve). Takes effect immediately. |
| 3:0 | `period[11:8]` | High 4 bits of the tone period. **Writing this register commits** `{period[11:8], x_LO}` as the new pitch in one step. |

**Pitch**: `f = F_clk / (256 · max(N, 1))`, where N = 12-bit period. At the nominal 24.576 MHz this is `96000 / N` Hz: 23.4 Hz (N=4095) to 96 kHz (N=1). **N = 0 behaves as N = 1.**

| Note | N | Actual |
|---|---|---|
| A0 27.5 Hz | 3491 | 27.50 Hz |
| C4 261.63 Hz | 367 | 261.58 Hz |
| A4 440 Hz | 218 | 440.37 Hz |
| C8 4186 Hz | 23 | 4173.9 Hz (−5 cents) |

**Update rule — always write `x_LO` first, then `x_CTRL`.** The pitch changes atomically on the `x_CTRL` write, so no intermediate pitch is ever heard. To change only volume, rewrite `x_CTRL` with the same period nibble; rewriting an unchanged period is harmless and doesn't restart the waveform. A change to `x_LO` alone isn't heard until `x_CTRL` is written.

### `NOISE` (idx 6)
| Bits | Field | Meaning |
|---|---|---|
| 7:4 | `vol` | Noise volume 0–15, same curve as tone channels. |
| 3:0 | `rate` | Noise shift rate `F_clk / (256 · (rate + 1))`: 96 kHz (0, bright hiss) to 6 kHz (15, low rumble). |

### `ENABLE` (idx 7) — reset value `0x70`
| Bit | Field | Meaning |
|---|---|---|
| 0 | `a_en` | Tone A enable |
| 1 | `b_en` | Tone B enable |
| 2 | `c_en` | Tone C enable |
| 3 | `noise_en` | Noise enable |
| 4 | `pwm_en` | PWM output enable (disabled → pin held low) |
| 5 | `i2s_en` | I2S output enable (disabled → all three I2S pins held low) |
| 6 | `spdif_en` | S/PDIF output enable (disabled → pin held low). Still read/write storage if S/PDIF is built out. |
| 7 | — | Reserved; read/write storage, no function. Write 0. |

## Reset state
All registers `0x00` except `ENABLE = 0x70`: every channel off, every output on and emitting silence. The PWM output is at 50% duty, and I2S and S/PDIF carry zero samples.

## Presence check
There's no ID register. To detect the chip, write a pattern to a register (e.g. `NOISE`), read it back, then restore it.

## Example (68k assembly, chip on LDS at `PSG`)
```asm
; A4 = 440 Hz on channel A at volume 12
    move.b  #$DA,PSG+$1      ; A_LO   = 218 & $FF
    move.b  #$C0,PSG+$3      ; A_CTRL = vol 12, period[11:8] = 0  -> pitch commits here
    move.b  #$71,PSG+$F      ; ENABLE = outputs on + tone A
```
