"""Two/three-column .xy reader (secondary format; .xrdml is primary).

Derived from upstream-eln@a3931a4 utils/scientific_reports/charts.py::_charts_from_xy.
Changes from upstream: skipped (non-numeric, non-comment) lines are counted
and reported instead of silently dropped; a third numeric column is kept as
the recorded intensity uncertainty; no point decimation; size limit is a
parameter. An .xy file carries no wavelength, so the caller must supply one
and the result is flagged for review.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from labpipe.methods.xrd.io_xrdml import DEFAULT_MAX_BYTES, PatternFile, Scan

_SPLIT = re.compile(r"[\s,;]+")


def read_xy(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> PatternFile:
    path = Path(path)
    with open(path, "rb") as stream:  # read-only
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"file exceeds the {max_bytes} byte limit")
    try:
        content = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
        encoding = "cp1252"

    xs: list[float] = []
    ys: list[float] = []
    errors: list[float] = []
    skipped: list[int] = []
    comment_lines = 0
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", ";", "!")):
            comment_lines += 1
            continue
        cells = _SPLIT.split(stripped)
        try:
            values = [float(cell) for cell in cells[:3]]
        except ValueError:
            skipped.append(line_number)
            continue
        if len(values) < 2 or not all(math.isfinite(value) for value in values):
            skipped.append(line_number)
            continue
        xs.append(values[0])
        ys.append(values[1])
        if len(values) == 3:
            errors.append(values[2])
    if len(xs) < 2:
        raise ValueError("not enough XY scan data")

    warnings = ["no wavelength in .xy file; configured default wavelength used"]
    if skipped:
        preview = ", ".join(str(number) for number in skipped[:10])
        warnings.append(f"{len(skipped)} non-numeric line(s) skipped (lines {preview}"
                        f"{', …' if len(skipped) > 10 else ''})")
    scan = Scan(
        two_theta=np.asarray(xs, dtype=float), intensity=np.asarray(ys, dtype=float),
        intensity_unit="counts (assumed)", counting_time_s=None, attenuation_applied=False,
        metadata={"scan_index": 0, "point_count": len(xs), "position_source": "column 1",
                  "has_error_column": len(errors) == len(xs) and bool(errors)})
    if scan.metadata["has_error_column"]:
        scan.metadata["recorded_errors"] = np.asarray(errors, dtype=float)
    return PatternFile(
        scans=[scan],
        metadata={"format": "xy", "encoding": encoding, "comment_lines": comment_lines,
                  "skipped_lines": len(skipped), "sample_id_in_file": None,
                  "measurements": [{"wavelength": {}}]},
        warnings=warnings)
