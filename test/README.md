# Sample testbench for a Tiny Tapeout project

This is a sample testbench for a Tiny Tapeout project. It uses [cocotb](https://docs.cocotb.org/en/stable/) to drive the DUT and check the outputs.
See below to get started or for more information, check the [website](https://tinytapeout.com/hdl/testing/).

## Toolchain setup

One command sets up everything (Python 3.13 venv, pinned deps, Verilator >= 5.036). It is
idempotent and never uses sudo -- it prints the apt line if system packages are missing:

```sh
../scripts/setup-toolchain.sh
```

Do it manually instead with the steps below.

## Setting up

Tests run on **Python 3.13**, the newest version supported by cocotb 2.0.1 and the version
`cocotb-coverage` 2.0 is tested on, from a project venv. The system `python3` here is 3.10, so
install 3.13 with `uv`:

```sh
uv python install 3.13
$(uv python find 3.13) -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # cocotb 2.0.1, cocotb-coverage 2.0, pytest
```

Activate the venv before any `make` below. Keep test code **3.11-compatible**: TinyTapeout's
`gl_test` CI action pins Python 3.11 and can't be changed from this repo. Functional coverage uses
[cocotb-coverage](https://github.com/mciepluc/cocotb-coverage); see the project constitution
(`.specify/memory/constitution.md`, Verification Standards).

1. Edit [Makefile](Makefile) and modify `PROJECT_SOURCES` to point to your Verilog files.
2. Edit [tb.v](tb.v) and replace `tt_um_example` with your module name.

## How to run

To run the RTL simulation:

```sh
make -B
```

To run gatelevel simulation, first harden your project and copy `../runs/wokwi/results/final/verilog/gl/{your_module_name}.v` to `gate_level_netlist.v`.

Then run:

```sh
make -B GATES=yes
```

If you wish to save the waveform in VCD format instead of FST format, edit tb.v to use `$dumpfile("tb.vcd");` and then run:

```sh
make -B FST=
```

This will generate `tb.vcd` instead of `tb.fst`.

## Coverage

Functional coverage (cocotb-coverage) is collected by the tests themselves and exported to
`coverage.yml` / `coverage.xml`.

Code coverage (line + toggle) needs Verilator 5.040, which must come before the apt-installed
4.038 on `PATH` (`~/.local/bin`):

```sh
make -B SIM=verilator EXTRA_ARGS=--coverage
verilator_coverage --annotate coverage_annotated coverage.dat   # prints total %, marks misses
verilator_coverage --write-info coverage.info coverage.dat      # lcov format (optional)
```

Icarus stays the primary simulator for RTL and gate-level runs; Verilator is used for coverage.

### Coverage in CI

No CI job runs coverage yet (see `TODO(CI_COVERAGE)` in the constitution) -- it needs the Makefile
targets from the plan's Foundation step first. apt is no help on the runners (Verilator 5.020 on
ubuntu-24.04, below cocotb's 5.036 floor), so build it once and cache it. Recipe to enable:

```yaml
  coverage:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - run: pip install -r test/requirements.txt
      - run: sudo apt-get update && sudo apt-get install -y iverilog help2man autoconf flex bison libfl-dev
      - uses: actions/cache@v4
        id: cache-verilator
        with:
          path: ~/.local
          key: verilator-v5.040-${{ runner.os }}
      - if: steps.cache-verilator.outputs.cache-hit != 'true'
        run: |
          git clone --depth 1 --branch v5.040 https://github.com/verilator/verilator.git /tmp/v
          cd /tmp/v && autoconf && ./configure --prefix=$HOME/.local && make -j$(nproc) && make install
      - run: echo "$HOME/.local/bin" >> $GITHUB_PATH
      - run: cd test && make -B SIM=verilator EXTRA_ARGS=--coverage
      - run: cd test && verilator_coverage --annotate coverage_annotated coverage.dat
```

First run costs ~4 minutes for the build; later runs restore from cache in seconds. The
third-party `veryl-lang/setup-verilator` action ships prebuilt binaries and would be faster, but
its provenance is unverified -- the source build matches what cocotb's own CI does.

## How to view the waveform file

Using GTKWave

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer

```sh
surfer tb.fst
```
