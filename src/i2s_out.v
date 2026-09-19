/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// i2s_out: Philips I2S output, master mode (research.md R6;
// contracts/audio-output-formats.md I2S section).
//
// Rides the shared master counter -- no separate BCLK/LRCLK dividers and
// no shift register:
//   BCLK  = registered count[2]  -> clk/8   = 3.072 MHz (64 per frame)
//   LRCLK = registered count[8]  -> clk/512 = 48 kHz, low = left
//   slot  = count[7:3] (0..31): SDATA carries sample bit 15-(s-1) for
//           s = 1..16 (MSB first, one-BCLK delay after the LRCLK edge),
//           0 otherwise
// `sample` (mix << 5, latched once per frame by the caller at `frame`)
// is shared with spdif_out rather than re-latched here, saving 16 FFs.
// All three outputs are plain registers clocked every `clk` from
// combinational functions of the (unregistered) counter, so they change
// together, one clk after the underlying counter bits do -- this is
// exactly the "registered outputs, mutually aligned edges" behavior R6
// asks for; the 1-clk phase shift versus the raw counter bits is
// immaterial since nothing downstream cares about that absolute phase.
module i2s_out (
    input  wire        clk,
    input  wire        rst_n,   // synchronized active-low reset
    input  wire [8:0]  count,   // tb_count (timebase), shared master counter
    input  wire [15:0] sample,  // shared latched sample (mix << 5)
    output wire        bclk,
    output wire        lrclk,
    output wire        sdata
);

  // Slot 0 is the fixed one-BCLK delay bit (always 0); slots 1..16 carry
  // the sample MSB..LSB; slots 17..31 are zero padding. idx = 16 - s maps
  // slot s (1..16) to sample bit 15-(s-1); guarded by `in_range` so the
  // subtraction is never read outside that window.
  wire [4:0] slot     = count[7:3];
  wire       in_range = (slot != 5'd0) && (slot <= 5'd16);
  wire [4:0] idx      = 5'd16 - slot;
  wire       sdata_bit = in_range ? sample[idx[3:0]] : 1'b0;

  reg bclk_q, lrclk_q, sdata_q;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bclk_q  <= 1'b0;
      lrclk_q <= 1'b0;
      sdata_q <= 1'b0;
    end else begin
      bclk_q  <= count[2];
      lrclk_q <= count[8];
      sdata_q <= sdata_bit;
    end
  end

  assign bclk  = bclk_q;
  assign lrclk = lrclk_q;
  assign sdata = sdata_q;

  // idx[4] is always 0 in-range (slot 1..16 -> idx 0..15) and never read
  // out of range; count[1:0] doesn't feature in any of BCLK/LRCLK/slot
  // (bit_cell granularity, not half_cell). Consumed explicitly so lint
  // stays clean.
  wire _unused = &{idx[4], count[1:0], 1'b0};

endmodule
