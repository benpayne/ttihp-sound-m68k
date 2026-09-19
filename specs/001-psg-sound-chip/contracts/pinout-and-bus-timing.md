# Contract: Pinout and Bus Timing

**Audience**: Board designer and bring-up engineer. **Satisfies**: FR-001–FR-006, FR-030–FR-035, FR-040–FR-041, FR-050.

## Pins

| TT pin | Signal | Dir | Active | Notes |
|---|---|---|---|---|
| `clk` | CLK | in | — | 24.576 MHz nominal (`clock_hz` in `info.yaml`). All output rates scale with it. |
| `rst_n` | RESET_n | in | low | Asynchronous assert, synchronized release. |
| `ui[0]` | CS_n | in | low | From board address decoder. |
| `ui[1]` | AS_n | in | low | 68000 address strobe. |
| `ui[2]` | R_W | in | — | 1 = read, 0 = write. |
| `ui[3]` | DS_n | in | low | UDS_n **or** LDS_n (board's choice; see register-map.md). |
| `ui[4]` | A1 | in | — | Register select bit 0. |
| `ui[5]` | A2 | in | — | Register select bit 1. |
| `ui[6]` | A3 | in | — | Register select bit 2. |
| `ui[7]` | — | in | — | Unused; tie low. |
| `uio[7:0]` | D7–D0 | bidir | — | Driven only during a selected read. Otherwise high-Z input. Wire to D15–D8 if on UDS, or D7–D0 if on LDS. |
| `uo[0]` | DTACK_n | out | low | **Push-pull.** Combine with other peripherals through an AND gate (not wire-OR). High after reset. |
| `uo[1]` | PWM_OUT | out | — | 192 kHz carrier, 7-bit. Needs an external RC low-pass (e.g. 1 kΩ + 4.7 nF ≈ 34 kHz, or 2nd-order). |
| `uo[2]` | I2S_BCLK | out | — | 3.072 MHz (64 × Fs). |
| `uo[3]` | I2S_LRCLK | out | — | 48 kHz; low = left. |
| `uo[4]` | I2S_SDATA | out | — | Philips I2S. |
| `uo[5]` | SPDIF_OUT | out | — | Biphase-mark, 3.072 Mbit/s. Needs a level shift to 0.5 Vpp / 75 Ω for coax, or a TOSLINK transmitter. Held low if S/PDIF is built out. |
| `uo[6]` | HEARTBEAT | out | — | 48 kHz square wave whenever clocked and out of reset. Bring-up aid. |
| `uo[7]` | WR_STROBE | out | high | One `clk` pulse (40.7 ns) per completed register write. Bring-up aid. |

## Bus timing (68000, CPU clock ≤ 16 MHz)

Chip clock period `T = 40.69 ns`. "Sync" means 2–3 T of synchronizer latency, depending on the edge's phase.

| # | Parameter | Guarantee | 68000 requirement it satisfies (16 MHz) |
|---|---|---|---|
| C1 | AS_n / CS_n high → DTACK_n high | ≤ 50 ns, combinational, independent of `clk` | #28 ≤ 110 ns |
| C2 | AS_n / CS_n high → D7–D0 high-Z | ≤ 50 ns, combinational | #29A ≤ 90 ns |
| C3 | Read: D7–D0 valid → DTACK_n low | ≥ 1 T (data driven one clock before DTACK) | #31 DTACK → data valid ≤ 50 ns |
| C4 | DS_n low → DTACK_n low (read or write) | sync + 2 T ≈ 4–5 T (163–203 ns) | none (asynchronous bus; the CPU inserts wait states) |
| C5 | Write data captured at | first clock after synchronized DS_n low | #26 data valid before DS asserted ≥ 15 ns |
| C6 | Minimum AS_n high between cycles | ≥ 1.25 T (51 ns) | #15 ≥ 60 ns |
| C7 | Unselected cycle (CS_n high) | DTACK_n stays high; D7–D0 never driven | — |

**Expected wait states**: With a DTACK latency of about 4–5 chip clocks, a 68000 at 8 MHz will usually see 1–2 wait states per access to this chip. That's normal for an asynchronous bus and requires no board logic.

**Reset during a cycle**: `rst_n` low forces DTACK_n high and releases D7–D0 immediately.

## Board-level obligations (out of chip scope)
1. Decode the full address map into CS_n. The chip decodes only A3:A1.
2. AND this chip's DTACK_n with every other peripheral's DTACK_n into the CPU.
3. Tie `ui[7]` low.
4. Provide the RC filter (PWM), a DAC module (I2S), and a line driver or TOSLINK transmitter (S/PDIF) for the outputs you want to use.
