# Contract: `bus68k_if` Reusable Bus Interface

**Audience**: This project's payload, and the sibling `ttihp-spi-m68k` project. **Satisfies**: FR-008.

A self-contained, single-file Verilog module (`src/bus68k_if.v`) that turns 68000 asynchronous bus cycles into single-clock register accesses. It contains all synchronizers and all bus-release logic. It knows nothing about audio.

## Parameters

| Name | Default | Meaning |
|---|---|---|
| `ADDR_BITS` | 3 | Number of register-select address lines (register count = 2^ADDR_BITS). |

## Ports

| Port | Dir | Width | Domain | Meaning |
|---|---|---|---|---|
| `clk` | in | 1 | — | System clock |
| `rst_n` | in | 1 | sync'd | Active-low reset, **already synchronized** by the top level |
| `cs_n` | in | 1 | async | Raw pin |
| `as_n` | in | 1 | async | Raw pin |
| `ds_n` | in | 1 | async | Raw pin |
| `r_w` | in | 1 | async | Raw pin (1 = read) |
| `addr` | in | `ADDR_BITS` | async | Raw register-select pins |
| `data_in` | in | 8 | async | Raw `uio_in` |
| `data_out` | out | 8 | clk | → `uio_out` |
| `data_oe` | out | 8 | comb | → `uio_oe`. Released combinationally by raw `as_n`/`cs_n`. |
| `dtack_n` | out | 1 | comb | → DTACK pin. Released combinationally by raw `as_n`/`cs_n`. |
| `reg_addr` | out | `ADDR_BITS` | clk | Register index, valid while `reg_write` or `reg_read` is high and held until the next cycle |
| `reg_wdata` | out | 8 | clk | Write data, valid while `reg_write` is high |
| `reg_write` | out | 1 | clk | One-clock pulse per write cycle |
| `reg_read` | out | 1 | clk | One-clock pulse per read cycle (for payloads with read side effects, e.g. FIFO pop) |
| `reg_rdata` | in | 8 | clk | Payload's read data for `reg_addr`. May be combinational from `reg_addr`. **Must be valid the clock after `reg_addr` changes.** |

## Behavioral guarantees

1. `reg_write` pulses exactly once per qualified write cycle and never on a read. `reg_read` is the mirror image.
2. `reg_addr` and `reg_wdata` are stable for the whole clock in which `reg_write` is high.
3. `reg_rdata` is sampled onto `data_out` the clock after `reg_addr` is captured, and `data_oe` is asserted before `dtack_n` falls.
4. `dtack_n` and `data_oe` return to their inactive state within combinational delay of raw `as_n` or `cs_n` going high, with no clock needed. See [pinout-and-bus-timing.md](pinout-and-bus-timing.md) C1/C2.
5. `data_oe` is never asserted while raw `r_w` = 0.
6. After reset: `dtack_n` = 1, `data_oe` = 0, no pulses.
7. A cycle aborted before qualification has no effect. Once qualified, the access completes atomically.

## Payload obligations

- Implement up to 2^`ADDR_BITS` byte registers against `reg_addr/reg_wdata/reg_write/reg_rdata`.
- Provide `reg_rdata` within one clock of `reg_addr`, e.g. a combinational mux of registers.
- Don't use any raw 68k pin directly. Everything goes through this module.

## Verification

`test/bus68k_if/` is a standalone cocotb bench (`bus68k_if` + a trivial 8×8 register payload) that checks guarantees 1–7 under randomized, non-clock-aligned bus timing. The sibling project should copy the module and the bench together.
