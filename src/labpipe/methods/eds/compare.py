"""EDS comparison figure for "Compare samples": the composition bars (formatting only).

One 100 % bar per measurement on the metal basis (at%, copied from results.json; a value INCA
recorded negative is already 0 there), the metals in atomic-number order, one fixed colour per
element in every figure and table. A sample with a nominal composition gets its nominal bar
(`nominal_comparison`, as the tables use it) right under the measured one. It replaces the
per-element dot plots `eds.at.*`; they are drawn only when the report setting names them.
"""

from __future__ import annotations

from typing import Any

from labpipe.methods.eds.summary import ATOMIC_NUMBER, _element_order, _nominal

COVERS = ["eds.at.*"]

# One colour per element, fixed (never by position): Okabe-Ito and Paul Tol colours for the
# common catalyst metals, distinct from each other in hue and lightness where possible.
COLOURS = {
    "Ti": "#882255", "Fe": "#332288", "Co": "#44AA99", "Ni": "#DDCC77", "Cu": "#CC6677",
    "Ru": "#117733", "Rh": "#88CCEE", "Pd": "#D55E00", "Ag": "#999933", "Ir": "#AA4499",
    "Pt": "#0072B2", "Au": "#E69F00",
}
# Any other element: this cycle, by atomic number (deterministic).
FALLBACK = ("#4C78A8", "#F58518", "#54A24B", "#B279A2", "#9D755D", "#72B7B2", "#EECA3B",
            "#FF9DA6")


def colour(element: str) -> str:
    if element in COLOURS:
        return COLOURS[element]
    return FALLBACK[ATOMIC_NUMBER.get(element, sum(map(ord, element))) % len(FALLBACK)]


def figures(measurements: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    if not measurements:
        return []
    metals = _element_order([m["results"] for m in measurements])[0]
    rows = []
    for m in measurements:
        measured = (m["results"].get("metal_basis") or {}).get("at_percent") or {}
        nominal = {e: v for e, v in _nominal(m["results"]).items() if v is not None}
        rows.append({
            "sample": m["sample"], "measurement_id": m["measurement_id"],
            "entry_id": m["entry_id"],
            "segments": [{"category": e, "value": measured[e]} for e in metals if e in measured],
            "nominal_segments": ([{"category": e, "value": nominal[e]}
                                  for e in metals if e in nominal] or None),
            "results_path": m["results_path"], "results_sha256": m["results_sha256"]})
    return [{"kind": "bars", "name": "composition", "title": "EDS composition",
             "caption": ("Metal basis: at% of the metals, non-metals left out, sum 100. "
                         "No error bars, the INCA export has no uncertainties."),
             "unit_label": "at% of the metals (metal basis)",
             "categories": [{"name": e, "color": colour(e)} for e in metals], "rows": rows}]
