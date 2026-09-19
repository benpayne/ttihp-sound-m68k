/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// psg_regs: the eight software-visible PSG registers, per
// contracts/register-map.md and data-model.md sections 1-2.
//
// Sits on the payload side of bus68k_if (contracts/bus68k_if.md):
// reg_addr/reg_wdata/reg_write select and drive a write, reg_rdata is a
// combinational mux valid the clock after reg_addr changes, reg_read is
// unused here (no register in this map has a read side effect, FR-013).
//
// Atomic pitch commit (research.md R9, FR-012): each tone channel's LO
// register only updates a staged low byte. Writing that channel's CTRL
// register updates volume immediately AND commits
// {CTRL[3:0], staged_lo} into the channel's 12-bit active_period on the
// same clock, so the tone generators (a later story) never see a mixed
// old/new pitch.
module psg_regs (
    input  wire        clk,
    input  wire        rst_n,      // already-synchronized active-low reset

    // Payload-facing register bus from bus68k_if.
    input  wire [2:0]  reg_addr,
    input  wire [7:0]  reg_wdata,
    input  wire        reg_write,
    input  wire        reg_read,   // unused: no register here has a read side effect
    output reg  [7:0]  reg_rdata,

    // Committed tone-generator state (data-model.md section 2).
    output wire [11:0] active_period_a,
    output wire [11:0] active_period_b,
    output wire [11:0] active_period_c,
    output wire [ 3:0] vol_a,
    output wire [ 3:0] vol_b,
    output wire [ 3:0] vol_c,
    output wire [ 3:0] noise_vol,
    output wire [ 3:0] noise_rate,

    // ENABLE register bits (contracts/register-map.md).
    output wire        a_en,
    output wire        b_en,
    output wire        c_en,
    output wire        noise_en,
    output wire        pwm_en,
    output wire        i2s_en,
    output wire        spdif_en
);

  // Register index, per contracts/register-map.md.
  localparam [2:0] IDX_A_LO   = 3'd0;
  localparam [2:0] IDX_A_CTRL = 3'd1;
  localparam [2:0] IDX_B_LO   = 3'd2;
  localparam [2:0] IDX_B_CTRL = 3'd3;
  localparam [2:0] IDX_C_LO   = 3'd4;
  localparam [2:0] IDX_C_CTRL = 3'd5;
  localparam [2:0] IDX_NOISE  = 3'd6;
  localparam [2:0] IDX_ENABLE = 3'd7;

  // Reset value for ENABLE: all outputs on, all channels off
  // (contracts/register-map.md "Reset state").
  localparam [7:0] ENABLE_RESET = 8'h70;

  // ------------------------------------------------------------------
  // Staged LO bytes: exactly what x_LO reads back (FR-011), with no
  // effect on the committed period until x_CTRL is written (FR-012).
  // ------------------------------------------------------------------
  reg [7:0] staged_lo_a;
  reg [7:0] staged_lo_b;
  reg [7:0] staged_lo_c;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      staged_lo_a <= 8'h00;
    end else if (reg_write && reg_addr == IDX_A_LO) begin
      staged_lo_a <= reg_wdata;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      staged_lo_b <= 8'h00;
    end else if (reg_write && reg_addr == IDX_B_LO) begin
      staged_lo_b <= reg_wdata;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      staged_lo_c <= 8'h00;
    end else if (reg_write && reg_addr == IDX_C_LO) begin
      staged_lo_c <= reg_wdata;
    end
  end

  // ------------------------------------------------------------------
  // CTRL registers: vol[7:4] + period_hi[3:0], per channel. Volume takes
  // effect immediately; a CTRL write also commits active_period below,
  // whether or not the period bits actually changed (harmless per R9).
  // ------------------------------------------------------------------
  reg [7:0] ctrl_a;
  reg [7:0] ctrl_b;
  reg [7:0] ctrl_c;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl_a <= 8'h00;
    end else if (reg_write && reg_addr == IDX_A_CTRL) begin
      ctrl_a <= reg_wdata;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl_b <= 8'h00;
    end else if (reg_write && reg_addr == IDX_B_CTRL) begin
      ctrl_b <= reg_wdata;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl_c <= 8'h00;
    end else if (reg_write && reg_addr == IDX_C_CTRL) begin
      ctrl_c <= reg_wdata;
    end
  end

  // ------------------------------------------------------------------
  // Committed 12-bit active periods (data-model.md section 2): updated
  // only on a CTRL write, same clock, from {wdata[3:0], staged_lo}.
  // ------------------------------------------------------------------
  reg [11:0] active_period_a_q;
  reg [11:0] active_period_b_q;
  reg [11:0] active_period_c_q;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_period_a_q <= 12'h000;
    end else if (reg_write && reg_addr == IDX_A_CTRL) begin
      active_period_a_q <= {reg_wdata[3:0], staged_lo_a};
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_period_b_q <= 12'h000;
    end else if (reg_write && reg_addr == IDX_B_CTRL) begin
      active_period_b_q <= {reg_wdata[3:0], staged_lo_b};
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_period_c_q <= 12'h000;
    end else if (reg_write && reg_addr == IDX_C_CTRL) begin
      active_period_c_q <= {reg_wdata[3:0], staged_lo_c};
    end
  end

  assign active_period_a = active_period_a_q;
  assign active_period_b = active_period_b_q;
  assign active_period_c = active_period_c_q;

  assign vol_a = ctrl_a[7:4];
  assign vol_b = ctrl_b[7:4];
  assign vol_c = ctrl_c[7:4];

  // ------------------------------------------------------------------
  // NOISE: vol[7:4], rate[3:0]. No commit semantics -- plain storage.
  // ------------------------------------------------------------------
  reg [7:0] noise_reg;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      noise_reg <= 8'h00;
    end else if (reg_write && reg_addr == IDX_NOISE) begin
      noise_reg <= reg_wdata;
    end
  end

  assign noise_vol  = noise_reg[7:4];
  assign noise_rate = noise_reg[3:0];

  // ------------------------------------------------------------------
  // ENABLE: all 8 bits are read/write storage, including reserved bit 7
  // (contracts/register-map.md).
  // ------------------------------------------------------------------
  reg [7:0] enable_reg;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      enable_reg <= ENABLE_RESET;
    end else if (reg_write && reg_addr == IDX_ENABLE) begin
      enable_reg <= reg_wdata;
    end
  end

  assign a_en     = enable_reg[0];
  assign b_en     = enable_reg[1];
  assign c_en     = enable_reg[2];
  assign noise_en = enable_reg[3];
  assign pwm_en   = enable_reg[4];
  assign i2s_en   = enable_reg[5];
  assign spdif_en = enable_reg[6];
  // enable_reg[7] is reserved: read/write storage only, no function.

  // ------------------------------------------------------------------
  // Readback: combinational mux on reg_addr. bus68k_if registers
  // reg_addr itself, so reg_rdata is valid the clock after reg_addr
  // changes, per the bus contract; reads have no side effects (FR-013).
  // ------------------------------------------------------------------
  always @* begin
    case (reg_addr)
      IDX_A_LO:   reg_rdata = staged_lo_a;
      IDX_A_CTRL: reg_rdata = ctrl_a;
      IDX_B_LO:   reg_rdata = staged_lo_b;
      IDX_B_CTRL: reg_rdata = ctrl_b;
      IDX_C_LO:   reg_rdata = staged_lo_c;
      IDX_C_CTRL: reg_rdata = ctrl_c;
      IDX_NOISE:  reg_rdata = noise_reg;
      IDX_ENABLE: reg_rdata = enable_reg;
      default:    reg_rdata = 8'h00;
    endcase
  end

  // reg_read has no side effects on this payload (FR-013); consumed here
  // only so lint stays clean.
  wire _unused = &{reg_read, 1'b0};

endmodule
