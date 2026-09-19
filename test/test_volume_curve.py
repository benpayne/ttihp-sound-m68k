# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""
Volume curve shape tests for the PSG reference model (audit gap #4).

Covers FR-023: "Each of the four channels MUST have a 16-level volume,
where level 0 is silent and successive levels are spaced as approximately
equal loudness steps (logarithmic, as on the AY-3-8910)."

Pure Python, no simulator: this only imports `AMP` from
`tbutil.psg_model` and checks its SHAPE with plain arithmetic. Before this
file, the only check on `AMP` was psg_model.py's own self-check (distinct
values) and test_tone_pwm.test_volume_curve, which only proves
monotonic non-increase at PWM's reduced 7-bit resolution -- neither
establishes that the curve is actually *logarithmic* (as opposed to, say,
linear, or any other strictly increasing sequence), and the AMP values
themselves were hand-copied from research.md's stated derivation rather
than checked against the shape research.md claims for them.

Run directly with plain pytest (no simulator, no Makefile, no cocotb):

    cd test && venv/bin/python -m pytest test_volume_curve.py -v

Tolerance derivation
---------------------
A perfect geometric/log curve would have every step be exactly the same
number of dB. `AMP` is a small table of ROUNDED INTEGERS (research.md R4:
"scaled to 255"), so individual per-step ratios inherently wobble -- e.g.
level 1->2 is 3->4, a 33% jump, simply because there is no integer between
3 and 4 to round to instead. A rigid per-step dB tolerance would either be
loose enough to hide a real problem or tight enough to fail on this
rounding noise alone, which is exactly the trap the task brief warns
against.

Instead this file fits AMP[1..15] (level 0 excluded: it's silence, not
part of the log curve) to a log-linear model `ln(AMP[level]) = a*level +
b` by least squares, then asserts:
  1. All 15 non-zero levels are distinct (level 0 aside).
  2. Strictly increasing (a real bug -- levels swapped or reversed --
     would violate this even though "distinct" alone would not).
  3. The fit is a good one: R^2 >= 0.95. This is the primary "is it really
     logarithmic" check -- a linear (non-log) curve of the same span would
     fit far worse. Measured: R^2 ~= 0.992.
  4. No single level deviates from the fitted line by more than 2.5 dB.
     Measured max deviation is ~2.04 dB (at level 15); 2.5 dB is a
     deliberately generous but bounded margin above that observed noise
     floor -- enough to tolerate this table's integer rounding without
     being loose enough to pass a curve that isn't actually log-shaped.
  5. Total span (level 1 to level 15) is roughly the AY-3-8910's ~40 dB:
     asserted within [30, 46] dB. Measured: ~38.6 dB.
"""

import math

from tbutil.psg_model import AMP


def _fit_log_linear(levels, amps):
    """Least-squares fit of ln(amps) = a*levels + b. Returns (slope,
    intercept, r_squared, residuals_db) where residuals_db[i] is level
    `levels[i]`'s deviation from the fitted line, converted from natural
    log to dB (`20/ln(10)` per neper)."""
    n = len(levels)
    ys = [math.log(a) for a in amps]
    mean_x = sum(levels) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(levels, ys))
    var_x = sum((x - mean_x) ** 2 for x in levels)
    slope = cov / var_x
    intercept = mean_y - slope * mean_x

    fitted = [slope * x + intercept for x in levels]
    resid_ln = [y - f for y, f in zip(ys, fitted)]
    ss_res = sum(r**2 for r in resid_ln)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1 - ss_res / ss_tot

    db_per_neper = 20.0 / math.log(10)
    residuals_db = [r * db_per_neper for r in resid_ln]
    return slope, intercept, r_squared, residuals_db


def test_amp_table_shape_sanity():
    """FR-023: exactly 16 entries, level 0 is silence."""
    assert len(AMP) == 16, f"AMP has {len(AMP)} entries, expected 16 (level 0..15)"
    assert AMP[0] == 0, f"AMP[0] (silence) is {AMP[0]}, expected exactly 0"


def test_nonzero_levels_distinct_and_increasing():
    """FR-023: "level 0 is silent" implies levels 1-15 are the actual
    volume steps; all 15 must be distinct AND strictly increasing (a
    reversed/swapped table would still pass a distinctness-only check)."""
    nonzero = AMP[1:]
    assert len(nonzero) == 15
    assert len(set(nonzero)) == 15, f"AMP has duplicate non-zero entries: {nonzero}"
    for i in range(1, len(nonzero)):
        assert nonzero[i] > nonzero[i - 1], (
            f"AMP is not strictly increasing at level {i + 1}: {nonzero[i - 1]} -> {nonzero[i]} "
            f"(full non-zero table: {nonzero})"
        )


def test_curve_is_logarithmic():
    """FR-023: "logarithmic, as on the AY-3-8910" -- fit levels 1-15 to a
    log-linear model and require a good fit (R^2) with no single level
    deviating too far from it. See module docstring for the tolerance
    derivation (2.5 dB bound, ~2.04 dB observed max)."""
    levels = list(range(1, 16))
    amps = AMP[1:]

    slope, intercept, r_squared, residuals_db = _fit_log_linear(levels, amps)
    db_per_level = slope * 20.0 / math.log(10)

    assert r_squared >= 0.95, (
        f"AMP[1:] fits a log-linear (constant-dB-per-step) model with R^2={r_squared:.4f}, "
        f"below the 0.95 threshold -- this table does not look logarithmic. Fitted slope "
        f"corresponds to {db_per_level:.3f} dB/level. AMP={AMP}"
    )

    max_resid = max(abs(r) for r in residuals_db)
    assert max_resid <= 2.5, (
        f"level {residuals_db.index(max(residuals_db, key=abs)) + 1} deviates "
        f"{max_resid:.2f} dB from the fitted log-linear curve ({db_per_level:.3f} dB/level), "
        f"exceeding the 2.5 dB bound (observed max in the validated table is ~2.04 dB; this "
        f"table has drifted further from log-shaped than that). Residuals by level "
        f"(1-indexed): {[round(r, 2) for r in residuals_db]}"
    )


def test_total_span_matches_ay3_8910():
    """FR-023 / research.md R4: "derived from the measured AY-3-8910 DAC
    curve." The AY-3-8910's usable dynamic range is roughly 40 dB;
    asserted within [30, 46] dB to allow for this table's 8-bit
    quantization and the "approximately" in FR-023's wording. Measured:
    ~38.6 dB.
    """
    nonzero = AMP[1:]
    span_db = 20.0 * math.log10(nonzero[-1] / nonzero[0])
    assert 30.0 <= span_db <= 46.0, (
        f"AMP[1] (quietest non-zero level) to AMP[15] (loudest) spans {span_db:.2f} dB, "
        f"expected roughly the AY-3-8910's ~40 dB (checked as [30, 46] dB). "
        f"AMP[1]={nonzero[0]}, AMP[15]={nonzero[-1]}"
    )


if __name__ == "__main__":
    test_amp_table_shape_sanity()
    test_nonzero_levels_distinct_and_increasing()
    test_curve_is_logarithmic()
    test_total_span_matches_ay3_8910()
    print("All test_volume_curve.py checks passed.")
