/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// psg_noise: the noise channel (research.md R8; data-model.md Sec 2).
//
// A 17-bit LFSR, feedback = bit16 XOR bit13 (x^17 + x^14 + 1, the
// AY-3-8910 polynomial), reset to 17'd1 so it can never lock at
// all-zeros. A 5-bit prescaler counts 192 kHz ticks; the LFSR shifts
// once every `2*(rate+1)` ticks, giving a shift rate of
// 96 kHz / (rate+1): 96 kHz (rate=0, bright hiss) down to 6 kHz
// (rate=15, low rumble). The channel's bipolar bit for the mixer is
// LFSR bit 0.
module psg_noise (
    input  wire       clk,
    input  wire       rst_n,     // synchronized active-low reset
    input  wire       tick,      // 192 kHz clock enable, one clk wide
    input  wire [3:0] rate,      // NOISE register's rate field
    output wire       noise_out  // bipolar bit for the mixer (lfsr[0])
);

  reg [16:0] lfsr;
  reg [ 4:0] presc_cnt;

  // Prescaler target: 2*(rate+1) - 1 = 2*rate + 1, always in [1,31], so
  // it fits the 5-bit counter with no overflow.
  wire [4:0] presc_target = {rate, 1'b0} + 5'd1;

  wire       shift_en = (presc_cnt >= presc_target);
  wire       feedback = lfsr[16] ^ lfsr[13];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lfsr      <= 17'd1;
      presc_cnt <= 5'd0;
    end else if (tick) begin
      if (shift_en) begin
        lfsr      <= {lfsr[15:0], feedback};
        presc_cnt <= 5'd0;
      end else begin
        presc_cnt <= presc_cnt + 5'd1;
      end
    end
  end

  assign noise_out = lfsr[0];

endmodule
