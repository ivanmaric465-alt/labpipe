"""Your own analyses (kind `imported`): files made outside labpipe, shown in the report.

One implementation for every entry of lab/methods.yaml with `kind: imported` (made by
`labpipe methods import`). labpipe does not redo or redraw anything: it reads the files
(read-only), hashes them, copies what the report shows and checks what a reader needs.

* **One measurement = the files with the same stem in the same folder** (`S-9_raman.png`,
  `.csv`, `.md`). The scanner makes one primary file per stem (`primary_of`: table, then image,
  then text, then PDF; the first by extension) the entry; the others are its companions
  (status duplicate, "part of the measurement"), found again here by the same rule.
* results.json: each file with its sha256 and kind (image, table, text, pdf); tables copied
  verbatim as strings (header and the first `max_rows` rows), text as written, images
  referenced; named `quantities` (config) parsed from a table column (copy, parse the number
  only); the checks; `made_by: "outside labpipe"`.
* outputs: results.json, plot.png (the first image as PNG, or a placeholder without one),
  figure-N.png (the other images), provenance.json with every source's hash. No plot.pdf:
  there is no labpipe figure to draw. `labpipe replay` checks the sources and copies instead
  of redrawing (replay.py).

Settings (lab/methods/<name>/config.yaml): `quantities`, `max_rows`.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from labpipe import labmethods
from labpipe import provenance as prov
from labpipe import settings as settings_mod
from labpipe.safety import assert_not_protected, assert_pipeline_writable, atomic_write_bytes

KIND = "imported"
MADE_BY = "outside labpipe"
RESULTS_SCHEMA = "labpipe.imported.results/1"
# By extension, in the order that picks the primary file of a measurement: table, image, text, PDF.
EXTENSIONS = {"table": (".csv", ".tsv", ".xlsx", ".xls"),
              "image": (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"),
              "text": (".md", ".txt"), "pdf": (".pdf",)}
ORDER = [suffix for suffixes in EXTENSIONS.values() for suffix in suffixes]
EXCEL = (".xlsx", ".xls")
MAX_TEXT_CHARS = 20_000
SAMPLE_ROWS = 20            # lines looked at to find a table's delimiter
TEMPLATE_MARK = "TODO"      # what an unfilled README.md still says


class AnalysisError(RuntimeError):
    """Unusable input; the message is recorded as the failure reason."""


# --- which files belong together (the scanner uses these) ---------------------------------

def _rank(suffix: str) -> tuple[int, str]:
    suffix = suffix.lower()
    return (ORDER.index(suffix) if suffix in ORDER else len(ORDER), suffix)


def primary_of(suffixes: set[str] | list[str]) -> str:
    """The extension of the file that stands for a measurement: table before image before
    text before PDF; within a kind the order of `EXTENSIONS`, other extensions by name."""
    return min((s.lower() for s in suffixes), key=_rank)


def kind_of(path: Path | str) -> str:
    """image | table | text | pdf | other, by extension (a .txt is a table when it has columns)."""
    suffix = Path(path).suffix.lower()
    return next((kind for kind, suffixes in EXTENSIONS.items() if suffix in suffixes), "other")


def siblings(input_path: Path, context: dict[str, Any], method: str) -> list[Path]:
    """The files of this measurement: same folder, same stem, claimed by this entry's patterns
    (the scanner's rule), the primary first, the others by kind then name."""
    from labpipe.registry import classify, load_methods
    methods = load_methods()
    folder = PurePosixPath(context.get("relative_path") or input_path.name).parent
    stem = input_path.stem.lower()
    found = []
    for path in sorted(input_path.parent.iterdir()):
        if path.is_symlink() or not path.is_file() or path.stem.lower() != stem:
            continue
        if path == input_path or classify((folder / path.name).as_posix(), methods) == (
                method, "analyse"):
            found.append(path)
    found.sort(key=lambda p: (p != input_path, _rank(p.suffix), p.name))
    return found


# --- reading (read-only) -------------------------------------------------------------------

def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _splitters(suffix: str):
    delimited = [(name, lambda line, d=d: next(csv.reader([line], delimiter=d)))
                 for name, d in (("comma", ","), ("semicolon", ";"), ("tab", "\t"))]
    if suffix == ".tsv":
        delimited.insert(0, delimited.pop(2))
    spaces = [("spaces", lambda line: re.split(r"\t|\s{2,}", line.strip())),
              ("whitespace", lambda line: line.split())]
    return delimited + spaces if suffix != ".txt" else spaces[1:] + delimited + spaces[:1]


def parse_table(text: str, suffix: str) -> tuple[str, list[list[str]]] | None:
    """(delimiter name, rows as the strings of the file; the first is the header), or None when
    the text has no columns (every one of the first lines splits into the same number, 2 or
    more). Nothing is converted."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    for name, split in _splitters(suffix):
        try:
            counts = {len(split(line)) for line in lines[:SAMPLE_ROWS]}
            if len(counts) == 1 and counts.pop() >= 2:
                return name, [[cell.strip() for cell in split(line)] for line in lines]
        except csv.Error:
            continue
    return None


def has_units(header: list[str]) -> bool:
    """Does a header cell name a unit in brackets: `Raman shift (cm-1)`, `Intensity [a.u.]`?"""
    return any(re.search(r"\([^)]+\)|\[[^\]]+\]", cell) for cell in header)


def number(text: str | None) -> float | None:
    """A cell as a number (a decimal comma is read as a point); None if it is not one."""
    if text is None:
        return None
    try:
        value = float(text.strip().replace("−", "-").replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def description(name: str) -> tuple[str | None, str | None]:
    """(first paragraph of lab/methods/<name>/README.md, why there is none): the paragraph is
    what the card shows; a missing file, an empty page or one that still holds the template's
    TODO has none."""
    path = labmethods.methods_dir() / name / "README.md"
    if not path.is_file():
        return None, f"lab/methods/{name}/README.md does not exist"
    text = re.sub(r"<!--.*?-->", "", read_text(path), flags=re.S)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", "\n".join(lines)) if b.strip()]
    if not blocks:
        return None, "the description (first paragraph) is empty"
    if TEMPLATE_MARK in blocks[0] or "{{" in blocks[0]:
        return None, f"the description is still the template text ({TEMPLATE_MARK})"
    return blocks[0], None


# --- run -----------------------------------------------------------------------------------

def _check(code: str, severity: str, message: str, **more: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, **more}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _png(path: Path) -> bytes:
    """An image as PNG bytes, the same every time (pixels only; a colour profile is kept)."""
    from PIL import Image
    with Image.open(path) as opened:
        opened.seek(0)
        transparent = "A" in opened.getbands() or (opened.mode == "P"
                                                   and "transparency" in opened.info)
        image = opened.convert("RGBA" if transparent else "L" if opened.mode in ("L", "1")
                               else "RGB")
        profile = opened.info.get("icc_profile")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", **({"icc_profile": profile} if profile else {}))
    return buffer.getvalue()


def _placeholder(style: dict[str, Any]) -> bytes:
    from labpipe.plotting import acs
    acs.apply_acs(style)
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(3.25, 2.0))
    axis.set_axis_off()
    axis.text(0.5, 0.5, "no figure: table only", ha="center", va="center", color="0.4")
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=300, metadata={"Software": None})
    plt.close(figure)
    return buffer.getvalue()


def _table_entry(path: Path, rel: str, max_rows: int, checks: list) -> dict[str, Any] | None:
    """The table of a file as results.json holds it (None: not a table, see the checks)."""
    suffix = path.suffix.lower()
    parsed = parse_table(read_text(path), suffix)
    if parsed is None:
        if suffix in (".csv", ".tsv"):
            checks.append(_check("table_unreadable", "review", f"{path.name}: no columns found "
                                 "(every line should split into the same number of cells)",
                                 file=rel))
        return None
    delimiter, rows = parsed
    header, body = rows[0], rows[1:]
    if not has_units(header):
        checks.append(_check("table_no_units", "warning",
                             f"{path.name}: no unit in the header (write it in brackets, e.g. "
                             "`Raman shift (cm-1)`), so values cannot be quoted with units",
                             file=rel))
    shown = body[:max_rows]
    if len(body) > max_rows:
        checks.append(_check("table_truncated", "info", f"{path.name}: the first {max_rows} of "
                             f"{len(body)} rows are copied", file=rel))
    return {"file": rel, "name": path.name, "delimiter": delimiter, "columns": header,
            "rows": shown, "n_rows": len(body), "truncated": len(body) > max_rows,
            "_all_rows": body}


def _text_entry(path: Path, rel: str, sha: str, checks: list) -> dict[str, Any]:
    text = read_text(path)
    if len(text) > MAX_TEXT_CHARS:
        checks.append(_check("text_truncated", "info", f"{path.name}: the first "
                             f"{MAX_TEXT_CHARS} characters are copied", file=rel))
    return {"file": rel, "name": path.name, "sha256": sha, "text": text[:MAX_TEXT_CHARS],
            "truncated": len(text) > MAX_TEXT_CHARS}


def _quantities(configured: dict[str, Any], tables: list[dict[str, Any]],
                checks: list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, spec in (configured or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        column, sigma_column = spec.get("column"), spec.get("sigma_column")
        try:
            row = int(spec.get("row") or 1)
        except (TypeError, ValueError):
            row = 0
        table = next((t for t in tables if column in t["columns"]), None)
        if table is None:
            checks.append(_check("quantity_column_missing", "review",
                                 f"{name}: no table has the column {column!r}", quantity=name))
            continue
        cells = table["_all_rows"]
        read = lambda col: (cells[row - 1][table["columns"].index(col)]       # noqa: E731
                            if 0 < row <= len(cells) and col in table["columns"]
                            and table["columns"].index(col) < len(cells[row - 1]) else None)
        value = number(read(column))
        if value is None:
            checks.append(_check("quantity_not_numeric", "review",
                                 f"{name}: row {row} of column {column!r} in {table['name']} is "
                                 f"{read(column)!r}, not a number", quantity=name))
            continue
        sigma = number(read(sigma_column)) if sigma_column else None
        if sigma_column and sigma is None:
            checks.append(_check("quantity_sigma_unreadable", "warning",
                                 f"{name}: sigma column {sigma_column!r} is missing or not a "
                                 "number in that row; no sigma recorded", quantity=name))
        if len(cells) > 1 and not spec.get("row"):
            checks.append(_check("quantity_row_assumed", "info",
                                 f"{name}: {table['name']} has {len(cells)} rows; the value is "
                                 "row 1 (set `row:` to choose another)", quantity=name))
        unit = str(spec.get("unit") or "")
        out[name] = {"value": value, "sigma": sigma, "unit": unit,
                     "label": str(spec.get("label") or f"{name}" + (f" ({unit})" if unit else "")),
                     "table": table["name"], "column": column, "sigma_column": sigma_column,
                     "row": row, "text": read(column)}
    return out


def run(input_path: Path, out_dir: Path, *, settings: dict | None = None,
        settings_sources: list | None = None, context: dict[str, Any] | None = None,
        pipeline: bool = True) -> dict[str, Any]:
    context = context or {}
    method = context.get("method")
    if not method:
        raise AnalysisError("no method name in the context (imported analyses run through "
                            "their entry in lab/methods.yaml)")
    out_dir = Path(out_dir)
    (assert_pipeline_writable if pipeline else assert_not_protected)(out_dir)
    if settings is None:
        settings, settings_sources = settings_mod.resolve(
            method, context.get("sample_id"), context.get("measurement_id"))
    input_path = Path(input_path)
    input_sha = prov.sha256_file(input_path)
    max_rows = settings.get("max_rows", 200)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise AnalysisError(f"max_rows must be a whole number above 0, not {max_rows!r}")
    if not isinstance(settings.get("quantities") or {}, dict):
        raise AnalysisError("quantities must be a list of named quantities (name: column: ...)")

    folder = PurePosixPath(context.get("relative_path") or input_path.name).parent
    files, tables, texts, images, copies = [], [], [], [], {}
    checks: list[dict[str, Any]] = []
    for path in siblings(input_path, context, method):
        rel, suffix, kind = (folder / path.name).as_posix(), path.suffix.lower(), kind_of(path)
        record = {"path": rel, "name": path.name, "sha256": prov.sha256_file(path),
                  "kind": kind, "size_bytes": path.stat().st_size, "primary": path == input_path}
        try:
            if kind == "image":
                name = "plot.png" if not images else f"figure-{len(images) + 1}.png"
                copies[name] = _png(path)
                images.append({"file": name, "caption": f"{path.name}, as you made it"})
                record["copy"] = name
            elif kind == "pdf":
                record["copy"] = f"file-{_safe(path.name)}"
                copies[record["copy"]] = path.read_bytes()
            elif suffix in EXCEL:
                checks.append(_check("excel_not_read", "review",
                                     f"{path.name} is an Excel file; Excel is not read yet: "
                                     "export the sheet as CSV (File > Save as > CSV) next to it",
                                     file=rel))
            elif kind == "table" or suffix == ".txt":
                table = _table_entry(path, rel, max_rows, checks)
                if table is not None:
                    tables.append({**table, "sha256": record["sha256"]})
                    record["kind"] = "table"
                elif suffix == ".txt":
                    record["kind"] = "text"
                    texts.append(_text_entry(path, rel, record["sha256"], checks))
            elif kind == "text":
                texts.append(_text_entry(path, rel, record["sha256"], checks))
        except Exception as exc:     # recorded as a check; the other files are still read
            code = "image_unreadable" if kind == "image" else "file_unreadable"
            checks.append(_check(code, "review", f"cannot read {path.name}: {exc}", file=rel))
        files.append(record)

    quantities = _quantities(settings.get("quantities") or {}, tables, checks)
    text, reason = description(method)
    if reason:
        checks.append(_check("description_missing", "warning", reason, file=(
            f"lab/methods/{method}/README.md")))
    if images and not tables:
        checks.append(_check("figure_without_table", "warning",
                             "a figure but no table: its numbers cannot be quoted"))
    for table in tables:
        table.pop("_all_rows")
    status = "review" if any(c["severity"] == "review" for c in checks) else "ok"
    readme = labmethods.methods_dir() / method / "README.md"
    results = {
        "schema": RESULTS_SCHEMA, "status": status, "kind": KIND, "made_by": MADE_BY,
        "method": method, "sample_id": context.get("sample_id"),
        "measurement_id": context.get("measurement_id"),
        "input": {"file_name": input_path.name, "sha256": input_sha},
        "description": {"file": f"lab/methods/{method}/README.md", "first_paragraph": text,
                        "sha256": prov.sha256_file(readme) if readme.is_file() else None},
        "files": files, "tables": tables, "texts": texts, "figures": images,
        "quantities": quantities, "units": {n: q["unit"] for n, q in quantities.items()},
        "checks": checks,
    }

    # --- outputs ------------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    if not images:
        from labpipe.plotting import acs
        copies["plot.png"] = _placeholder(acs.style_of(settings))
    for name, data in copies.items():
        atomic_write_bytes(out_dir / name, data, pipeline=pipeline)
    atomic_write_bytes(out_dir / "results.json",
                       (json.dumps(results, indent=1, ensure_ascii=False, allow_nan=False)
                        + "\n").encode(), pipeline=pipeline)
    outputs = {name: prov.sha256_file(out_dir / name) for name in ["results.json", *copies]}
    record = prov.build_record(
        method=method, entry_point="labpipe.methods.imported.analyze.run",
        input_path=input_path, input_sha256=input_sha, context=context, settings=settings,
        settings_sources=settings_sources, outputs=outputs, out_dir=out_dir,
        extra={"made_by": MADE_BY,
               "sources": [{"path": str(input_path.parent / f["name"]),
                            "relative_path": f["path"], "raw_root": context.get("raw_root"),
                            "sha256": f["sha256"], "kind": f["kind"]} for f in files],
               "references": ([{"file": f"lab/methods/{method}/README.md", "root": "project",
                                "sha256": results["description"]["sha256"]}]
                              if results["description"]["sha256"] else [])})
    atomic_write_bytes(out_dir / "provenance.json",
                       (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode(),
                       pipeline=pipeline)
    return {"status": status, "out_dir": str(out_dir),
            "checks": [c for c in checks if c["severity"] == "review"], "outputs": outputs,
            **settings_mod.fingerprints(settings)}
