#!/usr/bin/env bash
# Set up the verification toolchain for this project (and its 68k siblings).
# Idempotent: skips anything already present at a new-enough version.
# Never uses sudo -- it prints the apt command if system packages are missing.
#
# Installs: Python 3.13 venv + pinned Python deps, Verilator >= 5.036 (built into ~/.local).
# Assumes: iverilog and yosys already available (apt versions are fine).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERILATOR_TAG="v5.040"          # newest release cocotb 2.0.1's CI tests; floor is 5.036
PYTHON_VERSION="3.13"           # newest cocotb 2.0.1 supports; cocotb-coverage 2.0 needs >= 3.11
PREFIX="${HOME}/.local"

say() { printf '\n=== %s\n' "$*"; }

say "Checking system build dependencies"
MISSING=()
for t in git make g++ perl autoconf flex bison help2man; do
  command -v "$t" >/dev/null || MISSING+=("$t")
done
[ -f /usr/include/FlexLexer.h ] || MISSING+=("libfl-dev")
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "Missing: ${MISSING[*]}"
  echo "Install them, then re-run:"
  echo "  sudo apt-get install -y git make g++ perl python3 autoconf flex bison help2man libfl2 libfl-dev zlib1g zlib1g-dev"
  exit 1
fi
echo "all present"

say "Python ${PYTHON_VERSION}"
if ! command -v uv >/dev/null; then
  echo "uv not found. Install it (curl -LsSf https://astral.sh/uv/install.sh | sh) or provide"
  echo "python${PYTHON_VERSION} another way, then re-run."
  exit 1
fi
uv python install "${PYTHON_VERSION}"
PY="$(uv python find "${PYTHON_VERSION}")"
echo "using ${PY} ($("${PY}" --version))"

say "Virtualenv at test/venv"
if [ ! -x "${REPO_ROOT}/test/venv/bin/python" ] \
   || ! "${REPO_ROOT}/test/venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,13) else 1)' 2>/dev/null; then
  rm -rf "${REPO_ROOT}/test/venv"
  "${PY}" -m venv "${REPO_ROOT}/test/venv"
fi
"${REPO_ROOT}/test/venv/bin/python" -m pip install -q --upgrade pip
"${REPO_ROOT}/test/venv/bin/python" -m pip install -q -r "${REPO_ROOT}/test/requirements.txt"
"${REPO_ROOT}/test/venv/bin/python" -m pip list 2>/dev/null | grep -iE 'cocotb|pytest'

say "Verilator (need >= 5.036 for cocotb; apt ships 4.038 on jammy / 5.020 on noble)"
have_ok=0
if command -v verilator >/dev/null; then
  V="$(verilator --version | awk '{print $2}')"
  awk -v v="$V" 'BEGIN{exit !(v+0 >= 5.036)}' && have_ok=1
  echo "found Verilator ${V} at $(command -v verilator)$([ $have_ok = 1 ] && echo ' (ok)' || echo ' (too old)')"
fi
if [ "$have_ok" = 0 ]; then
  BUILD="$(mktemp -d)"
  echo "building Verilator ${VERILATOR_TAG} into ${PREFIX} (a few minutes, ~1.6 GB peak in ${BUILD})"
  git clone -q --depth 1 --branch "${VERILATOR_TAG}" https://github.com/verilator/verilator.git "${BUILD}"
  ( cd "${BUILD}" && autoconf && ./configure --prefix="${PREFIX}" && make -j"$(nproc)" && make install ) >/dev/null
  rm -rf "${BUILD}"
  echo "installed: $("${PREFIX}/bin/verilator" --version)"
  case ":${PATH}:" in *":${PREFIX}/bin:"*) ;; *) echo "NOTE: add ${PREFIX}/bin to PATH (ahead of /usr/bin)";; esac
fi

say "Done. Activate with: source test/venv/bin/activate"
