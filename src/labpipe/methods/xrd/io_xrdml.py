"""PANalytical XRDML reader.

Derived from upstream-eln@a3931a4 utils/scientific_reports/charts.py::_charts_from_xrdml.
Changes from upstream (see docs/PORTING.md for the full rationale):

* every <scan> is read, and each scan's 2θ positions are paired with the
  counts of the *same* <dataPoints> block (upstream searched the whole
  document independently for the first positions and the first counts);
* explicit <listPositions> are honoured; start/end positions are expanded
  only when no list is present;
* counting time, beam attenuation factors, intensity unit, wavelengths,
  anode, slit types, sample <id> and scan timestamps are returned as metadata;
* no point decimation (upstream thinned scans above 5000 points by stride);
* the size limit is a parameter (upstream: fixed 5 MB).

The file is opened read-only and is never modified.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from labpipe.methods.xrd.xmlsafe import parse_xml_rejecting_dtds

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


@dataclass
class Scan:
    """One diffraction scan: 2θ in degrees, raw intensities as recorded."""

    two_theta: np.ndarray
    intensity: np.ndarray
    intensity_unit: str
    counting_time_s: float | None
    attenuation_applied: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatternFile:
    scans: list[Scan]
    metadata: dict[str, Any]
    warnings: list[str]


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((child for child in element if _tag(child) == name), None)


def _children(element: ET.Element | None, name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [child for child in element if _tag(child) == name]


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _float(element: ET.Element | None) -> float | None:
    value = _text(element)
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _floats(element: ET.Element | None) -> np.ndarray | None:
    value = _text(element)
    if value is None:
        return None
    try:
        array = np.array([float(item) for item in value.split()], dtype=float)
    except ValueError as exc:
        raise ValueError(f"non-numeric values in <{_tag(element)}>") from exc
    if not np.all(np.isfinite(array)):
        raise ValueError(f"non-finite values in <{_tag(element)}>")
    return array


def _optic(path: ET.Element | None, name: str) -> dict[str, Any] | None:
    element = _child(path, name)
    if element is None:
        return None
    info: dict[str, Any] = {"name": element.attrib.get("name"),
                            "type": element.attrib.get(XSI_TYPE)}
    for child in element:
        number = _float(child)
        if number is not None:
            info[_tag(child)] = {"value": number, "unit": child.attrib.get("unit")}
    return info


def _wavelength(measurement: ET.Element) -> dict[str, Any]:
    used = _child(measurement, "usedWavelength")
    if used is None:
        return {}
    return {
        "intended": used.attrib.get("intended"),
        "k_alpha1_angstrom": _float(_child(used, "kAlpha1")),
        "k_alpha2_angstrom": _float(_child(used, "kAlpha2")),
        "k_beta_angstrom": _float(_child(used, "kBeta")),
        "ratio_k_alpha2_k_alpha1": _float(_child(used, "ratioKAlpha2KAlpha1")),
    }


def _two_theta_positions(data_points: ET.Element, count: int) -> tuple[np.ndarray, str]:
    block = next((element for element in _children(data_points, "positions")
                  if element.attrib.get("axis", "").replace(" ", "").lower() == "2theta"),
                 None)
    if block is None:
        raise ValueError("scan has no 2Theta positions")
    unit = block.attrib.get("unit", "deg")
    if unit.lower() not in {"deg", "degree", "degrees"}:
        raise ValueError(f"unsupported 2Theta unit {unit!r}")
    listed = _floats(_child(block, "listPositions"))
    if listed is not None:
        if listed.size != count:
            raise ValueError(
                f"2Theta list has {listed.size} positions for {count} intensities")
        return listed, "listPositions"
    start = _float(_child(block, "startPosition"))
    end = _float(_child(block, "endPosition"))
    if start is None or end is None:
        raise ValueError("2Theta start/end positions are missing")
    if count < 2:
        raise ValueError("scan has fewer than two intensities")
    # Same equidistant expansion as upstream: start + i * (end - start) / (n - 1).
    return np.linspace(start, end, count), "startPosition/endPosition"


def _scan(scan: ET.Element, index: int, warnings: list[str]) -> Scan:
    data_points = _child(scan, "dataPoints")
    if data_points is None:
        raise ValueError(f"scan {index} has no dataPoints")
    # Element truthiness means "has children", so compare with None explicitly.
    intensity_element = _child(data_points, "counts")
    if intensity_element is None:
        intensity_element = _child(data_points, "intensities")
    intensities = _floats(intensity_element)
    if intensities is None:
        raise ValueError(f"scan {index} has no counts/intensities")
    two_theta, position_source = _two_theta_positions(data_points, intensities.size)
    unit = intensity_element.attrib.get("unit") or "counts"

    attenuation = _floats(_child(data_points, "beamAttenuationFactors"))
    attenuation_applied = False
    if attenuation is not None:
        if attenuation.size == 1:
            attenuation = np.full(intensities.size, attenuation[0])
        if attenuation.size != intensities.size:
            raise ValueError(f"scan {index}: attenuation factor count mismatch")
        if not np.allclose(attenuation, 1.0):
            intensities = intensities * attenuation
            attenuation_applied = True
            warnings.append(
                f"scan {index}: beam attenuation factors applied; "
                "corrected intensities are no longer pure Poisson counts")

    counting_time = _float(_child(data_points, "commonCountingTime"))
    header = _child(scan, "header")
    return Scan(
        two_theta=two_theta, intensity=intensities, intensity_unit=unit,
        counting_time_s=counting_time, attenuation_applied=attenuation_applied,
        metadata={
            "scan_index": index,
            "mode": scan.attrib.get("mode"),
            "scan_axis": scan.attrib.get("scanAxis"),
            "status": scan.attrib.get("status"),
            "position_source": position_source,
            "start_timestamp": _text(_child(header, "startTimeStamp")),
            "end_timestamp": _text(_child(header, "endTimeStamp")),
            "point_count": int(intensities.size),
        })


def read_xrdml(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> PatternFile:
    """Read every scan of an XRDML file. Raises ValueError on unusable files."""
    path = Path(path)
    with open(path, "rb") as stream:  # read-only, raw data is never written
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"file exceeds the {max_bytes} byte limit")
    root = parse_xml_rejecting_dtds(raw, invalid_message="invalid XRDML")

    warnings: list[str] = []
    sample = _child(root, "sample")
    measurements = _children(root, "xrdMeasurement")
    if not measurements:
        raise ValueError("XRDML has no xrdMeasurement")
    if len(measurements) > 1:
        warnings.append(f"file has {len(measurements)} xrdMeasurement blocks")

    scans: list[Scan] = []
    measurement_meta: list[dict[str, Any]] = []
    for measurement in measurements:
        incident = _child(measurement, "incidentBeamPath")
        diffracted = _child(measurement, "diffractedBeamPath")
        tube = _child(incident, "xRayTube")
        measurement_meta.append({
            "measurement_type": measurement.attrib.get("measurementType"),
            "sample_mode": measurement.attrib.get("sampleMode"),
            "status": measurement.attrib.get("status"),
            "wavelength": _wavelength(measurement),
            "anode": _text(_child(tube, "anodeMaterial")),
            "tube_tension_kV": _float(_child(tube, "tension")),
            "tube_current_mA": _float(_child(tube, "current")),
            "goniometer_radius_mm": _float(_child(incident, "radius")),
            "divergence_slit": _optic(incident, "divergenceSlit"),
            "anti_scatter_slit": _optic(diffracted, "antiScatterSlit"),
            "detector": (_child(diffracted, "detector").attrib.get("name")
                         if _child(diffracted, "detector") is not None else None),
            "comments": [_text(entry) for entry in _children(
                _child(measurement, "comment"), "entry") if _text(entry)],
        })
        for scan_element in _children(measurement, "scan"):
            scan = _scan(scan_element, len(scans), warnings)
            scan.metadata["measurement_index"] = len(measurement_meta) - 1
            scans.append(scan)
    if not scans:
        raise ValueError("XRDML contains no scans")
    incomplete = [scan.metadata["scan_index"] for scan in scans
                  if (scan.metadata.get("status") or "Completed") != "Completed"]
    if incomplete:
        warnings.append(f"scan(s) {incomplete} not marked Completed")

    return PatternFile(
        scans=scans,
        metadata={
            "format": "xrdml",
            "namespace": root.tag.split("}")[0].lstrip("{") if "}" in root.tag else None,
            "status": root.attrib.get("status"),
            "sample_id_in_file": _text(_child(sample, "id")),
            "sample_name_in_file": _text(_child(sample, "name")),
            "measurements": measurement_meta,
            "file_comments": [_text(entry) for entry in _children(
                _child(root, "comment"), "entry") if _text(entry)],
        },
        warnings=warnings)
