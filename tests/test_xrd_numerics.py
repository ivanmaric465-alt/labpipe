"""Numerical checks with known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from labpipe.methods.xrd import fitting, lattice, peaks, sizing
from tests.conftest import LAMBDA1, LAMBDA2, fcc_positions


def test_kalpha2_position_matches_bragg():
    two_theta = 40.0
    d = LAMBDA1 / (2 * math.sin(math.radians(two_theta / 2)))
    expected = math.degrees(2 * math.asin(LAMBDA2 / (2 * d)))
    assert peaks.kalpha2_position(two_theta, LAMBDA1, LAMBDA2) == pytest.approx(expected, abs=1e-10)
    assert float(fitting.kalpha2_center(two_theta, LAMBDA2 / LAMBDA1)) == pytest.approx(
        expected, abs=1e-10)


def test_pseudo_voigt_is_area_normalised():
    x = np.linspace(-200, 200, 400001)
    for eta in (0.0, 0.5, 1.0):
        area = np.trapezoid(fitting.pseudo_voigt(x, 0.0, 3.0, 0.8, eta), x)
        # Lorentzian tails beyond ±200 FWHM hold ~0.16 % of the area
        assert area == pytest.approx(3.0, rel=2e-3)


@pytest.mark.parametrize("broad_eta_free", [False, True])
def test_analytic_jacobian_matches_finite_differences(broad_eta_free):
    x = np.linspace(20, 90, 3000)
    layout = fitting.Layout(3, (20, 90), 1, 2, LAMBDA2 / LAMBDA1, 0.5, broad_eta_free)
    broad = [25., 30000., 6., 0.3] if broad_eta_free else [25., 30000., 6.]
    p = np.array([1000., -50., 20., 5., *broad,
                  40.2, 5000., 1.2, 0.6, 68.1, 800., 0.11, 0.4])
    _, jac = fitting.model_and_jacobian(p, x, layout)
    for i in range(p.size):
        h = 1e-6 * max(1.0, abs(p[i]))
        up, down = p.copy(), p.copy()
        up[i] += h
        down[i] -= h
        numeric = (fitting.model(up, x, layout) - fitting.model(down, x, layout)) / (2 * h)
        scale = max(np.abs(numeric).max(), 1e-12)
        assert np.abs(jac[:, i] - numeric).max() / scale < 1e-5, f"parameter {i}"


def test_scherrer_known_value():
    peak = {"id": "P01", "two_theta_deg": 40.0, "fwhm_deg": 1.0, "fwhm_sigma_deg": 0.01,
            "fwhm_weight_sigma_deg": 0.01}
    rows = sizing.scherrer([{"peak": "P01", "hkl": [1, 1, 1]}], {"P01": peak},
                           wavelength=LAMBDA1, k=0.9, calibration=None)
    expected_nm = 0.9 * LAMBDA1 / (math.radians(1.0) * math.cos(math.radians(20.0))) / 10
    assert rows[0]["size_nm"] == pytest.approx(expected_nm, rel=1e-12)
    assert rows[0]["instrument_correction"] == "none"


def test_williamson_hall_recovers_size_and_strain():
    size_nm, strain = 8.0, 0.002
    rows = []
    for index, two_theta in enumerate(fcc_positions(3.9231)):
        theta = math.radians(two_theta / 2)
        beta = (0.9 * LAMBDA1 / (size_nm * 10) + 4 * strain * math.sin(theta)) / math.cos(theta)
        rows.append({"peak": f"P{index}", "hkl": [1, 1, 1], "two_theta_deg": two_theta,
                     "fwhm_sample_deg": math.degrees(beta), "fwhm_observed_sigma_deg": 0.01,
                     "fwhm_weight_sigma_deg": 0.01})
    result = sizing.williamson_hall(rows, wavelength=LAMBDA1, k=0.9)
    assert result["size_nm"] == pytest.approx(size_nm, rel=1e-6)
    assert result["strain"] == pytest.approx(strain, rel=1e-6)


def test_fcc_indexing_recovers_lattice_parameter():
    a = 3.8898
    found = [{"id": f"P{i}", "two_theta_deg": t, "two_theta_weight_sigma_deg": 0.002,
              "area_counts_deg": 1000.0 / (i + 1), "fwhm_deg": 1.0}
             for i, t in enumerate(fcc_positions(a))]
    phases = lattice.index_fcc(found, LAMBDA1, 10, 90, tolerance_deg=0.15, min_reflections=3,
                               max_phases=2, max_seeds=4)
    assert len(phases) == 1
    assert phases[0]["tentative"] is False
    assert phases[0]["a_angstrom"] == pytest.approx(a, abs=1e-6)
    assert [row["hkl"] for row in phases[0]["reflections"]] == [[1, 1, 1], [2, 0, 0],
                                                                [2, 2, 0], [3, 1, 1]]


def test_whole_pattern_fit_recovers_positions(pd_like_pattern):
    x, y, positions, _ = pd_like_pattern
    step = float(np.median(np.diff(x)))
    from labpipe.methods.xrd import background as bg
    start = bg.initial_estimate(x, y, order=2, clip_sigma=2.0)
    candidates = peaks.find_candidates(
        x, y, start(x), step=step, lambda1=LAMBDA1, lambda2=LAMBDA2, smoothing_window_deg=0.08,
        min_significance=5, min_separation_deg=0.12, min_fwhm_deg=0.03, max_fwhm_deg=4.0)
    fit = fitting.fit_with_pruning(
        x, y, min_area_significance=3.0, background_coefficients=start.coefficients,
        domain=start.domain, broad=[], narrow=candidates, ratio_lambda=LAMBDA2 / LAMBDA1,
        ratio_intensity=0.5, narrow_fwhm_range=(2 * step, 4.0), broad_fwhm_range=(5.0, 20.0),
        narrow_freedom_deg=0.15, broad_freedom_deg=3.0, max_nfev=20000)
    fitted = sorted(p["two_theta_deg"] for p in fit["peaks"])
    assert len(fitted) == 4
    for got, expected in zip(fitted, positions):
        assert got == pytest.approx(expected, abs=0.01)


def test_two_reflection_phase_is_only_tentative():
    """(111)+(200) alone: accepted only with min_reflections_tentative, and flagged."""
    a = 3.9
    found = [{"id": f"P{i}", "two_theta_deg": t, "two_theta_weight_sigma_deg": 0.01,
              "area_counts_deg": 100.0 - i, "fwhm_deg": 1.5}
             for i, t in enumerate(fcc_positions(a, hkls=((1, 1, 1), (2, 0, 0))))]
    strict = lattice.index_fcc(found, LAMBDA1, 10, 60, tolerance_deg=0.15, min_reflections=3,
                               max_phases=2, max_seeds=4)
    assert strict == []
    lenient = lattice.index_fcc(found, LAMBDA1, 10, 60, tolerance_deg=0.15, min_reflections=3,
                                min_reflections_tentative=2, max_phases=2, max_seeds=4)
    assert len(lenient) == 1 and lenient[0]["tentative"] is True
    assert lenient[0]["a_angstrom"] == pytest.approx(a, abs=1e-6)


def test_anchor_ranges_hold_background_on_the_data():
    """A very wide hump can masquerade as background; anchors put the background back."""
    from labpipe.methods.xrd import background as bg
    x = np.arange(10.0, 70.0, 0.02)
    true_background = 800.0 - 5.0 * (x - 10.0)
    hump = 3000.0 * np.exp(-4 * math.log(2) * ((x - 40.0) / 8.0) ** 2)
    y = np.random.default_rng(3).poisson(true_background + hump).astype(float)
    anchors = bg.anchor_mask(x, [[10, 18], [62, 70]])
    start = bg.initial_estimate(x, y, order=2, clip_sigma=2.0, anchors=anchors)
    fit = fitting.fit_pattern(
        x, y, background_coefficients=start.coefficients, domain=start.domain,
        broad=[{"position": 40.0, "net_height": 3000.0, "fwhm_guess": 8.0}], narrow=[],
        ratio_lambda=None, ratio_intensity=0.0, narrow_fwhm_range=(0.04, 4.0),
        broad_fwhm_range=(5.0, 10.0), narrow_freedom_deg=0.15, broad_freedom_deg=3.0,
        max_nfev=5000, anchors=anchors)
    background = fitting.background_curve(fit["params"], x, fit["layout"])
    assert np.abs(background[anchors] - true_background[anchors]).mean() < 15.0
