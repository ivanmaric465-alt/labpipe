"""Which SEM images of a sample go into the report (deterministic).

For each target magnification in `gallery.magnifications`, the image whose
magnification is closest on a log scale (×10k is as far from ×20k as ×50k from
×100k). Ties go to the earlier series letter, then the lower image number, then the
measurement ID. Both series (a, b, …) compete. `gallery.exclude` removes images,
`gallery.include` always adds them. An image is shown once even if it is the
closest for two targets.
"""

from __future__ import annotations

import math
from typing import Any


def select(items: list[dict[str, Any]], gallery: dict[str, Any]) -> list[dict[str, Any]]:
    """items: [{"entry": manifest entry, "results": results.json}] of one sample.
    Returns the chosen items, each with "targets" (the magnifications it stands for)."""
    exclude = set(gallery.get("exclude") or [])
    candidates = [item for item in items
                  if item["entry"]["measurement_id"] not in exclude
                  and (item["results"].get("acquisition") or {}).get("magnification")]

    def order(item: dict[str, Any]) -> tuple:
        return (item["results"].get("series") or "~", item["results"].get("index") or 0,
                item["entry"]["measurement_id"])

    chosen: dict[str, dict[str, Any]] = {}
    for target in gallery.get("magnifications") or []:
        if not candidates:
            break
        best = min(candidates, key=lambda item: (
            abs(math.log(item["results"]["acquisition"]["magnification"] / target)), order(item)))
        key = best["entry"]["measurement_id"]
        chosen.setdefault(key, {**best, "targets": []})["targets"].append(target)
    for measurement in gallery.get("include") or []:
        match = next((i for i in items if i["entry"]["measurement_id"] == measurement), None)
        if match and measurement not in chosen:
            chosen[measurement] = {**match, "targets": []}
    return sorted(chosen.values(), key=lambda item: (
        (item["results"].get("acquisition") or {}).get("magnification") or 0, order(item)))
