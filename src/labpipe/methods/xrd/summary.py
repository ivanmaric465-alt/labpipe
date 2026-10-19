"""Compact, human-readable summary of an XRD results.json (deterministic).

Used by `labpipe report` (the HTML report) and `labpipe xrd summary --entry ID`
(what agents read before writing notes). Every number here is formatted by code
from results.json; values with an uncertainty are rounded to the uncertainty
(2 significant digits of σ).
"""

from __future__ import annotations

import math
from typing import Any


def with_sigma(value: float | None, sigma: float | None, unit: str = "") -> str:
    if value is None:
        return "–"
    if not sigma:
        return f"{value:.4g}{unit}"
    decimals = max(0, 1 - int(math.floor(math.log10(abs(sigma)))))
    return f"{value:.{decimals}f} ± {sigma:.{decimals}f}{unit}"


def _hkl(hkl) -> str:
    return "(" + "".join(str(i) for i in hkl) + ")"


def _deg(value: float) -> str:
    return f"{value:.2f}°"


def plain_check(check: dict[str, Any], peaks: dict[str, dict], results: dict) -> str:
    """One plain-language sentence for a check (falls back to the code's message)."""
    code = check["code"]
    peak = peaks.get(check.get("peak") or "")
    where = _deg(peak["two_theta_deg"]) if peak else ""
    phases = {p["id"]: p for p in results["phases"]["fcc_indexed"]}
    if code == "tentative_phase":
        phase = phases.get(check.get("phase"), {})
        return (f"Phase {check.get('phase')} rests on only {phase.get('n_reflections', '?')} "
                "reflections, so it is tentative.")
    if code == "wh_unreliable":
        return ("Williamson–Hall gives no meaningful size or strain here; use the Scherrer "
                "sizes (lower bounds).")
    if code == "unfitted_signal":
        lo, hi = check["region"]
        return f"Unexplained signal at {lo:.1f}–{hi:.1f}°: a missed peak or a background shape problem."
    if code == "unfitted_signal_more":
        return "Further regions of unexplained signal (see results.json → fit.residual_regions)."
    if code == "unassigned_peaks":
        spots = ", ".join(_deg(peaks[p]["two_theta_deg"]) for p in check["peaks"] if p in peaks)
        return f"Peak(s) at {spots} not explained by any phase or the internal standard."
    if code == "poor_local_fit":
        return f"The peak at {where} is poorly described by the fit."
    if code == "parameter_at_bound":
        limits = ", ".join(name for name in peak["at_bounds"] if name != "eta") if peak else ""
        return f"Peak at {where}: fitted {limits} hit its limit, so that value is not a measurement."
    if code == "standard_lattice_parameter_placeholder":
        return ("Si standard: the database lattice parameter is used, not your certificate "
                "value; the zero correction depends on it.")
    if code == "standard_unusable":
        return "Si standard lines found, but none usable for a zero correction."
    if code == "sample_id_mismatch":
        return "The sample ID in the file name differs from the ID stored inside the file."
    if code == "broad_component_at_min_width":
        return "A broad band sits at its minimum width; it may be a crystalline peak instead."
    if code == "high_rwp":
        return "The overall fit is poor (high Rwp)."
    if code == "fit_not_converged":
        return "The fit did not converge; numbers are unreliable."
    if code == "uncertainties_unavailable":
        return "Some uncertainties could not be computed."
    if code == "user_peak_not_significant":
        return f"The peak you asked for at {where} is not significant in the data."
    if code == "multiple_scans":
        return "The file holds several scans; only one was analysed."
    if code == "wavelength_assumed":
        return "The wavelength was not in the file; a default was assumed."
    if code == "no_peaks":
        return "No peaks found."
    if code == "negative_intensity":
        return "The raw data contain negative intensities."
    # warnings
    if code == "no_instrument_correction":
        return "Sizes are lower bounds: no instrument-broadening calibration yet."
    if code == "reference_match_isostructural":
        return (f"The pattern matches pure {check.get('phase')}, but XRD alone cannot tell an "
                "alloy with a similar lattice from the pure element; composition (EDS) needed.")
    if code == "fcc_missing_reflections":
        return "Some expected reflections of an indexed phase have no fitted peak."
    if code == "broad_component_shape":
        return "A support band is not exactly Gaussian (residual under the band)."
    if code == "unfitted_signal_at_scan_edge":
        return "Unexplained signal at a scan edge."
    if code == "local_shape_misfit":
        return "Some peak shapes are slightly misfitted (e.g. asymmetry)."
    if code == "wh_few_points":
        return "Williamson–Hall uses very few reflections; size and strain are weakly separated."
    if code == "background_too_low":
        return "The background sits below the data in peak-free regions."
    if code == "peak_near_scan_edge":
        return f"The peak at {where} is near the scan edge and may be cut off."
    if code == "broad_component_at_bound":
        return "A support band reached its allowed position or width limit."
    if code == "scherrer_above_100nm":
        return "Scherrer sizes above about 100 nm are unreliable."
    return check["message"]


_PER_PEAK = {
    "poor_local_fit": "Peak(s) at {where} are poorly described by the fit.",
    "parameter_at_bound": "Peak(s) at {where}: a fitted width or position hit its limit, so "
                          "that value is not a measurement.",
}


def _grouped(checks: list[dict[str, Any]], peaks: dict[str, dict],
             results: dict) -> list[dict[str, str]]:
    """One line per check code: peaks and regions of the same kind are listed together."""
    out: list[dict[str, str]] = []
    by_code: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        by_code.setdefault(check["code"], []).append(check)
    for code, group in by_code.items():
        if code in _PER_PEAK:
            where = ", ".join(_deg(peaks[c["peak"]]["two_theta_deg"]) for c in group
                              if c.get("peak") in peaks)
            text = _PER_PEAK[code].format(where=where)
        elif code == "unfitted_signal" and len(group) > 1:
            where = ", ".join(f"{c['region'][0]:.1f}–{c['region'][1]:.1f}°" for c in group)
            text = f"Unexplained signal at {where}: missed peaks or a background shape problem."
        else:
            text = " ".join(dict.fromkeys(plain_check(c, peaks, results) for c in group))
        out.append({"code": code, "text": text})
    return out


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    peaks = {p["id"]: p for p in results.get("peaks", [])}
    sizes = {row["phase"]: row for row in results.get("size_strain", [])}
    phases = []
    for phase in results["phases"]["fcc_indexed"]:
        nearest = phase["reference_comparison"][0] if phase["reference_comparison"] else None
        scherrer = sizes.get(phase["id"], {}).get("scherrer", [])
        phases.append({
            "id": phase["id"],
            "lattice_a": with_sigma(phase["a_angstrom"], phase["a_sigma_angstrom"], " Å"),
            "reflections": " ".join(_hkl(r["hkl"]) for r in phase["reflections"]),
            "n_reflections": phase["n_reflections"],
            "tentative": phase["tentative"],
            "closest_reference": (f"{nearest['phase']} ({nearest['delta_a_percent']:+.2f} %)"
                                  if nearest else "–"),
            "scherrer_nm": ", ".join(
                f"{_hkl(row['hkl'])} {row['size_nm']:.1f}" for row in scherrer
                if row.get("size_nm") is not None),
        })
    standard = results.get("internal_standard")
    fit = results["fit"]["global"]
    checks = results.get("checks", [])
    overview = {
        "Phase (a)": "; ".join(f"{p['lattice_a']}" + (" tentative" if p["tentative"] else "")
                               for p in phases) or "no fcc phase",
        "Reflections": "; ".join(str(p["n_reflections"]) for p in phases) or "–",
        "Closest reference (Δa)": "; ".join(p["closest_reference"] for p in phases) or "–",
        "Size, lowest reflection (nm)": "; ".join(p["scherrer_nm"].split(", ")[0]
                                                  for p in phases if p["scherrer_nm"]) or "–",
        "Rwp": f"{100 * fit['rwp']:.1f} %",
    }
    return {
        "overview": overview,
        "sample_id": results.get("sample_id"),
        "measurement_id": results.get("measurement_id"),
        "status": results["status"],
        "phases": phases,
        "reference_candidates": results["phases"]["reference_candidates"],
        "internal_standard": (
            f"{standard['phase']}, {standard['correction_applied']} zero correction"
            + (" (database lattice parameter)" if standard.get("lattice_a_source") == "reference CIF"
               else "") if standard else "none found (no zero correction)"),
        "broad_bands": ", ".join(f"{_deg(b['two_theta_deg'])} (FWHM {b['fwhm_deg']:.1f}°)"
                                 for b in results.get("broad_components", [])) or "none",
        "fit_quality": {"rwp": round(fit["rwp"], 3), "rwp_percent": round(100 * fit["rwp"], 1),
                        "chi2_reduced": round(fit["chi2_reduced"], 2)},
        "unassigned_peaks": [_deg(peaks[p]["two_theta_deg"]) for p in
                             results["phases"]["unassigned_peaks"] if p in peaks],
        "check_before_using": _grouped([c for c in checks if c["severity"] == "review"],
                                       peaks, results),
        "worth_knowing": sorted({plain_check(c, peaks, results) for c in checks
                                 if c["severity"] == "warning"}),
        "steering": [f"{s['action']}: {s['two_theta_deg']}" for s in results.get("steering", [])],
    }


def quantities(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Named numbers for comparing samples (`labpipe dataset`); copied from results.json.

    - `xrd.a.fcc`: lattice parameter of the sample's fcc phase, with its σ, the same name in
      every sample. With several fcc phases, the main one (not tentative, most reflections,
      then the order found) is `fcc` and the others `fcc-2`, `fcc-3`. The note names the
      closest reference ("possibly Pd") as a candidate, never an identification.
    - `xrd.size.fcc`: Scherrer size of the phase's first reflection (as in the overview), σ
      from the peak-width fit only; a lower bound without instrument calibration.
    The internal standard is left out: its lattice parameter is a database or certificate
    value, not a result.
    """
    units = results.get("units") or {}
    out: dict[str, dict[str, Any]] = {}
    sizes = {row["phase"]: (i, row) for i, row in enumerate(results.get("size_strain") or [])}
    uncalibrated = any(c.get("code") == "no_instrument_correction"
                       for c in results.get("checks") or [])
    phases = list(enumerate(results["phases"]["fcc_indexed"]))
    ranked = sorted(phases, key=lambda item: (bool(item[1].get("tentative")),
                                              -(item[1].get("n_reflections") or 0), item[0]))
    for rank, (i, phase) in enumerate(ranked):
        name = "fcc" if rank == 0 else f"fcc-{rank + 1}"
        notes = []
        if len(ranked) > 1:
            notes.append(f"{'main' if rank == 0 else 'other'} of {len(ranked)} fcc phases "
                         f"(phase {phase['id']} in results.json)")
        if phase.get("tentative"):
            notes.append(f"tentative ({phase.get('n_reflections')} reflections)")
        nearest = (phase.get("reference_comparison") or [None])[0]
        if nearest:
            delta = nearest.get("delta_a_percent")
            notes.append(f"possibly {nearest['phase']} (closest reference"
                         + (f", Δa {delta:+.2f} %" if delta is not None else "") + ")")
        entry = {"value": phase["a_angstrom"], "sigma": phase.get("a_sigma_angstrom"),
                 "unit": "Å", "key": f"phases.fcc_indexed[{i}].a_angstrom"}
        if notes:
            entry["note"] = "; ".join(notes)
        out[f"xrd.a.{name}"] = entry
        if phase["id"] in sizes:
            j, row = sizes[phase["id"]]
            for k, scherrer in enumerate(row.get("scherrer") or []):
                if scherrer.get("size_nm") is None:
                    continue
                note = f"Scherrer, {_hkl(scherrer['hkl'])} reflection"
                if uncalibrated or str(row.get("instrument_correction", "")).startswith("none"):
                    note += "; lower bound until an instrument calibration exists"
                out[f"xrd.size.{name}"] = {
                    "value": scherrer["size_nm"], "sigma": scherrer.get("size_sigma_nm_from_fwhm"),
                    "unit": units.get("size", "nm"),
                    "key": f"size_strain[{j}].scherrer[{k}].size_nm", "note": note}
                break
    return out
