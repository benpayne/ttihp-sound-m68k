/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// psg_tone: one square-wave tone channel (research.md R3, R9;
// data-model.md Sec 2). Instantiated three times (channels A/B/C) by
// project.v, each fed its own committed `active_period` from psg_regs.
//
// A 12-bit up-counter advances once per 192 kHz `tick`. The channel
// output toggles once the counter reaches `max(active_period,1) - 1`,
// then the counter restarts at 0, giving
//   f = clk / (256 * max(active_period,1)) = 96000 / max(N,1) Hz
// at the nominal 24.576 MHz clock. The comparison uses `>=` rather than
// `==` so that shrinking `active_period` mid-count (already atomic,
// R9) can never leave the counter stranded above the new target for a
// full 4096-tick wrap -- it always toggles on the very next tick
// instead. Period 0 behaves exactly as period 1 (FR-027): the "or 1"
// floor is applied to the comparison target, not to `active_period`
// itself, so a genuinely-zero period is never miscounted.
//
// `active_period` is expected to already be the *committed* 12-bit
// period (psg_regs' `active_period_x` output, R9) -- this module does
// not know about the staged low byte or the CTRL commit, and does not
// reset `tone_cnt` just because `active_period` changes value; only the
// tick-driven compare above ever restarts the counter.
module psg_tone (
    input  wire        clk,
    input  wire        rst_n,          // synchronized active-low reset
    input  wire        tick,           // 192 kHz clock enable, one clk wide
    input  wire [11:0] active_period,  // committed period (psg_regs)
    output reg         tone_out        // bipolar square-wave bit for the mixer
);

  reg [11:0] tone_cnt;

  // max(active_period, 1) - 1: the counter's toggle target. Computed
  // combinationally each tick so a change in active_period is honored on
  // the very next tick, per the >= rationale above.
  wire [11:0] eff_period    = (active_period == 12'd0) ? 12'd1 : active_period;
  wire [11:0] toggle_target = eff_period - 12'd1;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tone_cnt <= 12'd0;
      tone_out <= 1'b0;
    end else if (tick) begin
      if (tone_cnt >= toggle_target) begin
        tone_out <= ~tone_out;
        tone_cnt <= 12'd0;
      end else begin
        tone_cnt <= tone_cnt + 12'd1;
      end
    end
  end

endmodule
