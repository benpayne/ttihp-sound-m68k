# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
#
# Standalone unit bench for the reusable `bus68k_if` 68000 bus IP
# (contracts/bus68k_if.md). Deliberately separate from ../Makefile: this
# pairs bus68k_if with a trivial register payload (tb_bus68k_if.v), no
# audio logic, so the IP is independently verifiable per FR-008 and so the
# sibling ttihp-spi-m68k project can copy this whole directory.
#
# Uses its own SIM_BUILD so it can never collide with sim_build/ under the
# parent test/ directory, which several other benches share concurrently.
#
# Usage:
#   cd test/bus68k_if && make -B

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
FST ?= -fst

SRC_DIR = $(PWD)/../../src

SIM_BUILD = sim_build/ip

VERILOG_SOURCES += $(SRC_DIR)/bus68k_if.v
VERILOG_SOURCES += $(PWD)/tb_bus68k_if.v

TOPLEVEL = tb_bus68k_if

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test_bus68k_if

# Make ../tbutil (the shared 68000 bus driver, IpLevelSignals adapter,
# etc.) importable as the `tbutil` package.
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
