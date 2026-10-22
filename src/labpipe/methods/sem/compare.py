"""SEM comparison figure for "Compare samples": the grid of images by gallery magnification.

Rows = samples (report order), columns = `gallery.magnifications`; each cell is the image the
per-sample gallery would choose for that magnification (`gallery.select`, so exclude and the
closest-on-a-log-scale rule are the same), shown by its `thumb.jpg`. Nothing is computed.
"""

from __future__ import annotations

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
            # one target at a time: the image closest to it (include would add more, not wanted)
            picked = gallery.select(items, {"magnifications": [target],
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
             "caption": ("The image closest to each magnification (log scale, both series "
                         "compete), as in the per-sample gallery; – where a sample has none."),
             "columns": columns, "rows": rows}]
