# 68k Bus Interface — Design Doc (ttihp-26b proposal)

Status: brainstorm / pre-RTL. Pin counts below assume the ttihp-26b template
exposes the same shape as tt08 (8 dedicated inputs, 8 dedicated outputs, 8
bidirectional, plus separate `clk`/`rst_n`/`ena`) — **confirm against the
actual ttihp-26b `info.yaml` template before finalizing.**

## 1. Motivation

We want a reusable "how does a TT ASIC act as a memory-mapped 68k peripheral"
block (`bus68k_if.v`), so every future chip in the 68k retrocomputer project
reuses the same bus glue instead of reinventing it. This is pure RTL IP, not
a standalone ttihp-26b submission on its own — it gets validated on real
silicon embedded inside whichever payload chip ships first (currently the
[sound chip](sound-chip.md), with a [SPI/I2C bridge](spi-i2c-bridge.md) as a
second candidate).

This directly follows the tt08 lesson: the PS/2 decoder shipped without
metastability synchronizers or glitch filtering on its async inputs and
failed on the fabricated board (`a1691c7`). The 68k bus signals here are
*fully asynchronous* to our ASIC clock in exactly the same way ps2_clk/data
were — same discipline applies from day one.

## 2. 68k Bus Primer (just what we need)

A classic asynchronous 68k bus cycle involves: address bus, `D0-D15` (68000)
or `D0-D7` (68008), `AS_n` (address strobe), `UDS_n`/`LDS_n` (byte-lane
selects), `R/W`, `DTACK_n` (ends the cycle — asserted by the slave or by a
fixed-wait-state generator), and `IPLx` interrupt lines.

For an 8-bit peripheral we only care about: a handful of low address bits
(register select within our device), `D0-D7`, one data-strobe line, `AS_n`,
`R/W`, `DTACK_n`, and an externally-decoded `CS_n` (system glue decodes the
full address map — we should not try to decode 20+ address bits on-chip).

**Recommendation: target 68008-style single-byte-lane bus**, not full 68000
16-bit bus. A real 68000 needs 16 data pins plus both `UDS_n`/`LDS_n`; we
don't have the pins for that. 68008 (or a 68000 wired for 8-bit peripheral
space via an external byte-lane mux, which many 68k retrocomputers do
anyway) needs just one data-strobe pin and 8 data lines — fits.

## 3. Pin Budget

| Pin | Signal | Direction | Notes |
|---|---|---|---|
| ui_in[0] | CS_n | in | external address decode selects us |
| ui_in[1] | AS_n | in | address strobe |
| ui_in[2] | R_W | in | 1=read, 0=write (68k convention) |
| ui_in[3] | DS_n | in | single byte-lane strobe (UDS or LDS, whichever we're wired to) |
| ui_in[4] | A1 | in | register select |
| ui_in[5] | A2 | in | register select |
| ui_in[6] | A3 | in | register select |
| ui_in[7] | spare | in | unused / future |
| uio[7:0] | D0-D7 | bidir | data bus, direction gated by qualified read cycle |
| uo_out[0] | DTACK_n | out | self-generated, see §3.1 |
| uo_out[1] | IRQ_n | out | optional, payload-module dependent |
| uo_out[7:2] | spare | out | available to whatever payload module sits behind this IP (sound chip outputs, etc.) |

That's 7 of 8 `ui_in` used, all 8 `uio` used for data, and only 2 of 8
`uo_out` used — plenty of headroom left for a payload module, which is the
whole point of centralizing the bus logic here instead of duplicating it per
chip.

### 3.1 DTACK: self-generated, plain push-pull output

`uo_out` pins are always push-pull driven — they can't be tri-stated, which
initially looks like a problem since real 68k backplanes usually wire-OR
multiple devices' DTACK together via open-drain outputs.

But we don't need open-drain here: `DTACK_n` is active-low, and each device
gets its own dedicated wire out to a small combiner instead of a shared bus
net. A plain multi-input **AND gate** combining every device's `DTACK_n`
naturally implements "asserted if any device asserts" for active-low
signals (AND's output is 0 if any input is 0). So:

- `bus68k_if.v` generates its own `DTACK_n` on a normal push-pull `uo_out`
  pin: assert (drive low) a fixed number of clocks after `CS_n & AS_n` are
  seen asserted (synchronized, 2-FF minimum — same discipline as every
  other async input here), deassert when `AS_n` releases.
- System glue combines each installed peripheral's `DTACK_n` output through
  one small AND gate (e.g. a single 74LS08/HC08 package handles several
  devices) into the single `DTACK_n` line the 68k sees. One gate, built
  once, shared by every future TT peripheral on the bus.
- This is self-timed per device (each chip's own logic depth decides its
  own wait states) rather than requiring a single fixed-wait-state
  generator calibrated for the slowest device on the bus — a real advantage
  over trying to do this with one shared external timer.

No `uio` pin cost, no tri-state/open-drain needed, no shared-bus contention
risk. Straightforward win — thanks for catching that the original "drop
DTACK" framing was solving a problem (open-drain contention) that doesn't
actually apply to discrete per-device DTACK lines.

## 4. Internal Register Bus (the reusable part)

`bus68k_if.v` exposes a simple synchronous interface to whatever payload
module sits behind it:

```
reg_addr   [2:0]  — latched register address
reg_wdata  [7:0]  — write data
reg_rdata  [7:0]  — read data, driven by payload module
reg_write         — one-cycle pulse
reg_read          — one-cycle pulse
```

All synchronous to `clk`. The payload module never has to know anything
about 68k bus timing — it just implements up to 8 memory-mapped byte
registers.

If a payload module needs more than 8 registers (the sound chip will — see
[sound-chip.md](sound-chip.md) §6), it can implement AY-3-8910-style
indirect addressing on top of just 2 of these 8 direct registers (one
address-latch register, one data-port register), reaching up to 16-32
internal registers without needing more `ui_in` address pins. Make the
number of direct address bits a parameter of `bus68k_if.v` rather than
hardcoding 3.

## 5. Bus Interface State Machine

1. `IDLE` — wait for `CS_n & AS_n` asserted, **synchronized** (2-FF, same
   spirit as `debounce.v`'s synchronizer — no need for the full debounce
   filter since these are clean logic-level control lines, not noisy
   mechanical/analog inputs, but the metastability sync is non-negotiable).
2. Latch `A1-A3`, `R_W`, `DS_n` on the synchronized strobe edge.
3. **Write:** next clock, sample `D0-D7` off `uio_in`, pulse `reg_write`.
4. **Read:** drive `reg_addr` immediately; next clock, present `reg_rdata`
   on `uio_out` with `uio_oe` asserted.
5. Hold until `AS_n` deasserts (synchronized), release the bus, return to
   `IDLE`.

## 6. Data Bus Direction Control

`uio_oe` is driven high only during a qualified, synchronized read cycle
(`CS_n & AS_n & DS_n & R_W=read`, fully latched); otherwise `uio` is treated
as input so we never contend with the 68k driving the bus during writes or
while not selected.

## 7. Test Plan

- New cocotb testbench modeling a simplified 68k bus master: drives
  `CS_n/AS_n/DS_n/R_W/A1-3`, waits a fixed delay (modeling the external
  DTACK generator), samples/drives `D0-D7`.
- Cases: single byte read, single byte write, back-to-back cycles, cycle
  aborted mid-transfer (`AS_n` deasserted early), `CS_n` never asserted
  (bus must stay idle / `uio` high-Z the whole time).
- Reuse the existing `make copy_gates` / `make -B GATES=yes` flow for
  gate-level verification once synthesized.

## 8. Open Questions / Risks

- Confirm ttihp-26b's actual pin/clock template before finalizing pin
  assignments above.
- Confirm target 68k variant (68000 vs 68008) with whoever's building the
  retrocomputer's CPU board — this doc assumes single-byte-lane access.
- The DTACK combiner (§3.1) is a small but real board-level dependency —
  one AND gate, designed once, shared by every future TT peripheral on this
  bus. Needs to exist before any of these chips can actually run on the CPU
  board.

## 9. Relationship to Payload Chips

Payload modules (sound chip, SPI/I2C bridge, etc.) plug directly into
`reg_addr / reg_wdata / reg_rdata / reg_write / reg_read` — see
[sound-chip.md](sound-chip.md) and [spi-i2c-bridge.md](spi-i2c-bridge.md).
Since `bus68k_if.v` isn't shipped as its own chip, whichever payload goes to
silicon first is what actually validates this interface on real hardware.
