# 68k Bus Interface — Design Doc (ttihp-26b proposal)

Status: brainstorm, **amended during RTL implementation**. Pin counts below
assume the ttihp-26b template exposes the same shape as tt08 (8 dedicated
inputs, 8 dedicated outputs, 8 bidirectional, plus separate
`clk`/`rst_n`/`ena`) — **confirm against the actual ttihp-26b `info.yaml`
template before finalizing.**

Blocks marked **Deviation (RTL)** are places where the original brainstorm
didn't survive contact with the implementation. The original reasoning is
kept rather than deleted, so the "why" of each change stays readable. The
authoritative statement of the module's interface and guarantees is
`specs/001-psg-sound-chip/contracts/bus68k_if.md`.

## 1. Motivation

We want a reusable "how does a TT ASIC act as a memory-mapped 68k peripheral"
block (`bus68k_if.v`), so every future chip in the 68k retrocomputer project
reuses the same bus glue instead of reinventing it. This is pure RTL IP, not
a standalone ttihp-26b submission on its own — it gets validated on real
silicon embedded inside whichever payload chip ships first (currently the
[sound chip](sound-chip.md), with a
[SPI/I2C bridge](https://github.com/benpayne/ttihp-spi-m68k) as a second
candidate).

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

> **Deviation (RTL):** the target is a **68000 wired to a single byte-lane
> strobe** (UDS *or* LDS, the board's choice), not a 68008. Register select
> is on **A1–A3**, and the consequence is that the chip's registers appear
> at **every other byte address** within its decoded block — even addresses
> if wired to UDS, odd addresses if wired to LDS. Byte accesses only
> (`move.b`); a word access would put the other byte lane on a strobe the
> chip never sees.
>
> *Reason:* this doc was internally inconsistent — it recommends a 68008 in
> this section, but its own pin budget in §3 assigns `A1`, `A2`, `A3`. A
> 68008 has a real `A0` and would select registers on A0–A2 at consecutive
> byte addresses; only a 68000 (whose A0 is implied by UDS/LDS) has A1 as
> its lowest address pin. The pinout is what the board is actually being
> built to, so the inconsistency is resolved in favour of the pinout, and
> the 68008 recommendation above is superseded.

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
| uo_out[1] | IRQ_n | out | optional, payload-module dependent — **but not a fixed pin, see below** |
| uo_out[7:2] | spare | out | available to whatever payload module sits behind this IP (sound chip outputs, etc.) |

That's 7 of 8 `ui_in` used, all 8 `uio` used for data, and only 2 of 8
`uo_out` used — plenty of headroom left for a payload module, which is the
whole point of centralizing the bus logic here instead of duplicating it per
chip.

> **Deviation (RTL) — IRQ is a module port, not a pin assignment.** Pinning
> `IRQ_n` to `uo_out[1]` in a table that describes *reusable IP* is a
> layering mistake: it silently spends a pin of every payload's budget on a
> feature that payload may not have. `bus68k_if` exposes interrupt support
> (if any) as a **port**, and the payload's top level decides which pin —
> if any — it lands on.
>
> This chip is the proof: the sound chip has **no interrupt at all**
> (resolved in [sound-chip.md](sound-chip.md) §8) and uses `uo_out[1]` for
> `PWM_OUT`. Its full pin map is in
> [sound-chip.md §2](sound-chip.md#2-pin-budget-combined-with-the-bus-interface).
> The sibling SPI project should make the same choice independently rather
> than inheriting `uo_out[1] = IRQ_n` from this table.

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

> **Deviation (RTL) — assert synchronously, release combinationally.** The
> bullet above says "deassert when `AS_n` releases", implicitly through the
> synchronizer. That is too slow. DTACK is **asserted synchronously** (after
> 2-FF synchronization of the strobes) but **released combinationally from
> the raw `AS_n`/`CS_n` pins**, and the internal DTACK flop is *also*
> **asynchronously cleared** by raw `AS_n`:
>
> ```
> dtack_n = ~dtack_q | as_n_raw | cs_n_raw
> uio_oe  = {8{oe_q & ~as_n_raw & ~cs_n_raw & r_w_raw}}
> dtack_q : flop with async clear on (as_n_raw | ~rst_n)
> ```
>
> *Reason — the real 68000 numbers* (MC68000 User's Manual §10.10, read/write
> AC specifications):
>
> | Parameter | 8 MHz | 10 MHz | 12.5 MHz | 16 MHz |
> |---|---|---|---|---|
> | #28 AS/DS negated → DTACK negated (max) | 240 ns | 190 ns | 150 ns | 110 ns |
> | #29A AS/DS negated → data-in high-Z (max) | 187 ns | 150 ns | 120 ns | 90 ns |
> | #15 AS/DS width negated (min) | 150 ns | 105 ns | 65 ns | 60 ns |
> | #31 DTACK asserted → data-in valid (max) | 90 ns | 65 ns | 50 ns | 50 ns |
>
> A fully synchronized release costs 2–3 clocks of synchronizer plus an
> output register: **81–163 ns** at our 40.69 ns clock. That meets 8 MHz and
> **violates both #28 and #29A at 12.5 MHz and above**. The combinational
> release is a few gate delays plus TT mux and pad delay, comfortably under
> 50 ns, which leaves ≥40 ns of the 16 MHz budget for board wiring.
>
> *Why the async clear as well:* in back-to-back cycles, `AS_n` can be high
> for as little as **60 ns** (#15) — about 1.5 of our clocks. The
> synchronizer might not have deasserted `dtack_q` before the next `AS_n`
> falls, and that **stale DTACK would terminate the new cycle before its
> data was captured**. The async clear removes the hazard. Note this is only
> about the flop: 60 ns is still wider than one 40.69 ns clock period, so the
> synchronized `AS_n` is guaranteed at least one clean high sample and the
> FSM always sees the release and returns to `IDLE` — no sticky "release
> seen" flag is needed.
>
> *Why the async clear is not a metastability hazard:* the clear is
> **removed** when `AS_n` falls, asynchronously to `clk`. At that instant
> `dtack_q`'s D input already equals its reset value (0), because the FSM
> cannot reach its acknowledge state until the new cycle's strobes have
> passed the synchronizer, at least 2 clocks later. A flop whose D input
> equals its reset value has no recovery-time metastability path — there is
> no disagreement for it to resolve.
>
> `rst_n` asserting mid-cycle likewise forces DTACK high and releases the
> data bus immediately, through the same clear.

## 4. Internal Register Bus (the reusable part)

`bus68k_if.v` exposes a simple synchronous interface to whatever payload
module sits behind it:

```
reg_addr   [ADDR_BITS-1:0]  — latched register address
reg_wdata  [7:0]  — write data
reg_rdata  [7:0]  — read data, driven by payload module
reg_write         — one-cycle pulse
reg_read          — one-cycle pulse
```

All synchronous to `clk`. The payload module never has to know anything
about 68k bus timing — it just implements up to 2^`ADDR_BITS` (by default 8)
memory-mapped byte registers, and must present `reg_rdata` for a given
`reg_addr` within one clock, e.g. as a combinational mux. The one hard rule
for the payload is that it **must not touch a raw 68k pin directly** —
everything goes through this module, or the synchronizer discipline is lost.

`ADDR_BITS` is a **parameter** of `bus68k_if.v` (default 3, giving
2³ = 8 registers) rather than a hardcoded 3, so a payload with different
needs just sets it and wires more `ui_in` pins.

If a payload module needs more than 8 registers, it can implement
AY-3-8910-style indirect addressing on top of just 2 of these 8 direct
registers (one address-latch register, one data-port register), reaching up
to 16-32 internal registers without needing more `ui_in` address pins.

> **Deviation (RTL):** the parenthetical above — "the sound chip will" need
> more than 8 registers — turned out to be wrong, and the sound chip does
> **not** use indirect addressing. Packing volume together with the pitch
> high nibble makes the whole PSG fit in exactly eight direct registers, one
> bus cycle each. See [sound-chip.md §6](sound-chip.md#6-register-map) for
> the map and the trade-offs that came with it. The indirect option stays
> documented here because it remains available to a future payload — it is
> just no longer this chip's plan.

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

> **Deviation (RTL) — synchronize the strobes only.** Steps 1–2 above imply
> synchronizing everything that is latched. In fact **only the three strobes
> get 2-FF synchronizers**: `CS_n`, `AS_n` and `DS_n`. A cycle is qualified
> as `~cs_s & ~as_s & ~ds_s`, and on the first qualified clock the address
> `A3:A1`, `R_W`, and (for writes) `D7:D0` are captured **combinationally
> straight from the pins**, with no synchronizers of their own.
>
> *Reason:* the 68000 guarantees address and R/W are valid before `AS_n`
> asserts (#11, #20A) and write data valid before `DS_n` asserts (#26,
> ≥ 15 ns). By the time a *synchronized* `DS_n` is seen — at least 2 clocks,
> 81 ns, after the pin edge — those signals have been stable for well over
> 80 ns. This is the standard "synchronize the strobe, sample the qualified
> bus" technique, and it saves roughly **14 flops** that would protect
> nothing. Note the discipline is unchanged where it matters: every signal
> that *gates* a decision still crosses through a synchronizer.
>
> Qualifying on `DS_n` as well as `AS_n` is what guarantees write data is
> valid — on a write, `DS_n` asserts a full CPU clock after `AS_n` (#22),
> while on a read the two assert together.
>
> Capturing on a single clock edge is also what makes an aborted cycle safe
> by construction: the register write happens in one clock, so an abort
> leaves either the complete old value (abort before qualification) or the
> complete new one (abort after) — never a mix.
>
> No debounce counter is needed on top of this. Requiring all three strobes
> low in the synchronized domain *is* the glitch filter; these are clean
> logic-level lines, not noisy mechanical inputs.

## 6. Data Bus Direction Control

`uio_oe` is driven high only during a qualified, synchronized read cycle
(`CS_n & AS_n & DS_n & R_W=read`, fully latched); otherwise `uio` is treated
as input so we never contend with the 68k driving the bus during writes or
while not selected.

> **Deviation (RTL) — "fully latched" is only half of it.** `uio_oe` is
> *asserted* from the latched, qualified read state as described above, but
> it is **released combinationally** by the raw `AS_n`/`CS_n` pins and is
> additionally gated by raw `R_W`:
>
> ```
> uio_oe = {8{oe_q & ~as_n_raw & ~cs_n_raw & r_w_raw}}
> ```
>
> *Reason:* the same #29A deadline that drives the DTACK release — see the
> §3.1 deviation. A purely latched release cannot get the data bus to high-Z
> within 90 ns at 16 MHz. The raw `r_w_raw` term is belt-and-braces: it makes
> "never drive the bus during a write" true combinationally, not just as a
> consequence of the FSM being in the right state.
>
> Read data is driven **one clock before** DTACK falls, so the data is
> already valid when the CPU is told to latch it (#31).

## 7. Test Plan

- New cocotb testbench modeling a simplified 68k bus master: drives
  `CS_n/AS_n/DS_n/R_W/A1-3`, waits for the cycle to be acknowledged,
  samples/drives `D0-D7`. *(Amended: the master waits on **this chip's own**
  `DTACK_n`, inserting wait states like a real 68000 would — there is no
  external DTACK generator to model, per §3.1.)*
- Cases: single byte read, single byte write, back-to-back cycles, cycle
  aborted mid-transfer (`AS_n` deasserted early), `CS_n` never asserted
  (bus must stay idle / `uio` high-Z the whole time).
- Reuse the existing `make copy_gates` / `make -B GATES=yes` flow for
  gate-level verification once synthesized.

**The bench now exists as a standalone**: `test/bus68k_if/` has its own
Makefile and toplevel, pairing `bus68k_if` with a trivial 8×8 register
payload, and checks the module's behavioural guarantees under randomized,
deliberately non-clock-aligned bus timing. That last part matters — stimulus
that lands exactly on a clock edge races the sampling flops and hides
exactly the class of bug this IP exists to prevent.

**The sibling project should copy the module and the bench together.**
`bus68k_if.v` on its own is untested IP; the pair is the reusable artifact.

## 8. Open Questions / Risks

- Confirm ttihp-26b's actual pin/clock template before finalizing pin
  assignments above.
- ~~Confirm target 68k variant (68000 vs 68008)~~ — **resolved: 68000 with a
  single byte-lane strobe, register select on A1–A3.** See the §2 deviation.
  Single-byte-lane access was the right assumption; the variant was not.
- The DTACK combiner (§3.1) is a small but real board-level dependency —
  one AND gate, designed once, shared by every future TT peripheral on this
  bus. Needs to exist before any of these chips can actually run on the CPU
  board.

## 9. Relationship to Payload Chips

Payload modules (sound chip, SPI/I2C bridge, etc.) plug directly into
`reg_addr / reg_wdata / reg_rdata / reg_write / reg_read` — see
[sound-chip.md](sound-chip.md) and
[ttihp-spi-m68k](https://github.com/benpayne/ttihp-spi-m68k).
Since `bus68k_if.v` isn't shipped as its own chip, whichever payload goes to
silicon first is what actually validates this interface on real hardware.
