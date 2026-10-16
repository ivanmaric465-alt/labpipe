"""Whole-pattern profile fit: polynomial background + broad components + doublets.

Model over the full scan:
    y(x) = Σ_j c_j T_j(x̃)                                    Chebyshev background
         + Σ_broad G(x; x_b, A_b, Γ_b)                         broad components (Gaussian)
         + Σ_peaks [ pV(x; x1, A, Γ, η) + R·pV(x; x2, A, Γ, η) ]  Kα1/Kα2 doublets
with x2 = 2·asin((λ2/λ1)·sin(x1/2)) and R the Kα2/Kα1 ratio from the file.
pV = η·L + (1-η)·G, both area-normalised with FWHM Γ (same Γ, η for Kα1/Kα2).

Broad components (e.g. carbon-support bands) and crystalline peaks occupy
disjoint FWHM ranges, so one cannot stand in for the other. Broad components
are pure Gaussians by default: Lorentzian tails would let them act as a
hidden second background far from their centre. They are reported
separately and never indexed or used for size/strain.

Anchor ranges (user steering): inside them the background alone is also
fitted to the data, which keeps it on the counts where the user says there
is nothing but background.

Residuals are weighted by Poisson σ = sqrt(max(y, 1)); the Jacobian is
analytic. Uncertainties: cov = (JᵀJ)⁻¹ · max(1, χ²_red); parameters at a
bound get no uncertainty (None).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from labpipe.methods.xrd.background import basis as background_basis

LN2 = math.log(2.0)
_G_NORM = 2.0 * math.sqrt(LN2 / math.pi)
PEAK_FIELDS = ("position", "area", "fwhm", "eta")


def pseudo_voigt(x: np.ndarray, center: float, area: float, fwhm: float,
                 eta: float) -> np.ndarray:
    u = (x - center) / fwhm
    lorentz = (2.0 / (math.pi * fwhm)) / (1.0 + 4.0 * u * u)
    gauss = (_G_NORM / fwhm) * np.exp(-4.0 * LN2 * u * u)
    return area * (eta * lorentz + (1.0 - eta) * gauss)


def pv_peak_value(area: float, fwhm: float, eta: float) -> float:
    return area * (eta * 2.0 / (math.pi * fwhm) + (1.0 - eta) * _G_NORM / fwhm)


def kalpha2_center(center, ratio_lambda: float):
    argument = np.clip(ratio_lambda * np.sin(np.radians(center) / 2.0), -1.0, 1.0)
    return np.degrees(2.0 * np.arcsin(argument))


@dataclass
class Layout:
    order: int
    domain: tuple[float, float]
    n_broad: int
    n_peaks: int
    ratio_lambda: float | None
    ratio_intensity: float
    broad_eta_free: bool = False   # False: broad components are pure Gaussians (3 parameters)

    @property
    def broad_width(self) -> int:
        return 4 if self.broad_eta_free else 3

    @property
    def n_background(self) -> int:
        return self.order + 1

    def broad_slice(self, k: int) -> slice:
        start = self.n_background + self.broad_width * k
        return slice(start, start + self.broad_width)

    def peak_slice(self, k: int) -> slice:
        start = self.n_background + self.broad_width * self.n_broad + 4 * k
        return slice(start, start + 4)


def background_curve(params: np.ndarray, x: np.ndarray, layout: Layout) -> np.ndarray:
    return background_basis(x, layout.order, layout.domain) @ params[:layout.n_background]


def broad_curve(params: np.ndarray, x: np.ndarray, layout: Layout, k: int) -> np.ndarray:
    values = params[layout.broad_slice(k)]
    eta = values[3] if layout.broad_eta_free else 0.0
    return pseudo_voigt(x, values[0], values[1], values[2], eta)


def peak_curve(params: np.ndarray, x: np.ndarray, layout: Layout, k: int) -> np.ndarray:
    center, area, fwhm, eta = params[layout.peak_slice(k)]
    curve = pseudo_voigt(x, center, area, fwhm, eta)
    if layout.ratio_lambda is not None and layout.ratio_intensity > 0:
        curve = curve + pseudo_voigt(x, float(kalpha2_center(center, layout.ratio_lambda)),
                                     layout.ratio_intensity * area, fwhm, eta)
    return curve


def model(params: np.ndarray, x: np.ndarray, layout: Layout) -> np.ndarray:
    y = background_curve(params, x, layout)
    for k in range(layout.n_broad):
        y = y + broad_curve(params, x, layout, k)
    for k in range(layout.n_peaks):
        y = y + peak_curve(params, x, layout, k)
    return y


def _pv_and_derivatives(x: np.ndarray, center: float, area: float, fwhm: float, eta: float):
    """pV value and analytic derivatives w.r.t. (center, area, fwhm, eta)."""
    u = (x - center) / fwhm
    q = 1.0 + 4.0 * u * u
    lorentz = (2.0 / (math.pi * fwhm)) / q
    gauss = (_G_NORM / fwhm) * np.exp(-4.0 * LN2 * u * u)
    shape = eta * lorentz + (1.0 - eta) * gauss
    d_center = area * (eta * lorentz * 8.0 * u / (q * fwhm)
                       + (1.0 - eta) * gauss * 8.0 * LN2 * u / fwhm)
    d_fwhm = area * (eta * (lorentz / fwhm) * (4.0 * u * u - 1.0) / q
                     + (1.0 - eta) * (gauss / fwhm) * (8.0 * LN2 * u * u - 1.0))
    return area * shape, d_center, shape, d_fwhm, area * (lorentz - gauss)


def model_and_jacobian(params: np.ndarray, x: np.ndarray,
                       layout: Layout) -> tuple[np.ndarray, np.ndarray]:
    """Model and its analytic Jacobian (n_points × n_params)."""
    jac = np.empty((x.size, params.size))
    vander = background_basis(x, layout.order, layout.domain)
    jac[:, :layout.n_background] = vander
    y = vander @ params[:layout.n_background]
    for k in range(layout.n_broad):
        s = layout.broad_slice(k)
        values = params[s]
        eta = values[3] if layout.broad_eta_free else 0.0
        value, dc, da, dw, de = _pv_and_derivatives(x, values[0], values[1], values[2], eta)
        y = y + value
        jac[:, s] = np.column_stack([dc, da, dw, de] if layout.broad_eta_free
                                    else [dc, da, dw])
    doublet = layout.ratio_lambda is not None and layout.ratio_intensity > 0
    for k in range(layout.n_peaks):
        s = layout.peak_slice(k)
        center, area, fwhm, eta = params[s]
        value, dc, da, dw, de = _pv_and_derivatives(x, center, area, fwhm, eta)
        if doublet:
            r = layout.ratio_intensity
            center2 = float(kalpha2_center(center, layout.ratio_lambda))
            # d(2θ2)/d(2θ1) = (λ2/λ1)·cos θ1 / cos θ2 (same in degrees and radians)
            chain = (layout.ratio_lambda * math.cos(math.radians(center) / 2.0)
                     / math.cos(math.radians(center2) / 2.0))
            v2, dc2, da2, dw2, de2 = _pv_and_derivatives(x, center2, area, fwhm, eta)
            value = value + r * v2
            dc = dc + r * dc2 * chain
            da = da + r * da2
            dw = dw + r * dw2
            de = de + r * de2
        y = y + value
        jac[:, s] = np.column_stack([dc, da, dw, de])
    return y, jac


def _initial(candidate: dict, *, fwhm_range: tuple[float, float], freedom: float,
             ratio_intensity: float, with_eta: bool = True,
             freedom_at_least_half_fwhm: bool = True) -> tuple[list, list, list]:
    low_fwhm, high_fwhm = fwhm_range
    fwhm = float(np.clip(candidate["fwhm_guess"], low_fwhm * 1.02, high_fwhm * 0.98))
    area = max(candidate["net_height"], 1.0) * fwhm * 1.064 / (1.0 + ratio_intensity)
    position = candidate["position"]
    if freedom_at_least_half_fwhm:  # crystalline peaks: a seed may be off by up to half a width
        freedom = max(freedom, 0.5 * fwhm)
    initial = [position, area, fwhm]
    lower = [position - freedom, 0.0, low_fwhm]
    upper = [position + freedom, np.inf, high_fwhm]
    if with_eta:
        initial.append(0.5)
        lower.append(0.0)
        upper.append(1.0)
    return initial, lower, upper


def fit_pattern(x: np.ndarray, y: np.ndarray, *, background_coefficients: np.ndarray,
                domain: tuple[float, float], broad: list[dict], narrow: list[dict],
                ratio_lambda: float | None, ratio_intensity: float,
                narrow_fwhm_range: tuple[float, float], broad_fwhm_range: tuple[float, float],
                narrow_freedom_deg: float, broad_freedom_deg: float, max_nfev: int,
                broad_eta_free: bool = False,
                anchors: np.ndarray | None = None) -> dict[str, Any]:
    order = len(background_coefficients) - 1
    layout = Layout(order, domain, len(broad), len(narrow), ratio_lambda, ratio_intensity,
                    broad_eta_free)
    initial = list(background_coefficients)
    lower = [-np.inf] * len(initial)
    upper = [np.inf] * len(initial)
    for candidate in broad:
        # Broad components: the configured freedom is used exactly (steerable).
        i, lo, hi = _initial(candidate, fwhm_range=broad_fwhm_range, freedom=broad_freedom_deg,
                             ratio_intensity=0.0, with_eta=broad_eta_free,
                             freedom_at_least_half_fwhm=False)
        initial += i
        lower += lo
        upper += hi
    for candidate in narrow:
        i, lo, hi = _initial(candidate, fwhm_range=narrow_fwhm_range,
                             freedom=narrow_freedom_deg, ratio_intensity=ratio_intensity)
        initial += i
        lower += lo
        upper += hi
    lower_arr, upper_arr = np.array(lower), np.array(upper)
    start = np.clip(np.array(initial), lower_arr + 1e-9, upper_arr - 1e-9)
    sigma = np.sqrt(np.clip(y, 1.0, None))
    use_anchors = anchors is not None and anchors.any()
    anchor_basis = background_basis(x[anchors], order, domain) if use_anchors else None
    cache: dict[str, Any] = {}

    def evaluate(params: np.ndarray):
        key = params.tobytes()
        if cache.get("key") != key:
            cache["key"] = key
            cache["value"] = model_and_jacobian(params, x, layout)
        return cache["value"]

    def residual(params: np.ndarray) -> np.ndarray:
        main = (y - evaluate(params)[0]) / sigma
        if not use_anchors:
            return main
        extra = (y[anchors] - anchor_basis @ params[:layout.n_background]) / sigma[anchors]
        return np.concatenate([main, extra])

    def jacobian(params: np.ndarray) -> np.ndarray:
        main = -evaluate(params)[1] / sigma[:, None]
        if not use_anchors:
            return main
        extra = np.zeros((int(anchors.sum()), params.size))
        extra[:, :layout.n_background] = -anchor_basis / sigma[anchors][:, None]
        return np.vstack([main, extra])

    result = least_squares(residual, start, jac=jacobian, bounds=(lower_arr, upper_arr),
                           method="trf", x_scale="jac", max_nfev=max_nfev)
    params = result.x
    main_residual = result.fun[:x.size]
    dof = max(x.size - params.size, 1)
    chi2_red = float(np.sum(main_residual ** 2) / dof)

    at_bound = np.array([
        (np.isfinite(lo) and abs(value - lo) <= 1e-6 * max(1.0, abs(lo)))
        or (np.isfinite(hi) and abs(value - hi) <= 1e-6 * max(1.0, abs(hi)))
        for value, lo, hi in zip(params, lower_arr, upper_arr)])
    free = ~at_bound
    errors = np.full(params.size, np.nan)
    covariance_ok = True
    jac = result.jac[:, free]
    try:
        covariance = np.linalg.inv(jac.T @ jac) * max(1.0, chi2_red)
        errors[free] = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    except np.linalg.LinAlgError:
        covariance_ok = False
    if not np.all(np.isfinite(errors[free])):
        covariance_ok = False

    def describe(slice_: slice) -> dict[str, Any]:
        values = list(params[slice_])
        sigmas = list(errors[slice_])
        bounds = list(at_bound[slice_])
        if len(values) == 3:  # Gaussian broad component: η fixed at 0, not fitted
            values.append(0.0)
            sigmas.append(np.nan)
            bounds.append(False)
        center, area, fwhm, eta = (float(v) for v in values)
        height = pv_peak_value(area, fwhm, eta)
        return {
            "two_theta_deg": center,
            "two_theta_sigma_deg": float(sigmas[0]) if np.isfinite(sigmas[0]) else None,
            "area_counts_deg": area,
            "area_sigma": float(sigmas[1]) if np.isfinite(sigmas[1]) else None,
            "fwhm_deg": fwhm,
            "fwhm_sigma_deg": float(sigmas[2]) if np.isfinite(sigmas[2]) else None,
            "eta": eta,
            "eta_sigma": float(sigmas[3]) if np.isfinite(sigmas[3]) else None,
            "height_counts": height,
            "integral_breadth_deg": area / height if height > 0 else None,
            "at_bounds": [name for name, flag in zip(PEAK_FIELDS, bounds) if flag],
        }

    broad_out = []
    for k, candidate in enumerate(broad):
        item = describe(layout.broad_slice(k))
        item["seed"] = candidate
        broad_out.append(item)
    peaks_out = []
    for k, candidate in enumerate(narrow):
        item = describe(layout.peak_slice(k))
        item["kalpha2_two_theta_deg"] = (float(kalpha2_center(item["two_theta_deg"],
                                                              ratio_lambda))
                                         if ratio_lambda is not None else None)
        center, _, fwhm, _ = params[layout.peak_slice(k)]
        window = np.abs(x - center) <= max(2.0 * fwhm, 0.2)
        local_dof = max(int(window.sum()) - 4, 1)
        item["local_chi2_reduced"] = float(np.sum(main_residual[window] ** 2) / local_dof)
        # Fraction of this peak's signal left unexplained in its ±2·FWHM window:
        # scale-free, so intense sharp peaks are not penalised for counting statistics.
        own = peak_curve(params, x[window], layout, k)
        misfit = y[window] - model(params, x[window], layout)
        own_norm = float(np.sqrt(np.sum(own ** 2)))
        item["local_misfit_fraction"] = (float(np.sqrt(np.sum(misfit ** 2))) / own_norm
                                         if own_norm > 0 else None)
        item["seed"] = candidate
        peaks_out.append(item)

    return {
        "params": params, "layout": layout,
        "success": bool(result.success), "status": int(result.status),
        "message": str(result.message), "nfev": int(result.nfev),
        "chi2_reduced": chi2_red, "n_parameters": int(params.size),
        "covariance_ok": covariance_ok,
        "background_coefficients": params[:layout.n_background].tolist(),
        "broad": broad_out, "peaks": peaks_out,
    }


def fit_with_pruning(x: np.ndarray, y: np.ndarray, *, min_area_significance: float,
                     max_rounds: int = 3, **kwargs) -> dict[str, Any]:
    """Fit; drop automatically found peaks that are not significant; refit.

    Not significant: area < k·σ_area, area at zero, or position run to its bound.
    Peaks the user asked for (origin 'user') are never dropped, only reported.
    """
    narrow = list(kwargs.pop("narrow"))
    dropped: list[dict[str, Any]] = []
    round_number = 0
    for round_number in range(max_rounds + 1):
        fit = fit_pattern(x, y, narrow=narrow, **kwargs)
        keep, removed = [], []
        for candidate, peak in zip(narrow, fit["peaks"]):
            sigma = peak["area_sigma"]
            insignificant = ("area" in peak["at_bounds"] or "position" in peak["at_bounds"]
                             or (sigma is not None and sigma > 0
                                 and peak["area_counts_deg"] < min_area_significance * sigma))
            if insignificant and candidate.get("origin") != "user":
                removed.append((candidate, peak))
            else:
                keep.append((candidate, peak))
        if not removed or round_number == max_rounds:
            break
        for candidate, peak in removed:
            dropped.append({"seed_position_deg": candidate["position"],
                            "fitted_area": peak["area_counts_deg"],
                            "area_sigma": peak["area_sigma"],
                            "reason": ("position ran to its bound"
                                       if "position" in peak["at_bounds"]
                                       else f"area < {min_area_significance}·σ or at zero")})
        narrow = [candidate for candidate, _ in keep]
        kwargs["background_coefficients"] = np.array(fit["background_coefficients"])
    fit["dropped_candidates"] = dropped
    fit["pruning_rounds"] = round_number
    return fit
