"""XRD figure: data, fit, background, residual and phase tick rows (full range)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from labpipe.plotting.acs import PHASE_COLORS, apply_acs


def render(path_stem: Path, *, x: np.ndarray, y: np.ndarray, background: np.ndarray,
           model: np.ndarray | None, broad: list[np.ndarray],
           tick_rows: list[dict[str, Any]],
           peak_positions: list[float], width_in: float, height_in: float,
           dpi: int, title: str | None) -> dict[str, Any]:
    font = apply_acs()
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    n_rows = max(len(tick_rows), 1)
    figure = plt.figure(figsize=(width_in, height_in))
    grid = figure.add_gridspec(3, 1, height_ratios=[5.0, 0.35 * n_rows + 0.3, 1.4], hspace=0.0)
    ax_main = figure.add_subplot(grid[0])
    ax_ticks = figure.add_subplot(grid[1], sharex=ax_main)
    ax_res = figure.add_subplot(grid[2], sharex=ax_main)

    # Powers of ten go into the axis label (ACS convention), not an offset text.
    peak_value = float(np.max(np.abs(y))) if y.size else 1.0
    exponent = int(np.floor(np.log10(peak_value))) if peak_value >= 1e4 else 0
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
    ax_main.set_ylabel(f"Intensity ({unit})")
    ax_main.legend(loc="upper right", handlelength=1.2, fontsize=6.5, borderaxespad=0.3,
                   labelspacing=0.25)
    if title:
        ax_main.set_title(title, fontsize=6.5, loc="left", color="0.25", pad=3)
    ax_main.tick_params(labelbottom=False)
    ax_main.set_xlim(float(x[0]), float(x[-1]))

    ax_ticks.set_ylim(-0.5, n_rows - 0.5)
    ax_ticks.set_yticks([])
    ax_ticks.tick_params(labelbottom=False, left=False, right=False)
    for row_index, row in enumerate(tick_rows):
        level = n_rows - 1 - row_index
        color = PHASE_COLORS[row_index % len(PHASE_COLORS)]
        ax_ticks.vlines(row["positions"], level - 0.32, level + 0.32, color=color, lw=0.8)
        ax_ticks.text(1.01, level, row["label"], transform=ax_ticks.get_yaxis_transform(),
                      va="center", ha="left", fontsize=6.5, color=color, clip_on=False)

    if model is not None:
        ax_res.plot(x, y - model, color="0.3", lw=0.5)
    ax_res.axhline(0.0, color="0.6", lw=0.5)
    ax_res.set_ylabel("Res.", fontsize=8)
    ax_res.yaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
    ax_res.ticklabel_format(axis="y", style="plain")
    ax_res.tick_params(axis="y", labelsize=6.5)
    ax_res.set_xlabel("2θ (°)")

    figure.align_ylabels([ax_main, ax_res])
    figure.subplots_adjust(left=0.17, right=0.80, top=0.93, bottom=0.14)
    png = path_stem.with_suffix(".png")
    pdf = path_stem.with_suffix(".pdf")
    figure.savefig(png, dpi=dpi, metadata={"Software": None})
    figure.savefig(pdf, metadata={"Creator": None, "Producer": None, "CreationDate": None})
    plt.close(figure)
    return {"font": font, "files": [png.name, pdf.name]}
