"""Shared helpers: synthetic diffraction patterns with known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from labpipe.methods.xrd import fitting

LAMBDA1 = 1.540598
LAMBDA2 = 1.544426
RATIO = 0.5


def fcc_positions(a: float, hkls=((1, 1, 1), (2, 0, 0), (2, 2, 0), (3, 1, 1))) -> list[float]:
    positions = []
    for h, k, l in hkls:
        d = a / math.sqrt(h * h + k * k + l * l)
        positions.append(math.degrees(2 * math.asin(LAMBDA1 / (2 * d))))
    return positions


def synthetic_pattern(peaks: list[tuple[float, float, float, float]], *, start=10.0, stop=90.0,
                      step=0.013, background=(800.0, -300.0, 60.0), broad=None,
                      seed=7) -> tuple[np.ndarray, np.ndarray]:
    """Poisson counts: Chebyshev background + optional broad pV + Kα1/Kα2 pV doublets.

    peaks: (two_theta, area, fwhm, eta) of the Kα1 component.
    """
    x = np.arange(start, stop, step)
    order = len(background) - 1
    layout = fitting.Layout(order, (x[0], x[-1]), 1 if broad else 0, len(peaks),
                            LAMBDA2 / LAMBDA1, RATIO, bool(broad) and len(broad) == 4)
    params = list(background)
    if broad:
        params += list(broad)
    for peak in peaks:
        params += list(peak)
    expected = fitting.model(np.array(params), x, layout)
    rng = np.random.default_rng(seed)
    return x, rng.poisson(np.clip(expected, 0, None)).astype(float)


def xrdml_text(x: np.ndarray, y: np.ndarray, sample_id: str = "") -> str:
    counts = " ".join(str(int(v)) for v in y)
    return f"""<?xml version="1.0"?>
<xrdMeasurements xmlns="http://www.xrdml.com/XRDMeasurement/2.2" status="Completed">
 <sample type="To be analyzed"><id>{sample_id}</id></sample>
 <xrdMeasurement measurementType="Scan" status="Completed" sampleMode="Reflection">
  <usedWavelength intended="K-Alpha 1">
   <kAlpha1 unit="Angstrom">{LAMBDA1}</kAlpha1><kAlpha2 unit="Angstrom">{LAMBDA2}</kAlpha2>
   <kBeta unit="Angstrom">1.3922500</kBeta><ratioKAlpha2KAlpha1>{RATIO}</ratioKAlpha2KAlpha1>
  </usedWavelength>
  <incidentBeamPath><xRayTube><anodeMaterial>Cu</anodeMaterial></xRayTube></incidentBeamPath>
  <scan appendNumber="0" mode="Continuous" scanAxis="Gonio" status="Completed">
   <dataPoints>
    <positions axis="2Theta" unit="deg"><startPosition>{x[0]}</startPosition><endPosition>{x[-1]}</endPosition></positions>
    <commonCountingTime unit="seconds">60.0</commonCountingTime>
    <counts unit="counts">{counts}</counts>
   </dataPoints>
  </scan>
 </xrdMeasurement>
</xrdMeasurements>
"""


@pytest.fixture
def pd_like_pattern():
    """fcc a = 3.8898 Å (Pd), nanocrystalline widths, on a sloping background."""
    positions = fcc_positions(3.8898)
    peaks = [(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
             (positions[2], 2200.0, 1.8, 0.6), (positions[3], 2000.0, 2.1, 0.6)]
    x, y = synthetic_pattern(peaks)
    return x, y, positions, 3.8898
