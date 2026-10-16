"""Chebyshev polynomial background: starting estimate.

Without anchors: Poisson-weighted fit with iterative asymmetric clipping
(drop points more than ``clip_sigma`` above the curve, refit until stable).
With anchor ranges (user steering): fit only the points inside the anchors.
The final coefficients are co-refined with the peaks in fitting.py, where
anchor ranges also keep the background on the data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.polynomial import chebyshev as cheb


def scaled(x: np.ndarray, domain: tuple[float, float]) -> np.ndarray:
    low, high = domain
    return (2.0 * x - (low + high)) / (high - low)


def basis(x: np.ndarray, order: int, domain: tuple[float, float]) -> np.ndarray:
    return cheb.chebvander(scaled(x, domain), order)


def anchor_mask(x: np.ndarray, ranges: list[list[float]]) -> np.ndarray:
    mask = np.zeros_like(x, dtype=bool)
    for low, high in ranges or []:
        mask |= (x >= low) & (x <= high)
    return mask


@dataclass
class Background:
    order: int
    coefficients: np.ndarray
    domain: tuple[float, float]
    used_fraction: float
    iterations: int
    converged: bool
    stage: str

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return basis(x, self.order, self.domain) @ self.coefficients

    def to_json(self) -> dict:
        return {
            "model": "Chebyshev polynomial",
            "order": self.order,
            "domain_2theta_deg": list(self.domain),
            "coefficients": [float(value) for value in self.coefficients],
            "points_used_fraction": self.used_fraction,
            "iterations": self.iterations,
            "converged": self.converged,
            "stage": self.stage,
        }


def _fit(design: np.ndarray, y: np.ndarray, mask: np.ndarray,
         reference: np.ndarray) -> np.ndarray:
    weights = 1.0 / np.sqrt(np.clip(reference[mask], 1.0, None))
    coefficients, *_ = np.linalg.lstsq(design[mask] * weights[:, None], y[mask] * weights,
                                       rcond=None)
    return coefficients


def initial_estimate(x: np.ndarray, y: np.ndarray, *, order: int, clip_sigma: float,
                     anchors: np.ndarray | None = None,
                     max_iterations: int = 50) -> Background:
    domain = (float(x[0]), float(x[-1]))
    design = basis(x, order, domain)
    if anchors is not None and anchors.sum() > 2 * (order + 1):
        coefficients = _fit(design, y, anchors, y)
        return Background(order, coefficients, domain, float(anchors.mean()), 1, True,
                          "fitted to user anchor ranges")
    mask = np.ones_like(x, dtype=bool)
    coefficients = _fit(design, y, mask, y)
    converged = False
    iteration = 0
    for iteration in range(1, max_iterations + 1):
        curve = design @ coefficients
        new_mask = (y - curve) <= clip_sigma * np.sqrt(np.clip(curve, 1.0, None))
        if new_mask.sum() <= design.shape[1]:
            break
        if np.array_equal(new_mask, mask):
            converged = True
            break
        mask = new_mask
        coefficients = _fit(design, y, mask, np.clip(curve, 1.0, None))
    return Background(order, coefficients, domain, float(mask.mean()), iteration, converged,
                      "clipped")
