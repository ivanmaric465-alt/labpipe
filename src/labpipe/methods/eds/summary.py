"""Compact, readable EDS results for the report and the analyst (formatting only)."""

from __future__ import annotations

from typing import Any


def _composition(values: dict[str, float]) -> str:
    return " · ".join(f"{name} {value:.1f}" for name, value in
                      sorted(values.items(), key=lambda item: -item[1]))


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    elements = results["composition"]["elements"]
    non_metals = set(results["metal_basis"]["excluded_non_metals"])
    overview = {
        "Metals (at%, metal basis)": _composition(results["metal_basis"]["at_percent"]) or "none",
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
