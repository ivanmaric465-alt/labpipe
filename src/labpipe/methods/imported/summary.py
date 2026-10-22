"""Compact, readable results of your own analyses for the report and the analyst (formatting only).

`summarize` gives the lines the analyst and `labpipe <name> summary` read; `quantities` offers the
named numbers of the config for `labpipe dataset` and the comparison figures (copied from
results.json, nothing computed); `table(results)` is the card's table: the first table of the
files, as the file has it; `more_tables` are the others.
"""

from __future__ import annotations

from typing import Any

CAPTION = "your figure, exported as it is: made outside labpipe, not redrawn or changed"
BADGE = "your analysis, made outside labpipe"


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    checks = results.get("checks", [])
    tables = results.get("tables") or []
    overview = {"Made by": results.get("made_by") or "outside labpipe",
                "Files": ", ".join(f"{f['name']} ({f['kind']})" for f in results.get("files") or [])
                or "none"}
    quantities = results.get("quantities") or {}
    if quantities:
        overview["Quantities"] = "; ".join(
            f"{name} {q['value']}" + (f" ± {q['sigma']}" if q.get("sigma") is not None else "")
            + (f" {q['unit']}" if q.get("unit") else "") for name, q in quantities.items())
    return {
        "overview": overview,
        "badge": BADGE,
        "source_files": [f["path"] for f in results.get("files") or []],
        "links": [(f["name"], f["copy"]) for f in results.get("files") or []
                  if f["kind"] == "pdf" and f.get("copy")],
        "more_tables": [{"title": t["name"], **_spec(t)} for t in tables[1:]],
        "description": (results.get("description") or {}).get("first_paragraph"),
        "texts": [{"name": t["name"], "text": t["text"]} for t in results.get("texts") or []],
        "check_before_using": [{"code": c["code"], "text": c["message"]}
                               for c in checks if c["severity"] == "review"],
        "worth_knowing": [c["message"] for c in checks if c["severity"] in ("warning", "info")],
    }


def quantities(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The named quantities of the config, as parsed from the table (value, σ or None, unit)."""
    return {name: {"value": q["value"], "sigma": q.get("sigma"), "unit": q.get("unit") or "",
                   "key": f"quantities[{name}].value", "label": q.get("label") or name}
            for name, q in (results.get("quantities") or {}).items()}


def _spec(table: dict[str, Any]) -> dict[str, Any]:
    width = len(table["columns"])
    shown = len(table["rows"])
    return {"columns": [{"label": c, "align": "l"} for c in table["columns"]],
            "rows": [list(row[:width]) + [""] * (width - len(row)) for row in table["rows"]],
            "notes": [f"{table['name']}: " + (f"the first {shown} of {table['n_rows']} rows"
                                              if table["truncated"] else f"{table['n_rows']} row(s)")
                      + ", exactly as in your file."]}


def table(results: dict[str, Any]) -> dict[str, Any] | None:
    """The card's table: the first table of the files, every cell as the file has it (the
    others are `more_tables` of `summarize`)."""
    tables = results.get("tables") or []
    return _spec(tables[0]) if tables else None
