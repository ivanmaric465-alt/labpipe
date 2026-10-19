"""XRD figure: data, fit, background, residual and phase tick rows (full range, or a zoom)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from labpipe.plotting.acs import apply_acs, palette, save_figure, style_of


def _sqrt_scale(values):
    return np.sign(values) * np.sqrt(np.abs(values))


def _square_scale(values):
    return np.sign(values) * np.square(values)


def render(path_stem: Path, *, x: np.ndarray, y: np.ndarray, background: np.ndarray,
           model: np.ndarray | None, broad: list[np.ndarray],
           tick_rows: list[dict[str, Any]],
           peak_positions: list[float], width_in: float, height_in: float,
           dpi: int, title: str | None, style: dict[str, Any] | None = None,
           y_scale: str = "linear", residual_panel: bool = True, phase_ticks: bool = True,
           x_range: tuple[float, float] | None = None) -> dict[str, Any]:
    """The figure as path_stem.png (+ other formats of the style). `x_range` cuts every
    curve and tick row to that 2θ window (the zoom figure); otherwise the full range."""
    style = style_of({"plots": style})
    font = apply_acs(style)
    colors = palette(style)
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    if x_range is not None:
        keep = (x >= x_range[0]) & (x <= x_range[1])
        x, y, background = x[keep], y[keep], background[keep]
        model = model[keep] if model is not None else None
        broad = [curve[keep] for curve in broad]
        peak_positions = [p for p in peak_positions if x_range[0] <= p <= x_range[1]]
        tick_rows = [{**row, "positions": [p for p in row["positions"]
                                           if x_range[0] <= p <= x_range[1]]}
                     for row in tick_rows]
    n_rows = max(len(tick_rows), 1)
    panels = ["main"] + (["ticks"] if phase_ticks else []) + (["res"] if residual_panel else [])
    ratios = {"main": 5.0, "ticks": 0.35 * n_rows + 0.3, "res": 1.4}
    figure = plt.figure(figsize=(width_in, height_in))
    grid = figure.add_gridspec(len(panels), 1, height_ratios=[ratios[p] for p in panels],
                               hspace=0.0)
    ax_main = figure.add_subplot(grid[0])
    ax_ticks = figure.add_subplot(grid[panels.index("ticks")], sharex=ax_main) \
        if phase_ticks else None
    ax_res = figure.add_subplot(grid[panels.index("res")], sharex=ax_main) \
        if residual_panel else None

    # Powers of ten go into the axis label (ACS convention), not an offset text.
    peak_value = float(np.max(np.abs(y))) if y.size else 1.0
    exponent = int(np.floor(np.log10(peak_value))) if peak_value >= 1e4 and y_scale == "linear" \
        else 0
    scale = 10.0 ** -exponent
    ax_main.plot(x, y * scale, color="0.15", lw=0.6, label="Data", zorder=2)
    ax_main.plot(x, background * scale, color="0.55", lw=0.8, ls="--", label="Background",
                 zorder=3)
    if broad:
        ax_main.plot(x, (background + np.sum(broad, axis=0)) * scale, color="#0072B2", lw=0.7,
                     ls=":", label="Bkg. + broad", zorder=3)
    if model is not None:
        ax_main.plot(x, model * scale, color="#D62728", lw=0.8, label="Fit", zorder=4)
    for position in peak_positions:
        ax_main.axvline(position, color="#D62728", lw=0.3, alpha=0.35, zorder=1)
    unit = f"10$^{{{exponent}}}$ counts" if exponent else "counts"
    if y_scale == "sqrt":
        ax_main.set_yscale("function", functions=(_sqrt_scale, _square_scale))
        unit = "counts, sqrt scale"
    elif y_scale == "log":
        positive = np.concatenate([c[c > 0] for c in (y, background) if (c > 0).any()]
                                  or [np.array([1.0])])
        ax_main.set_yscale("log")
        ax_main.set_ylim(bottom=float(positive.min()) * 0.8)
        unit = "counts, log"
    ax_main.set_ylabel(f"Intensity ({unit})")
    ax_main.legend(loc="upper right", handlelength=1.2, fontsize=6.5, borderaxespad=0.3,
                   labelspacing=0.25)
    if title:
        ax_main.set_title(title, fontsize=6.5, loc="left", color="0.25", pad=3)
    ax_main.set_xlim(float(x[0]), float(x[-1]))
    bottom = ax_res or ax_ticks or ax_main

    if ax_ticks is not None:
        ax_ticks.set_ylim(-0.5, n_rows - 0.5)
        ax_ticks.set_yticks([])
        ax_ticks.tick_params(left=False, right=False)
        for row_index, row in enumerate(tick_rows):
            level = n_rows - 1 - row_index
            color = colors[row_index % len(colors)]
            ax_ticks.vlines(row["positions"], level - 0.32, level + 0.32, color=color, lw=0.8)
            ax_ticks.text(1.01, level, row["label"], transform=ax_ticks.get_yaxis_transform(),
                          va="center", ha="left", fontsize=6.5, color=color, clip_on=False)

    if ax_res is not None:
        if model is not None:
            ax_res.plot(x, y - model, color="0.3", lw=0.5)
        ax_res.axhline(0.0, color="0.6", lw=0.5)
        ax_res.set_ylabel("Res.", fontsize=8)
        ax_res.yaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
        ax_res.ticklabel_format(axis="y", style="plain")
        ax_res.tick_params(axis="y", labelsize=6.5)
    for axis in (ax_main, ax_ticks, ax_res):
        if axis is not None and axis is not bottom:
            axis.tick_params(labelbottom=False)
    bottom.set_xlabel("2θ (°)")

    figure.align_ylabels([ax_main] + ([ax_res] if ax_res is not None else []))
    figure.subplots_adjust(left=0.17, right=0.80, top=0.93, bottom=0.14)
    files = save_figure(figure, path_stem, style, dpi)
    plt.close(figure)
    return {"font": font, "files": files}
