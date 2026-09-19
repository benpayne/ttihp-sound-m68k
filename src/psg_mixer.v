/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// psg_mixer: serial 4-slot mixer (research.md R4; data-model.md Sec 2).
//
// Shares ONE 16-entry logarithmic amplitude table across all four
// channels (tone A/B/C + noise) instead of four parallel tables and an
// adder tree, since there are 128 clocks per 192 kHz tick and only 4
// slots are needed:
//   count7 == 120  LOADS   mix_acc <- contrib(A)
//   count7 == 121  ADDS    mix_acc <- mix_acc + contrib(B)
//   count7 == 122  ADDS    mix_acc <- mix_acc + contrib(C)
//   count7 == 123  ADDS    mix_acc <- mix_acc + contrib(noise)
// Every other count7 value holds mix_acc unchanged.
//
// contrib(ch) = 0            if !en[ch] or vol[ch] == 0
//             = +AMP[vol]    if the channel's square/noise bit is 1
//             = -AMP[vol]    if the channel's square/noise bit is 0
// AMP[0] == 0, so the vol==0 case falls out of the +-AMP selection for
// free; only the enable needs an explicit check. `mix` is an 11-bit
// signed accumulator in [-1020, +1020] and cannot overflow, so no
// saturation logic is needed (FR-025). Silence (every channel disabled
// or at vol 0) holds `mix` at exactly 0 (FR-026).
module psg_mixer (
    input  wire              clk,
    input  wire              rst_n,     // synchronized active-low reset
    input  wire [6:0]        count7,    // tb_count[6:0] (timebase)
    input  wire              a_en,
    input  wire              b_en,
    input  wire              c_en,
    input  wire              noise_en,
    input  wire [3:0]        vol_a,
    input  wire [3:0]        vol_b,
    input  wire [3:0]        vol_c,
    input  wire [3:0]        noise_vol,
    input  wire              tone_a,    // channel A square bit
    input  wire              tone_b,    // channel B square bit
    input  wire              tone_c,    // channel C square bit
    input  wire              noise_bit, // noise LFSR bit
    output reg  signed [10:0] mix
);

  localparam [6:0] SLOT_A = 7'd120;
  localparam [6:0] SLOT_B = 7'd121;
  localparam [6:0] SLOT_C = 7'd122;
  localparam [6:0] SLOT_N = 7'd123;

  // 16-entry logarithmic amplitude table (research.md R4), shared by all
  // four channels via the case-slot schedule above rather than four
  // copies of the ROM.
  function [7:0] amp_lut;
    input [3:0] vol;
    begin
      case (vol)
        4'd0:  amp_lut = 8'd0;
        4'd1:  amp_lut = 8'd3;
        4'd2:  amp_lut = 8'd4;
        4'd3:  amp_lut = 8'd6;
        4'd4:  amp_lut = 8'd9;
        4'd5:  amp_lut = 8'd12;
        4'd6:  amp_lut = 8'd19;
        4'd7:  amp_lut = 8'd27;
        4'd8:  amp_lut = 8'd34;
        4'd9:  amp_lut = 8'd55;
        4'd10: amp_lut = 8'd76;
        4'd11: amp_lut = 8'd99;
        4'd12: amp_lut = 8'd125;
        4'd13: amp_lut = 8'd157;
        4'd14: amp_lut = 8'd203;
        4'd15: amp_lut = 8'd255;
        default: amp_lut = 8'd0;
      endcase
    end
  endfunction

  function signed [10:0] contrib;
    input       en;
    input [3:0] vol;
    input       bit_val;
    reg   signed [10:0] amp_s;
    begin
      amp_s   = {3'b000, amp_lut(vol)};
      contrib = !en ? 11'sd0 : (bit_val ? amp_s : -amp_s);
    end
  endfunction

  wire signed [10:0] contrib_a = contrib(a_en, vol_a, tone_a);
  wire signed [10:0] contrib_b = contrib(b_en, vol_b, tone_b);
  wire signed [10:0] contrib_c = contrib(c_en, vol_c, tone_c);
  wire signed [10:0] contrib_n = contrib(noise_en, noise_vol, noise_bit);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mix <= 11'sd0;
    end else begin
      case (count7)
        SLOT_A:  mix <= contrib_a;
        SLOT_B:  mix <= mix + contrib_b;
        SLOT_C:  mix <= mix + contrib_c;
        SLOT_N:  mix <= mix + contrib_n;
        default: mix <= mix;
      endcase
    end
  end

endmodule
