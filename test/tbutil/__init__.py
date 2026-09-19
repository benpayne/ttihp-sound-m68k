"""Shared cocotb testbench helpers for the 68k PSG sound chip project.

This package holds reusable simulation helpers used across the cocotb
test suite in `test/`. Modules (added by later tasks, not yet present):

- `bus68k_master.py` — 68000 bus driver (drives cs_n/as_n/r_w/ds_n/
  a1-3/data for register read/write cycles against the chip).
- `psg_model.py` — reference model of the sound engine (tone/noise
  generation, mixing) used to check decoded audio against expected
  samples.
- `i2s_decoder.py` — decodes the BCLK/LRCLK/SDATA I2S output into a
  sample stream.
- `spdif_decoder.py` — decodes the biphase-mark S/PDIF output into
  subframes and samples per IEC 60958.
- `pwm_meter.py` — measures PWM duty cycle / filtered level and
  carrier frequency.
- `covutil.py` — `export_scoped_yaml`, a prefix-filtered replacement for
  `CoverageDB.export_to_yaml()` safe to call from multiple coverage-bearing
  test modules sharing one process (constitution Principle II).
"""
