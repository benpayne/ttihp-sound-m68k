# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
PWM duty-cycle and recovered-tone-frequency measurement helpers.

Covers task T027 (specs/001-psg-sound-chip/tasks.md, Phase 4 / User Story
2). Used by test_tone_pwm.py (T028) to check the PWM_OUT pin
(contracts/pinout-and-bus-timing.md `uo[1]`) against
contracts/audio-output-formats.md and test/tbutil/psg_model.py.

All three entry points below (`sample_pwm_periods`, `measure_pwm`,
`measure_tone_freq`) time real edges of `dut_signal` against `clk` and
therefore need the simulation to actually be running in a cocotb test --
they cannot be exercised by a bare `python -m py_compile`/import.

Timeout behavior: every wait for an edge of `dut_signal` is bounded by a
generous multiple of the expected carrier period, expressed in `clk`
cycles via `cocotb.triggers.First`/`ClockCycles` (not a raw simulation-time
Timer, so it stays correct regardless of the clock period passed in). If
`dut_signal` never toggles at all -- e.g. the placeholder src/project.v,
which drives every output to a constant 0 -- the very first wait times out
and raises `PwmMeterTimeout` instead of hanging the simulation forever.

Known limitation (applies to `measure_pwm`/`measure_tone_freq` only --
   `sample_pwm_periods` counts clocks and is immune): because those
   functions locate carrier-period boundaries
by watching `dut_signal`'s own edges, a PWM duty pinned at a hard extreme
for many consecutive carrier periods (0% or, for this design's 7-bit PWM,
127/128) produces no edges to sync to and will read as a timeout, the same
as a genuinely stuck pin. That is an intentional, documented trade-off for
staying simple and generic (this module has no knowledge of the mixer's
amplitude range or channel count) -- callers exercising the extreme end of
the volume/mix range (FR-025) should choose test parameters (e.g. distinct
per-channel periods, so all channels rarely sit at the same extreme for
long) that keep the signal toggling, and should assert directly on
`sample_pwm_periods`' per-period duty codes rather than relying on this
module inferring carrier frequency from constant-level runs.
"""

from cocotb.triggers import ClockCycles, FallingEdge, First, RisingEdge
from cocotb.utils import get_sim_time


class PwmMeterTimeout(TimeoutError):
    """Raised when an expected edge never appears on the measured pin."""


def _signal_path(signal) -> str:
    return getattr(signal, "_path", str(signal))


def _default_timeout_cycles(clk_hz: float, carrier_hz: float, margin_periods: int = 20) -> int:
    """A generous bound, in `clk` cycles, for waiting on one edge of a
    signal that toggles at roughly `carrier_hz`. `margin_periods` carrier
    periods is far more than one edge should ever need, so a real timeout
    here means the pin is genuinely stuck, not just slow."""
    clks_per_carrier = max(1, round(clk_hz / carrier_hz))
    return clks_per_carrier * margin_periods


async def _edge_or_timeout(signal, edge_cls, clk, timeout_cycles: int):
    """Wait for `edge_cls(signal)`, racing it against a `clk`-cycle budget
    so a pin that never toggles raises instead of hanging the sim."""
    result = await First(edge_cls(signal), ClockCycles(clk, timeout_cycles))
    if isinstance(result, ClockCycles):
        raise PwmMeterTimeout(
            f"no {edge_cls.__name__} seen on {_signal_path(signal)} within "
            f"{timeout_cycles} clk cycles -- the pin appears stuck (e.g. "
            "never toggling, as the placeholder src/project.v does)."
        )
    return result


async def sample_pwm_periods(
    dut_signal,
    clk,
    count: int,
    carrier_hz: float = 192_000,
    clk_hz: float = 24_576_000,
    resolution_bits: int = 7,
    timeout_cycles: int = None,
) -> list:
    """Return `count` consecutive per-carrier-period PWM duty codes.

    Each value is the number of `clk` cycles the pin was High during one
    carrier period, i.e. a duty code in 0..128 for this project's 7-bit PWM
    (contracts/audio-output-formats.md). pwm_out.v latches a new duty once
    per carrier period and holds it (research.md R5), so each sample IS that
    192 kHz tick's instantaneous mixed amplitude.

    Overflow/wrap note (FR-025): the value is NOT clamped. A correct mixer
    yields 0..127; a code of 128 means the pin was High for an entire period,
    which needs a duty register value past the legal 7-bit range and is the
    observable signature of a mixer wrap. Callers should assert `code <= 127`.

    Implementation: this counts `clk` cycles rather than watching the pin's
    own edges. The carrier is exactly clk_hz/carrier_hz = 128 clocks and runs
    free from the timebase, so a fixed-width window is well defined at EVERY
    duty -- including the extremes (0 and 127) where an edge-synced sampler
    has no edge to lock onto and times out indistinguishably from a stuck
    pin. At four channels of max volume the mixer legitimately sits at duty 0
    for roughly a quarter of all periods, so edge syncing cannot measure this
    design's own legal operating range.
    """
    period_clocks = int(round(clk_hz / carrier_hz))
    if period_clocks <= 0:
        raise ValueError(f"carrier_hz {carrier_hz} is not below clk_hz {clk_hz}")

    # Best-effort phase alignment to a period boundary: pwm goes High at
    # count7==0, so one rising edge marks a boundary exactly. If none arrives
    # (duty pinned at an extreme) proceed unaligned -- consecutive samples are
    # identical there, so the phase offset cannot change the result.
    try:
        await _edge_or_timeout(dut_signal, RisingEdge, clk, 2 * period_clocks)
    except PwmMeterTimeout:
        pass

    codes = []
    for _ in range(count):
        high = 0
        for _ in range(period_clocks):
            await RisingEdge(clk)
            if int(dut_signal.value):
                high += 1
        codes.append(high)
    return codes


async def measure_pwm(
    dut_signal,
    clk,
    periods: int = 8,
    carrier_hz: float = 192_000,
    clk_hz: float = 24_576_000,
    timeout_cycles: int = None,
) -> dict:
    """Measure average PWM duty cycle and carrier timing over `periods`
    consecutive carrier cycles of `dut_signal`, by timing its edges.

    Returns a dict with:
      - 'duty': average high-time fraction, 0.0..1.0
      - 'carrier_hz': measured carrier frequency (1e9 / average period_ns)
      - 'high_ns': average high time per period, in ns
      - 'period_ns': average carrier period, in ns

    Raises PwmMeterTimeout (see module docstring) if `dut_signal` does not
    toggle -- the placeholder src/project.v never does, so this call fails
    fast against it instead of hanging.
    """
    if timeout_cycles is None:
        timeout_cycles = _default_timeout_cycles(clk_hz, carrier_hz)

    await _edge_or_timeout(dut_signal, RisingEdge, clk, timeout_cycles)
    t_rise = get_sim_time("ns")

    high_samples = []
    period_samples = []
    for _ in range(periods):
        await _edge_or_timeout(dut_signal, FallingEdge, clk, timeout_cycles)
        t_fall = get_sim_time("ns")
        high_samples.append(t_fall - t_rise)

        await _edge_or_timeout(dut_signal, RisingEdge, clk, timeout_cycles)
        t_rise2 = get_sim_time("ns")
        period_samples.append(t_rise2 - t_rise)

        t_rise = t_rise2

    high_ns = sum(high_samples) / len(high_samples)
    period_ns = sum(period_samples) / len(period_samples)
    duty = (high_ns / period_ns) if period_ns else 0.0
    measured_carrier_hz = (1e9 / period_ns) if period_ns else 0.0
    return {
        "duty": duty,
        "carrier_hz": measured_carrier_hz,
        "high_ns": high_ns,
        "period_ns": period_ns,
    }


def _fundamental_from_zero_crossings(samples, carrier_hz: float):
    """Estimate the fundamental frequency of a per-carrier-period duty
    sample stream by mean-removing it and averaging the spacing between
    rising (negative-to-positive) zero crossings, linearly interpolated
    for sub-sample accuracy.

    Zero crossings are used instead of an FFT because the underlying
    signal is (for a single active tone channel) an exact two-level square
    wave once demodulated -- crossings are simple, exact, and unaffected
    by the +-1 LSB duty jitter that pwm_out's integer duty math produces:
    a 1-code wobble near a true crossing shifts the interpolated crossing
    time by at most one sample, which is negligible next to a period that
    spans many samples, and does not create or remove crossings elsewhere
    in the mean-removed signal because the wobble is small relative to the
    signal's peak-to-peak swing.

    Returns None if fewer than two rising crossings are found (e.g. a flat
    / silent signal), leaving the timeout-vs-more-data decision to the
    caller.
    """
    n = len(samples)
    mean = sum(samples) / n
    deviations = [s - mean for s in samples]

    crossings = []
    for i in range(n - 1):
        a, b = deviations[i], deviations[i + 1]
        if a <= 0 < b:
            frac = -a / (b - a)
            crossings.append(i + frac)

    if len(crossings) < 2:
        return None

    diffs = [b - a for a, b in zip(crossings, crossings[1:])]
    avg_period_samples = sum(diffs) / len(diffs)
    if avg_period_samples <= 0:
        return None
    return carrier_hz / avg_period_samples


async def measure_tone_freq(
    dut_signal,
    clk,
    min_edges: int = 64,
    carrier_hz: float = 192_000,
    clk_hz: float = 24_576_000,
    timeout_ns: float = None,
) -> float:
    """Recover the audible square-wave frequency modulated onto
    `dut_signal`'s PWM duty cycle.

    Method: each carrier period's high-time is one software-low-pass-
    filtered sample of the audio waveform -- exactly the boxcar filter an
    external RC filter performs in hardware, since pwm_out.v latches one
    duty value per carrier period (research.md R5). Once at least
    `min_edges` such samples have been collected, the fundamental is
    recovered via mean-removed zero crossings (see
    `_fundamental_from_zero_crossings`), which is simple, exact for a
    symmetric square wave, and robust to +-1 LSB duty-cycle jitter.

    `min_edges` should be chosen so that it spans at least ~2 periods of
    the *expected* audible frequency (too few samples and no crossings
    will ever be found); `timeout_ns` bounds the total simulated time
    spent collecting samples, so a flat/silent signal -- which never
    produces a crossing -- raises `TimeoutError` instead of sampling
    forever. Callers that want to assert "no audible tone" (e.g. the
    silence acceptance scenario) should call this expecting the
    TimeoutError.

    Raises PwmMeterTimeout if a single carrier-period edge does not
    appear (a stuck pin, as with the placeholder src/project.v). Raises
    TimeoutError if edges keep appearing (the pin is alive) but no stable
    fundamental is found within `timeout_ns` of simulated time.
    """
    if timeout_ns is None:
        expected_total_ns = (min_edges / carrier_hz) * 1e9
        timeout_ns = max(50_000.0, expected_total_ns * 8)

    timeout_cycles = _default_timeout_cycles(clk_hz, carrier_hz)

    start_ns = get_sim_time("ns")

    await _edge_or_timeout(dut_signal, RisingEdge, clk, timeout_cycles)
    t_prev = get_sim_time("ns")

    samples = []
    while True:
        await _edge_or_timeout(dut_signal, FallingEdge, clk, timeout_cycles)
        t_fall = get_sim_time("ns")
        await _edge_or_timeout(dut_signal, RisingEdge, clk, timeout_cycles)
        t_rise = get_sim_time("ns")

        period_ns = t_rise - t_prev
        high_ns = t_fall - t_prev
        samples.append((high_ns / period_ns) if period_ns else 0.0)
        t_prev = t_rise

        if len(samples) >= min_edges:
            freq = _fundamental_from_zero_crossings(samples, carrier_hz)
            if freq is not None:
                return freq

        if (get_sim_time("ns") - start_ns) > timeout_ns:
            raise TimeoutError(
                f"measure_tone_freq: no stable fundamental found on "
                f"{_signal_path(dut_signal)} within {timeout_ns:.0f} ns of "
                f"simulated time ({len(samples)} carrier-period samples "
                "collected). If this pin is expected to be silent/flat, "
                "this is the expected outcome -- catch TimeoutError."
            )
