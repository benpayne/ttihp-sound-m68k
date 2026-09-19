/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// pwm_out: 7-bit PWM audio output (research.md R5;
// contracts/audio-output-formats.md PWM section).
//
// Rides the shared 192 kHz carrier off the timebase's low 7 bits
// (`count7`) -- no separate PWM counter. `duty` is latched once per
// carrier period, at the period's last cycle (`tick`, count7==127), so
// the new value takes effect exactly at the next period's start
// (count7==0) and never changes mid-period:
//   duty = (mix >>> 4) + 64                  (arithmetic shift; 0..127)
//   pwm  = (count7 < duty)
// mix's range (+-1020) guarantees duty lands in [0,127] with no
// saturation needed. Silence (mix==0) latches duty=64, exactly 50%
// (FR-026).
module pwm_out (
    input  wire               clk,
    input  wire               rst_n,  // synchronized active-low reset
    input  wire [6:0]         count7, // tb_count[6:0] (timebase), the PWM carrier
    input  wire               tick,   // count7==127, one clk wide: latch strobe
    input  wire signed [10:0] mix,    // mixer output
    output wire               pwm     // PWM_OUT pin drive
);

  reg [6:0] duty;

  wire signed [10:0] shifted     = mix >>> 4;
  wire signed [10:0] duty_signed = shifted + 11'sd64;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      duty <= 7'd64;
    end else if (tick) begin
      duty <= duty_signed[6:0];
    end
  end

  assign pwm = (count7 < duty);

  // duty_signed[10:7] are always the sign-extension of a value guaranteed
  // (by mix's +-1020 range) to land in [0,127] -- only the low 7 bits are
  // ever needed for `duty`. Consumed explicitly so lint stays clean.
  wire _unused = &{duty_signed[10:7], 1'b0};

endmodule
