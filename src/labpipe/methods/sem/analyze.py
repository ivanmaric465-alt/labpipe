"""SEM images (JEOL, TIFF with a .txt metadata file): method `sem` (design: docs/methods/sem.md).

One measurement = one `.tif`; its companion `.txt` (same stem, JEOL `$CM_…` lines)
holds the acquisition metadata. Everything here is code; there is no analyst agent
(methods.yaml → `subagent: null`, `notes: false`).

Per image: acquisition metadata (date, instrument, kV, magnification, signal,
working distance, scan speed, stage), the pixel size from the scale bar
(scale-bar length / scale-bar pixels) and the field of view without JEOL's info
bar. Outputs: results.json, plot.png/plot.pdf (the image), thumb.jpg (for the
report gallery), provenance.json. The `$CM_` reading follows eln_refactor
`utils/scientific_reports/instrument.py` (a3931a4), extended with kV, pixel size
and field of view (docs/methods/sem.md).
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from labpipe import provenance as prov
from labpipe import settings as settings_mod
from labpipe.plotting import acs
from labpipe.safety import assert_not_protected, assert_pipeline_writable, atomic_write_bytes

METHOD = "sem"
RESULTS_SCHEMA = "labpipe.sem.results/1"
IMAGE_SUFFIXES = (".tif", ".tiff")
UNIT_TO_NM = {"nm": 1.0, "um": 1000.0, "µm": 1000.0, "μm": 1000.0, "mm": 1.0e6}


class AnalysisError(RuntimeError):
    """Unusable input; the message is recorded as the failure reason."""


# --- recognition and metadata -------------------------------------------------------------

def _is_jeol_text(path: Path) -> bool:
    try:
        with open(path, "rb") as stream:
            head = stream.read(64)
    except OSError:
        return False
    return head.lstrip(b"\xef\xbb\xbf").startswith(b"$CM_")


def metadata_file(image: Path) -> Path | None:
    """The companion .txt with the same stem (case-insensitive), if it is a JEOL file."""
    for candidate in sorted(image.parent.iterdir()):
        if (candidate.suffix.lower() == ".txt" and candidate.stem.lower() == image.stem.lower()
                and _is_jeol_text(candidate)):
            return candidate
    return None


def recognise(path: Path) -> bool:
    """methods.yaml `recognise`: a JEOL image (has its .txt) or a JEOL .txt itself."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return metadata_file(path) is not None
    return path.suffix.lower() == ".txt" and _is_jeol_text(path)


def read_metadata(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("$"):
            continue
        key, _, value = line.lstrip("$").partition(" ")
        values[key] = value.strip()
    return values


def _number(value: str | None) -> float | None:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value or "")
    return float(match.group(0).replace(",", ".")) if match else None


def scale_bar(values: dict[str, str]) -> dict[str, Any] | None:
    pixels = _number(values.get("SM_MICRON_BAR"))
    label = values.get("SM_MICRON_MARKER", "")
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(nm|um|µm|μm|mm)\s*", label)
    if not pixels or not match:
        return None
    length_nm = float(match.group(1).replace(",", ".")) * UNIT_TO_NM[match.group(2)]
    return {"scale_bar_px": pixels, "scale_bar_label": label.strip(), "scale_bar_nm": length_nm,
            "pixel_size_nm": length_nm / pixels}


def series_and_index(stem: str) -> tuple[str | None, int | None]:
    """'S-10 a001' -> ('a', 1): JEOL's series letter and image number, if present."""
    match = re.search(r"[\s_]([A-Za-z])(\d{1,4})$", stem)
    return (match.group(1).lower(), int(match.group(2))) if match else (None, None)


# --- run ----------------------------------------------------------------------------------

def run(input_path: Path, out_dir: Path, *, settings: dict | None = None,
        settings_sources: list | None = None, context: dict[str, Any] | None = None,
        pipeline: bool = True) -> dict[str, Any]:
    from PIL import Image

    context = context or {}
    out_dir = Path(out_dir)
    (assert_pipeline_writable if pipeline else assert_not_protected)(out_dir)
    if settings is None:
        settings, settings_sources = settings_mod.resolve(
            METHOD, context.get("sample_id"), context.get("measurement_id"))
    input_path = Path(input_path)
    input_sha = prov.sha256_file(input_path)
    checks: list[dict[str, Any]] = []

    try:
        with Image.open(input_path) as opened:
            image = opened.convert("L")
            mode = opened.mode
    except OSError as exc:
        raise AnalysisError(f"cannot read {input_path.name} as an image: {exc}") from exc
    width, height = image.size

    sidecar = metadata_file(input_path)
    values = read_metadata(sidecar) if sidecar else {}
    if sidecar is None:
        checks.append({"code": "metadata_missing", "severity": "review",
                       "message": "no JEOL .txt with the same name: no magnification, kV or scale"})
    resolution = values.get("CM_IMAGE_RES")
    if resolution and resolution.replace(" ", "") != f"{width}x{height}":
        checks.append({"code": "image_size_mismatch", "severity": "warning",
                       "message": f"image is {width}x{height} px, metadata says {resolution}"})
    scale = scale_bar(values) if sidecar else None
    if sidecar and scale is None:
        checks.append({"code": "scale_bar_unreadable", "severity": "review",
                       "message": f"scale bar not readable (SM_MICRON_BAR="
                                  f"{values.get('SM_MICRON_BAR')!r}, SM_MICRON_MARKER="
                                  f"{values.get('SM_MICRON_MARKER')!r}): no pixel size"})
    info_bar = int(settings["info_bar_px"])
    if scale:
        scale["field_of_view_um"] = {"width": width * scale["pixel_size_nm"] / 1000.0,
                                     "height": (height - info_bar) * scale["pixel_size_nm"] / 1000.0}
    checks.append({"code": "sample_id_from_name_only", "severity": "info",
                   "message": "JEOL files carry no sample ID; it comes from the file name"})
    series, index = series_and_index(input_path.stem)
    magnification = _number(values.get("CM_MAG"))
    acquisition = {
        "instrument": values.get("CM_INSTRUMENT"),
        "date": values.get("CM_DATE"), "time": values.get("CM_TIME"),
        "signal": values.get("CM_SIGNAL"),
        "accelerating_voltage_kV": _number(values.get("CM_ACCEL_VOLT")),
        "magnification": int(magnification) if magnification is not None else None,
        "working_distance_mm": _number(values.get("SM_WD")),
        "scan_speed": values.get("CM_SCAN_SPEED"),
        "image_resolution": resolution,
        "stage_position": values.get("CM_STAGE_POSITION"),
    }
    status = "review" if any(c["severity"] == "review" for c in checks) else "ok"
    results = {
        "schema": RESULTS_SCHEMA, "status": status,
        "sample_id": context.get("sample_id"), "measurement_id": context.get("measurement_id"),
        "input": {"file_name": input_path.name, "sha256": input_sha,
                  "metadata_file": sidecar.name if sidecar else None,
                  "metadata_sha256": prov.sha256_file(sidecar) if sidecar else None},
        "series": series, "index": index,
        "acquisition": acquisition,
        "image": {"width_px": width, "height_px": height, "mode": mode, "info_bar_px": info_bar},
        "scale": scale,
        "units": {"accelerating_voltage_kV": "kV", "working_distance_mm": "mm",
                  "magnification": "×", "scale_bar_px": "px", "scale_bar_nm": "nm",
                  "pixel_size_nm": "nm/px", "field_of_view_um": "µm"},
        "checks": checks,
    }

    # --- outputs ------------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    style = acs.style_of(settings)
    font = acs.apply_acs(style)
    import matplotlib.pyplot as plt
    plot_settings = settings["plot"]
    figure_width = acs.width_in(plot_settings, style)
    # scale_bar: the scale bar is JEOL's own, burned into the info bar at the bottom of the
    # image (labpipe draws none); false leaves the info bar out of the figure.
    shown = image if plot_settings.get("scale_bar", True) else image.crop((0, 0, width,
                                                                          height - info_bar))
    shown_width, shown_height = shown.size
    figure, axis = plt.subplots(figsize=(figure_width, figure_width * shown_height / shown_width
                                         + 0.3))
    axis.imshow(shown, cmap="gray", vmin=0, vmax=255, interpolation="antialiased")
    axis.set_axis_off()
    title = " · ".join(str(p) for p in (context.get("sample_id"), input_path.stem,
                                       f"×{acquisition['magnification']:,}"
                                       if acquisition["magnification"] else None) if p)
    axis.set_title(title, fontsize=8)
    figure.tight_layout(pad=0.2)
    staging = out_dir / ".plot-staging"
    staging.mkdir(exist_ok=True)
    plot_files = acs.save_figure(figure, staging / "plot", style, int(plot_settings["dpi"]))
    plt.close(figure)
    for name in plot_files:
        atomic_write_bytes(out_dir / name, (staging / name).read_bytes(), pipeline=pipeline)
        (staging / name).unlink()
    staging.rmdir()
    thumb_width = int(settings["thumbnail"]["width_px"])
    thumb = image.resize((thumb_width, round(height * thumb_width / width)), Image.LANCZOS)
    buffer = io.BytesIO()
    thumb.save(buffer, format="JPEG", quality=int(settings["thumbnail"]["jpeg_quality"]))
    atomic_write_bytes(out_dir / "thumb.jpg", buffer.getvalue(), pipeline=pipeline)
    atomic_write_bytes(out_dir / "results.json",
                       (json.dumps(results, indent=1, ensure_ascii=False, allow_nan=False)
                        + "\n").encode(), pipeline=pipeline)
    outputs = {name: prov.sha256_file(out_dir / name)
               for name in ["results.json", *plot_files, "thumb.jpg"]}
    record = prov.build_record(
        method=METHOD, entry_point="labpipe.methods.sem.analyze.run",
        input_path=input_path, input_sha256=input_sha, context=context, settings=settings,
        settings_sources=settings_sources, outputs=outputs, out_dir=out_dir,
        extra={"companions": ([{"file": sidecar.name, "sha256": results["input"]["metadata_sha256"]}]
                              if sidecar else []),
               "plot": {"style": "ACS", "font": font, "formats": acs.formats(style),
                        "files": plot_files}})
    atomic_write_bytes(out_dir / "provenance.json",
                       (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode(),
                       pipeline=pipeline)
    return {"status": status, "out_dir": str(out_dir),
            "checks": [c for c in checks if c["severity"] == "review"], "outputs": outputs,
            **settings_mod.fingerprints(settings)}
