# Data Model: 68k PSG Sound Chip

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md)

This is a chip, so "data" means architectural state: what's stored, how wide it is, what resets it, what changes it, and what reads it. The software-visible bit layout is in [contracts/register-map.md](contracts/register-map.md). This document covers all state, including what software can't see.

## 1. Software-visible registers (8 × 8 bits)

Register index = `A3:A1`. There are no other addressable locations (FR-010).

| Idx | Name | Fields | Reset | Write side effect |
|---|---|---|---|---|
| 0 | `A_LO` | `period_lo[7:0]` staged | `0x00` | none (staged only) |
| 1 | `A_CTRL` | `vol[7:4]`, `period_hi[3:0]` | `0x00` | commits channel A active period |
| 2 | `B_LO` | `period_lo[7:0]` staged | `0x00` | none |
| 3 | `B_CTRL` | `vol[7:4]`, `period_hi[3:0]` | `0x00` | commits channel B active period |
| 4 | `C_LO` | `period_lo[7:0]` staged | `0x00` | none |
| 5 | `C_CTRL` | `vol[7:4]`, `period_hi[3:0]` | `0x00` | commits channel C active period |
| 6 | `NOISE` | `vol[7:4]`, `rate[3:0]` | `0x00` | none |
| 7 | `ENABLE` | `rsvd[7]`, `spdif_en[6]`, `i2s_en[5]`, `pwm_en[4]`, `noise_en[3]`, `c_en[2]`, `b_en[1]`, `a_en[0]` | `0x70` | none |

**Validation rules**
- Every bit is read/write storage and reads back exactly as last written (FR-011), including `rsvd[7]` and, when S/PDIF is built out, `spdif_en[6]`.
- Reads have no side effects (FR-013).
- Reset state: all channels off, volumes 0, all three outputs enabled and emitting silence (FR-014).

## 2. Internal (non-visible) state

| Entity | Width | Reset | Updated when | Read by |
|---|---|---|---|---|
| `active_period[c]`, c ∈ {A,B,C} | 12 | 0 | `c_CTRL` write: ← `{wdata[3:0], c_LO}` | tone channel c |
| `tone_cnt[c]` | 12 | 0 | 192 kHz tick | tone channel c |
| `tone_out[c]` | 1 | 0 | toggles when `tone_cnt ≥ max(active_period,1) − 1`, then `tone_cnt ← 0` | mixer |
| `noise_pre` | 5 | 0 | 192 kHz tick: counts 0…`2·rate + 1`, then wraps (one LFSR shift per wrap → 96 kHz/(rate+1)) | noise LFSR enable |
| `lfsr` | 17 | `1` | noise shift enable: `lfsr ← {lfsr[15:0], lfsr[16] ^ lfsr[13]}` | mixer (bit 0) |
| `count` (master time base) | 9 | 0 | every `clk` | everything (see research R10) |
| `mix_acc` | 11 signed | 0 | mixer slot 120: `← contrib(A)`; slots 121–123: `+= contrib(B, C, noise)` | PWM latch |
| `pwm_duty` | 7 | 64 | tick (`count[6:0]==127`): `← (mix_acc >>> 4) + 64` | PWM comparator |
| `frame_sample` | 16 signed | 0 | frame (`count==511`): `← mix_acc << 5` | I2S, S/PDIF |
| `spdif_frame` | 8 (0–191) | 0 | frame boundary | S/PDIF preamble/C-bit select |
| `spdif_level` | 1 | 0 | bit-cell boundary (toggle), mid-cell (toggle if 1), or preamble pattern | S/PDIF pin |
| `spdif_parity` | 1 | 0 | accumulates bits 4–30, cleared per subframe | S/PDIF bit 31 |
| I2S output regs (`bclk_q`, `lrclk_q`, `sdata_q`) | 3 | 0 | every `clk` | I2S pins |
| Debug regs (`hb_q`, `wr_q`) | 2 | 0 | `hb_q ← count[8]`; `wr_q ← reg_write` | `uo_out[6]`, `uo_out[7]` |

**Mixer contribution per channel** (research R4):
```
contrib(ch) = 0                   if !en[ch] or vol[ch] == 0
            = +AMP[vol[ch]]       if bit[ch] == 1
            = −AMP[vol[ch]]       if bit[ch] == 0
AMP = {0, 3, 4, 6, 9, 12, 19, 27, 34, 55, 76, 99, 125, 157, 203, 255}
bit[A..C] = tone_out[A..C]; bit[noise] = lfsr[0]
mix = Σ contrib   ∈ [−1020, +1020]  (11-bit signed, never overflows)
```

## 3. Bus interface state (`bus68k_if`)

| Entity | Width | Reset | Notes |
|---|---|---|---|
| `cs_sync`, `as_sync`, `ds_sync` | 2 each | 1 (inactive) | 2-FF synchronizers on the strobes only |
| `state` | 2 | IDLE | see state machine below |
| `reg_addr` | `ADDR_BITS` (3) | 0 | captured from `A3:A1` on qualification |
| `is_read` | 1 | 0 | captured from `R_W` on qualification |
| `reg_wdata` | 8 | 0 | captured from `uio_in` on qualified write |
| `oe_q` | 1 | 0 | drives `uio_oe` (gated combinationally by raw strobes) |
| `dtack_q` | 1 | 0 | **async clear on raw `AS_n` high or reset** (research R1) |

### Bus-cycle state machine

```
          qualified = ~cs_s & ~as_s & ~ds_s

 ┌──────┐ qualified & write  ┌───────┐  (1 clk: reg_write pulse)   ┌─────┐
 │ IDLE ├───────────────────►│ WRITE ├────────────────────────────►│ ACK │
 │      │ qualified & read   ┌───────┐  (1 clk: oe_q=1, rdata out) │     │
 │      ├───────────────────►│ READ  ├────────────────────────────►│     │
 └──▲───┘                    └───────┘                             └──┬──┘
    │                                       set dtack_q on entry      │
    └──────────────── as_s == 1 (AS released) ────────────────────────┘
                       clear oe_q; dtack_q already cleared async
```

**Transition rules**
- IDLE → WRITE/READ: on the first clock `qualified` is seen. Capture `A3:A1` → `reg_addr` and `R_W` → `is_read`, and for writes `uio_in` → `reg_wdata`.
- WRITE → ACK: pulse `reg_write` for exactly one clock; set `dtack_q`.
- READ → ACK: `oe_q ← 1` one clock *before* `dtack_q` is set, so data is valid on the bus before DTACK asserts (68000 #31). Pulse `reg_read` once.
- ACK → IDLE: when synchronized `AS_n` is high. `oe_q ← 0`.
- Any state → IDLE: on reset.

**Output equations (combinational release)**
```
dtack_n  = ~dtack_q | as_n_raw | cs_n_raw
uio_oe   = {8{ oe_q & ~as_n_raw & ~cs_n_raw & r_w_raw }}
uio_out  = reg_rdata   (value irrelevant when uio_oe = 0)
```

**Invariants** (checked by tests)
1. `uio_oe` is never 1 while `r_w_raw` = 0 (write) or while not selected.
2. `dtack_n` is 1 whenever raw `AS_n` or `CS_n` is 1, with no clock needed.
3. `reg_write` pulses at most once per bus cycle, and never on a read.
4. After reset: `dtack_n` = 1, `uio_oe` = 0.

## 4. S/PDIF frame sequencing

```
count[8]   = subframe (0 = A/left, 1 = B/right)
count[7:3] = bit b in subframe (0..31)
count[2]   = half-cell within bit

b 0–3   : preamble — B if (subframe A and spdif_frame == 0), M if subframe A, W if subframe B
b 4–11  : 0 (aux + audio LSBs)
b 12–27 : frame_sample[b−12]   (LSB first)
b 28    : V = 0
b 29    : U = 0
b 30    : C = (spdif_frame == 2) | (spdif_frame == 25)
b 31    : P = even parity over b4..b30
spdif_frame: 0 → 1 → … → 191 → 0, advancing at count == 511
```

## 5. Relationships

```
68k pins ─► bus68k_if ─(reg_addr/wdata/write/read, rdata)─► psg_regs ─┬─► tone ×3 ─┐
                                                                      ├─► noise  ──┤
count (timebase) ─► enables to every block                            │            ▼
                                                                      └─ vol/en ─► mixer ─► mix_acc
                                                                                            ├─► pwm_out   ─► uo[1]
                                                                                            └─► frame_sample ─┬─► i2s_out   ─► uo[4:2]
                                                                                                              └─► spdif_out ─► uo[5]
```
