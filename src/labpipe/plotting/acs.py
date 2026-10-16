"""ACS journal style.

Values copied from upstream-echem@41b9c69 (docs/PORTING.md) core/plotting.py JOURNAL_PRESETS['ACS']
(Arial 9 pt, axis labels 10 pt, ticks 9 pt, legend 8 pt, line width 1.0,
no grid, 300 dpi, single column 3.25 in). Arial is used when installed,
otherwise the metric-compatible Liberation Sans; the font actually used is
returned so provenance can record it.
"""

from __future__ import annotations

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


def resolve_font() -> dict[str, str]:
    for family in FONT_PREFERENCE:
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        return {"family": family, "file": path}
    path = font_manager.findfont("sans-serif")
    return {"family": "sans-serif", "file": path}


def apply_acs() -> dict[str, str]:
    font = resolve_font()
    plt.rcdefaults()
    plt.rcParams.update({
        "font.family": font["family"],
        "font.size": ACS["font_size"],
        "axes.labelsize": ACS["axes_labelsize"],
        "axes.titlesize": ACS["axes_titlesize"],
        "xtick.labelsize": ACS["tick_labelsize"],
        "ytick.labelsize": ACS["tick_labelsize"],
        "legend.fontsize": ACS["legend_fontsize"],
        "lines.linewidth": ACS["default_lw"],
        "lines.markersize": ACS["default_ms"],
        "axes.grid": ACS["show_grid"],
        "axes.linewidth": 0.8,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "legend.frameon": False,
        "savefig.dpi": ACS["dpi"],
        "pdf.fonttype": 42,
        "svg.hashsalt": "labpipe",
    })
    return font
