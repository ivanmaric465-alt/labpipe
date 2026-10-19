"""Reference phases (built by `labpipe xrd refs build`, see refbuild.py) and pattern matching."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Reference:
    id: str
    role: str
    structure_type: str
    cell: dict[str, float]
    reflections: list[dict[str, Any]]
    source: dict[str, Any]
    file: str
    file_sha256: str
    origin: str = "code"        # "code": shipped with labpipe; "project": the lab's (lab/references/xrd)


def bragg_two_theta(d: float, wavelength: float) -> float | None:
    argument = wavelength / (2.0 * d)
    if not 0.0 < argument < 1.0:
        return None
    return math.degrees(2.0 * math.asin(argument))


def load_references(directory: Path, origin: str = "code") -> list[Reference]:
    references = []
    for path in sorted(directory.glob("*.json")):
        raw = path.read_bytes()
        data = json.loads(raw)
        references.append(Reference(
            id=data["id"], role=data["role"], structure_type=data["structure_type"],
            cell=data["cell"], reflections=data["reflections"], source=data["source"],
            file=str(path.name), file_sha256=hashlib.sha256(raw).hexdigest(), origin=origin))
    return references


def load_all(code_dir: Path, lab_dir: Path | None) -> list[Reference]:
    """The references shipped with labpipe plus the lab's own (lab/references/xrd/patterns,
    which updates never touch). A lab reference may not reuse a shipped id."""
    references = load_references(code_dir)
    if lab_dir is not None and lab_dir.is_dir():
        shipped = {r.id for r in references}
        lab = load_references(lab_dir, origin="project")
        clash = sorted(r.id for r in lab if r.id in shipped)
        if clash:
            raise ValueError(f"lab/references/xrd uses ids that come with labpipe: {clash}; "
                             "give the lab's references other ids")
        references += lab
    return references


def predicted_lines(reference: Reference, wavelength: float, low: float, high: float,
                    min_intensity: float, d_scale: float = 1.0) -> list[dict[str, Any]]:
    """Reference reflections inside [low, high] at this wavelength.

    ``d_scale`` rescales every d (used when the standard's certified lattice
    parameter differs from the CIF value; cubic phases only).
    """
    lines = []
    for reflection in reference.reflections:
        if reflection["intensity_rel"] < min_intensity:
            continue
        d = reflection["d_angstrom"] * d_scale
        two_theta = bragg_two_theta(d, wavelength)
        if two_theta is None or not low <= two_theta <= high:
            continue
        lines.append({"hkl": reflection["hkl"][0], "d_angstrom": d,
                      "two_theta_deg": two_theta,
                      "intensity_rel": reflection["intensity_rel"]})
    return lines


def match_reference(reference: Reference, peaks: list[dict[str, Any]], wavelength: float,
                    low: float, high: float, *, tolerance_deg: float,
                    min_intensity: float, offset_deg: float = 0.0) -> dict[str, Any]:
    """Match predicted lines to fitted peaks (nearest peak within tolerance).

    ``offset_deg`` is subtracted from observed positions (zero correction).
    Score = intensity-weighted fraction of predicted lines that found a peak.
    """
    lines = predicted_lines(reference, wavelength, low, high, min_intensity)
    matched, unmatched = [], []
    for line in lines:
        best = None
        for peak in peaks:
            delta = (peak["two_theta_deg"] - offset_deg) - line["two_theta_deg"]
            if abs(delta) <= tolerance_deg and (best is None or abs(delta) < abs(best[1])):
                best = (peak["id"], delta)
        if best is None:
            unmatched.append(line)
        else:
            matched.append({**line, "peak": best[0], "delta_two_theta_deg": best[1]})
    total = sum(line["intensity_rel"] for line in lines)
    score = (sum(line["intensity_rel"] for line in matched) / total) if total else 0.0
    strongest = max(lines, key=lambda line: line["intensity_rel"]) if lines else None
    return {
        "phase": reference.id, "role": reference.role,
        "structure_type": reference.structure_type,
        "cod_id": reference.source["cod_id"],
        "lines_in_range": len(lines), "matched": matched, "unmatched": unmatched,
        "weighted_match_fraction": score,
        "strongest_line_matched": bool(strongest is not None and any(
            line["two_theta_deg"] == strongest["two_theta_deg"] for line in matched)),
        "tolerance_deg": tolerance_deg, "offset_applied_deg": offset_deg,
    }
