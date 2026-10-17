"""Checks on agent-written notes.md files (the agent never computes numbers).

`labpipe notes verify --entry ID` passes only if
* every number in notes.md is a value from that measurement's results.json or
  its resolved settings (provenance.json), as written or rounded;
* every `review`-severity check code from results.json is mentioned;
* the notes name the sha256 of the results.json they describe.

Not checked as numbers: identifiers glued to letters (S-9, Kα1, RuO2, P03),
hkl in parentheses, ISO dates, list markers, and inline code containing a
letter (key paths, entry IDs). A number checked here is "found somewhere in
the results", not "used in the right place"; a human still reads the notes.
"""

from __future__ import annotations

import importlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterator

from labpipe import manifest as mf
from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT

_NUMBER = re.compile(r"(?<![\w.\-−])[-−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![0-9A-Za-z_])")
_EXEMPT = [
    re.compile(r"`[^`\n]*[A-Za-z][^`\n]*`"),            # key paths, entry IDs, codes
    re.compile(r"\(\s*-?\d\s*-?\d\s*-?\d\s*\)"),        # hkl: (111), (2 0 0)
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?"),  # dates and timestamps
    re.compile(r"(?m)^\s*(?:#+\s*)?\d+[.)]\s"),        # list markers, numbered headings
    re.compile(r"2θ"),                                  # the axis name
]
_SHA = re.compile(r"\b[0-9a-f]{64}\b")


def _walk_numbers(value: Any, path: str = "") -> Iterator[tuple[float, str]]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            yield float(value), path
    elif isinstance(value, str):
        for match in _NUMBER.finditer(value.replace("−", "-")):
            yield float(match.group().replace("−", "-")), path + " (text)"
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_numbers(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_numbers(item, f"{path}[{index}]")


def _decimals(text: str) -> int | None:
    """Decimal places written, or None for scientific notation."""
    if "e" in text.lower():
        return None
    return len(text.split(".", 1)[1]) if "." in text else 0


def _matches(text: str, value: float, known: float) -> bool:
    places = _decimals(text)
    if places is None:  # 1.2e-3: same significant digits
        mantissa = text.lower().split("e", 1)[0].lstrip("-")
        digits = len(mantissa.replace(".", "").lstrip("0")) or 1
        return known != 0 and float(f"{known:.{digits - 1}e}") == value
    return math.isclose(round(known, places), value, rel_tol=0, abs_tol=0.5 * 10 ** -(places + 3))


def numbers_in_notes(text: str) -> list[dict[str, Any]]:
    masked = text
    for pattern in _EXEMPT:
        masked = pattern.sub(lambda m: " " * len(m.group()), masked)
    masked = _SHA.sub(lambda m: " " * len(m.group()), masked)
    found = []
    for match in _NUMBER.finditer(masked):
        line = masked.count("\n", 0, match.start()) + 1
        found.append({"text": match.group(), "line": line,
                      "context": text.splitlines()[line - 1].strip()[:160]})
    return found


def verify(notes_path: Path, results: dict[str, Any], provenance: dict[str, Any] | None,
           results_sha256: str | None, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    text = notes_path.read_text(encoding="utf-8")
    known: list[tuple[float, str]] = list(_walk_numbers(results))
    if summary:  # `labpipe <method> summary`: the same numbers, formatted by code
        known += list(_walk_numbers(summary, "summary"))
    if provenance:
        known += list(_walk_numbers(provenance.get("parameters", {}).get("resolved", {}),
                                    "provenance.parameters.resolved"))
    matched, unmatched = [], []
    for item in numbers_in_notes(text):
        value = float(item["text"].replace("−", "-"))
        source = next((path for number, path in known
                       if _matches(item["text"].replace("−", "-"), value, number)), None)
        (matched if source else unmatched).append({**item, "source": source} if source else item)
    review_codes = sorted({c["code"] for c in results.get("checks", [])
                           if c.get("severity") == "review"})
    missing_codes = [code for code in review_codes if code not in text]
    problems = []
    if unmatched:
        problems.append(f"{len(unmatched)} number(s) not found in results.json or the settings")
    if missing_codes:
        problems.append(f"review checks not mentioned: {', '.join(missing_codes)}")
    if results_sha256 and results_sha256 not in text:
        problems.append("notes do not name the results.json sha256 they describe "
                        "(they may describe an older analysis)")
    return {"notes": str(notes_path.relative_to(PROJECT_ROOT)) if notes_path.is_relative_to(
                PROJECT_ROOT) else str(notes_path),
            "ok": not problems, "problems": problems, "numbers_checked": len(matched) + len(unmatched),
            "unmatched": unmatched, "review_codes_required": review_codes,
            "matched": [{"text": m["text"], "line": m["line"], "source": m["source"]}
                        for m in matched]}


def verify_entry(config: dict, eid: str) -> dict[str, Any]:
    entry = mf.load(config)["entries"].get(eid)
    if not entry or not entry.get("output_dir"):
        return {"entry_id": eid, "ok": False, "problems": ["no analysed output for this entry"]}
    out = PROJECT_ROOT / entry["output_dir"]
    if not (out / "notes.md").exists():
        return {"entry_id": eid, "ok": False, "problems": [f"{entry['output_dir']}/notes.md missing"]}
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    provenance_path = out / "provenance.json"
    provenance = (json.loads(provenance_path.read_text(encoding="utf-8"))
                  if provenance_path.exists() else None)
    spec = load_methods().get(entry["method"], {})
    summary = (importlib.import_module(spec["summary"]).summarize(results)
               if spec.get("summary") else None)
    return {"entry_id": eid, **verify(out / "notes.md", results, provenance,
                                      entry.get("results_sha256"), summary)}


def todo(config: dict) -> list[dict[str, Any]]:
    """Analysed entries whose notes.md is missing or describes another results.json."""
    from labpipe.registry import load_methods
    without_notes = {name for name, spec in load_methods().items() if spec.get("notes") is False}
    rows = []
    for entry in mf.load(config)["entries"].values():
        if entry.get("status") not in ("done", "stale") or not entry.get("output_dir"):
            continue
        if entry["method"] in without_notes:  # methods analysed by code alone
            continue
        notes = PROJECT_ROOT / entry["output_dir"] / "notes.md"
        if not notes.exists():
            reason = "no notes.md"
        elif entry.get("results_sha256") and entry["results_sha256"] not in notes.read_text(
                encoding="utf-8"):
            reason = "notes.md describes a different results.json"
        else:
            continue
        rows.append({"entry_id": entry["entry_id"], "method": entry["method"],
                     "subagent": entry.get("subagent"), "sample_id": entry["sample_id"],
                     "output_dir": entry["output_dir"], "reason": reason})
    return sorted(rows, key=lambda row: row["output_dir"])


def retire(config: dict, eid: str) -> dict[str, Any]:
    """Move an entry's notes.md to _superseded/ (kept, never deleted) so it is rewritten."""
    from labpipe.pipeline import _move, _stamp
    entry = mf.load(config)["entries"].get(eid)
    if not entry or not entry.get("output_dir"):
        return {"entry_id": eid, "retired": None, "problem": "no analysed output for this entry"}
    notes = PROJECT_ROOT / entry["output_dir"] / "notes.md"
    if not notes.exists():
        return {"entry_id": eid, "retired": None, "problem": "no notes.md"}
    target = (notes.parent.parent / "_superseded" / entry["measurement_id"]
              / f"{_stamp()}-notes" / "notes.md")
    _move(notes, target, config)
    return {"entry_id": eid, "retired": str(target.relative_to(PROJECT_ROOT))}
