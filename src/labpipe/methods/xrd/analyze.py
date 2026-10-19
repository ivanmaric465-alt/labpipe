"""XRD analysis: one input file -> results.json, plot.png/pdf, provenance.json.

All numerics happen here, in a fixed order:

1. parse, sort/deduplicate, drop user-excluded ranges
2. background starting estimate (clipped polynomial, or user anchor ranges)
3. crystalline peak candidates (+ user-added, − user-removed peaks)
4. broad components (automatic, or the positions given in the settings)
5. whole-pattern fit (background + broad Gaussians + Kα1/Kα2 pseudo-Voigt
   doublets); insignificant automatic peaks pruned
6. internal standard and 2θ zero correction
7. fcc indexing, reference comparison and matching (candidates only)
8. Scherrer / Williamson-Hall
9. deterministic checks -> status "ok" or "review"

Settings come from settings.resolve(): global defaults, sample presets,
sample and measurement overrides. That is how the user steers an analysis.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from labpipe import provenance as prov
from labpipe import settings as settings_mod
from labpipe.methods.xrd import background as bg
from labpipe.methods.xrd import fitting, lattice, peaks as peakfinder, plot, sizing
from labpipe.methods.xrd import standard as std
from labpipe.methods.xrd.io_xrdml import PatternFile, read_xrdml
from labpipe.methods.xrd.io_xy import read_xy
from labpipe.methods.xrd.references import load_references, match_reference
from labpipe.naming import sample_ids_agree
from labpipe.plotting import acs
from labpipe.safety import (CODE_ROOT, PROJECT_ROOT, assert_not_protected, assert_pipeline_writable,
                            atomic_write_bytes)

METHOD = "xrd"
RESULTS_SCHEMA = "labpipe.xrd.results/2"
REFERENCE_DIR = CODE_ROOT / "references" / "xrd" / "patterns"
CUBIC_F = ("fcc", "rocksalt")  # face-centred cubic lattices: same reflection conditions


class AnalysisError(RuntimeError):
    """Raised for inputs that cannot be analysed; the message is the recorded reason."""


def _clean(value: Any) -> Any:
    """JSON-safe copy: numpy -> Python, NaN/inf -> None, drop private keys."""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_clean(item) for item in value.tolist()]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _check(checks: list, code: str, severity: str, message: str, **values: Any) -> None:
    checks.append({"code": code, "severity": severity, "message": message, **values})


def _plot_options(config: dict) -> dict[str, Any]:
    """The method's `plot:` keys with their defaults (records of older runs lack the newer
    ones); a bad value is an AnalysisError before any work is done."""
    pconf = config["plot"]
    options = {"y_scale": pconf.get("y_scale", "linear"),
               "residual_panel": bool(pconf.get("residual_panel", True)),
               "phase_ticks": bool(pconf.get("phase_ticks", True)),
               "zoom_deg": pconf.get("zoom_deg")}
    if options["y_scale"] not in ("linear", "sqrt", "log"):
        raise AnalysisError(f"plot.y_scale must be linear, sqrt or log, not {options['y_scale']!r}")
    zoom = options["zoom_deg"]
    if zoom is not None:
        if (not isinstance(zoom, (list, tuple)) or len(zoom) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in zoom)
                or not zoom[0] < zoom[1]):
            raise AnalysisError(f"plot.zoom_deg must be [x1, x2] with x1 < x2, not {zoom!r}")
        options["zoom_deg"] = (float(zoom[0]), float(zoom[1]))
    return options


def _read(path: Path, config: dict) -> PatternFile:
    suffix = path.suffix.lower()
    try:
        if suffix == ".xrdml":
            return read_xrdml(path, max_bytes=config["input"]["max_bytes"])
        if suffix == ".xy":
            return read_xy(path, max_bytes=config["input"]["max_bytes"])
    except (ValueError, OSError, UnicodeError) as exc:
        raise AnalysisError(f"cannot parse {path.name}: {exc}") from exc
    raise AnalysisError(f"unsupported file type {suffix}")


def _prepare_data(pattern: PatternFile, config: dict, checks: list) -> dict[str, Any]:
    scan_index = config["input"]["scan_index"]
    if scan_index >= len(pattern.scans):
        raise AnalysisError(f"scan index {scan_index} not present ({len(pattern.scans)} scans)")
    if len(pattern.scans) > 1:
        _check(checks, "multiple_scans", "review",
               f"file holds {len(pattern.scans)} scans; only scan {scan_index} was analysed")
    scan = pattern.scans[scan_index]
    points = list(zip(scan.two_theta.tolist(), scan.intensity.tolist()))
    ordered, monotonic, duplicates = peakfinder.upstream_ordered_unique(points)
    x = np.array([p[0] for p in ordered])
    y = np.array([p[1] for p in ordered])
    keep = ~bg.anchor_mask(x, config["exclude_ranges_deg"])
    excluded = int((~keep).sum())
    x, y = x[keep], y[keep]
    if x.size < 50:
        raise AnalysisError(f"only {x.size} usable points")
    if not monotonic:
        _check(checks, "non_monotonic_x", "warning", "2θ axis was not monotonic; points sorted")
    if duplicates:
        _check(checks, "duplicate_x", "warning", f"{duplicates} duplicate 2θ values averaged")
    if excluded:
        _check(checks, "user_excluded_ranges", "info",
               f"{excluded} points in user-excluded ranges {config['exclude_ranges_deg']} "
               "left out")
    if np.any(y < 0):
        _check(checks, "negative_intensity", "review",
               f"{int(np.sum(y < 0))} negative intensities in raw data")
    return {"scan": scan, "scan_index": scan_index, "x": x, "y": y, "ordered": ordered,
            "monotonic": monotonic, "duplicates": duplicates, "excluded_points": excluded,
            "step": float(np.median(np.diff(x)))}


def _wavelength(pattern: PatternFile, scan, config: dict, checks: list) -> dict[str, Any]:
    measurement = pattern.metadata["measurements"][scan.metadata.get("measurement_index", 0)]
    info = dict(measurement.get("wavelength") or {})
    if not info.get("k_alpha1_angstrom"):
        info = dict(config["input"]["default_wavelength"], source="config default (not in file)")
        _check(checks, "wavelength_assumed", "review",
               "wavelength not recorded in the file; configured values assumed")
    else:
        info["source"] = "file"
    lambda1 = info["k_alpha1_angstrom"]
    lambda2 = info.get("k_alpha2_angstrom") if config["fit"]["kalpha2_doublet"] else None
    ratio_intensity = (info.get("ratio_k_alpha2_k_alpha1") or 0.0) if lambda2 else 0.0
    if measurement.get("anode") and measurement["anode"].lower() != "cu":
        _check(checks, "non_cu_anode", "info", f"anode is {measurement['anode']}")
    return {"info": info, "measurement": measurement, "lambda1": lambda1, "lambda2": lambda2,
            "ratio_lambda": (lambda2 / lambda1) if lambda2 else None,
            "ratio_intensity": ratio_intensity}


def _candidates(x, y, start, step, wl, config, checks) -> tuple[list, list, list]:
    cconf, fconf = config["candidates"], config["fit"]
    narrow = peakfinder.find_candidates(
        x, y, start(x), step=step, lambda1=wl["lambda1"], lambda2=wl["lambda2"],
        smoothing_window_deg=cconf["smoothing_window_deg"],
        min_significance=cconf["min_significance"],
        min_separation_deg=cconf["min_separation_deg"], min_fwhm_deg=cconf["min_fwhm_deg"],
        max_fwhm_deg=fconf["max_fwhm_deg"], edge_exclusion_deg=cconf["edge_exclusion_deg"])
    steering: list[dict[str, Any]] = []
    for position in config["peaks"]["remove_deg"]:
        before = len(narrow)
        narrow = [c for c in narrow if abs(c["position"] - position) > max(0.15, 0.5 * c["fwhm_guess"])]
        steering.append({"action": "remove peak", "two_theta_deg": position,
                         "removed": before - len(narrow)})
    typical_fwhm = float(np.median([c["fwhm_guess"] for c in narrow])) if narrow else 0.5
    for position in config["peaks"]["add_deg"]:
        index = int(np.argmin(np.abs(x - position)))
        narrow.append({"position": float(position),
                       "net_height": max(float(y[index] - start(x)[index]), 1.0),
                       "fwhm_guess": typical_fwhm, "significance": 0.0, "near_edge": False,
                       "origin": "user"})
        steering.append({"action": "add peak", "two_theta_deg": position})
    narrow.sort(key=lambda c: c["position"])

    seeds = config["broad_components_deg"]
    if not fconf["broad_components"]:
        broad = []
    elif seeds is None:
        broad = peakfinder.find_broad_candidates(
            x, y, start(x), step=step, smoothing_window_deg=cconf["broad_smoothing_window_deg"],
            min_fwhm_deg=fconf["broad_min_fwhm_deg"],
            min_significance=cconf["broad_min_significance"], narrow=narrow)
    else:
        broad = []
        for position in seeds:
            index = int(np.argmin(np.abs(x - position)))
            broad.append({"position": float(position),
                          "net_height": max(float(y[index] - start(x)[index]), 1.0),
                          "fwhm_guess": 1.5 * fconf["broad_min_fwhm_deg"], "significance": 0.0,
                          "near_edge": False, "origin": "settings"})
        steering.append({"action": "broad components from settings", "two_theta_deg": seeds})
    for item in steering:
        _check(checks, "user_steering", "info", f"{item['action']}: {item['two_theta_deg']}")
    return narrow, broad, steering


def _label_peaks(fit: dict, wl: dict, step: float) -> list[dict[str, Any]]:
    peaks = []
    for number, item in enumerate(sorted(fit["peaks"], key=lambda p: p["two_theta_deg"]), 1):
        peak = {key: value for key, value in item.items() if key != "seed"}
        seed = item["seed"]
        sigma = peak["two_theta_sigma_deg"]
        peak.update({
            "id": f"P{number:02d}",
            "origin": seed.get("origin", "automatic"),
            "seed_position_deg": seed["position"],
            "significance": seed["significance"],
            "near_scan_edge": seed.get("near_edge", False),
            "d_angstrom": lattice.d_from_two_theta(peak["two_theta_deg"], wl["lambda1"]),
            "d_sigma_angstrom": (lattice.d_sigma(peak["two_theta_deg"], sigma, wl["lambda1"])
                                 if sigma is not None else None),
            # Weights for later least squares: the fitted σ, or the scan step when
            # the fit gave none (conservative; such peaks are flagged via at_bounds).
            "two_theta_weight_sigma_deg": sigma if sigma is not None else step,
            "fwhm_weight_sigma_deg": (peak["fwhm_sigma_deg"]
                                      if peak["fwhm_sigma_deg"] is not None else step),
            "assignment": None,
        })
        peaks.append(peak)
    return peaks


def _internal_standard(peaks, by_id, wl, low, high, config, checks):
    sconf = config["internal_standard"]
    if not (sconf.get("phase") and sconf["phase"] in by_id and peaks):
        return None, (lambda t: 0.0)
    result = std.find_standard(
        by_id[sconf["phase"]], peaks, wl["lambda1"], low, high,
        lattice_a=sconf.get("lattice_a_angstrom"), search_range_deg=sconf["search_range_deg"],
        search_step_deg=sconf["search_step_deg"], tolerance_deg=sconf["tolerance_deg"],
        min_intensity=sconf["min_intensity_rel"], min_lines=sconf["min_lines"])
    if result is None:
        return None, (lambda t: 0.0)
    peaks_by_id = {p["id"]: p for p in peaks}
    usable = result["matched"]
    result["usable_lines"] = len(usable)
    choice = sconf["correction_model"]
    corrections = std.fit_corrections(usable) if usable else {}
    result["corrections"] = corrections
    if choice == "auto":
        displacement = corrections.get("displacement")
        separable = bool(displacement and len(usable) >= 4
                         and abs(displacement["D_deg"]) > 3.0 * displacement["D_sigma_deg"]
                         and abs(displacement["correlation_z_D"]) < 0.95)
        choice = "displacement" if separable else "constant" if len(usable) >= 2 else "none"
        result["auto_rule"] = ("displacement only if ≥4 lines, |D| > 3σ_D and "
                               "|corr(z,D)| < 0.95; otherwise constant")
    if choice == "displacement" and "displacement" not in corrections:
        choice = "constant"
    result["correction_applied"] = choice
    for line in result["matched"]:
        peaks_by_id[line["peak"]]["assignment"] = {
            "phase": result["phase"], "hkl": line["hkl"], "role": "internal_standard"}
    if result["lattice_a_source"] == "reference CIF":
        _check(checks, "standard_lattice_parameter_placeholder", "review",
               "internal standard detected; its lattice parameter is the COD value, not the "
               "certificate value of the standard actually used")
    if choice == "none":
        _check(checks, "standard_unusable", "review",
               "internal standard lines found but none usable for a zero correction")
        return result, (lambda t: 0.0)
    return result, std.correction_function(corrections, choice)


def _index(peaks, wl, low, high, correction, cubic_references, config):
    iconf = config["indexing"]
    peaks_by_id = {p["id"]: p for p in peaks}
    available = [p for p in peaks if p["assignment"] is None]
    phases = lattice.index_fcc(
        available, wl["lambda1"], low, high, tolerance_deg=iconf["fcc_tolerance_deg"],
        min_reflections=iconf["min_reflections"],
        min_reflections_tentative=iconf["min_reflections_tentative"],
        fwhm_fraction=iconf["fcc_tolerance_fwhm_fraction"],
        max_fwhm_ratio=iconf["max_fwhm_ratio"], max_phases=iconf["max_phases"],
        max_seeds=iconf["max_seeds"], correction=correction)
    for phase in phases:
        assigned = [(peaks_by_id[row["peak"]], tuple(row["hkl"])) for row in phase["reflections"]]
        scatter = phase["a_scatter_angstrom"]
        # Reported σ: the larger of the statistical σ and scatter/√n (nanoparticle
        # reflections often disagree by more than their position errors suggest).
        phase["a_sigma_angstrom"] = max(phase["a_sigma_stat_angstrom"],
                                        scatter / math.sqrt(len(assigned)) if scatter else 0.0)
        phase["a_sigma_rule"] = "max(statistical σ, scatter/√n)"
        phase["a_uncorrected_angstrom"] = lattice.refine(assigned, wl["lambda1"],
                                                         lambda t: 0.0)["a_angstrom"]
        phase["reference_comparison"] = sorted(({
            "phase": ref.id, "structure_type": ref.structure_type,
            "cod_id": ref.source["cod_id"], "a_reference_angstrom": ref.cell["a"],
            "delta_a_angstrom": phase["a_angstrom"] - ref.cell["a"],
            "delta_a_percent": 100.0 * (phase["a_angstrom"] - ref.cell["a"]) / ref.cell["a"]}
            for ref in cubic_references), key=lambda r: abs(r["delta_a_angstrom"]))
        phase["closest_references"] = [row["phase"] for row in phase["reference_comparison"][:2]]
        for row in phase["reflections"]:
            peaks_by_id[row["peak"]]["assignment"] = {
                "phase": phase["id"], "hkl": row["hkl"], "role": "fcc_indexed"}
    return phases


def _match(peaks, references, wl, low, high, correction, phases, config, checks):
    mconf = config["matching"]
    corrected = [dict(p, two_theta_deg=p["two_theta_deg"] - correction(p["two_theta_deg"]))
                 for p in peaks]
    matches = []
    for reference in references:
        if reference.role == "internal_standard":
            continue
        result = match_reference(reference, corrected, wl["lambda1"], low, high,
                                 tolerance_deg=mconf["tolerance_deg"],
                                 min_intensity=mconf["min_intensity_rel"])
        result["is_candidate"] = (result["weighted_match_fraction"]
                                  >= mconf["candidate_min_fraction"]
                                  and result["strongest_line_matched"])
        matched_ids = {line["peak"] for line in result["matched"]}
        result["coincides_with_fcc_phase"] = next(
            (phase["id"] for phase in phases if matched_ids
             and matched_ids <= {row["peak"] for row in phase["reflections"]}), None)
        if result["is_candidate"] and result["coincides_with_fcc_phase"]:
            _check(checks, "reference_match_isostructural", "warning",
                   f"{result['phase']} lines coincide with {result['coincides_with_fcc_phase']} "
                   f"within {result['tolerance_deg']}°; a phase with a similar lattice parameter "
                   "cannot be distinguished from the reference by XRD alone (composition, e.g. "
                   "EDS, needed)", phase=result["phase"])
        matches.append(result)
    return matches


def _size_strain(phases, peaks, wl, config):
    zconf = config["sizing"]
    calibration = None
    if zconf.get("calibration_file"):
        path = PROJECT_ROOT / zconf["calibration_file"]
        calibration = json.loads(path.read_text(encoding="utf-8"))
        calibration["_sha256"] = prov.sha256_file(path)
    peaks_by_id = {p["id"]: p for p in peaks}
    rows = []
    for phase in phases:
        scherrer = sizing.scherrer(phase["reflections"], peaks_by_id, wavelength=wl["lambda1"],
                                   k=zconf["scherrer_k"], calibration=calibration)
        rows.append({
            "phase": phase["id"], "scherrer_k": zconf["scherrer_k"],
            "wavelength_angstrom": wl["lambda1"], "breadth": "FWHM of fitted Kα1 component",
            "instrument_correction": ("Caglioti from calibration" if calibration
                                      else "none (no instrument calibration)"),
            "scherrer": scherrer,
            "williamson_hall": sizing.williamson_hall(scherrer, wavelength=wl["lambda1"],
                                                      k=zconf["scherrer_k"])})
    return rows, calibration


def _quality(x, y, model, base, peaks, broad, step, config):
    weights = 1.0 / np.clip(y, 1.0, None)
    rwp = float(math.sqrt(np.sum(weights * (y - model) ** 2) / np.sum(weights * y ** 2)))
    # Unfitted signal: positive residual averaged over ~0.3° in units of its Poisson σ,
    # away from the flanks of fitted peaks (shape misfit there is reported per peak).
    window = max(3, int(round(0.3 / step)))
    kernel = np.ones(window) / window
    mean_residual = np.convolve(y - model, kernel, mode="same")
    mean_sigma = np.sqrt(np.convolve(np.clip(model, 1.0, None), kernel, mode="same") / window)
    significance = mean_residual / mean_sigma
    near_peak = np.zeros_like(x, dtype=bool)
    for peak in peaks:
        near_peak |= np.abs(x - peak["two_theta_deg"]) <= 3.0 * peak["fwhm_deg"] + 0.3
    flagged = np.flatnonzero((significance > config["checks"]["residual_sigma_review"])
                             & ~near_peak)
    regions = []
    if flagged.size:
        gap = max(1, int(round(0.5 / step)))
        for group in np.split(flagged, np.flatnonzero(np.diff(flagged) > gap) + 1):
            regions.append({"two_theta_deg": [float(x[group[0]]), float(x[group[-1]])],
                            "max_significance_sigma": float(significance[group].max()),
                            "mean_excess_counts": float(mean_residual[group].mean())})
        regions.sort(key=lambda r: -r["max_significance_sigma"])
    # Background tracking: where no feature is nearby, the background alone should
    # carry the counts.
    free = ~near_peak
    for component in broad:
        free &= np.abs(x - component["two_theta_deg"]) > 1.5 * component["fwhm_deg"]
    tracking: dict[str, Any] = {"peak_free_fraction_of_scan": float(free.mean())}
    if free.sum() > 20:
        tracking["component_share_in_peak_free_regions"] = float(
            np.sum(model[free] - base[free]) / np.sum(model[free]))
        tracking["mean_data_minus_background_counts"] = float(np.mean(y[free] - base[free]))
        indices = np.flatnonzero(free)
        tracking["regions"] = [
            {"two_theta_deg": [float(x[g[0]]), float(x[g[-1]])],
             "mean_data_minus_background_counts": float(np.mean(y[g] - base[g])),
             "mean_background_counts": float(np.mean(base[g]))}
            for g in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
            if x[g[-1]] - x[g[0]] >= 0.5]
    return rwp, regions, tracking


def _quality_checks(fit, rwp, regions, tracking, peaks, broad, phases, size_strain,
                    calibration, standard, low, high, broad_fwhm, unassigned, config, checks):
    kconf = config["checks"]
    if not peaks:
        _check(checks, "no_peaks", "review", "no peaks above the significance threshold")
    if not fit["success"]:
        _check(checks, "fit_not_converged", "review", f"fit did not converge: {fit['message']}")
    if not fit["covariance_ok"]:
        _check(checks, "uncertainties_unavailable", "review",
               "covariance matrix singular; some parameter uncertainties are missing")
    if rwp > kconf["rwp_review"]:
        _check(checks, "high_rwp", "review", f"whole-pattern Rwp = {rwp:.3g}", rwp=rwp)
    share = tracking.get("component_share_in_peak_free_regions")
    if share is not None and share > kconf["background_component_fraction_warning"]:
        _check(checks, "background_too_low", "warning",
               f"{100 * share:.0f} % of the fitted intensity in peak-free regions comes from "
               "peak or broad-component tails rather than the background (consider "
               "background.anchor_ranges_deg)", share=share)
    for component in broad:
        if abs(component["fwhm_deg"] - broad_fwhm[0]) <= 1e-6 * broad_fwhm[0]:
            _check(checks, "broad_component_at_min_width", "review",
                   f"{component['id']} at {component['two_theta_deg']:.2f}° sits at the minimum "
                   f"broad width ({broad_fwhm[0]}°); it may be a crystalline reflection")
        elif component["at_bounds"]:
            _check(checks, "broad_component_at_bound", "warning",
                   f"{component['id']}: {', '.join(component['at_bounds'])} at fit bound")
    for peak in peaks:
        misfit = peak["local_misfit_fraction"]
        if misfit is not None and misfit > kconf["local_misfit_review"]:
            _check(checks, "poor_local_fit", "review",
                   f"{peak['id']} at {peak['two_theta_deg']:.3f}°: {100 * misfit:.0f} % of the "
                   "peak signal unexplained in its ±2·FWHM window", peak=peak["id"])
        elif misfit is not None and misfit > kconf["local_misfit_warning"]:
            _check(checks, "local_shape_misfit", "warning",
                   f"{peak['id']} at {peak['two_theta_deg']:.3f}°: {100 * misfit:.0f} % misfit "
                   "(e.g. asymmetry not described by a symmetric pseudo-Voigt)", peak=peak["id"])
        bounded = [name for name in peak["at_bounds"] if name != "eta"]
        if bounded:
            _check(checks, "parameter_at_bound", "review",
                   f"{peak['id']} at {peak['two_theta_deg']:.3f}°: {', '.join(bounded)} at fit "
                   "bound", peak=peak["id"])
        if peak["near_scan_edge"]:
            _check(checks, "peak_near_scan_edge", "warning",
                   f"{peak['id']} at {peak['two_theta_deg']:.3f}° is near the scan edge; profile "
                   "may be truncated", peak=peak["id"])
        if peak["origin"] == "user" and peak["area_sigma"] and \
                peak["area_counts_deg"] < config["fit"]["min_area_significance"] * peak["area_sigma"]:
            _check(checks, "user_peak_not_significant", "review",
                   f"{peak['id']} at {peak['two_theta_deg']:.3f}° was added on request but its "
                   "area is not significant", peak=peak["id"])
    for region in regions[:5]:
        at_edge = region["two_theta_deg"][0] <= low + 1.0 or region["two_theta_deg"][1] >= high - 1.0
        under_broad = any(abs(0.5 * sum(region["two_theta_deg"]) - b["two_theta_deg"])
                          <= 0.5 * b["fwhm_deg"] for b in broad)
        code = ("unfitted_signal_at_scan_edge" if at_edge
                else "broad_component_shape" if under_broad else "unfitted_signal")
        _check(checks, code, "review" if code == "unfitted_signal" else "warning",
               f"systematic positive residual at {region['two_theta_deg'][0]:.2f}–"
               f"{region['two_theta_deg'][1]:.2f}° ({region['max_significance_sigma']:.0f}σ)",
               region=region["two_theta_deg"])
    if len(regions) > 5:
        _check(checks, "unfitted_signal_more", "review",
               f"{len(regions) - 5} further residual regions (see fit.residual_regions)")
    if unassigned:
        _check(checks, "unassigned_peaks", "review",
               f"{len(unassigned)} peak(s) not explained by indexing, the internal standard or a "
               "reference candidate", peaks=unassigned)
    if not phases:
        _check(checks, "no_fcc_phase", "info", "no face-centred cubic phase indexed")
    for phase in phases:
        if phase["tentative"]:
            _check(checks, "tentative_phase", "review",
                   f"{phase['id']} indexed from only {phase['n_reflections']} reflections "
                   "(tentative)", phase=phase["id"])
        if phase["missing_reflections"]:
            _check(checks, "fcc_missing_reflections", "warning",
                   f"{phase['id']}: expected reflections without a fitted peak: "
                   + ", ".join("".join(map(str, line["hkl"])) for line in phase["missing_reflections"]))
    for entry in size_strain:
        wh = entry["williamson_hall"]
        if wh["status"] != "computed":
            _check(checks, "wh_not_computed", "info", f"{entry['phase']}: {wh['status']}")
        else:
            if wh["n_reflections"] < 4:
                _check(checks, "wh_few_points", "warning",
                       f"{entry['phase']}: Williamson-Hall from {wh['n_reflections']} reflections "
                       f"({wh['dof']} degree(s) of freedom); size/strain separation is weak")
            if wh["size_nm"] is None or wh["slope_strain"] < 0 or (wh["r_squared"] or 0) < 0.5:
                _check(checks, "wh_unreliable", "review",
                       f"{entry['phase']}: Williamson-Hall fit not physically meaningful "
                       f"(intercept {wh['intercept']:.3g}, slope {wh['slope_strain']:.3g}, "
                       f"R² {wh['r_squared'] if wh['r_squared'] is not None else float('nan'):.2f})")
        if calibration is None:
            _check(checks, "no_instrument_correction", "warning",
                   f"{entry['phase']}: no instrument-broadening calibration; sizes are lower "
                   "bounds of the true crystallite size")
        if any(row.get("size_nm") and row["size_nm"] > 100 for row in entry["scherrer"]):
            _check(checks, "scherrer_above_100nm", "warning",
                   f"{entry['phase']}: Scherrer sizes above ~100 nm are unreliable")
    if standard is None:
        _check(checks, "zero_shift_not_assessed", "info",
               "no internal standard found; 2θ zero shift and sample displacement are not "
               "corrected and not assessed")


def run(input_path: Path, out_dir: Path, *, settings: dict | None = None,
        settings_sources: list | None = None, context: dict[str, Any] | None = None,
        pipeline: bool = True) -> dict[str, Any]:
    context = context or {}
    out_dir = Path(out_dir)
    # Refuse unsafe output locations before any work (and before any mkdir).
    (assert_pipeline_writable if pipeline else assert_not_protected)(out_dir)
    if settings is None:
        settings, settings_sources = settings_mod.resolve(
            METHOD, context.get("sample_id"), context.get("measurement_id"))
    config = settings
    plot_options = _plot_options(config)
    input_path = Path(input_path)
    input_sha = prov.sha256_file(input_path)
    pattern = _read(input_path, config)
    checks: list[dict[str, Any]] = []

    data = _prepare_data(pattern, config, checks)
    x, y, step = data["x"], data["y"], data["step"]
    low, high = float(x[0]), float(x[-1])
    in_file = pattern.metadata.get("sample_id_in_file")
    if in_file and context.get("sample_id") and in_file.strip() != context["sample_id"]:
        if sample_ids_agree(context["sample_id"], in_file):
            _check(checks, "sample_id_in_file_extended", "info",
                   f"the ID stored in the file ({in_file.strip()}) starts with the sample ID "
                   f"({context['sample_id']}); accepted (user rule, 2026-10-16)")
        else:
            _check(checks, "sample_id_mismatch", "review",
                   f"sample ID from the file name ({context['sample_id']}) differs from the ID "
                   f"stored in the file ({in_file.strip()})")
    wl = _wavelength(pattern, data["scan"], config, checks)

    bconf, fconf = config["background"], config["fit"]
    anchors = bg.anchor_mask(x, bconf["anchor_ranges_deg"])
    start = bg.initial_estimate(x, y, order=bconf["order"], clip_sigma=bconf["clip_sigma"],
                                anchors=anchors if anchors.any() else None)
    narrow, broad_seeds, steering = _candidates(x, y, start, step, wl, config, checks)
    narrow_fwhm = (max(2.0 * step, config["candidates"]["min_fwhm_deg"]), fconf["max_fwhm_deg"])
    broad_fwhm = (fconf["broad_min_fwhm_deg"], fconf["broad_max_fwhm_deg"])
    fit = fitting.fit_with_pruning(
        x, y, min_area_significance=fconf["min_area_significance"],
        background_coefficients=start.coefficients, domain=start.domain, broad=broad_seeds,
        narrow=narrow, ratio_lambda=wl["ratio_lambda"], ratio_intensity=wl["ratio_intensity"],
        narrow_fwhm_range=narrow_fwhm, broad_fwhm_range=broad_fwhm,
        narrow_freedom_deg=fconf["position_freedom_deg"],
        broad_freedom_deg=fconf["broad_position_freedom_deg"], max_nfev=fconf["max_nfev"],
        broad_eta_free=fconf["broad_profile"] != "gaussian",
        anchors=anchors if anchors.any() else None)
    params, layout = fit.pop("params"), fit.pop("layout")
    base = fitting.background_curve(params, x, layout)
    model = fitting.model(params, x, layout)
    broad_curves = [fitting.broad_curve(params, x, layout, k) for k in range(layout.n_broad)]

    peaks = _label_peaks(fit, wl, step)
    broad = []
    for number, item in enumerate(sorted(fit.pop("broad"), key=lambda b: b["two_theta_deg"]), 1):
        component = {key: value for key, value in item.items() if key != "seed"}
        component.update({"id": f"B{number:02d}", "seed_position_deg": item["seed"]["position"],
                          "origin": item["seed"].get("origin", "automatic")})
        broad.append(component)
    fit.pop("peaks")

    references = load_references(REFERENCE_DIR)
    allowed = config["phases"]
    if allowed is not None:
        unknown = sorted(set(allowed) - {r.id for r in references})
        if unknown:
            raise AnalysisError(f"settings.phases names unknown references: {unknown}")
        references = [r for r in references if r.id in allowed or r.role == "internal_standard"]
    by_id = {r.id: r for r in references}
    standard, correction = _internal_standard(peaks, by_id, wl, low, high, config, checks)
    cubic_references = [r for r in references
                        if r.structure_type in CUBIC_F and r.role == "reference"]
    phases = _index(peaks, wl, low, high, correction, cubic_references, config)
    for phase in phases:
        phase["zero_correction"] = standard["correction_applied"] if standard else "none"
    matches = _match(peaks, references, wl, low, high, correction, phases, config, checks)
    candidate_peaks = {line["peak"] for m in matches if m["is_candidate"] for line in m["matched"]}
    unassigned = [p["id"] for p in peaks if p["assignment"] is None
                  and p["id"] not in candidate_peaks]
    size_strain, calibration = _size_strain(phases, peaks, wl, config)

    rwp, regions, tracking = _quality(x, y, model, base, peaks, broad, step, config)
    _quality_checks(fit, rwp, regions, tracking, peaks, broad, phases, size_strain, calibration,
                    standard, low, high, broad_fwhm, unassigned, config, checks)
    status = "review" if any(c["severity"] == "review" for c in checks) else "ok"
    upstream, upstream_threshold = peakfinder.upstream_peaks(data["ordered"])

    scan = data["scan"]
    results = _clean({
        "schema": RESULTS_SCHEMA,
        "status": status,
        "sample_id": context.get("sample_id"),
        "measurement_id": context.get("measurement_id"),
        "input": {"file_name": input_path.name, "relative_path": context.get("relative_path"),
                  "sha256": input_sha, "format": pattern.metadata["format"]},
        "instrument": {key: value for key, value in pattern.metadata.items()
                       if key != "measurements"} | {"measurement": wl["measurement"]},
        "scan": {"scan_index": data["scan_index"], "scans_in_file": len(pattern.scans),
                 "metadata": scan.metadata, "intensity_unit": scan.intensity_unit,
                 "counting_time_s": scan.counting_time_s,
                 "attenuation_applied": scan.attenuation_applied, "n_points": int(x.size),
                 "excluded_points": data["excluded_points"], "two_theta_range_deg": [low, high],
                 "step_median_deg": step, "monotonic": data["monotonic"],
                 "duplicate_x": data["duplicates"]},
        "wavelength": wl["info"],
        "units": {"two_theta": "degree", "d": "angstrom", "intensity": "counts",
                  "area": "counts·degree", "size": "nm", "strain": "dimensionless"},
        "steering": steering,
        "background": {"model": "Chebyshev polynomial, co-refined in the whole-pattern fit",
                       "order": bconf["order"], "domain_2theta_deg": list(layout.domain),
                       "coefficients": fit["background_coefficients"],
                       "anchor_ranges_deg": bconf["anchor_ranges_deg"],
                       "initial_estimate": start.to_json()},
        "fit": {"type": "whole-pattern individual-profile fit",
                "profile": "pseudo-Voigt (same FWHM/η for Kα1 and Kα2); broad components "
                           + ("Gaussian" if fconf["broad_profile"] == "gaussian" else "pseudo-Voigt"),
                "kalpha2": {"modelled": wl["lambda2"] is not None,
                            "lambda_ratio": wl["ratio_lambda"],
                            "intensity_ratio": wl["ratio_intensity"]},
                "fwhm_ranges_deg": {"crystalline": list(narrow_fwhm), "broad": list(broad_fwhm)},
                "success": fit["success"], "message": fit["message"], "nfev": fit["nfev"],
                "covariance_ok": fit["covariance_ok"],
                "dropped_candidates": fit["dropped_candidates"],
                "global": {"rwp": rwp, "chi2_reduced": fit["chi2_reduced"],
                           "n_points": int(x.size), "n_parameters": fit["n_parameters"]},
                "residual_regions": regions, "background_tracking": tracking},
        "broad_components": broad,
        "peaks": peaks,
        "internal_standard": standard,
        "phases": {"fcc_indexed": phases, "reference_matches": matches,
                   "reference_candidates": [m["phase"] for m in matches if m["is_candidate"]],
                   "unassigned_peaks": unassigned,
                   "assignment_policy": "candidates only; no phase is confirmed by this code"},
        "size_strain": size_strain,
        "checks": checks,
        "warnings": pattern.warnings,
        "upstream_peak_candidates": {
            "source": "upstream-eln@a3931a4 utils/analysis_methods/xrd.py::_peaks "
                      "(traceability only)",
            "threshold": upstream_threshold, "items": upstream},
    })

    # --- outputs ------------------------------------------------------------------------
    def write(path: Path, payload: bytes) -> None:
        atomic_write_bytes(path, payload, pipeline=pipeline)

    tick_rows = [{"label": f"{phase['id']} a={phase['a_angstrom']:.4f}"
                           + (" (tent.)" if phase["tentative"] else ""),
                  "positions": [line["two_theta_deg"] + correction(line["two_theta_deg"])
                                for line in phase["expected_reflections_in_range"]]}
                 for phase in phases]
    if standard:
        tick_rows.append({"label": f"{standard['phase']} (std)",
                          "positions": [line["two_theta_calc_deg"] + correction(
                              line["two_theta_calc_deg"]) for line in standard["matched"]]})
    for match in matches:
        if match["is_candidate"] and not match["coincides_with_fcc_phase"]:
            tick_rows.append({"label": f"{match['phase']} (cand.)",
                              "positions": [line["two_theta_deg"]
                                            for line in match["matched"] + match["unmatched"]]})
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / ".plot-staging"
    staging.mkdir(exist_ok=True)
    pconf = config["plot"]
    style = acs.style_of(config)
    title = " · ".join(str(part) for part in (context.get("sample_id"),
                                               context.get("measurement_id") or input_path.stem)
                       if part)
    draw = dict(x=x, y=y, background=base, model=model, broad=broad_curves, tick_rows=tick_rows,
                peak_positions=[p["two_theta_deg"] for p in peaks],
                width_in=acs.width_in(pconf, style), height_in=pconf["height_in"],
                dpi=pconf["dpi"], style=style, y_scale=plot_options["y_scale"],
                residual_panel=plot_options["residual_panel"],
                phase_ticks=plot_options["phase_ticks"])
    plot_info = plot.render(staging / "plot", title=title, **draw)
    plot_files = list(plot_info["files"])
    zoom = plot_options["zoom_deg"]
    if zoom is not None:
        if int(((x >= zoom[0]) & (x <= zoom[1])).sum()) < 2:
            raise AnalysisError(f"plot.zoom_deg {list(zoom)} has no measured points "
                                f"(measured {low:.2f} to {high:.2f} °2θ)")
        zoom_info = plot.render(staging / "zoom", title=f"{title} · zoom {zoom[0]:g}–{zoom[1]:g}°",
                                x_range=zoom, **draw)
        plot_files += zoom_info["files"]
        results["figures"] = [
            {"file": "plot.png", "caption": f"full measured range ({low:.1f}–{high:.1f}°)"},
            {"file": "zoom.png", "caption": f"zoom {zoom[0]:g}–{zoom[1]:g}° 2θ"}]
    for name in plot_files:
        write(out_dir / name, (staging / name).read_bytes())
        (staging / name).unlink()
    staging.rmdir()
    write(out_dir / "results.json",
          (json.dumps(results, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode())

    outputs = {name: prov.sha256_file(out_dir / name)
               for name in ["results.json", *plot_files]}
    provenance = prov.build_record(
        method=METHOD, entry_point="labpipe.methods.xrd.analyze.run", input_path=input_path,
        input_sha256=input_sha, context=context, settings=config,
        settings_sources=settings_sources, outputs=outputs, out_dir=out_dir, extra={
            "references": [{"id": r.id, "file": f"references/xrd/patterns/{r.file}", "root": "code",
                            "sha256": r.file_sha256, "cod_id": r.source.get("cod_id"),
                            "cif_sha256": r.source["cif_sha256"]} for r in references],
            "calibration": ({"file": config["sizing"]["calibration_file"],
                             "sha256": calibration["_sha256"]} if calibration else None),
            "plot": {"style": "ACS (upstream-echem@41b9c69 JOURNAL_PRESETS)",
                     "font": plot_info["font"],
                     "formats": acs.formats(style), "files": plot_files},
        })
    write(out_dir / "provenance.json",
          (json.dumps(_clean(provenance), indent=1, ensure_ascii=False) + "\n").encode())
    return {"status": status, "out_dir": str(out_dir), "n_peaks": len(peaks),
            "checks": [c for c in checks if c["severity"] == "review"], "outputs": outputs,
            **settings_mod.fingerprints(config)}
