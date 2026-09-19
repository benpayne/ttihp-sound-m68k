/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// psg_timebase: single free-running master counter and the clock-enable
// strobes derived from it (research.md R10). Every audio-rate function in
// this design is a clock enable off this one counter -- there is no
// second clock domain and no gated/derived clock anywhere downstream.
//
// All rates below are exact divisions of `clk` (24.576 MHz nominal):
//   half_cell : count[1:0] == 2'd3   -> clk/4   = 6.144 MHz  (S/PDIF half-cell)
//   bit_cell  : count[2:0] == 3'd7   -> clk/8   = 3.072 MHz  (I2S BCLK slot / S/PDIF bit cell)
//   tick      : count[6:0] == 7'd127 -> clk/128 = 192 kHz    (tone/noise tick, PWM period)
//   frame     : count == 9'd511      -> clk/512 = 48 kHz     (sample frame)
module psg_timebase (
    input  wire       clk,
    input  wire       rst_n,      // synchronized active-low reset (async assert, sync release)
    output reg  [8:0] count,      // free-running master counter, exposed for downstream bit-taps
    output wire       half_cell,  // 6.144 MHz strobe, one clk wide
    output wire       bit_cell,   // 3.072 MHz strobe, one clk wide
    output wire       tick,       // 192 kHz strobe, one clk wide
    output wire       frame       // 48 kHz strobe, one clk wide
);

  localparam [1:0] HALF_CELL_MATCH = 2'd3;
  localparam [2:0] BIT_CELL_MATCH = 3'd7;
  localparam [6:0] TICK_MATCH = 7'd127;
  localparam [8:0] FRAME_MATCH = 9'd511;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      count <= 9'd0;
    end else begin
      count <= count + 9'd1;
    end
  end

  assign half_cell = (count[1:0] == HALF_CELL_MATCH);
  assign bit_cell  = (count[2:0] == BIT_CELL_MATCH);
  assign tick      = (count[6:0] == TICK_MATCH);
  assign frame     = (count == FRAME_MATCH);

endmodule
