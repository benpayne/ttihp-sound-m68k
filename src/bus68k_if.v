// SPDX-FileCopyrightText: © 2026 Ben Payne
// SPDX-License-Identifier: Apache-2.0
`default_nettype none

/* bus68k_if -- reusable 68000 asynchronous-bus register interface.
 *
 * Turns a 68000-style asynchronous bus cycle (CS_n/AS_n/DS_n/R_W/address)
 * into a single-clock register access (reg_addr/reg_wdata/reg_write/
 * reg_read/reg_rdata) for a payload module. Knows nothing about the
 * payload's register semantics -- see contracts/bus68k_if.md.
 *
 * The three strobes (cs_n, as_n, ds_n) are combined COMBINATIONALLY into
 * one "raw_qualified" signal first, and that single signal is what gets
 * a 2-FF synchronizer (constitution Principle V) -- see "One synchronizer,
 * not three" below for why. Address, R/W and write data are captured
 * combinationally, straight from the pins, on the clock edge the
 * synchronized qualify signal first asserts -- safe because the 68000
 * guarantees those signals are stable tens of ns before a synchronized
 * strobe can be observed (research.md R2).
 *
 * DTACK_n and the data-bus output enable are asserted synchronously (via
 * the FSM) but released COMBINATIONALLY from the raw (unsynchronized)
 * as_n/cs_n pins, and the internal dtack_q flop additionally has an
 * asynchronous clear on raw as_n/cs_n high (mirroring the dtack_n release
 * equation itself). The FSM's own state register and oe_q share that same
 * async clear -- see "Leaving ACK asynchronously" below. This is the only
 * place in the design that intentionally uses an unsynchronized signal to
 * control anything; it exists because a fully synchronized release cannot
 * meet 68000 DTACK/data-hold timing at 16 MHz and above (research.md R1).
 * None of these async clears have a metastability path: each is removed
 * when as_n/cs_n fall, at which point the flop's D input already equals
 * its reset value (ST_IDLE / 0), because the FSM cannot re-enter the
 * acknowledge state until the new cycle's strobes have passed back
 * through the synchronizer, at least two clocks later.
 *
 * One synchronizer, not three (revision history, kept for the next
 * person who is tempted to "fix" this back): an earlier revision gave
 * cs_n/as_n/ds_n their OWN independent 2-FF synchronizers and ANDed the
 * three synchronized outputs together to form "qualified". Three
 * separately-clocked 2-FF chains fed by simultaneously-changing raw
 * inputs are only guaranteed to move in lockstep so long as none of them
 * individually misses a single-cycle-wide pulse; under tight back-to-back
 * timing they can briefly disagree about whether the bus is currently
 * selected. Combining the raw strobes into one signal BEFORE the
 * flip-flops removes that structurally: there is only ever one 2-bit
 * history to reason about, so it cannot internally disagree with itself.
 * It is still exactly the "synchronize the strobe, sample the qualified
 * bus" technique research.md R2 calls for, and computes the identical
 * function of the pins in steady state (all three low, including DS's
 * write-only extra clock of delay, research R2/#22) -- only the
 * synchronization boundary moved.
 *
 * Leaving ACK asynchronously (revision history): even with one combined
 * synchronizer, gating the ACK->IDLE exit on a *synchronized* qualify
 * signal going low is unsound, for a reason independent of the skew bug
 * above: that 2-FF pipeline always lags the raw pins by up to two clocks,
 * so right after a genuine release it can still read a stale "1" left
 * over from the cycle that just ended, and briefly again after a
 * following cycle reasserts before the pipeline empties. A "latch any
 * momentary low" sticky flag (tried and discarded here) chases that lag
 * rather than removing it, and is itself fooled by transient dips the
 * pipeline can produce when releases and reassertions land close
 * together -- observed directly as a real qualified write silently
 * failing to reach ST_WRITE (reg_write never pulsed) because a stale or
 * spurious "release" retired ST_ACK one cycle early. The synchronized
 * qualify signal is trustworthy for *entering* a cycle (Principle V) but
 * must not be trusted for *leaving* ACK, because ACK's own dtack_n release
 * is already, by design (research R1), driven asynchronously off the raw
 * pins -- state and dtack_q need to agree on that instant, and only the
 * raw pins give it exactly, with zero pipeline lag. So state (like
 * dtack_q) is forced back to ST_IDLE the instant raw as_n or cs_n goes
 * high, via the same kind of single OR'd async-clear net as dtack_clr;
 * ST_ACK's synchronous case arm simply holds until that clear fires.
 * ST_WRITE/ST_READ always resolve to ST_ACK in one clock regardless, so
 * they cannot straddle a release long enough for this to cut a cycle
 * short.
 */
module bus68k_if #(
    parameter ADDR_BITS = 3
) (
    input  wire                  clk,
    input  wire                  rst_n,       // active-low, already synchronized by the top level

    // Raw 68000-side pins (asynchronous to clk)
    input  wire                  cs_n,
    input  wire                  as_n,
    input  wire                  ds_n,
    input  wire                  r_w,         // 1 = read
    input  wire [ADDR_BITS-1:0]  addr,
    input  wire [7:0]            data_in,

    // Raw 68000-side pins driven by this module
    output wire [7:0]            data_out,
    output wire [7:0]            data_oe,
    output wire                  dtack_n,

    // Payload-facing register bus (clk domain)
    output reg  [ADDR_BITS-1:0]  reg_addr,
    output reg  [7:0]            reg_wdata,
    output wire                  reg_write,
    output wire                  reg_read,
    input  wire [7:0]            reg_rdata
);

  // ------------------------------------------------------------------
  // Single combined-strobe synchronizer (2-FF), reset to inactive (0).
  // See the module header ("One synchronizer, not three") for why the
  // combining happens before, not after, the flip-flops. Used only to
  // gate ENTERING a new cycle from ST_IDLE -- see "Leaving ACK
  // asynchronously" for why it is not used to leave ST_ACK.
  // ------------------------------------------------------------------
  wire raw_qualified = ~cs_n & ~as_n & ~ds_n;

  reg qual_meta, qualified;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      qual_meta <= 1'b0;
      qualified <= 1'b0;
    end else begin
      qual_meta <= raw_qualified;
      qualified <= qual_meta;
    end
  end

  // ------------------------------------------------------------------
  // Bus-cycle FSM (data-model.md section 3)
  //
  // Async clear (state_clr): forces ST_IDLE the instant raw as_n or cs_n
  // goes high, exactly mirroring dtack_n's own release equation below (so
  // the FSM and DTACK_n always agree on the release instant) and dtack_q's
  // async clear. All three async triggers (reset, as_n, cs_n) drive state
  // to the SAME value (ST_IDLE, i.e. all zero bits), so this is one clear
  // condition, not a set/reset pair -- it maps to one ordinary
  // async-reset flop per state bit. See dtack_q below for the matching
  // IHP SG13G2 constraint (no usable async-set+reset cell).
  // ------------------------------------------------------------------
  localparam [1:0] ST_IDLE  = 2'd0,
                    ST_WRITE = 2'd1,
                    ST_READ  = 2'd2,
                    ST_ACK   = 2'd3;

  wire state_clr = ~rst_n | as_n | cs_n;

  reg [1:0] state;

  always @(posedge clk or posedge state_clr) begin
    if (state_clr) begin
      state <= ST_IDLE;
    end else begin
      case (state)
        ST_IDLE:  state <= qualified ? (r_w ? ST_READ : ST_WRITE) : ST_IDLE;
        ST_WRITE: state <= ST_ACK;
        ST_READ:  state <= ST_ACK;
        ST_ACK:   state <= ST_ACK;   // holds until state_clr fires
        default:  state <= ST_IDLE;
      endcase
    end
  end

  // Address/write-data capture, straight from the pins, on the single
  // clock edge the cycle qualifies (research R2). Read-vs-write is not
  // separately latched: ST_WRITE/ST_READ already encode it, so no extra
  // flop is needed (R11 area budget).
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_addr  <= {ADDR_BITS{1'b0}};
      reg_wdata <= 8'h00;
    end else if (state == ST_IDLE && qualified) begin
      reg_addr <= addr;
      if (!r_w) reg_wdata <= data_in;
    end
  end

  assign reg_write = (state == ST_WRITE);
  assign reg_read  = (state == ST_READ);

  // oe_q is set one clock before dtack_q (data-model.md section 3): it
  // arms at the IDLE->READ transition, so it is already asserted for
  // the whole ST_READ clock, a full clock ahead of dtack_q asserting at
  // ST_READ->ST_ACK (68000 timing #31, contract guarantee 3). It shares
  // state's async clear so it always returns to a safe, deasserted value
  // in lockstep with the FSM leaving ACK -- not load-bearing for the
  // data_oe pin itself, which already ANDs in raw ~as_n/~cs_n directly
  // below, but keeps internal state consistent for the next cycle.
  reg oe_q;
  always @(posedge clk or posedge state_clr) begin
    if (state_clr) begin
      oe_q <= 1'b0;
    end else if (state == ST_IDLE && qualified && r_w) begin
      oe_q <= 1'b1;
    end
  end

  // dtack_q: set synchronously on entering ACK, cleared ASYNCHRONOUSLY by
  // raw as_n/cs_n (research R1). Do not synchronize this clear -- see the
  // module header and research.md R1 for the timing/metastability
  // argument.
  //
  // Reset and the as_n/cs_n clear all drive dtack_q to the SAME value (0),
  // so they are one clear condition, not a set/reset pair -- they are
  // OR'd into a single active-high async-clear net (dtack_clr) so this
  // maps to one ordinary async-reset flop. IHP SG13G2 has no usable
  // flop with both an async set and an async reset (every general-purpose
  // cell, e.g. sg13g2_dfrbpq_1, is reset-only); a `posedge as_n` term
  // alongside `negedge rst_n` in one sensitivity list makes yosys infer
  // exactly that unsupported set+reset flop.
  wire dtack_clr = state_clr;
  reg dtack_q;
  always @(posedge clk or posedge dtack_clr) begin
    if (dtack_clr) begin
      dtack_q <= 1'b0;
    end else if (state == ST_WRITE || state == ST_READ) begin
      dtack_q <= 1'b1;
    end
  end

  // ------------------------------------------------------------------
  // Combinational output equations (data-model.md section 3) -- released
  // by the raw pins with no clock needed, per research R1/C1/C2.
  // ------------------------------------------------------------------
  assign dtack_n  = ~dtack_q | as_n | cs_n;
  assign data_out = reg_rdata;
  assign data_oe  = {8{oe_q & ~as_n & ~cs_n & r_w}};

endmodule
