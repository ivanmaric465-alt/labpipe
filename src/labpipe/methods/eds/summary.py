"""Compact, readable EDS results for the report and the analyst (formatting only).

Besides `summarize` and `quantities`, the report calls two optional functions that only lay
numbers out (nothing is computed): `table(results)` for the card's key results and
`overview_tables(items)` for the overview (one row per measurement, one column per element in
a fixed order). Both return table specs the report renders (see `report._spec_table`).
"""

from __future__ import annotations

import re
from typing import Any

CAPTION = ("electron image, INCA spectrum and composition bars: at% of the metals (metal "
           "basis), dark = measured, light = nominal; no error bars, the export has no "
           "uncertainties.")

# Symbols by atomic number (H = 1 ... Cm = 96): the column order of the tables.
_SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
            "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
            "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At "
            "Rn Fr Ra Ac Th Pa U Np Pu Am Cm").split()
ATOMIC_NUMBER = {symbol: i + 1 for i, symbol in enumerate(_SYMBOLS)}


def _by_atomic_number(elements) -> list[str]:
    """Atomic number order; an element not in the table goes last, by symbol."""
    return sorted(set(elements), key=lambda e: (ATOMIC_NUMBER.get(e, 10_000), e))


def _composition(values: dict[str, float], not_detected: dict[str, float] | None = None) -> str:
    """`not_detected`: elements recorded negative, shown as n.d. (their value is 0)."""
    not_detected = not_detected or {}
    return " · ".join(f"{name} n.d. (negative)" if name in not_detected else f"{name} {value:.1f}"
                      for name, value in
                      sorted(values.items(), key=lambda item: -item[1]))


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    elements = results["composition"]["elements"]
    non_metals = set(results["metal_basis"]["excluded_non_metals"])
    overview = {
        "Metals (at%, metal basis)": _composition(
            results["metal_basis"]["at_percent"],
            results["metal_basis"].get("taken_as_zero")) or "none",
        "All elements (wt%)": _composition({e["element"]: e["weight_percent"] for e in elements}),
        "Non-metals (wt%)": _composition({e["element"]: e["weight_percent"] for e in elements
                                          if e["element"] in non_metals}) or "none",
    }
    comparison = results.get("nominal_comparison")
    if comparison:
        overview["Measured − nominal (at% points)"] = " · ".join(
            f"{row['element']} {row['difference_at_percent']:+.1f}" for row in comparison["rows"]
            if row["difference_at_percent"] is not None)
    else:
        overview["Nominal"] = "not given"
    checks = results.get("checks", [])
    return {
        "overview": overview,
        "processing": results.get("processing"),
        "check_before_using": [{"code": c["code"], "text": c["message"]}
                               for c in checks if c["severity"] == "review"],
        "worth_knowing": [c["message"] for c in checks if c["severity"] in ("warning", "info")],
    }


def quantities(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Named numbers for comparing samples (`labpipe dataset`); copied from results.json.

    - `eds.at.<element>`: metal-basis composition (no σ in the results).
    - `eds.at_minus_nominal.<element>`: measured − nominal, when the sample has a nominal.
    """
    units = results.get("units") or {}
    out: dict[str, dict[str, Any]] = {}
    for element, value in results["metal_basis"]["at_percent"].items():
        out[f"eds.at.{element}"] = {
            "value": value, "sigma": None,
            "unit": units.get("metal_basis.at_percent", "at%"),
            "key": f"metal_basis.at_percent.{element}"}
    for i, row in enumerate((results.get("nominal_comparison") or {}).get("rows") or []):
        if row.get("difference_at_percent") is not None:
            out[f"eds.at_minus_nominal.{row['element']}"] = {
                "value": row["difference_at_percent"], "sigma": None,
                "unit": units.get("difference_at_percent", "at% points"),
                "key": f"nominal_comparison.rows[{i}].difference_at_percent"}
    return out


# --- tables (layout only) --------------------------------------------------------------------

def _low_elements(results: dict[str, Any]) -> set[str]:
    """Elements the `low_concentration` check lists: its `elements`, or (results from before
    that field) the symbols in its message."""
    found: set[str] = set()
    for check in results.get("checks") or []:
        if check.get("code") == "low_concentration":
            found.update(check.get("elements") or re.findall(
                r"\b([A-Z][a-z]?) [-−]?\d[\d.]* wt%", check.get("message", "")))
    return found


def _zeroed(results: dict[str, Any]) -> tuple[dict[str, float], str]:
    """(element -> recorded negative value, its unit) of `metal_basis.taken_as_zero`."""
    basis = results.get("metal_basis") or {}
    unit = "wt%" if str(basis.get("computed_from", "")).startswith("weight_percent") else "at%"
    return basis.get("taken_as_zero") or {}, unit


def _number(value: float) -> str:
    return f"{value:.1f}".replace("-", "−")


def _columns(elements: list[str], unit: str) -> list[dict[str, str]]:
    return [{"label": e, "unit": unit, "align": "r"} for e in elements]


def _metal_cell(results: dict[str, Any], element: str) -> dict[str, Any] | str:
    zeroed, unit = _zeroed(results)
    if element in zeroed:
        return {"text": "n.d.", "state": "nd",
                "title": f"recorded {_number(zeroed[element])} {unit}, taken as 0 (review check)"}
    value = ((results.get("metal_basis") or {}).get("at_percent") or {}).get(element)
    if value is None:
        return {"text": "–", "state": "missing"}
    return {"text": _number(value), "bar": value / 100,
            "state": "low" if element in _low_elements(results) else None}


def _wt_values(results: dict[str, Any]) -> dict[str, float]:
    return {e["element"]: e["weight_percent"]
            for e in (results.get("composition") or {}).get("elements") or []}


def _wt_cell(results: dict[str, Any], element: str, top: float) -> dict[str, Any]:
    """`top`: the largest value of this column in the table (the bar is value / top)."""
    values = _wt_values(results)
    if element not in values:
        return {"text": "–", "state": "missing"}
    value = values[element]
    if value < 0 or element in _zeroed(results)[0]:
        return {"text": "n.d.", "state": "nd",
                "title": f"recorded {_number(value)} wt%, taken as 0 in the metal basis"}
    return {"text": _number(value), "bar": value / top if top > 0 else 0,
            "state": "low" if element in _low_elements(results) else None}


def _nominal(results: dict[str, Any]) -> dict[str, float | None]:
    rows = (results.get("nominal_comparison") or {}).get("rows") or []
    return {r["element"]: r.get("nominal_at_percent") for r in rows}


def _element_order(all_results: list[dict[str, Any]], with_nominal: bool = True
                   ) -> tuple[list[str], list[str]]:
    """(metals, non-metals) over the measurements, each by atomic number."""
    non_metals: set[str] = set()
    elements: set[str] = set()
    for r in all_results:
        non_metals |= set((r.get("metal_basis") or {}).get("excluded_non_metals") or [])
        elements |= set(_wt_values(r)) | set((r.get("metal_basis") or {}).get("at_percent") or {})
        elements |= set(_zeroed(r)[0])
        if with_nominal:
            elements |= set(_nominal(r))
    return (_by_atomic_number(elements - non_metals), _by_atomic_number(elements & non_metals))


def _states_note(rows_cells: list[Any]) -> list[str]:
    seen = {c.get("state") for cells in rows_cells for c in cells if isinstance(c, dict)}
    notes = []
    if "nd" in seen:
        notes.append("n.d.: INCA reported a negative value (not detected); taken as 0 in the "
                     "metal basis. Hover for the recorded value.")
    if "low" in seen:
        notes.append("Grey: near the detection limit (check low_concentration).")
    if "missing" in seen:
        notes.append("–: the element is not in this measurement.")
    return notes


def overview_tables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two tables over all measurements: metals (at%, metal basis) and all elements (wt%, as
    recorded). `items`: [{"results": results.json}], one row per item (and a grey "nominal"
    row under an item whose sample has a nominal composition, in the metals table)."""
    results = [item["results"] for item in items]
    metals, non_metals = _element_order(results)
    rows = []
    for i, r in enumerate(results):
        rows.append({"item": i, "cells": [_metal_cell(r, e) for e in metals]})
        nominal = _nominal(r)
        if nominal:
            rows.append({"item": None, "label": "nominal", "class": "nominal", "cells": [
                {"text": _number(nominal[e]), "bar": nominal[e] / 100}
                if nominal.get(e) is not None else {"text": "–", "state": "missing"}
                for e in metals]})
    metal_table = {"title": "Metals, at% of the metals (metal basis, sum 100)",
                   "columns": _columns(metals, "at%"), "rows": rows,
                   "notes": ["The bar in a cell is the value out of 100 at%."]
                   + _states_note([row["cells"] for row in rows])}
    wt_elements = _element_order(results, with_nominal=False)
    wt_elements = wt_elements[0] + wt_elements[1]
    tops = {e: max([_wt_values(r).get(e, 0) for r in results] + [0]) for e in wt_elements}
    wt_rows = [{"item": i, "cells": [_wt_cell(r, e, tops[e]) for e in wt_elements]}
               for i, r in enumerate(results)]
    wt_table = {"title": "All elements, wt% as recorded (metals, then non-metals)",
                "columns": _columns(wt_elements, "wt%"), "rows": wt_rows,
                "notes": ["The bar in a cell is the value relative to the largest of its column."]
                + _states_note([row["cells"] for row in wt_rows])}
    return [metal_table, wt_table]


def table(results: dict[str, Any]) -> dict[str, Any]:
    """The card's key results: one row per element (metals, then non-metals)."""
    metals, non_metals = _element_order([results])
    order = metals + non_metals
    nominal = _nominal(results)
    measured = (results.get("metal_basis") or {}).get("at_percent") or {}
    top = max(list(_wt_values(results).values()) + [0])
    rows = []
    for e in order:
        in_basis = e in metals
        n = nominal.get(e)
        diff = (measured[e] - n) if n is not None and e in measured else None
        rows.append([e, _metal_cell(results, e) if in_basis else {"text": "–", "state": "missing"},
                     _wt_cell(results, e, top),
                     {"text": _number(n), "state": "nominal"} if n is not None else "–",
                     f"{diff:+.1f}".replace("-", "−") if diff is not None else "–"])
    columns = [{"label": "Element", "align": "l"},
               {"label": "Metal basis", "unit": "at%", "align": "r"},
               {"label": "As recorded", "unit": "wt%", "align": "r"},
               {"label": "Nominal", "unit": "at%", "align": "r"},
               {"label": "Measured − nominal", "unit": "at% points", "align": "r"}]
    return {"columns": columns, "rows": rows,
            "notes": _states_note([r[1:3] for r in rows])}
