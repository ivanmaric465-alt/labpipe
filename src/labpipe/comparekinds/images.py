"""`images`: a grid, rows = samples, columns = a category (e.g. the magnification).

Spec:

    {"kind": "images", "method", "name", "title",
     "columns": [{"key", "label"}, ...]
     "rows": [{"sample", "cells": {column_key: {"measurement_id", "entry_id", "path", "sha256",
                                                "label"}}}, ...]
     "plot": the resolved plot style (added by labpipe.compare)}

`path` is project-relative and read-only; its sha256 is checked when the figure is drawn. A
cell without an image says "–"; an image that is missing or no longer has the recorded sha256
is not drawn, and the cell says so (the figure still comes out). In the HTML each image is
re-encoded to at most 320 px wide JPEG (fixed quality), embedded; the PNG is a matplotlib montage.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from labpipe import comparekinds as ck
from labpipe import safety
from labpipe.provenance import sha256_file

MAX_PX = 320
QUALITY = 80


def check(spec: dict[str, Any]) -> None:
    ck.require(spec, ("columns", "rows"), "images")
    keys = {c["key"] for c in spec["columns"]}
    for row in spec["rows"]:
        for key, cell in (row.get("cells") or {}).items():
            if key not in keys:
                raise ValueError(f"{row['sample']}: cell for an unknown column {key!r}")
            for field in ("measurement_id", "path", "sha256"):
                if field not in cell:
                    raise ValueError(f"{row['sample']}/{key}: a cell needs {field}")


def inventory(spec: dict[str, Any]) -> list[tuple[str, str]]:
    return [(r["sample"], c["measurement_id"]) for r in spec["rows"]
            for c in (r.get("cells") or {}).values()]


def caption(spec: dict[str, Any]) -> str:
    cells = len(inventory(spec))
    return (f"{spec.get('title') or spec['name']}: {cells} image{'s' if cells != 1 else ''}, "
            f"one row per sample, one column per {', '.join(c['label'] for c in spec['columns'])}"
            f" (– where the sample has none), scaled down for the page; the files are untouched.")


def _load(cell: dict[str, Any]):
    """(a PIL image of at most MAX_PX wide, None) or (None, why not)."""
    from PIL import Image
    path = safety.PROJECT_ROOT / cell["path"]
    if not path.is_file():
        return None, "image missing"
    if sha256_file(path) != cell["sha256"]:
        return None, "image changed"
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode in ("I;16", "I", "F"):
                import numpy as np
                data = np.asarray(image, dtype="float64")
                span = (data.max() - data.min()) or 1.0
                image = Image.fromarray(((data - data.min()) / span * 255).astype("uint8"))
            image = image.convert("RGB")
            if image.width > MAX_PX:
                image = image.resize((MAX_PX, max(1, round(image.height * MAX_PX / image.width))),
                                     Image.LANCZOS)
            return image.copy(), None
    except Exception as exc:       # an unreadable file: said in the cell
        return None, f"image unreadable ({type(exc).__name__})"


def _cells(spec: dict[str, Any]):
    for row in spec["rows"]:
        for column in spec["columns"]:
            yield row, column, (row.get("cells") or {}).get(column["key"])


def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    from labpipe.plotting.acs import apply_acs, save_figure, style_of, width_in
    import matplotlib.pyplot as plt

    plot = style_of({"plots": spec["plot"]})
    font = apply_acs(plot)
    rows, columns = spec["rows"], spec["columns"]
    width = width_in({"width_in": "double"}, plot)
    cell_w = (width - 0.5) / max(len(columns), 1)
    height = 0.25 + len(rows) * (cell_w * 0.75 + 0.3)
    figure, axes = plt.subplots(len(rows) or 1, len(columns) or 1, figsize=(width, height),
                                squeeze=False)
    figure.subplots_adjust(left=0.5 / width, right=0.995, top=1 - 0.25 / height, bottom=0.02,
                           wspace=0.04, hspace=0.2)
    small = plot["font_size"] - 2
    for r, row in enumerate(rows):
        for c, column in enumerate(columns):
            axis, cell = axes[r][c], (row.get("cells") or {}).get(column["key"])
            axis.set_xticks([])
            axis.set_yticks([])
            for side in axis.spines.values():
                side.set_visible(False)
            if r == 0:
                axis.set_title(column["label"], fontsize=small + 0.5)
            if c == 0:
                axis.set_ylabel(row["sample"].replace("$", r"\$"), rotation=0, ha="right",
                                va="center", fontsize=small + 0.5)
            image, why = _load(cell) if cell else (None, None)
            if image is not None:
                axis.imshow(image, aspect="equal")
                axis.set_xlabel(cell.get("label", ""), fontsize=small - 0.5, labelpad=1.5)
            else:
                axis.text(0.5, 0.5, why or "–", ha="center", va="center", fontsize=small,
                          color="0.4", transform=axis.transAxes)
    files = save_figure(figure, stem, plot, int(plot["dpi"]))
    plt.close(figure)
    return {"font": font, "files": files}


def html(spec: dict[str, Any], fig_id: str) -> str:
    columns = spec["columns"]
    head = "".join(f'<div class=hd>{ck.esc(c["label"])}</div>' for c in columns)
    body = []
    for row in spec["rows"]:
        # a row is a `display: contents` group, so switching a sample off removes the whole row;
        # a cell switched off keeps its place (visibility), so the columns stay aligned
        body.append(f'<div class=gr data-row="{ck.esc(row["sample"])}">'
                    f'<div class=rl>{ck.esc(row["sample"])}</div>')
        for column in columns:
            cell = (row.get("cells") or {}).get(column["key"])
            if not cell:
                body.append('<div class=na>–</div>')
                continue
            image, why = _load(cell)
            if image is None:
                inner = f'<div class=na>{ck.esc(why)}</div>'
            else:
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=QUALITY, subsampling=2, optimize=False)
                inner = (f'<img alt="{ck.esc(cell.get("label", ""))}" width="{image.width}" '
                         f'height="{image.height}" src="data:image/jpeg;base64,'
                         f'{base64.b64encode(buffer.getvalue()).decode()}">')
            label = f'<figcaption class=m>{ck.esc(cell.get("label", ""))}</figcaption>'
            body.append(ck.mark(cell["measurement_id"], row["sample"],
                                f"<figure>{inner}{label}</figure>", tag="div"))
        body.append("</div>")
    grid = (f'<div class=grid style="grid-template-columns:auto repeat({len(columns)},minmax(0,1fr))">'
            f'<div></div>{head}{"".join(body)}</div>')
    items = [(s, m, None) for s, m in inventory(spec)]
    return ck.wrap(fig_id, "images", grid + ck.legend(items))
