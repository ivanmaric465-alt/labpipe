"""fcc indexing and cubic lattice-parameter refinement.

Face-centred cubic lattices (fcc metals and alloys, rock-salt compounds such
as TiN/TiO share the same reflection conditions) are indexed. Other lattices
are covered by reference matching only. Peaks not explained by an indexed
phase, the internal standard or a reference candidate are reported as
unassigned.

Algorithm, per phase:
1. Seed: each of the strongest unused peaks is tried as (111) and as (200).
2. Predict fcc reflections inside the measured range, match the nearest peak
   within max(``tolerance_deg``, ``fwhm_fraction``·FWHM of that peak), so broad
   nanoparticle reflections get a proportionally wider window; refine a and
   re-match until the set is stable.
3. Keep the hypothesis with the most matched reflections (ties: lowest
   weighted rms). Accept it if it has ≥ ``min_reflections``; a phase with
   only (111) and (200) (``min_reflections_tentative`` = 2) is accepted as
   tentative and flagged, which covers broad nanoparticle patterns where
   (220) is lost in the background.
4. Remove its peaks and repeat for further phases (``max_phases``).

Reflections of one phase must have comparable widths: a peak whose FWHM
differs from the seed's by more than ``max_fwhm_ratio`` is not matched (size
broadening grows only as 1/cos θ). This keeps sharp support lines and broad
nanoparticle lines in separate phases, and keeps broad humps out entirely.

a is the inverse-variance weighted mean of a_i = d_i·√(h²+k²+l²).
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from labpipe.methods.xrd.references import bragg_two_theta

FCC_HKL = [(1, 1, 1), (2, 0, 0), (2, 2, 0), (3, 1, 1), (2, 2, 2), (4, 0, 0), (3, 3, 1),
           (4, 2, 0), (4, 2, 2), (5, 1, 1), (4, 4, 0), (5, 3, 1), (6, 0, 0)]


def d_from_two_theta(two_theta: float, wavelength: float) -> float:
    return wavelength / (2.0 * math.sin(math.radians(two_theta) / 2.0))


def d_sigma(two_theta: float, sigma_two_theta: float, wavelength: float) -> float:
    theta = math.radians(two_theta) / 2.0
    return d_from_two_theta(two_theta, wavelength) / math.tan(theta) * math.radians(
        sigma_two_theta) / 2.0


def refine(assigned: list[tuple[dict, tuple[int, int, int]]], wavelength: float,
            correction: Callable[[float], float]) -> dict[str, Any]:
    a_values, a_sigmas, rows = [], [], []
    for peak, hkl in assigned:
        corrected = peak["two_theta_deg"] - correction(peak["two_theta_deg"])
        n = hkl[0] ** 2 + hkl[1] ** 2 + hkl[2] ** 2
        d = d_from_two_theta(corrected, wavelength)
        sd = d_sigma(corrected, max(peak["two_theta_weight_sigma_deg"], 1e-5), wavelength)
        a_i = d * math.sqrt(n)
        a_values.append(a_i)
        a_sigmas.append(sd * math.sqrt(n))
        rows.append({"peak": peak["id"], "hkl": list(hkl), "two_theta_used_deg": corrected,
                     "d_angstrom": d, "a_i_angstrom": a_i})
    a_values_arr, a_sigmas_arr = np.array(a_values), np.array(a_sigmas)
    weights = 1.0 / a_sigmas_arr ** 2
    a = float(np.sum(weights * a_values_arr) / np.sum(weights))
    result = {
        "a_angstrom": a,
        "a_sigma_stat_angstrom": float(math.sqrt(1.0 / np.sum(weights))),
        "a_scatter_angstrom": (float(np.std(a_values_arr, ddof=1)) if a_values_arr.size > 1
                               else None),
        "reflections": rows,
    }
    for row in rows:
        row["a_i_minus_a_angstrom"] = row["a_i_angstrom"] - a
    if a_values_arr.size >= 4:
        theta = np.radians([row["two_theta_used_deg"] for row in rows]) / 2.0
        nr = np.cos(theta) ** 2 / np.sin(theta) + np.cos(theta) ** 2 / theta
        slope, intercept = np.polyfit(nr, a_values_arr, 1, w=np.sqrt(weights))
        result["nelson_riley"] = {
            "a0_angstrom": float(intercept), "slope": float(slope),
            "n": int(a_values_arr.size),
            "note": "extrapolation of a_i vs cos²θ/sinθ + cos²θ/θ; diagnostic only",
        }
    return result


def _match(peaks: list[dict], a: float, wavelength: float, low: float, high: float,
           tolerance: float, correction: Callable[[float], float],
           fwhm_fraction: float = 0.0, seed_fwhm: float | None = None,
           max_fwhm_ratio: float | None = None):
    assigned, used = [], set()
    for hkl in FCC_HKL:
        n = hkl[0] ** 2 + hkl[1] ** 2 + hkl[2] ** 2
        predicted = bragg_two_theta(a / math.sqrt(n), wavelength)
        if predicted is None or not low <= predicted <= high:
            continue
        best = None
        for peak in peaks:
            if peak["id"] in used:
                continue
            delta = (peak["two_theta_deg"] - correction(peak["two_theta_deg"])) - predicted
            if seed_fwhm and max_fwhm_ratio and not (
                    1.0 / max_fwhm_ratio <= peak["fwhm_deg"] / seed_fwhm <= max_fwhm_ratio):
                continue  # reflections of one phase have comparable widths
            allowed = max(tolerance, fwhm_fraction * peak["fwhm_deg"])
            if abs(delta) <= allowed and (best is None or abs(delta) < abs(best[1])):
                best = (peak, delta)
        if best is not None:
            assigned.append((best[0], hkl))
            used.add(best[0]["id"])
    return assigned


def predicted_in_range(a: float, wavelength: float, low: float, high: float) -> list[dict]:
    lines = []
    for hkl in FCC_HKL:
        n = hkl[0] ** 2 + hkl[1] ** 2 + hkl[2] ** 2
        predicted = bragg_two_theta(a / math.sqrt(n), wavelength)
        if predicted is not None and low <= predicted <= high:
            lines.append({"hkl": list(hkl), "two_theta_deg": predicted})
    return lines


def index_fcc(peaks: list[dict[str, Any]], wavelength: float, low: float, high: float, *,
              tolerance_deg: float, min_reflections: int, max_phases: int, max_seeds: int,
              min_reflections_tentative: int | None = None, fwhm_fraction: float = 0.0,
              max_fwhm_ratio: float | None = None,
              correction: Callable[[float], float] = lambda t: 0.0) -> list[dict[str, Any]]:
    available = list(peaks)
    phases = []
    for phase_number in range(1, max_phases + 1):
        seeds = sorted(available, key=lambda p: -p["area_counts_deg"])[:max_seeds]
        best = None
        for seed in seeds:
            corrected = seed["two_theta_deg"] - correction(seed["two_theta_deg"])
            d_seed = d_from_two_theta(corrected, wavelength)
            for seed_hkl in ((1, 1, 1), (2, 0, 0)):
                a = d_seed * math.sqrt(sum(i * i for i in seed_hkl))
                assigned = []
                for _ in range(10):
                    assigned = _match(available, a, wavelength, low, high, tolerance_deg,
                                      correction, fwhm_fraction, seed["fwhm_deg"],
                                      max_fwhm_ratio)
                    if len(assigned) < 2:
                        break
                    new_a = refine(assigned, wavelength, correction)["a_angstrom"]
                    if abs(new_a - a) < 1e-7:
                        break
                    a = new_a
                if not any(peak["id"] == seed["id"] for peak, _ in assigned):
                    continue
                if len(assigned) < 2:
                    continue
                refined = refine(assigned, wavelength, correction)
                rms = math.sqrt(sum(r["a_i_minus_a_angstrom"] ** 2
                                    for r in refined["reflections"]) / len(assigned))
                key = (len(assigned), -rms)
                if best is None or key > best[0]:
                    best = (key, assigned, refined, seed, seed_hkl)
        if best is None:
            break
        tentative = len(best[1]) < min_reflections
        if tentative and not (
                min_reflections_tentative and len(best[1]) >= min_reflections_tentative
                and {hkl for _, hkl in best[1]} == {(1, 1, 1), (2, 0, 0)}):
            break
        _, assigned, refined, seed, seed_hkl = best
        expected = predicted_in_range(refined["a_angstrom"], wavelength, low, high)
        matched_hkl = {tuple(row["hkl"]) for row in refined["reflections"]}
        refined.update({
            "id": f"fcc-{phase_number}",
            "structure_type": "fcc",
            "n_reflections": len(assigned),
            "tentative": tentative,
            "expected_reflections_in_range": expected,
            "missing_reflections": [line for line in expected
                                    if tuple(line["hkl"]) not in matched_hkl],
            "seed": {"peak": seed["id"], "as_hkl": list(seed_hkl)},
            "tolerance_deg": tolerance_deg,
            "tolerance_fwhm_fraction": fwhm_fraction,
        })
        phases.append(refined)
        used = {peak["id"] for peak, _ in assigned}
        available = [peak for peak in available if peak["id"] not in used]
    return phases
