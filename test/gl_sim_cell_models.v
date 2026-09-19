// Functional-only replacements for IHP SG13G2 sequential standard cells,
// used ONLY for local/CI gate-level cocotb simulation with Icarus Verilog.
//
// Why this file exists:
//
// The vendor sg13g2_stdcell.v models every sequential cell (flip-flops,
// latches, integrated clock gating cells) by routing the cell's real D/CLK/
// RESET_B/etc. inputs through internal "delayed_*" nets. Those nets are only
// ever driven as a side effect of $setuphold/$recrem/$width calls inside the
// cell's `specify` block - i.e. the *functional* behavior of the cell is
// entangled with its SDF timing-check machinery.
//
// Icarus Verilog does not support that flavor of timing check ("timing
// checks are not supported and delayed signal 'delayed_CLK' will not be
// driven" - Icarus's own warning, emitted even with -gspecify). Because the
// delayed_* nets are never driven, they stay at 'z' forever, which the
// vendor cell's internal primitives (ihp_dff_r, ihp_dff_r_err, ...) resolve
// to a permanent 'x' output - independent of clock, reset or data. This
// means every flip-flop in a synthesized netlist reads X under Icarus,
// regardless of the design's actual (correct) logic: purely combinational
// signals resolve fine, only registered ones go X. Verified locally:
// gl_test fails identically on a trivial single-FF reset check.
//
// This is a simulator/PDK-model interaction (confirmed present for any
// sequential ihp26b design under Icarus), not a defect in this project's
// RTL or synthesis result - LibreLane's synthesis and the tapeout precheck
// both pass cleanly on the same netlist.
//
// The functional truth table for each of these cells is simple and not in
// question (standard D flip-flop / transparent latch / integrated clock
// gate behavior); this file re-implements exactly that, using the same
// module names and port lists as the vendor cells, with plain behavioral
// Verilog that doesn't depend on specify-block side effects. test/Makefile
// strips the vendor definitions of these specific cells out of the netlist
// compile (see strip_gl_timing_cells.py) and substitutes this file instead,
// for simulation purposes only - the actual GDS/precheck flow is untouched.

`default_nettype none

// ---- Flip-flops: async active-low reset, posedge CLK ----

module sg13g2_dfrbp_1 (Q, Q_N, D, RESET_B, CLK);
  output Q, Q_N;
  input D, RESET_B, CLK;
  reg q;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= D;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_dfrbp_2 (Q, Q_N, D, RESET_B, CLK);
  output Q, Q_N;
  input D, RESET_B, CLK;
  reg q;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= D;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_dfrbpq_1 (Q, D, RESET_B, CLK);
  output Q;
  input D, RESET_B, CLK;
  reg q;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= D;
  assign Q = q;
endmodule

module sg13g2_dfrbpq_2 (Q, D, RESET_B, CLK);
  output Q;
  input D, RESET_B, CLK;
  reg q;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= D;
  assign Q = q;
endmodule

// ---- Scan flip-flops: same, plus a scan-in mux (D selected when SCE=0) ----

module sg13g2_sdfrbp_1 (Q, Q_N, D, SCD, SCE, RESET_B, CLK);
  output Q, Q_N;
  input D, SCD, SCE, RESET_B, CLK;
  reg q;
  wire d_sel = SCE ? SCD : D;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= d_sel;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_sdfrbp_2 (Q, Q_N, D, SCD, SCE, RESET_B, CLK);
  output Q, Q_N;
  input D, SCD, SCE, RESET_B, CLK;
  reg q;
  wire d_sel = SCE ? SCD : D;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= d_sel;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_sdfrbpq_1 (Q, D, SCD, SCE, RESET_B, CLK);
  output Q;
  input D, SCD, SCE, RESET_B, CLK;
  reg q;
  wire d_sel = SCE ? SCD : D;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= d_sel;
  assign Q = q;
endmodule

module sg13g2_sdfrbpq_2 (Q, D, SCD, SCE, RESET_B, CLK);
  output Q;
  input D, SCD, SCE, RESET_B, CLK;
  reg q;
  wire d_sel = SCE ? SCD : D;
  always @(posedge CLK or negedge RESET_B)
    if (!RESET_B) q <= 1'b0;
    else q <= d_sel;
  assign Q = q;
endmodule

module sg13g2_sdfbbp_1 (Q, Q_N, D, SCD, SCE, RESET_B, SET_B, CLK);
  output Q, Q_N;
  input D, SCD, SCE, RESET_B, SET_B, CLK;
  reg q;
  wire d_sel = SCE ? SCD : D;
  always @(posedge CLK or negedge RESET_B or negedge SET_B)
    if (!RESET_B) q <= 1'b0;
    else if (!SET_B) q <= 1'b1;
    else q <= d_sel;
  assign Q = q;
  assign Q_N = ~q;
endmodule

// ---- Transparent latches: high-enable (GATE) and low-enable (GATE_N) ----

module sg13g2_dlhq_1 (Q, D, GATE);
  output Q;
  input D, GATE;
  reg q;
  always @(GATE or D)
    if (GATE) q = D;
  assign Q = q;
endmodule

module sg13g2_dlhr_1 (Q, Q_N, D, RESET_B, GATE);
  output Q, Q_N;
  input D, RESET_B, GATE;
  reg q;
  always @(RESET_B or GATE or D)
    if (!RESET_B) q = 1'b0;
    else if (GATE) q = D;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_dlhrq_1 (Q, D, RESET_B, GATE);
  output Q;
  input D, RESET_B, GATE;
  reg q;
  always @(RESET_B or GATE or D)
    if (!RESET_B) q = 1'b0;
    else if (GATE) q = D;
  assign Q = q;
endmodule

module sg13g2_dllr_1 (Q, Q_N, D, RESET_B, GATE_N);
  output Q, Q_N;
  input D, RESET_B, GATE_N;
  reg q;
  always @(RESET_B or GATE_N or D)
    if (!RESET_B) q = 1'b0;
    else if (!GATE_N) q = D;
  assign Q = q;
  assign Q_N = ~q;
endmodule

module sg13g2_dllrq_1 (Q, D, RESET_B, GATE_N);
  output Q;
  input D, RESET_B, GATE_N;
  reg q;
  always @(RESET_B or GATE_N or D)
    if (!RESET_B) q = 1'b0;
    else if (!GATE_N) q = D;
  assign Q = q;
endmodule

// ---- Integrated clock gating: latch-based enable, ANDed with CLK ----

module sg13g2_lgcp_1 (GCLK, GATE, CLK);
  output GCLK;
  input GATE, CLK;
  reg en_latch;
  always @(CLK or GATE)
    if (!CLK) en_latch = GATE;
  assign GCLK = CLK & en_latch;
endmodule

module sg13g2_slgcp_1 (GCLK, GATE, SCE, CLK);
  output GCLK;
  input GATE, SCE, CLK;
  reg en_latch;
  always @(CLK or GATE or SCE)
    if (!CLK) en_latch = GATE | SCE;
  assign GCLK = CLK & en_latch;
endmodule
