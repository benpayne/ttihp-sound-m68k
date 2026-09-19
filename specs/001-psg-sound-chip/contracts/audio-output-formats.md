# Contract: Audio Output Formats

**Audience**: Whoever connects a DAC, receiver, or filter; test decoder authors. **Satisfies**: FR-026, FR-030–FR-035.

All rates are for the nominal 24.576 MHz clock and scale linearly with it. All three outputs carry the same mono mix (FR-030). The internal mix is an 11-bit signed value, −1020…+1020, with silence = 0.

## PWM (`uo[1]`)

| Property | Value |
|---|---|
| Carrier | `F_clk / 128` = 192 kHz |
| Resolution | 7 bits (128 levels) |
| Duty | `((mix >>> 4) + 64) / 128` — silence = exactly 50% |
| Update | Duty latched at each carrier-period start; never changes mid-period |
| Disabled | Pin held low |

## I2S (`uo[2]` BCLK, `uo[3]` LRCLK, `uo[4]` SDATA)

| Property | Value |
|---|---|
| Format | Philips I2S; the chip is the clock master |
| Fs | `F_clk / 512` = 48 kHz |
| BCLK | 64 × Fs = 3.072 MHz, 50% duty |
| LRCLK | low = left, high = right; changes on BCLK falling edge |
| Slot | 32 BCLKs per channel |
| Word | 16-bit two's-complement, MSB first, **starting one BCLK after the LRCLK edge**, followed by 16 zero bits |
| Sample value | `mix << 5` (±32640) |
| L / R | identical sample in both slots, updated once per frame |
| Edges | SDATA and LRCLK change on the BCLK falling edge; stable for 4 `clk` (163 ns) before the rising edge |
| Disabled | All three pins held low |
| Compatible with | PCM5102A-class DACs in slave mode with SCK tied low (internal PLL), FMT = I2S |

## S/PDIF (`uo[5]`)

| Property | Value |
|---|---|
| Standard | IEC 60958-3 consumer |
| Line code | Biphase-mark; 128 half-cells per frame (6.144 MHz half-cell rate), 3.072 Mbit/s |
| Frame | 2 subframes × 32 bits; subframe A = left, subframe B = right (identical audio) |
| Preambles | B = `11101000` (subframe A, frame 0 of block), M = `11100010` (subframe A, frames 1–191), W = `11100100` (subframe B); written as half-cell levels starting from a low line |
| Bits 4–11 | 0 (aux + 4 audio LSBs) |
| Bits 12–27 | 16-bit sample, same value as I2S, LSB first |
| Bit 28 V | 0 (valid audio) |
| Bit 29 U | 0 |
| Bit 30 C | channel status, see below |
| Bit 31 P | even parity over bits 4–31 |
| Block | 192 frames |
| Channel status | All zero except **bit 2 = 1** (copy permitted) and **bit 25 = 1** (byte 3 = `0x02`, Fs = 48 kHz). That means: consumer, linear PCM, no emphasis, general category, word length not indicated. Same in both subframes. |
| Disabled / built out | Pin held low |

## Silence (all formats)

With all channels disabled or at volume 0: PWM is exactly 50% duty, and every I2S and S/PDIF sample is exactly `0x0000` (FR-026, SC-004). The outputs keep toggling and framing in silence; only a disable bit stops a format.
