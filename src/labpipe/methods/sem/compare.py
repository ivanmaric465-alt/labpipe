"""SEM comparison figure for "Compare samples": the grid of images by gallery magnification.

Rows = samples (report order), columns = `gallery.magnifications`. Each image belongs to the
column nearest its own magnification (log scale), so one image never fills two columns; among
a column's images the gallery's rule chooses (`gallery.select`: exclude, closest on a log
scale). A column no image is near stays empty ("–"). Shown by `thumb.jpg`. Nothing is
computed but the comparison of magnifications.
"""

from __future__ import annotations

import math
from typing import Any

from labpipe import settings as settings_mod
from labpipe.methods.sem import gallery
from labpipe.provenance import sha256_file
from labpipe.safety import PROJECT_ROOT


def _gallery_of(sample: str, context: dict[str, Any]) -> dict[str, Any]:
    """The sample's gallery settings (per-sample overrides apply, as in the report)."""
    try:
        return settings_mod.resolve("sem", sample, None)[0]["gallery"]
    except Exception:
        return (context.get("config") or {}).get("gallery") or {}


def _nearest(item: dict[str, Any], targets: list[int]) -> int | None:
    """The column (magnification) nearest the image's own, on a log scale; ties: the lower."""
    magnification = (item["results"].get("acquisition") or {}).get("magnification")
    if not magnification:
        return None
    return min(targets, key=lambda t: (abs(math.log(magnification / t)), t))


def figures(measurements: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    targets = list(((context.get("config") or {}).get("gallery") or {}).get("magnifications")
                   or [])
    if not measurements or not targets:
        return []
    samples: dict[str, list[dict[str, Any]]] = {}
    for m in measurements:
        samples.setdefault(m["sample"], []).append(
            {"entry": {"measurement_id": m["measurement_id"], "entry_id": m["entry_id"]},
             "results": m["results"], "measurement": m})
    columns = [{"key": str(t), "label": f"×{t:,}"} for t in targets]
    rows = []
    for sample, items in samples.items():
        settings = _gallery_of(sample, context)
        cells = {}
        for target in targets:
            # only the images nearest this column; then the gallery's rule (include is not used)
            near = [i for i in items if _nearest(i, targets) == target]
            picked = gallery.select(near, {"magnifications": [target],
                                           "exclude": settings.get("exclude")})
            if not picked:
                continue
            m = picked[0]["measurement"]
            path = PROJECT_ROOT / m["output_dir"] / "thumb.jpg"
            if not path.is_file():
                continue
            magnification = (m["results"].get("acquisition") or {}).get("magnification")
            cells[str(target)] = {
                "measurement_id": m["measurement_id"], "entry_id": m["entry_id"],
                "path": f"{m['output_dir']}/thumb.jpg", "sha256": sha256_file(path),
                "label": f"×{magnification:,} · {m['measurement_id']}"}
        rows.append({"sample": sample, "cells": cells})
    return [{"kind": "images", "name": "gallery", "title": "SEM images by magnification",
             "caption": ("Each image in the column nearest its magnification (log scale); "
                         "per column the gallery's choice; – where no image of the sample is "
                         "near that magnification. The label gives the true magnification."),
             "columns": columns, "rows": rows}]
