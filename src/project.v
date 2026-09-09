/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_benpayne_sound_chip (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // TODO: implement per docs/design/sound-chip.md and docs/design/68k-bus-interface.md.
  // Placeholder: all outputs held inactive/idle until real logic lands.
  assign uo_out  = 8'h00;
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;

  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};

endmodule
