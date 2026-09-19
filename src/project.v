/*
 * Copyright (c) 2026 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// tt_um_benpayne_sound_chip: top level for the 68k PSG sound chip.
//
// User Story 1 (bus + register file) is wired up here: reset discipline,
// the time base, the 68k bus interface (bus68k_if.v) and the register
// file (psg_regs.v) are all real. User Story 2 (tone/noise/mixer/PWM,
// docs/design/sound-chip.md) is wired up too, and so is User Story 3/4
// (I2S/S-PDIF, research.md R6/R7): both encoders share one 16-bit sample
// latch (`audio_sample`, mix << 5, latched once per frame) rather than
// each keeping their own copy.
//
// Pin map (normative -- contracts/pinout-and-bus-timing.md):
//   uo_out[0] DTACK_n    -- driven by bus68k_if, combinationally released (FR-004)
//   uo_out[1] PWM_OUT    -- tone/noise mix, 7-bit PWM @ 192 kHz (research.md R5); held 0 when pwm_en=0 (FR-035)
//   uo_out[2] I2S_BCLK   -- research.md R6; held 0 when i2s_en=0 (FR-035)
//   uo_out[3] I2S_LRCLK  -- research.md R6; held 0 when i2s_en=0 (FR-035)
//   uo_out[4] I2S_SDATA  -- research.md R6; held 0 when i2s_en=0 (FR-035)
//   uo_out[5] SPDIF_OUT  -- research.md R7; held 0 when spdif_en=0 or ENABLE_SPDIF=0 (FR-035, FR-051)
//   uo_out[6] HEARTBEAT  -- registered copy of the time base's count[8]: 48 kHz square wave,
//                           runs off nothing but power/clock/reset (FR-040, research R10)
//   uo_out[7] WR_STROBE  -- one-clock pulse per completed register write (FR-041)
//   uio_out/uio_oe       -- the shared 68k data bus, driven by bus68k_if on a qualified read
module tt_um_benpayne_sound_chip #(
    parameter ENABLE_SPDIF = 1  // gates the S/PDIF module once it exists (FR-051 cut path)
) (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // ---------------------------------------------------------------------
  // Reset synchronizer (constitution Principle IV): asynchronous assert,
  // synchronous release, through a 2-FF chain. `rst_n_sync` is the reset
  // every downstream flop in this design uses -- it is itself asserted
  // asynchronously (both flops below share the same async clear on the
  // raw pin) and released synchronously to `clk`.
  // ---------------------------------------------------------------------
  reg rst_n_meta;
  reg rst_n_sync;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rst_n_meta <= 1'b0;
      rst_n_sync <= 1'b0;
    end else begin
      rst_n_meta <= 1'b1;
      rst_n_sync <= rst_n_meta;
    end
  end

  // ---------------------------------------------------------------------
  // Time base: one free-running counter, every audio-rate function is a
  // clock enable off it (research R10). Only `count[8]` (via the
  // HEARTBEAT register below) is consumed at this stage; the other
  // strobes are wired up as the audio engine and bus interface land.
  // ---------------------------------------------------------------------
  wire [8:0] tb_count;
  wire       tb_half_cell;
  wire       tb_bit_cell;
  wire       tb_tick;
  wire       tb_frame;

  psg_timebase u_timebase (
      .clk      (clk),
      .rst_n    (rst_n_sync),
      .count    (tb_count),
      .half_cell(tb_half_cell),
      .bit_cell (tb_bit_cell),
      .tick     (tb_tick),
      .frame    (tb_frame)
  );

  // HEARTBEAT (uo_out[6]): registered copy of count[8] -- a clean 48 kHz
  // square wave with no dependency on register contents or bus activity
  // (FR-040, SC-007).
  reg hb_q;

  always @(posedge clk or negedge rst_n_sync) begin
    if (!rst_n_sync) begin
      hb_q <= 1'b0;
    end else begin
      hb_q <= tb_count[8];
    end
  end

  // ---------------------------------------------------------------------
  // 68k bus interface (contracts/bus68k_if.md): turns the async 68000
  // bus cycle on ui_in/uio_in into single-clock register accesses. Owns
  // all synchronizers on the bus strobes and all bus-release logic.
  // ---------------------------------------------------------------------
  wire [2:0] reg_addr;
  wire [7:0] reg_wdata;
  wire       reg_write;
  wire       reg_read;
  wire [7:0] reg_rdata;
  wire       dtack_n;
  wire [7:0] data_out;
  wire [7:0] data_oe;

  bus68k_if #(
      .ADDR_BITS(3)
  ) u_bus (
      .clk      (clk),
      .rst_n    (rst_n_sync),
      .cs_n     (ui_in[0]),
      .as_n     (ui_in[1]),
      .ds_n     (ui_in[3]),
      .r_w      (ui_in[2]),
      .addr     (ui_in[6:4]),
      .data_in  (uio_in),
      .data_out (data_out),
      .data_oe  (data_oe),
      .dtack_n  (dtack_n),
      .reg_addr (reg_addr),
      .reg_wdata(reg_wdata),
      .reg_write(reg_write),
      .reg_read (reg_read),
      .reg_rdata(reg_rdata)
  );

  // ---------------------------------------------------------------------
  // Register file (contracts/register-map.md, data-model.md sections
  // 1-2): the eight software-visible PSG registers. Tone/noise/enable
  // outputs are not consumed yet -- the audio engine lands in a later
  // story -- so they're reduced into _unused below.
  // ---------------------------------------------------------------------
  wire [11:0] active_period_a, active_period_b, active_period_c;
  wire [ 3:0] vol_a, vol_b, vol_c;
  wire [ 3:0] noise_vol, noise_rate;
  wire        a_en, b_en, c_en, noise_en, pwm_en, i2s_en, spdif_en;

  psg_regs u_regs (
      .clk            (clk),
      .rst_n          (rst_n_sync),
      .reg_addr       (reg_addr),
      .reg_wdata      (reg_wdata),
      .reg_write      (reg_write),
      .reg_read       (reg_read),
      .reg_rdata      (reg_rdata),
      .active_period_a(active_period_a),
      .active_period_b(active_period_b),
      .active_period_c(active_period_c),
      .vol_a          (vol_a),
      .vol_b          (vol_b),
      .vol_c          (vol_c),
      .noise_vol      (noise_vol),
      .noise_rate     (noise_rate),
      .a_en           (a_en),
      .b_en           (b_en),
      .c_en           (c_en),
      .noise_en       (noise_en),
      .pwm_en         (pwm_en),
      .i2s_en         (i2s_en),
      .spdif_en       (spdif_en)
  );

  // ---------------------------------------------------------------------
  // Audio engine (User Story 2, docs/design/sound-chip.md): tone A/B/C,
  // noise, the serial mixer, and the PWM output. All four blocks run off
  // the timebase's 192 kHz `tb_tick` and the mixer/PWM additionally key
  // off the master counter's low 7 bits (research.md R4/R5/R10).
  // ---------------------------------------------------------------------
  wire tone_a_bit, tone_b_bit, tone_c_bit;
  wire noise_bit;
  wire signed [10:0] mix;
  wire pwm_bit;

  psg_tone u_tone_a (
      .clk          (clk),
      .rst_n        (rst_n_sync),
      .tick         (tb_tick),
      .active_period(active_period_a),
      .tone_out     (tone_a_bit)
  );

  psg_tone u_tone_b (
      .clk          (clk),
      .rst_n        (rst_n_sync),
      .tick         (tb_tick),
      .active_period(active_period_b),
      .tone_out     (tone_b_bit)
  );

  psg_tone u_tone_c (
      .clk          (clk),
      .rst_n        (rst_n_sync),
      .tick         (tb_tick),
      .active_period(active_period_c),
      .tone_out     (tone_c_bit)
  );

  psg_noise u_noise (
      .clk      (clk),
      .rst_n    (rst_n_sync),
      .tick     (tb_tick),
      .rate     (noise_rate),
      .noise_out(noise_bit)
  );

  psg_mixer u_mixer (
      .clk      (clk),
      .rst_n    (rst_n_sync),
      .count7   (tb_count[6:0]),
      .a_en     (a_en),
      .b_en     (b_en),
      .c_en     (c_en),
      .noise_en (noise_en),
      .vol_a    (vol_a),
      .vol_b    (vol_b),
      .vol_c    (vol_c),
      .noise_vol(noise_vol),
      .tone_a   (tone_a_bit),
      .tone_b   (tone_b_bit),
      .tone_c   (tone_c_bit),
      .noise_bit(noise_bit),
      .mix      (mix)
  );

  pwm_out u_pwm (
      .clk   (clk),
      .rst_n (rst_n_sync),
      .count7(tb_count[6:0]),
      .tick  (tb_tick),
      .mix   (mix),
      .pwm   (pwm_bit)
  );

  // ---------------------------------------------------------------------
  // Shared 16-bit sample latch (User Story 3/4, research.md R6/R7): the
  // I2S and S/PDIF encoders both carry the identical `mix << 5` value,
  // latched once per 48 kHz frame at `tb_frame` (count==511). Latching
  // it once here and feeding both encoders saves 16 FFs versus each
  // module keeping its own copy. mix is an 11-bit signed value; `{mix,
  // 5'b0}` is exactly its two's-complement left-shift-by-5 as a 16-bit
  // value, so no arithmetic shift is needed.
  // ---------------------------------------------------------------------
  reg [15:0] audio_sample;

  always @(posedge clk or negedge rst_n_sync) begin
    if (!rst_n_sync) begin
      audio_sample <= 16'd0;
    end else if (tb_frame) begin
      audio_sample <= {mix, 5'b0};
    end
  end

  wire i2s_bclk, i2s_lrclk, i2s_sdata;

  i2s_out u_i2s (
      .clk   (clk),
      .rst_n (rst_n_sync),
      .count (tb_count),
      .sample(audio_sample),
      .bclk  (i2s_bclk),
      .lrclk (i2s_lrclk),
      .sdata (i2s_sdata)
  );

  wire spdif_bit;

  spdif_out #(
      .ENABLE_SPDIF(ENABLE_SPDIF)
  ) u_spdif (
      .clk      (clk),
      .rst_n    (rst_n_sync),
      .count    (tb_count),
      .half_cell(tb_half_cell),
      .bit_cell (tb_bit_cell),
      .frame    (tb_frame),
      .sample   (audio_sample),
      .spdif    (spdif_bit)
  );

  // WR_STROBE (uo_out[7]): one-clock pulse per completed write (FR-041),
  // a registered copy of the bus interface's reg_write pulse -- reg_write
  // is already exactly one clock wide and never asserted on a read
  // (contracts/bus68k_if.md guarantee 1), so this only needs a single
  // flop, not its own strobe logic.
  reg wr_q;

  always @(posedge clk or negedge rst_n_sync) begin
    if (!rst_n_sync) begin
      wr_q <= 1'b0;
    end else begin
      wr_q <= reg_write;
    end
  end

  // ---------------------------------------------------------------------
  // Output pin assembly. DTACK_n and the shared data bus are driven by
  // bus68k_if, which releases both combinationally off the raw pins
  // (68k-bus-interface.md sec 3.1, FR-003/FR-004/FR-005). Each audio
  // output format is independently gated by its own ENABLE bit, held low
  // (not tri-stated) when disabled (FR-035).
  // ---------------------------------------------------------------------
  assign uo_out[0] = dtack_n;  // DTACK_n
  assign uo_out[1] = pwm_en ? pwm_bit : 1'b0;  // PWM_OUT (FR-035: held low when disabled)
  assign uo_out[2] = i2s_en ? i2s_bclk : 1'b0;  // I2S_BCLK
  assign uo_out[3] = i2s_en ? i2s_lrclk : 1'b0;  // I2S_LRCLK
  assign uo_out[4] = i2s_en ? i2s_sdata : 1'b0;  // I2S_SDATA
  assign uo_out[5] = spdif_en ? spdif_bit : 1'b0;  // SPDIF_OUT (also 0 when ENABLE_SPDIF=0, FR-051)
  assign uo_out[6] = hb_q;     // HEARTBEAT
  assign uo_out[7] = wr_q;     // WR_STROBE

  assign uio_out = data_out;
  assign uio_oe  = data_oe;

  // Nothing consumes ui_in[7] (unused pin) or ena yet. Everything else is
  // now a real input to the bus interface, register file, or audio engine
  // (tone/noise/mixer/PWM/I2S/S-PDIF) above. Consumed explicitly so lint
  // stays clean.
  wire _unused = &{
    ena, ui_in[7],
    1'b0
  };

endmodule
