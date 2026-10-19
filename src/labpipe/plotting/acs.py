"""ACS journal style.

Values copied from upstream-echem@41b9c69 (docs/PORTING.md) core/plotting.py JOURNAL_PRESETS['ACS']
(Arial 9 pt, axis labels 10 pt, ticks 9 pt, legend 8 pt, line width 1.0,
no grid, 300 dpi, single column 3.25 in). Arial is used when installed,
otherwise the metric-compatible Liberation Sans; the font actually used is
returned so provenance can record it.

The style used is the resolved `plots` settings (config/plots.yaml, lab.yaml `plots:`),
read through `style_of(settings)`; ACS below is what they default to (and what a
provenance record without `plots` is replayed with).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ACS = {
    "font_family": "Arial",
    "font_size": 9,
    "axes_labelsize": 10,
    "axes_titlesize": 11,
    "tick_labelsize": 9,
    "legend_fontsize": 8,
    "default_lw": 1.0,
    "default_ms": 4,
    "show_grid": False,
    "dpi": 300,
    "fig_single_w": 3.25,
    "fig_double_w": 7.0,
    "fig_default_h": 2.5,
}

FONT_PREFERENCE = ["Arial", "Liberation Sans", "DejaVu Sans"]

# Colour-blind-safe (Okabe-Ito) cycle for phase tick rows.
PHASE_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442"]


# The same values as config/plots.yaml (a test keeps them equal).
DEFAULT_STYLE = {
    "preset": "acs", "font": "Arial", "font_size": 9, "label_size": 10, "line_width": 1.0,
    "palette": "okabe-ito", "single_column_in": 3.25, "double_column_in": 7.0, "dpi": 300,
    "formats": ["png", "pdf"],
}


def style_of(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The plot style of resolved settings (defaults for records that have none)."""
    return DEFAULT_STYLE | dict((settings or {}).get("plots") or {})


def palette(style: dict[str, Any]) -> list[str]:
    return list(PHASE_COLORS if style["palette"] == "okabe-ito" else style["palette"])


def width_in(plot: dict[str, Any], style: dict[str, Any]) -> float:
    """A method's plot width: inches, or `single` / `double` (the style's column widths)."""
    width = plot["width_in"]
    if width in ("single", "double"):
        return float(style[f"{width}_column_in"])
    return float(width)


def formats(style: dict[str, Any]) -> list[str]:
    """png always (the report shows it), then the other chosen formats."""
    return ["png"] + [f for f in ("pdf", "svg") if f in style["formats"]]


def keep_inside(figure, pad_in: float = 0.03) -> None:
    """Widen the fixed margins of `figure` only where text would be cut off (e.g. larger
    fonts); a figure that already fits is left exactly as it is."""
    figure.canvas.draw()
    box = figure.get_tightbbox(figure.canvas.get_renderer())   # inches
    width, height = figure.get_size_inches()
    params = figure.subplotpars
    left = params.left + max(0.0, -box.x0 + pad_in) / width if box.x0 < 0 else params.left
    right = params.right - max(0.0, box.x1 - width + pad_in) / width if box.x1 > width else params.right
    bottom = params.bottom + max(0.0, -box.y0 + pad_in) / height if box.y0 < 0 else params.bottom
    top = params.top - max(0.0, box.y1 - height + pad_in) / height if box.y1 > height else params.top
    if (left, right, bottom, top) != (params.left, params.right, params.bottom, params.top):
        figure.subplots_adjust(left=left, right=right, bottom=bottom, top=top)


def save_figure(figure, stem: Path, style: dict[str, Any], dpi: int) -> list[str]:
    """Write stem.png (+ .pdf, .svg as chosen) without volatile metadata; the file names."""
    names = []
    for suffix in formats(style):
        path = stem.with_suffix(f".{suffix}")
        if suffix == "png":
            figure.savefig(path, dpi=dpi, metadata={"Software": None})
        elif suffix == "pdf":
            figure.savefig(path, metadata={"Creator": None, "Producer": None,
                                           "CreationDate": None})
        else:
            figure.savefig(path, metadata={"Date": None})
        names.append(path.name)
    return names


def resolve_font(preferred: str = "Arial") -> dict[str, str]:
    for family in dict.fromkeys([preferred, *FONT_PREFERENCE]):
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        return {"family": family, "file": path}
    path = font_manager.findfont("sans-serif")
    return {"family": "sans-serif", "file": path}


def apply_acs(style: dict[str, Any] | None = None) -> dict[str, str]:
    style = DEFAULT_STYLE | (style or {})
    font = resolve_font(style["font"])
    plt.rcdefaults()
    plt.rcParams.update({
        "font.family": font["family"],
        "font.size": style["font_size"],
        "axes.labelsize": style["label_size"],
        "axes.titlesize": style["label_size"] + 1,
        "xtick.labelsize": style["font_size"],
        "ytick.labelsize": style["font_size"],
        "legend.fontsize": style["font_size"] - 1,
        "lines.linewidth": style["line_width"],
        "lines.markersize": ACS["default_ms"],
        "axes.grid": ACS["show_grid"],
        "axes.linewidth": 0.8,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "legend.frameon": False,
        "savefig.dpi": style["dpi"],
        "pdf.fonttype": 42,
        "svg.hashsalt": "labpipe",
    })
    return font
