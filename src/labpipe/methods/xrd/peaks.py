"""Peak candidates.

Two finders live here:

* ``upstream_*``: ported verbatim from upstream-eln@a3931a4
  utils/analysis_methods/xrd.py (``_ordered_unique``, ``_peaks``). Kept so that
  every results.json records what the previous code would have reported. It
  is NOT used to seed fits: on raw data it splits single reflections into
  several candidates (no smoothing), keeps only the 10 most intense, and uses
  a fixed 5-point window whose width in degrees depends on the scan step.
* ``find_candidates``: the replacement used to seed the profile fits. It
  works on background-subtracted, Savitzky-Golay-smoothed counts, with
  thresholds in Poisson-noise units, a minimum separation in degrees, no top-N
  cap, and explicit suppression of Kα2 shoulders.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import fmean, median
from typing import Any

import numpy as np
from scipy.signal import find_peaks, peak_widths, savgol_filter


# --- upstream (verbatim logic) -------------------------------------------------

def upstream_ordered_unique(points: list[tuple[float, float]]) -> tuple[
        list[tuple[float, float]], bool, int]:
    differences = [points[index + 1][0] - points[index][0]
                   for index in range(len(points) - 1)]
    nonzero = [value for value in differences if value]
    monotonic = not nonzero or all(value > 0 for value in nonzero) \
        or all(value < 0 for value in nonzero)
    grouped: dict[float, list[float]] = defaultdict(list)
    for x_value, y_value in points:
        grouped[x_value].append(y_value)
    ordered = [(x_value, fmean(values)) for x_value, values in sorted(grouped.items())]
    return ordered, monotonic, len(points) - len(ordered)


def upstream_peaks(points: list[tuple[float, float]]) -> tuple[list[dict[str, float]], float]:
    if len(points) < 3:
        return [], 0.0
    ys = [point[1] for point in points]
    span = max(ys) - min(ys)
    differences = [abs(ys[index + 1] - ys[index]) for index in range(len(ys) - 1)]
    noise = median(differences) if differences else 0.0
    threshold = max(span * 0.02, noise * 0.75)
    candidates = []
    for index in range(1, len(points) - 1):
        x_value, y_value = points[index]
        if not (y_value > ys[index - 1] and y_value >= ys[index + 1]):
            continue
        left = ys[max(0, index - 5):index]
        right = ys[index + 1:min(len(ys), index + 6)]
        prominence = y_value - max(min(left), min(right))
        if prominence >= threshold:
            candidates.append({
                "position": x_value, "intensity": y_value,
                "local_prominence": prominence,
            })
    return sorted(candidates, key=lambda item: item["intensity"], reverse=True)[:10], threshold


# --- replacement ---------------------------------------------------------------

def kalpha2_position(two_theta_deg: float, lambda1: float, lambda2: float) -> float | None:
    """Bragg position of the Kα2 line belonging to a Kα1 reflection at 2θ."""
    argument = (lambda2 / lambda1) * math.sin(math.radians(two_theta_deg) / 2.0)
    if not -1.0 < argument < 1.0:
        return None
    return math.degrees(2.0 * math.asin(argument))


def _odd_window(points: int, minimum: int = 5) -> int:
    points = max(points, minimum)
    return points if points % 2 else points + 1


def find_candidates(two_theta: np.ndarray, counts: np.ndarray, background: np.ndarray,
                    *, step: float, lambda1: float, lambda2: float | None,
                    smoothing_window_deg: float, min_significance: float,
                    min_separation_deg: float, min_fwhm_deg: float, max_fwhm_deg: float,
                    edge_exclusion_deg: float = 0.0) -> list[dict[str, Any]]:
    """Return peak candidates sorted by position.

    Significance = smoothed net height / sqrt(background counts) at the peak.
    """
    net = counts - background
    window = _odd_window(int(round(smoothing_window_deg / step)))
    smooth = savgol_filter(net, window_length=window, polyorder=2, mode="interp")
    sigma = np.sqrt(np.clip(background, 1.0, None))
    distance = max(1, int(round(min_separation_deg / step)))
    indices, properties = find_peaks(
        smooth, height=min_significance * sigma, prominence=min_significance * sigma,
        distance=distance)
    if indices.size == 0:
        return []
    widths, _, left_ips, right_ips = peak_widths(smooth, indices, rel_height=0.5)

    low, high = two_theta[0] + edge_exclusion_deg, two_theta[-1] - edge_exclusion_deg
    candidates = []
    for order, index in enumerate(indices):
        fwhm = float(widths[order] * step)
        if fwhm < min_fwhm_deg or fwhm > max_fwhm_deg:
            continue  # spikes, and broad features (handled by find_broad_candidates)
        position = float(two_theta[index])
        candidates.append({
            "position": position,
            "net_height": float(smooth[index]),
            "prominence": float(properties["prominences"][order]),
            "significance": float(smooth[index] / sigma[index]),
            "fwhm_guess": fwhm,
            "near_edge": not (low <= position <= high),
        })

    # Drop Kα2 shoulders: a weaker candidate sitting where the Kα2 line of a
    # stronger candidate is expected (the doublet fit models Kα2 explicitly).
    removed: set[int] = set()
    if lambda2:
        by_height = sorted(range(len(candidates)),
                           key=lambda i: candidates[i]["net_height"], reverse=True)
        for parent in by_height:
            if parent in removed:
                continue
            expected = kalpha2_position(candidates[parent]["position"], lambda1, lambda2)
            if expected is None:
                continue
            tolerance = max(0.35 * candidates[parent]["fwhm_guess"], 2.5 * step)
            for child in by_height:
                if child == parent or child in removed:
                    continue
                if (abs(candidates[child]["position"] - expected) <= tolerance
                        and candidates[child]["net_height"]
                        < 0.8 * candidates[parent]["net_height"]):
                    removed.add(child)
                    candidates[parent].setdefault("suppressed_kalpha2", []).append(
                        candidates[child]["position"])
    kept = [candidate for index, candidate in enumerate(candidates) if index not in removed]
    return sorted(kept, key=lambda item: item["position"])


def find_broad_candidates(two_theta: np.ndarray, counts: np.ndarray, background: np.ndarray,
                          *, step: float, smoothing_window_deg: float, min_fwhm_deg: float,
                          min_significance: float,
                          narrow: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Broad components (FWHM ≥ min_fwhm_deg) above the clipped polynomial.

    Detected on heavily smoothed net counts. Crystalline peaks are first
    removed from the signal (replaced by a straight line across their
    ±1.5·FWHM range) so they do not register as broad features.
    """
    net = counts - background
    masked = net.copy()
    for candidate in narrow:
        half = 1.5 * candidate["fwhm_guess"]
        inside = np.flatnonzero(np.abs(two_theta - candidate["position"]) <= half)
        if inside.size < 2:
            continue
        left, right = inside[0], inside[-1]
        masked[left:right + 1] = np.linspace(masked[left], masked[right], right - left + 1)
    window = _odd_window(int(round(smoothing_window_deg / step)))
    smooth = savgol_filter(masked, window_length=window, polyorder=2, mode="interp")
    sigma = np.sqrt(np.clip(background, 1.0, None))
    indices, properties = find_peaks(
        smooth, prominence=min_significance * sigma, width=min_fwhm_deg / step)
    candidates = []
    for order, index in enumerate(indices):
        candidates.append({
            "position": float(two_theta[index]),
            "net_height": float(smooth[index]),
            "prominence": float(properties["prominences"][order]),
            "significance": float(smooth[index] / sigma[index]),
            "fwhm_guess": float(properties["widths"][order] * step),
            "near_edge": False,
            "kind": "broad",
        })
    return candidates
