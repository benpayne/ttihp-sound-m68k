// SPDX-FileCopyrightText: © 2026 Ben Payne
// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`timescale 1ns / 1ps

/* Standalone unit bench for the reusable `bus68k_if` 68000 bus IP.
 *
 * Instantiates `bus68k_if` (contracts/bus68k_if.md) plus a TRIVIAL 8x8
 * register payload -- no audio logic whatsoever -- so the IP can be
 * verified completely independently of this chip's audio engine, per
 * FR-008 ("the bus-interface function MUST be ... verifiable on its
 * own"). This directory (module + bench) is meant to be copied wholesale
 * into the sibling ttihp-spi-m68k project.
 *
 * Every port bus68k_if exposes (per the contract's port table) is also
 * exposed here, at this module's top scope, under the same names
 * (cs_n/as_n/ds_n/r_w/addr/data_in/data_out/data_oe/dtack_n, plus
 * clk/rst_n) so the cocotb `IpLevelSignals` adapter
 * (test/tbutil/bus68k_master.py) can drive and sample them without
 * knowing about any internal hierarchy.
 */
module tb_bus68k_if #(
    parameter ADDR_BITS = 3
) ();

  // Dump waveforms for debugging. Guarded behind a define so a run can
  // disable it (e.g. `iverilog -DNO_WAVES` / a Makefile knob) when the
  // dump itself isn't wanted, matching the intent of the template's
  // top-level tb.v dump block.
`ifndef NO_WAVES
  initial begin
    $dumpfile("tb_bus68k_if.fst");
    $dumpvars(0, tb_bus68k_if);
  end
`endif

  // ---- Clock / reset -----------------------------------------------
  reg clk;
  reg rst_n;

  // ---- Raw 68k-side pins (async, driven by the cocotb bus driver) ---
  reg cs_n;
  reg as_n;
  reg ds_n;
  reg r_w;
  reg [ADDR_BITS-1:0] addr;
  reg [7:0] data_in;

  // ---- Raw 68k-side pins driven by the DUT ---------------------------
  wire [7:0] data_out;
  wire [7:0] data_oe;
  wire dtack_n;

  // ---- Payload-facing register bus -----------------------------------
  wire [ADDR_BITS-1:0] reg_addr;
  wire [7:0] reg_wdata;
  wire reg_write;
  wire reg_read;
  wire [7:0] reg_rdata;

  bus68k_if #(
      .ADDR_BITS(ADDR_BITS)
  ) dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .cs_n     (cs_n),
      .as_n     (as_n),
      .ds_n     (ds_n),
      .r_w      (r_w),
      .addr     (addr),
      .data_in  (data_in),
      .data_out (data_out),
      .data_oe  (data_oe),
      .dtack_n  (dtack_n),
      .reg_addr (reg_addr),
      .reg_wdata(reg_wdata),
      .reg_write(reg_write),
      .reg_read (reg_read),
      .reg_rdata(reg_rdata)
  );

  // ---- Trivial 8x8 register payload ----------------------------------
  //
  // Deliberately dumb: plain storage, no audio semantics, no read side
  // effects, no staging. It exists only to give bus68k_if something to
  // read and write so the contract guarantees can be exercised
  // end-to-end. Real payloads (e.g. src/psg_regs.v) are verified
  // separately at the chip level.
  reg [7:0] regs[0:(1<<ADDR_BITS)-1];
  reg [7:0] reg_rdata_r;
  integer i;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < (1 << ADDR_BITS); i = i + 1) regs[i] <= 8'h00;
    end else if (reg_write) begin
      regs[reg_addr] <= reg_wdata;
    end
  end

  // Combinational readback mux: valid the clock after reg_addr changes,
  // as the contract's payload obligations require.
  always @* begin
    reg_rdata_r = regs[reg_addr];
  end

  assign reg_rdata = reg_rdata_r;

endmodule
