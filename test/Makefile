# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# Use bash so recipes below can rely on `set -o pipefail` (needed anywhere we
# `tee` a tool's output but still want its real exit code to reach make).
SHELL := bash

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v psg_timebase.v bus68k_if.v psg_regs.v psg_tone.v psg_noise.v psg_mixer.v pwm_out.v i2s_out.v spdif_out.v

# Design top module (per info.yaml). Used by the non-cocotb targets below
# (lint/area/fpga), which synthesize/lint the design directly rather than
# through the `tb` wrapper cocotb drives.
TOP_MODULE = tt_um_benpayne_sound_chip

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v

# The vendor sg13g2_stdcell.v models sequential cells (flip-flops, latches,
# clock gates) via specify-block-driven "delayed_*" nets that Icarus Verilog
# does not support, which makes every register read X in gate-level sim (see
# gl_sim_cell_models.v for the full explanation). Strip those cell
# definitions out of a filtered copy of the vendor file and substitute our
# own functional-only replacements instead - simulation only, this doesn't
# touch the real PDK files used for synthesis/GDS.
GL_STDCELL_SRC := $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v
GL_STDCELL_FILTERED := $(PWD)/sim_build/gl/sg13g2_stdcell_filtered.v
$(shell mkdir -p $(PWD)/sim_build/gl)
$(shell python3 $(PWD)/strip_gl_timing_cells.py $(GL_STDCELL_SRC) $(GL_STDCELL_FILTERED))

VERILOG_SOURCES += $(GL_STDCELL_FILTERED)
VERILOG_SOURCES += $(PWD)/gl_sim_cell_models.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
# Every real suite. TinyTapeout CI (test.yaml and the gl_test action) runs a
# bare `make`, so this list IS what CI verifies -- it previously pointed at the
# template stub test.py, which asserted uo_out==50 and could never pass against
# the real design, leaving CI effectively testing nothing.
#
# Runtime note: this is a ~40 minute job. test_noise alone is ~26 min of that,
# and test_noise_rate_sweep_monotonic ~20 min on its own (16 rates x 2400 ticks).
# If CI wall time becomes a problem, that single test is the thing to shorten --
# not the module list.
# NOTE: test_volume_curve.py is deliberately NOT in this list. It holds pytest
# tests (plain `def test_*()`, no @cocotb.test, no dut) that check the AMP table
# in pure Python with no simulator. Listing it here does NOT run them -- cocotb
# prints "UserWarning: No tests were discovered in module: test_volume_curve"
# and make still exits 0, so it silently contributes nothing. Run it with the
# `unit` target below; `make all-tests` runs both halves.
COCOTB_TEST_MODULES = test_reset,test_timebase,test_bus,test_regs,test_debug,test_tone_pwm,test_channels,test_noise,test_i2s,test_spdif,test_output_consistency

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# ============================================================================
# Extra targets: area estimate, FPGA portability proof, lint, coverage.
#
# These are additive - `make -B` (RTL) and `make -B GATES=yes` (gate level)
# above are the TinyTapeout CI entry points and must keep working unchanged.
# ============================================================================

# ---- IHP SG13G2 typical-corner liberty resolution (area target only) ------
#
# Preference order:
#   1. $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib
#   2. a ciel PDK cache under /tmp (as used by this dev environment)
#   3. fail loudly - never silently skip the area check
IHP_LIBERTY := $(shell \
	if [ -n "$$PDK_ROOT" ] && [ -f "$$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib" ]; then \
		echo "$$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib"; \
	else \
		ls /tmp/pdk/ciel/ihp-sg13g2/versions/*/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib 2>/dev/null | head -n1; \
	fi)

AREA_SOURCES := $(addprefix ../src/,$(PROJECT_SOURCES))
FPGA_SOURCES := $(addprefix ../src/,$(PROJECT_SOURCES))

.PHONY: check-liberty area fpga lint coverage unit all-tests

check-liberty:
	@if [ -z "$(IHP_LIBERTY)" ]; then \
		echo "ERROR: could not resolve the IHP SG13G2 typical-corner liberty file."; \
		echo "  Set PDK_ROOT to your PDK install, so that this file exists:"; \
		echo "    \$$PDK_ROOT/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib"; \
		echo "  (looked for a ciel cache under /tmp/pdk/ciel/... too, and found nothing there either)."; \
		exit 1; \
	fi

# T005: synthesis-based area estimate against the IHP typ liberty. Reference:
# sibling ttihp-ps2-m68k measured 12,620 um^2 (fits a 1x1 tile, 31,318 um^2).
# Budget: <=17,500 um^2 proceed | 17,500-21,000 raise density | >21,000 cut S/PDIF.
area: check-liberty
	@mkdir -p sim_build
	@sed \
		-e 's#@@SOURCES@@#$(AREA_SOURCES)#' \
		-e 's#@@TOP@@#$(TOP_MODULE)#' \
		-e 's#@@LIBERTY@@#$(IHP_LIBERTY)#' \
		area.ys > sim_build/area.ys.gen
	@set -o pipefail; yosys -s sim_build/area.ys.gen 2>&1 | tee sim_build/area.log
	@echo ""
	@echo "==================== AREA RESULT ===================="
	@if grep -q "Chip area" sim_build/area.log; then \
		grep "Chip area" sim_build/area.log | tail -n1; \
	elif [ "$$(grep 'Number of cells:' sim_build/area.log | tail -n1 | awk '{print $$NF}')" = "0" ]; then \
		echo "Chip area for module '$(TOP_MODULE)': 0.000000 (0 cells - purely combinational/constant logic, e.g. the placeholder project.v)"; \
	else \
		echo "WARNING: no 'Chip area' line found in yosys output - see sim_build/area.log"; \
	fi
	@echo "Budget: <=17500 proceed | 17500-21000 raise density | >21000 cut S/PDIF (um^2); 1x1 tile = 31318 um^2"
	@echo "======================================================="

# T006: FPGA/ASIC portability proof (constitution Principle III) - same RTL
# synthesized for the iCE40UP5K (the TT FPGA demo board target). Fails loudly
# (non-zero exit) on any synthesis error.
fpga:
	@mkdir -p sim_build
	@sed \
		-e 's#@@SOURCES@@#$(FPGA_SOURCES)#' \
		-e 's#@@TOP@@#$(TOP_MODULE)#' \
		fpga.ys > sim_build/fpga.ys.gen
	@set -o pipefail; yosys -s sim_build/fpga.ys.gen 2>&1 | tee sim_build/fpga.log

# T007a: lint. Verilator treats an unwaived warning as fatal under
# --lint-only, so this exits non-zero on any warning, not just hard errors.
# -Wno-DECLFILENAME: the Tiny Tapeout template requires the top module to
# live in a file literally named project.v, which will always trip
# verilator's filename-matches-module-name check. That's a structural
# TT convention, not a real lint issue, so it's the one warning class we
# waive; everything else in -Wall stays fatal.
lint:
	verilator --lint-only -Wall -Wno-DECLFILENAME -I$(SRC_DIR) --top-module $(TOP_MODULE) \
		$(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

# T007b: coverage. This is a local/milestone check (constitution decision
# 2026-09-12), NOT a CI gate - run it ahead of a story checkpoint, not on
# every commit.
coverage:
	# --timing is REQUIRED: tb.v's $dumpvars block ends with `#1;`, and Verilator
	# refuses to compile a delay without an explicit timing mode (NEEDTIMINGOPT).
	# --no-timing is not an option -- it escalates the ignored-delay STMTDLY
	# warning to an error. This target had never been executed end to end.
	$(MAKE) -B SIM=verilator EXTRA_ARGS="--coverage --timing"
	verilator_coverage --annotate coverage_annotated coverage.dat
	verilator_coverage --write-info coverage.info coverage.dat

# Pure-Python unit tests: no simulator, no cocotb, ~0.02s. test_volume_curve.py
# checks the AMP table's shape, monotonicity, logarithmic step and total span
# (FR-023) directly in Python. These are pytest tests, so `make` CANNOT run
# them: listing the module in COCOTB_TEST_MODULES makes cocotb print
# "No tests were discovered in module: test_volume_curve" and exit 0, which
# looks green while running nothing. They need this separate entry point, and
# CI invokes pytest directly for the same reason.
unit:
	python -m pytest test_volume_curve.py -v

# Both halves of the suite. `make` on its own stays the TinyTapeout CI entry
# point and is unchanged; this is what a human should run before pushing.
# Unit tests go first: they are instant and catch an AMP-table regression
# without waiting ~40 minutes for the cocotb suite.
all-tests: unit
	$(MAKE) -B
