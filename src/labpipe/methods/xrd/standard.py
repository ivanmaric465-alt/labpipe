"""Internal standard detection and 2θ zero correction.

If the sample contains the internal standard (e.g. Si powder), its fitted
peaks are compared with positions calculated from the standard's lattice
parameter. Two correction models are fitted to Δ2θ = observed − calculated:

* constant:      Δ2θ = z
* displacement:  Δ2θ = z + D·cos θ   (Bragg-Brentano sample-height error)

The constant offset is searched first on a grid (robust against overlaps
with sample peaks), then lines are matched within a tight tolerance.
Peaks that also coincide with a sample reflection are reported as
ambiguous by the caller and are not used here.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from labpipe.methods.xrd.references import Reference, predicted_lines


def find_standard(reference: Reference, peaks: list[dict[str, Any]], wavelength: float,
                  low: float, high: float, *, lattice_a: float | None,
                  search_range_deg: float, search_step_deg: float, tolerance_deg: float,
                  min_intensity: float, min_lines: int) -> dict[str, Any] | None:
    scale = (lattice_a / reference.cell["a"]) if lattice_a else 1.0
    lines = predicted_lines(reference, wavelength, low, high, min_intensity, d_scale=scale)
    if len(lines) < min_lines:
        return None
    positions = np.array([peak["two_theta_deg"] for peak in peaks])
    best_offset, best_count, best_rms = 0.0, 0, math.inf
    for offset in np.arange(-search_range_deg, search_range_deg + 1e-12, search_step_deg):
        deltas = [np.min(np.abs(positions - offset - line["two_theta_deg"]))
                  for line in lines]
        hits = [delta for delta in deltas if delta <= tolerance_deg]
        rms = math.sqrt(sum(h * h for h in hits) / len(hits)) if hits else math.inf
        if len(hits) > best_count or (len(hits) == best_count and rms < best_rms):
            best_offset, best_count, best_rms = float(offset), len(hits), rms
    if best_count < min_lines:
        return None

    matched = []
    for line in lines:
        index = int(np.argmin(np.abs(positions - best_offset - line["two_theta_deg"])))
        if abs(positions[index] - best_offset - line["two_theta_deg"]) <= tolerance_deg:
            peak = peaks[index]
            matched.append({
                "hkl": line["hkl"], "peak": peak["id"],
                "two_theta_calc_deg": line["two_theta_deg"],
                "two_theta_obs_deg": peak["two_theta_deg"],
                "two_theta_obs_sigma_deg": peak["two_theta_weight_sigma_deg"],
                "delta_two_theta_deg": peak["two_theta_deg"] - line["two_theta_deg"],
                "intensity_rel": line["intensity_rel"],
            })
    return {
        "phase": reference.id, "cod_id": reference.source["cod_id"],
        "lattice_a_angstrom": lattice_a or reference.cell["a"],
        "lattice_a_source": "config override" if lattice_a else "reference CIF",
        "lines_in_range": len(lines), "grid_offset_deg": best_offset,
        "matched": matched,
    }


def fit_corrections(matched: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = np.array([item["delta_two_theta_deg"] for item in matched])
    sigmas = np.array([max(item["two_theta_obs_sigma_deg"], 1e-4) for item in matched])
    cos_theta = np.cos(np.radians([item["two_theta_calc_deg"] for item in matched]) / 2.0)
    weights = 1.0 / sigmas ** 2
    result: dict[str, Any] = {"n_lines": int(deltas.size)}

    z = float(np.sum(weights * deltas) / np.sum(weights))
    z_sigma_stat = float(math.sqrt(1.0 / np.sum(weights)))
    scatter = float(np.std(deltas, ddof=1)) if deltas.size > 1 else None
    result["constant"] = {
        "z_deg": z, "z_sigma_stat_deg": z_sigma_stat,
        "residual_scatter_deg": scatter,
        "model": "Δ2θ = z",
    }
    if deltas.size >= 3:
        design = np.column_stack([np.ones_like(cos_theta), cos_theta])
        w_design = design * np.sqrt(weights)[:, None]
        w_delta = deltas * np.sqrt(weights)
        coefficients, *_ = np.linalg.lstsq(w_design, w_delta, rcond=None)
        residuals = deltas - design @ coefficients
        dof = deltas.size - 2
        chi2_red = float(np.sum(weights * residuals ** 2) / dof) if dof > 0 else None
        covariance = np.linalg.inv(w_design.T @ w_design) * max(1.0, chi2_red or 1.0)
        errors = np.sqrt(np.diag(covariance))
        result["displacement"] = {
            "z_deg": float(coefficients[0]), "z_sigma_deg": float(errors[0]),
            "D_deg": float(coefficients[1]), "D_sigma_deg": float(errors[1]),
            "correlation_z_D": float(covariance[0, 1] / (errors[0] * errors[1])),
            "chi2_reduced": chi2_red, "dof": dof,
            "cos_theta_range": [float(cos_theta.min()), float(cos_theta.max())],
            "model": "Δ2θ = z + D·cos θ",
        }
    return result


def correction_function(corrections: dict[str, Any], model: str):
    if model == "displacement":
        z = corrections["displacement"]["z_deg"]
        d = corrections["displacement"]["D_deg"]
        return lambda two_theta: z + d * math.cos(math.radians(two_theta) / 2.0)
    if model == "constant":
        z = corrections["constant"]["z_deg"]
        return lambda two_theta: z
    return lambda two_theta: 0.0
