"""EDS quantification (Oxford INCA HTML reports): method `eds` (design: docs/methods/eds.md).

One measurement = one INCA `.htm` export: processing notes, standards, one
Element / Weight% / Atomic% table and two linked JPGs (electron image with the
analysed area, spectrum). The JPGs and `.ipj` project files belong to it.

Computed here (code, never the agent): the composition as recorded; the
metal-only composition (atomic % of the elements not listed in `non_metals`,
renormalised to 100, from wt% / standard atomic weight because INCA rounds at%
to 0.01); with a nominal composition for the sample
(`nominal_at_percent`, also renormalised over its metals): measured − nominal and
measured / nominal per metal. No flag on the deviation (user's decision,
2026-10-17); the numbers are shown.

The INCA table reading follows eln_refactor `utils/instrument_reports.py`
(a3931a4) without its bugs: σ columns are kept as uncertainty, not composition;
decimal commas are read; image links match file names case-insensitively.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from labpipe import provenance as prov
from labpipe import settings as settings_mod
from labpipe.plotting.acs import apply_acs
from labpipe.safety import assert_not_protected, assert_pipeline_writable, atomic_write_bytes

METHOD = "eds"
RESULTS_SCHEMA = "labpipe.eds.results/1"
REPORT_SUFFIXES = (".htm", ".html")
MAX_REPORT_BYTES = 512 * 1024


class AnalysisError(RuntimeError):
    """Unusable input; the message is recorded as the failure reason."""


# --- reading ------------------------------------------------------------------------------

class _Inca(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.images: list[str] = []
        self.text: list[str] = [""]
        self._in_table = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self.rows.append([])
        elif tag in ("td", "th") and self._in_table and self.rows:
            self.rows[-1].append("")
        elif tag == "img":
            source = dict(attrs).get("src")
            if source:
                self.images.append(source)
        elif tag in ("br", "p") and not self._in_table:
            self.text.append("")

    def handle_endtag(self, tag):
        if tag.lower() == "table":
            self._in_table = False

    def handle_data(self, data):
        if self._in_table and self.rows and self.rows[-1]:
            self.rows[-1][-1] += data
        elif not self._in_table:
            self.text[-1] += data


def _read_text(path: Path) -> str:
    raw = path.read_bytes()[:MAX_REPORT_BYTES]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _is_inca(text: str) -> bool:
    return "Processing option" in text and re.search(r"Weight\s*%", text) is not None


def _linked_images(report: Path) -> list[str]:
    parser = _Inca()
    parser.feed(_read_text(report))
    return parser.images


def _named_for(image: Path, report: Path) -> bool:
    """INCA names a report's images after it: S-10.htm -> s-100.jpg, s-101.jpg."""
    return image.stem.lower()[:-1] == report.stem.lower()


def recognise(path: Path) -> bool:
    """methods.yaml `recognise`: INCA reports, the JPGs they link or that are named for
    one (`images` in the settings may choose those), INCA .ipj projects."""
    suffix = path.suffix.lower()
    if suffix in REPORT_SUFFIXES:
        return _is_inca(_read_text(path))
    if suffix == ".ipj":
        return True
    if suffix in (".jpg", ".jpeg"):
        for report in sorted(path.parent.iterdir()):
            if report.suffix.lower() in REPORT_SUFFIXES and _is_inca(_read_text(report)) and \
                    (_named_for(path, report) or
                     path.name.lower() in (name.lower() for name in _linked_images(report))):
                return True
    return False


def _number(text: str) -> float | None:
    text = text.strip()
    return float(text.replace(",", ".")) if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", text) else None


def parse_report(text: str) -> dict[str, Any]:
    parser = _Inca()
    parser.feed(text)
    lines = [line.strip() for line in parser.text]
    header_index = next((i for i, row in enumerate(parser.rows)
                         if row and row[0].strip().lower() == "element"), None)
    if header_index is None:
        raise AnalysisError("no Element / Weight% table in the report")
    header = [cell.strip() for cell in parser.rows[header_index]]
    columns = {name: i for i, name in enumerate(header) if name}

    def column(*words: str, sigma: bool = False) -> int | None:
        for name, i in columns.items():
            low = name.lower().replace(" ", "")
            if all(w in low for w in words) and ("sigma" in low) == sigma:
                return i
        return None

    weight, atomic = column("weight"), column("atomic")
    weight_sigma, atomic_sigma = column("weight", sigma=True), column("atomic", sigma=True)
    if weight is None:
        raise AnalysisError(f"table has no Weight% column (header {header})")
    elements, total, unread = [], None, []
    for row in parser.rows[header_index + 1:]:
        cells = [c.strip() for c in row]
        if not cells or not cells[0]:
            continue
        if cells[0].lower().startswith("total"):
            total = _number(cells[1]) if len(cells) > 1 else None
            continue
        match = re.fullmatch(r"([A-Z][a-z]?)\s*([KLMN]\w*)?", cells[0])
        get = lambda i: _number(cells[i]) if i is not None and i < len(cells) else None
        if not match or get(weight) is None:
            unread.append(cells)
            continue
        elements.append({"element": match.group(1), "line": match.group(2),
                         "weight_percent": get(weight), "atomic_percent": get(atomic),
                         "weight_percent_sigma": get(weight_sigma),
                         "atomic_percent_sigma": get(atomic_sigma)})

    def after(label: str) -> str | None:
        for line in lines:
            if line.lower().startswith(label.lower()):
                return line.split(":", 1)[1].strip() if ":" in line else line.split("=", 1)[-1].strip()
        return None

    standards, collecting = [], False
    for line in lines:
        if line.lower().startswith("standard"):
            collecting = True
            continue
        if collecting:
            match = re.fullmatch(r"([A-Z][a-z]?)\s+(\S+)\s*(.*)", line)
            if not match:
                break
            standards.append({"element": match.group(1), "standard": match.group(2),
                              "date": match.group(3) or None})
    spectrum_processing = None
    for i, line in enumerate(lines):
        if line.lower().startswith("spectrum processing") and i + 1 < len(lines):
            spectrum_processing = lines[i + 1] or None
    iterations = after("Number of iterations")
    comment = after("Comment")
    return {"elements": elements, "total_weight_percent": total, "unread_rows": unread,
            "processing_option": after("Processing option"),
            "spectrum_processing": spectrum_processing,
            "iterations": int(iterations) if iterations and iterations.isdigit() else None,
            "standards": standards, "comment": comment or None,
            "linked_images": parser.images}


def find_image(report: Path, link: str) -> Path | None:
    for candidate in sorted(report.parent.iterdir()):
        if candidate.name.lower() == Path(link).name.lower():
            return candidate
    return None


# --- numerics -----------------------------------------------------------------------------

# IUPAC standard atomic weights (abridged, 2021), g/mol: the metal basis is computed from
# wt% / atomic weight, because INCA rounds at% to 0.01 (0.03 at% carries ±17 % rounding)
# while the wt% of the same element has more significant digits.
ATOMIC_WEIGHT = {
    "H": 1.008, "Li": 6.94, "Be": 9.0122, "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999,
    "F": 18.998, "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974,
    "S": 32.06, "Cl": 35.45, "K": 39.098, "Ca": 40.078, "Sc": 44.956, "Ti": 47.867,
    "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933, "Ni": 58.693,
    "Cu": 63.546, "Zn": 65.38, "Ga": 69.723, "Ge": 72.630, "As": 74.922, "Se": 78.971,
    "Br": 79.904, "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224, "Nb": 92.906,
    "Mo": 95.95, "Ru": 101.07, "Rh": 102.91, "Pd": 106.42, "Ag": 107.87, "Cd": 112.41,
    "In": 114.82, "Sn": 118.71, "Sb": 121.76, "Te": 127.60, "I": 126.90, "Cs": 132.91,
    "Ba": 137.33, "La": 138.91, "Ce": 140.12, "Pr": 140.91, "Nd": 144.24, "Sm": 150.36,
    "Eu": 151.96, "Gd": 157.25, "Tb": 158.93, "Dy": 162.50, "Ho": 164.93, "Er": 167.26,
    "Tm": 168.93, "Yb": 173.05, "Lu": 174.97, "Hf": 178.49, "Ta": 180.95, "W": 183.84,
    "Re": 186.21, "Os": 190.23, "Ir": 192.22, "Pt": 195.08, "Au": 196.97, "Hg": 200.59,
    "Tl": 204.38, "Pb": 207.2, "Bi": 208.98, "U": 238.03,
}


def _renormalised(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    return {name: 100.0 * value / total for name, value in values.items()} if total > 0 else {}


def metal_basis(elements: list[dict[str, Any]], non_metals: list[str]) -> dict[str, Any]:
    """Atomic % of the metals (sum 100), from wt% / atomic weight; also from the rounded
    at% for comparison. Falls back to at% if a metal has no atomic weight in the table."""
    metals = [e for e in elements if e["element"] not in set(non_metals)]
    from_atomic = _renormalised({e["element"]: e["atomic_percent"] for e in metals
                                 if e["atomic_percent"] is not None})
    if all(e["element"] in ATOMIC_WEIGHT for e in metals):
        at_percent = _renormalised({e["element"]: e["weight_percent"] / ATOMIC_WEIGHT[e["element"]]
                                    for e in metals})
        basis = "weight_percent / standard atomic weight"
    else:
        at_percent, basis = from_atomic, "atomic_percent as recorded (an atomic weight is missing)"
    return {"at_percent": at_percent, "computed_from": basis,
            "at_percent_from_recorded_atomic_percent": from_atomic}


def compare_with_nominal(measured: dict[str, float],
                         nominal: dict[str, float] | None) -> dict[str, Any] | None:
    if not nominal:
        return None
    total = sum(float(v) for v in nominal.values())
    nominal_basis = {k: 100.0 * float(v) / total for k, v in nominal.items()}
    rows = []
    for element in sorted(set(measured) | set(nominal_basis)):
        m, n = measured.get(element), nominal_basis.get(element)
        rows.append({"element": element, "measured_at_percent": m, "nominal_at_percent": n,
                     "difference_at_percent": (m - n) if m is not None and n is not None else None,
                     "ratio": (m / n) if m is not None and n else None})
    return {"nominal_as_given": dict(nominal), "rows": rows}


# --- run ----------------------------------------------------------------------------------

def _composition_bars(bars, metals: dict[str, float], nominal: dict | None) -> None:
    names = sorted(metals, key=lambda name: -metals[name])
    positions = range(len(names))
    if nominal:
        nominal_values = {r["element"]: r["nominal_at_percent"] for r in nominal["rows"]}
        bars.bar([p - 0.2 for p in positions], [metals[n] for n in names], 0.4, color="0.2",
                 label="measured")
        bars.bar([p + 0.2 for p in positions], [nominal_values.get(n) or 0 for n in names], 0.4,
                 color="0.7", label="nominal")
        bars.legend(frameon=False, fontsize=7)
    else:
        bars.bar(list(positions), [metals[n] for n in names], 0.6, color="0.2")
    bars.set_xticks(list(positions), names)
    bars.set_ylabel("Metals (at%, sum 100)")


def run(input_path: Path, out_dir: Path, *, settings: dict | None = None,
        settings_sources: list | None = None, context: dict[str, Any] | None = None,
        pipeline: bool = True) -> dict[str, Any]:
    context = context or {}
    out_dir = Path(out_dir)
    (assert_pipeline_writable if pipeline else assert_not_protected)(out_dir)
    if settings is None:
        settings, settings_sources = settings_mod.resolve(
            METHOD, context.get("sample_id"), context.get("measurement_id"))
    input_path = Path(input_path)
    input_sha = prov.sha256_file(input_path)
    text = _read_text(input_path)
    if not _is_inca(text):
        raise AnalysisError(f"{input_path.name} is not an INCA quantification report")
    report = parse_report(text)
    if not report["elements"]:
        raise AnalysisError("the quantification table has no readable element rows")
    checks: list[dict[str, Any]] = []
    elements = report["elements"]
    non_metals = list(settings["non_metals"])
    basis = metal_basis(elements, non_metals)
    metals = basis["at_percent"]
    nominal = compare_with_nominal(metals, settings.get("nominal_at_percent"))

    option = report["processing_option"] or ""
    if "normalised" in option.lower() or "normalized" in option.lower():
        checks.append({"code": "normalised_totals", "severity": "info",
                       "message": "results were normalised to 100 %, so the analytical total "
                                  "(a quality indicator) is not known"})
    if all(e["weight_percent_sigma"] is None for e in elements):
        checks.append({"code": "no_uncertainty_reported", "severity": "warning",
                       "message": "the export has no σ: small differences between elements or "
                                  "samples cannot be judged"})
    low = [e for e in elements if e["weight_percent"] < float(settings["low_concentration_wt_percent"])]
    if low:
        checks.append({"code": "low_concentration", "severity": "warning",
                       "message": "near the detection limit (< "
                                  f"{settings['low_concentration_wt_percent']} wt%): "
                                  + ", ".join(f"{e['element']} {e['weight_percent']} wt%" for e in low)})
    non_metal_wt = sum(e["weight_percent"] for e in elements if e["element"] in set(non_metals))
    metal_wt = sum(e["weight_percent"] for e in elements if e["element"] not in set(non_metals))
    if non_metal_wt > metal_wt:
        checks.append({"code": "support_dominates", "severity": "info",
                       "message": f"non-metals give most of the signal ({non_metal_wt:.1f} wt% vs "
                                  f"{metal_wt:.1f} wt% metals): the metal ratios rest on small peaks"})
    if nominal is None:
        checks.append({"code": "no_nominal", "severity": "info",
                       "message": "no nominal composition for this sample (config/samples.yaml → "
                                  "samples.<id>.eds.nominal_at_percent)"})
    chosen = settings.get("images")
    if chosen:
        images = [(name, find_image(input_path, name)) for name in chosen]
        checks.append({"code": "images_from_settings", "severity": "info",
                       "message": f"images set in the settings: {list(chosen)} (the report "
                                  f"links {report['linked_images']})"})
    else:
        images = [(link, find_image(input_path, link)) for link in report["linked_images"]]
    missing = [link for link, found in images if found is None]
    if missing:
        checks.append({"code": "images_missing", "severity": "warning",
                       "message": f"image(s) not found next to the report: {missing}"})
    foreign = [link for link in report["linked_images"]
               if not _named_for(Path(link), input_path)]
    if foreign and not chosen:
        own = sorted(p.name for p in input_path.parent.iterdir()
                     if p.suffix.lower() in (".jpg", ".jpeg") and _named_for(p, input_path))
        checks.append({"code": "linked_images_other_name", "severity": "review",
                       "message": f"the report links {foreign}, named for another report"
                                  + (f"; images named for this report exist: {own}" if own else "")
                                  + ". Check which electron image and spectrum belong here; "
                                    "set them with `images:` in config/measurements/eds/"
                                    "<measurement_id>.yaml"})
    if report["unread_rows"]:
        checks.append({"code": "rows_not_read", "severity": "review",
                       "message": f"table rows that could not be read: {report['unread_rows']}"})
    status = "review" if any(c["severity"] == "review" for c in checks) else "ok"
    results = {
        "schema": RESULTS_SCHEMA, "status": status,
        "sample_id": context.get("sample_id"), "measurement_id": context.get("measurement_id"),
        "input": {"file_name": input_path.name, "sha256": input_sha,
                  "images_source": "settings" if chosen else "report links",
                  "images": [{"link": link, "file": found.name if found else None,
                              "sha256": prov.sha256_file(found) if found else None}
                             for link, found in images]},
        "processing": {"option": report["processing_option"],
                       "spectrum_processing": report["spectrum_processing"],
                       "iterations": report["iterations"], "comment": report["comment"]},
        "standards": report["standards"],
        "composition": {"elements": elements, "total_weight_percent": report["total_weight_percent"]},
        "metal_basis": {"excluded_non_metals": non_metals, **basis},
        "nominal_comparison": nominal,
        "units": {"weight_percent": "wt%", "atomic_percent": "at%",
                  "metal_basis.at_percent": "at% of the metals (sum 100)",
                  "difference_at_percent": "at% points (metal basis)", "ratio": "measured/nominal"},
        "checks": checks,
    }

    # --- outputs ------------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    font = apply_acs()
    import matplotlib.pyplot as plt
    from PIL import Image
    pictures = [found for _, found in images if found is not None][:2]
    panels = len(pictures) + 1
    figure, axes = plt.subplots(1, panels, figsize=(float(settings["plot"]["width_in"]), 2.6),
                                gridspec_kw={"width_ratios": [1.25] * len(pictures) + [1.0]})
    axes = list(axes) if panels > 1 else [axes]
    for axis, picture, label in zip(axes, pictures, ("electron image", "spectrum")):
        with Image.open(picture) as opened:
            axis.imshow(opened.convert("RGB"))
        axis.set_axis_off()
        axis.set_title(label, fontsize=8)
    _composition_bars(axes[-1], metals, nominal)
    figure.tight_layout()
    staging = out_dir / ".plot-staging"
    staging.mkdir(exist_ok=True)
    figure.savefig(staging / "plot.png", dpi=int(settings["plot"]["dpi"]),
                   metadata={"Software": None})
    figure.savefig(staging / "plot.pdf",
                   metadata={"Creator": None, "Producer": None, "CreationDate": None})
    plt.close(figure)
    # The panels one by one, for the report (shown large, one at a time); the images are
    # byte copies of the INCA JPGs, the bar chart is drawn again on its own.
    single, axis = plt.subplots(figsize=(3.25, 2.6))
    _composition_bars(axis, metals, nominal)
    single.tight_layout()
    single.savefig(staging / "panel_composition.png", dpi=int(settings["plot"]["dpi"]),
                   metadata={"Software": None})
    plt.close(single)
    figures = []
    for picture, label, name in zip(pictures, ("electron image", "spectrum"),
                                    ("panel_electron_image", "panel_spectrum")):
        file = name + picture.suffix.lower()
        (staging / file).write_bytes(picture.read_bytes())
        figures.append({"file": file, "caption": f"{label} ({picture.name})"})
    figures.append({"file": "panel_composition.png", "caption": "metals (at%, sum 100)"})
    results["figures"] = figures
    files = ["plot.png", "plot.pdf"] + [f["file"] for f in figures]
    for name in files:
        atomic_write_bytes(out_dir / name, (staging / name).read_bytes(), pipeline=pipeline)
        (staging / name).unlink()
    staging.rmdir()
    atomic_write_bytes(out_dir / "results.json",
                       (json.dumps(results, indent=1, ensure_ascii=False, allow_nan=False)
                        + "\n").encode(), pipeline=pipeline)
    outputs = {name: prov.sha256_file(out_dir / name) for name in ["results.json"] + files}
    record = prov.build_record(
        method=METHOD, entry_point="labpipe.methods.eds.analyze.run",
        input_path=input_path, input_sha256=input_sha, context=context, settings=settings,
        settings_sources=settings_sources, outputs=outputs, out_dir=out_dir,
        extra={"companions": [{"file": i["file"], "sha256": i["sha256"]}
                              for i in results["input"]["images"] if i["file"]],
               "plot": {"style": "ACS", "font": font}})
    atomic_write_bytes(out_dir / "provenance.json",
                       (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode(),
                       pipeline=pipeline)
    return {"status": status, "out_dir": str(out_dir),
            "checks": [c for c in checks if c["severity"] == "review"], "outputs": outputs,
            "settings_sha256": settings_mod.fingerprint(settings)}
