/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// spdif_out: IEC 60958 consumer S/PDIF encoder (research.md R7;
// contracts/audio-output-formats.md S/PDIF section; data-model.md sec 4).
//
// Generated directly from the shared master counter with no subframe
// shift register: a frame counter (0..191), a line-level flop, and a
// parity-accumulator flop are the only state. Every bit's value and
// every preamble half-cell level are produced combinationally and
// looked up on the fly.
//
//   count[8]   = subframe (0 = A/left, 1 = B/right)
//   count[7:3] = bit b within the subframe (0..31)
//   count[2]   = half-cell within the bit (half-cell = 4 clk)
//
// `line_level` is a registered output that must already be correct on
// the very first clock of each new half-cell, so its next value has to
// be computed one half-cell ahead of `count` -- from `count + 1`, which
// mirrors exactly what psg_timebase's own counter is about to latch on
// the same clock edge. The same one-step-ahead reasoning applies to the
// frame counter (`spdif_frame`) wherever a subframe-A preamble choice
// (B vs M) is made right at a frame-boundary transition.
//
// Bit-cell boundary vs mid-cell toggle (biphase-mark): the line always
// toggles entering a new bit cell, and toggles again mid-cell iff that
// bit's value is 1 -- so a decoder recovers bit = (hc0 != hc1) for the
// two half-cells of any non-preamble bit, independent of the previous
// line level. Preamble bits (0-3 of every subframe) instead specify
// literal half-cell levels and deliberately break that rule.
//
// Behind ENABLE_SPDIF (FR-051 area cut path): when 0, no state is built
// and the pin ties low.
module spdif_out #(
    parameter ENABLE_SPDIF = 1
) (
    input  wire        clk,
    input  wire        rst_n,      // synchronized active-low reset
    input  wire [8:0]  count,      // tb_count (timebase), shared master counter
    input  wire        half_cell,  // tb_half_cell: count[1:0]==3, one clk wide
    input  wire        bit_cell,   // tb_bit_cell:  count[2:0]==7, one clk wide
    input  wire        frame,      // tb_frame:     count==511,   one clk wide
    input  wire [15:0] sample,     // shared latched sample (mix << 5), same as I2S
    output wire        spdif       // SPDIF_OUT pin drive
);

  generate
    if (ENABLE_SPDIF) begin : g_spdif

      localparam [7:0] FRAME_MAX = 8'd191;

      // Preamble half-cell patterns (contracts/audio-output-formats.md).
      // Bit 7 = the pattern's first (earliest) half-cell, bit 0 = last.
      localparam [7:0] PREAMBLE_B = 8'b1110_1000;  // subframe A, block frame 0
      localparam [7:0] PREAMBLE_M = 8'b1110_0010;  // subframe A, other frames
      localparam [7:0] PREAMBLE_W = 8'b1110_0100;  // subframe B, every frame

      // Current position (bit-value lookups for parity accumulation --
      // no lookahead needed, this only reads state from before "now").
      wire [4:0] bit_now  = count[7:3];

      // Upcoming position (drives the line-level register, which must
      // already be correct starting the first clock of the new half-cell).
      wire [8:0] count_next   = count + 9'd1;
      wire [4:0] bit_nxt      = count_next[7:3];
      wire       half_nxt     = count_next[2];
      wire       subframe_nxt = count_next[8];

      reg [7:0] spdif_frame;  // 0..191, advances once per full frame
      reg       line_level;
      reg       parity_acc;   // running XOR of bits 4..(bit_now-1) this subframe

      // In effect for the upcoming subframe: equals `spdif_frame` except
      // exactly at the count==511 -> 0 wrap, where it's already the
      // post-increment value (needed to pick B vs M for the new frame's
      // subframe-A preamble one half-cell before `spdif_frame` itself
      // updates).
      wire [7:0] spdif_frame_next = frame ? ((spdif_frame == FRAME_MAX) ? 8'd0 : spdif_frame + 8'd1)
                                           : spdif_frame;

      wire c_bit_now = (spdif_frame == 8'd2) || (spdif_frame == 8'd25);

      // Data-bit value for bit index 4..30 (b0-3 are preamble, handled
      // separately; b31 is P, supplied by parity_acc/parity_for_p below).
      function bit_val;
        input [4:0] idx;
        input       c;
        begin
          case (idx)
            // aux (4-7) + audio LSBs (8-11): always 0.
            5'd4, 5'd5, 5'd6, 5'd7, 5'd8, 5'd9, 5'd10, 5'd11: bit_val = 1'b0;
            // b12-27: the 16-bit sample, LSB first. Enumerated (rather than
            // a subtracted index) so no variable-width arithmetic is needed.
            5'd12: bit_val = sample[0];
            5'd13: bit_val = sample[1];
            5'd14: bit_val = sample[2];
            5'd15: bit_val = sample[3];
            5'd16: bit_val = sample[4];
            5'd17: bit_val = sample[5];
            5'd18: bit_val = sample[6];
            5'd19: bit_val = sample[7];
            5'd20: bit_val = sample[8];
            5'd21: bit_val = sample[9];
            5'd22: bit_val = sample[10];
            5'd23: bit_val = sample[11];
            5'd24: bit_val = sample[12];
            5'd25: bit_val = sample[13];
            5'd26: bit_val = sample[14];
            5'd27: bit_val = sample[15];
            5'd28, 5'd29: bit_val = 1'b0;  // V, U
            default: bit_val = c;  // b30 (C); b31 (P) never passed in here
          endcase
        end
      endfunction

      // Parity accumulates the transmitted value of bits 4..30 at each
      // bit's last clock (`bit_cell`); it's held cleared through the
      // preamble (b<=3) and after transmitting P (b==31), ready for the
      // next subframe.
      wire accumulate_now = (bit_now >= 5'd4) && (bit_now <= 5'd30);

      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          parity_acc <= 1'b0;
        end else if (bit_cell) begin
          parity_acc <= accumulate_now ? (parity_acc ^ bit_val(bit_now, c_bit_now)) : 1'b0;
        end
      end

      // P as it reads once bit 30's contribution has committed: on bit
      // 30's own last clock that's the same edge parity_acc is updating
      // on (the one-step-ahead case, see module header), so recompute it
      // combinationally rather than reading the not-yet-updated register;
      // on every other clock (including the rest of b==31's window)
      // parity_acc already holds the correct, settled value.
      wire parity_for_p = (bit_now == 5'd30) ? (parity_acc ^ bit_val(bit_now, c_bit_now)) : parity_acc;

      wire data_bit_nxt = (bit_nxt == 5'd31) ? parity_for_p : bit_val(bit_nxt, c_bit_now);

      wire       preamble_nxt    = (bit_nxt <= 5'd3);
      wire [2:0] pre_idx_nxt     = {bit_nxt[1:0], half_nxt};
      wire [7:0] pre_pat_nxt     = subframe_nxt ? PREAMBLE_W
                                   : (spdif_frame_next == 8'd0) ? PREAMBLE_B : PREAMBLE_M;
      wire       pre_level_nxt   = pre_pat_nxt[3'd7-pre_idx_nxt];

      // Biphase-mark: always toggle entering a new bit cell (half_nxt==0);
      // toggle again mid-cell (half_nxt==1) iff that bit's value is 1.
      wire toggle_nxt     = half_nxt ? data_bit_nxt : 1'b1;
      wire data_level_nxt = toggle_nxt ? ~line_level : line_level;
      wire next_level     = preamble_nxt ? pre_level_nxt : data_level_nxt;

      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          line_level <= 1'b0;
        end else if (half_cell) begin
          line_level <= next_level;
        end
      end

      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          spdif_frame <= 8'd0;
        end else if (frame) begin
          spdif_frame <= (spdif_frame == FRAME_MAX) ? 8'd0 : spdif_frame + 8'd1;
        end
      end

      assign spdif = line_level;

      // count_next[1:0] (the upcoming half-cell's sub-half-cell clock
      // position) doesn't feature in any of bit_nxt/half_nxt/subframe_nxt;
      // consumed explicitly so lint stays clean.
      wire _unused = &{count_next[1:0], 1'b0};

    end else begin : g_no_spdif

      assign spdif = 1'b0;

      wire _unused = &{clk, rst_n, count, half_cell, bit_cell, frame, sample, 1'b0};

    end
  endgenerate

endmodule
