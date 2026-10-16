"""Crystallite size (Scherrer) and size/strain (Williamson-Hall).

Breadth: FWHM of the Kα1 component of the fitted pseudo-Voigt doublet, in
radians of 2θ. Scherrer: D = K·λ / (β·cos θ). Williamson-Hall (uniform
deformation model): β·cos θ = K·λ/D + 4·ε·sin θ.

Instrument broadening: if a calibration (Caglioti U, V, W from the Si
standard) is supplied, β_inst² = U·tan²θ + V·tanθ + W (degrees²) and the
sample breadth is β = sqrt(β_obs² − β_inst²) (Gaussian-type quadratic
subtraction). Without a calibration no correction is applied and every
size is a lower bound of the true size.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def instrument_fwhm_deg(two_theta: float, calibration: dict[str, Any]) -> float:
    tan_theta = math.tan(math.radians(two_theta) / 2.0)
    value = calibration["U"] * tan_theta ** 2 + calibration["V"] * tan_theta + calibration["W"]
    return math.sqrt(max(value, 0.0))


def _sample_breadth(peak: dict[str, Any], calibration: dict[str, Any] | None):
    observed = peak["fwhm_deg"]
    if calibration is None:
        return observed, None, "none"
    instrument = instrument_fwhm_deg(peak["two_theta_deg"], calibration)
    if observed <= instrument:
        return None, instrument, "quadratic (observed ≤ instrumental: not resolvable)"
    return math.sqrt(observed ** 2 - instrument ** 2), instrument, "quadratic"


def scherrer(reflections: list[dict[str, Any]], peaks_by_id: dict[str, dict], *,
             wavelength: float, k: float,
             calibration: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for reflection in reflections:
        peak = peaks_by_id[reflection["peak"]]
        breadth, instrument, method = _sample_breadth(peak, calibration)
        theta = math.radians(peak["two_theta_deg"]) / 2.0
        row = {
            "peak": peak["id"], "hkl": reflection["hkl"],
            "two_theta_deg": peak["two_theta_deg"],
            "fwhm_observed_deg": peak["fwhm_deg"],
            "fwhm_observed_sigma_deg": peak["fwhm_sigma_deg"],
            "fwhm_weight_sigma_deg": peak["fwhm_weight_sigma_deg"],
            "fwhm_instrument_deg": instrument,
            "fwhm_sample_deg": breadth,
            "instrument_correction": method,
        }
        if breadth is not None and breadth > 0:
            beta = math.radians(breadth)
            size_nm = k * wavelength / (beta * math.cos(theta)) / 10.0
            relative = (peak["fwhm_sigma_deg"] / peak["fwhm_deg"]
                        if peak["fwhm_deg"] and peak["fwhm_sigma_deg"] is not None else None)
            row["size_nm"] = size_nm
            row["size_sigma_nm_from_fwhm"] = (size_nm * relative if relative is not None
                                              and calibration is None else None)
        else:
            row["size_nm"] = None
        rows.append(row)
    return rows


def williamson_hall(scherrer_rows: list[dict[str, Any]], *, wavelength: float,
                    k: float) -> dict[str, Any]:
    usable = [row for row in scherrer_rows if row.get("fwhm_sample_deg")]
    result: dict[str, Any] = {
        "model": "β·cosθ = K·λ/D + 4·ε·sinθ (β = FWHM, rad)",
        "n_reflections": len(usable),
        "reflections": [row["peak"] for row in usable],
    }
    if len(usable) < 3:
        result["status"] = "not computed: fewer than 3 reflections"
        return result
    theta = np.radians([row["two_theta_deg"] for row in usable]) / 2.0
    beta = np.radians([row["fwhm_sample_deg"] for row in usable])
    beta_sigma = np.radians([max(row["fwhm_weight_sigma_deg"], 1e-6) for row in usable])
    x = 4.0 * np.sin(theta)
    y = beta * np.cos(theta)
    y_sigma = beta_sigma * np.cos(theta)
    weights = 1.0 / y_sigma ** 2
    design = np.column_stack([np.ones_like(x), x])
    w_design = design * np.sqrt(weights)[:, None]
    coefficients, *_ = np.linalg.lstsq(w_design, y * np.sqrt(weights), rcond=None)
    intercept, slope = (float(value) for value in coefficients)
    fitted = design @ coefficients
    dof = len(usable) - 2
    chi2_red = float(np.sum(weights * (y - fitted) ** 2) / dof) if dof > 0 else None
    covariance = np.linalg.inv(w_design.T @ w_design) * max(1.0, chi2_red or 1.0)
    errors = np.sqrt(np.diag(covariance))
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    result.update({
        "status": "computed",
        "intercept": intercept, "intercept_sigma": float(errors[0]),
        "slope_strain": slope, "slope_sigma": float(errors[1]),
        "size_nm": (k * wavelength / intercept / 10.0) if intercept > 0 else None,
        "strain": slope, "strain_percent": slope * 100.0,
        "r_squared": (1.0 - ss_res / ss_tot) if ss_tot > 0 else None,
        "chi2_reduced": chi2_red, "dof": dof,
        "points": [{"peak": row["peak"], "hkl": row["hkl"], "x_4sin_theta": float(xi),
                    "y_beta_cos_theta": float(yi)}
                   for row, xi, yi in zip(usable, x, y)],
    })
    return result
