`default_nettype none
`timescale 1ns / 1ps

/* This testbench just instantiates the module and makes some convenient wires
   that can be driven / tested by the cocotb test.py.
*/
module tb ();

  // Dump the signals to a FST file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #1;
  end

  // Wire up the inputs and outputs:
  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  tt_um_benpayne_sound_chip user_project (
      .ui_in  (ui_in),    // Dedicated inputs
      .uo_out (uo_out),   // Dedicated outputs
      .uio_in (uio_in),   // IOs: Input path
      .uio_out(uio_out),  // IOs: Output path
      .uio_oe (uio_oe),   // IOs: Enable path (active high: 0=input, 1=output)
      .ena    (ena),      // enable - goes high when design is selected
      .clk    (clk),      // clock
      .rst_n  (rst_n)     // not reset
  );

  // Individual uo_out bits as standalone 1-bit nets.
  // cocotb 2.0 forbids indexing a packed vector handle (handle.py: "Packed
  // objects, either arrays or structs, cannot be indexed"), so edge triggers
  // (RisingEdge/FallingEdge) need a real 1-bit signal. See tbutil/dutbits.py.
  wire uo_bit0 = uo_out[0];
  wire uo_bit1 = uo_out[1];
  wire uo_bit2 = uo_out[2];
  wire uo_bit3 = uo_out[3];
  wire uo_bit4 = uo_out[4];
  wire uo_bit5 = uo_out[5];
  wire uo_bit6 = uo_out[6];
  wire uo_bit7 = uo_out[7];

endmodule
