"""`dots`: rows = samples, one value per measurement along a horizontal axis.

Spec (values copied from results.json, nothing computed here):

    {"kind": "dots", "method", "name", "title",
     "label": axis label with its unit, "unit",
     "samples": [sample, ...]                 row order, top to bottom
     "points": [{"sample", "entry_id", "measurement_id", "value", "sigma": number | None,
                 "nominal": number | None, "unit", "status", "results_path", "results_sha256",
                 "key"}, ...]
     "reference_lines": [{"value", "label"}, ...]       optional
     "plot": the resolved plot style (added by labpipe.compare)}

One dark colour, filled markers; an error bar (σ) only where the analysis gives one; a nominal
value is a grey open diamond in the same row; reference lines are thin dashed grey verticals
with their label at the top. Several measurements of a sample sit side by side inside its row
(nothing averaged). `plots.compare` sets the size (`width_in`, `height_per_sample_in`,
`min_height_in`, `marker_size`, `point_offset`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from labpipe import comparekinds as ck

MARGIN_IN = {"top": 0.12, "bottom": 0.5, "bottom_two_lines": 0.72, "left": 0.55, "right": 0.12,
             "labels": 0.2}
DEFAULT_STYLE = {"width_in": "single", "height_per_sample_in": 0.3, "min_height_in": 1.6,
                 "marker_size": 4.5, "point_offset": 0.2}


def check(spec: dict[str, Any]) -> None:
    ck.require(spec, ("samples", "points"), "dots")
    for p in spec["points"]:
        if not ck.finite(p.get("value")) or "sample" not in p or "measurement_id" not in p:
            raise ValueError(f"a dots point needs sample, measurement_id and a finite value: {p!r}")
        if p.get("sigma") is not None and not ck.finite(p["sigma"]):
            raise ValueError(f"sigma of {p['measurement_id']} is not a number")
    known = set(spec["samples"])
    if any(p["sample"] not in known for p in spec["points"]):
        raise ValueError("a point belongs to a sample that is not in `samples`")


def inventory(spec: dict[str, Any]) -> list[tuple[str, str]]:
    return [(p["sample"], p["measurement_id"]) for p in spec["points"]]


def _name(spec: dict[str, Any]) -> str:
    unit = f" ({spec['unit']})" if spec.get("unit") else ""
    return spec.get("label") or f"{spec.get('title') or spec['name']}{unit}"


def caption(spec: dict[str, Any]) -> str:
    points = spec["points"]
    counts = ck.counts(inventory(spec))
    several = [f"{s} ×{n}" for s, n in counts.items() if n > 1]
    text = (f"{_name(spec)}: {len(points)} measurement{'s' if len(points) != 1 else ''} of "
            f"{len(counts)} samples, one point each, nothing averaged"
            + (f" ({', '.join(several)})" if several else "") + ". ")
    bare = [p["measurement_id"] for p in points if p.get("sigma") is None]
    if not bare:
        text += "Error bars: σ from the fit."
    elif len(bare) == len(points):
        text += "No σ for any point, so no error bars."
    else:
        shown = ", ".join(bare[:5]) + (f" and {len(bare) - 5} more" if len(bare) > 5 else "")
        text += f"Error bars: σ from the fit; no σ, so no bar, for {shown}."
    if any(p.get("nominal") is not None for p in points):
        text += " Grey open diamonds: the nominal value."
    if spec.get("reference_lines"):
        text += " Dashed lines: " + ", ".join(r["label"] for r in spec["reference_lines"]) + "."
    stale = sum(1 for p in points if p.get("status") == "stale")
    if stale:
        text += f" {stale} point{'s are' if stale != 1 else ' is'} from an analysis that is stale."
    return text


def _numbers(spec: dict[str, Any]) -> list[float]:
    """Every value the x axis must show: the points with their σ, the nominals, the references."""
    out: list[float] = []
    for p in spec["points"]:
        out += [p["value"] - (p.get("sigma") or 0), p["value"] + (p.get("sigma") or 0)]
        if ck.finite(p.get("nominal")):
            out.append(p["nominal"])
    return out + [r["value"] for r in spec.get("reference_lines") or []]


def _axis_label(spec: dict[str, Any]) -> str:
    """The method's label (unit included), else the name and the unit; two lines when long."""
    label = spec.get("label")
    if label:
        label = label.replace("$", r"\$")
        return label if len(label) <= 34 or " (" not in label else label.replace(" (", "\n(", 1)
    name, unit = (spec.get("title") or spec["name"]).replace("$", r"\$"), (
        spec.get("unit") or "").replace("$", r"\$")
    if not unit:
        return name
    return f"{name} ({unit})" if len(name) + len(unit) <= 30 else f"{name}\n({unit})"


def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    """Draw the dot plot as stem.png (+ the other formats of the plot style); deterministic."""
    from labpipe.plotting.acs import apply_acs, keep_inside, save_figure, style_of, width_in
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    plot = style_of({"plots": spec["plot"]})
    style = DEFAULT_STYLE | dict(spec["plot"].get("compare") or {})
    font = apply_acs(plot)
    rows, references = spec["samples"], spec.get("reference_lines") or []
    label = _axis_label(spec)
    bottom = MARGIN_IN["bottom_two_lines"] if "\n" in label else MARGIN_IN["bottom"]
    ordered = sorted(references, key=lambda r: r["value"])
    low, high = ck.extent(_numbers(spec)) if spec["points"] else (0.0, 1.0)
    heights = ck.levels([r["value"] for r in ordered], high - low)
    top = MARGIN_IN["top"] + (MARGIN_IN["labels"] * (1 + max(heights)) if ordered else 0)
    height = max(style["min_height_in"], len(rows) * style["height_per_sample_in"] + top + bottom)
    width = width_in({"width_in": style["width_in"]}, plot)
    figure, axis = plt.subplots(figsize=(width, height))
    figure.subplots_adjust(left=MARGIN_IN["left"] / width, right=1 - MARGIN_IN["right"] / width,
                           bottom=bottom / height, top=1 - top / height)
    for index, sample in enumerate(rows):
        axis.axhline(index, color="0.9", lw=0.5, zorder=0)
        mine = [p for p in spec["points"] if p["sample"] == sample]
        step = min(style["point_offset"], 0.7 / (len(mine) - 1)) if len(mine) > 1 else 0.0
        for n, point in enumerate(mine):
            y = index + (n - (len(mine) - 1) / 2) * step
            if ck.finite(point.get("nominal")):
                axis.plot([point["nominal"]], [y], marker="D", ls="none", mfc="none", mec="0.55",
                          mew=0.9, ms=style["marker_size"] * 0.9, zorder=2)
            axis.errorbar(point["value"], y, xerr=point.get("sigma"), fmt="o", color=ck.INK,
                          ms=style["marker_size"], capsize=1.8, elinewidth=0.8, capthick=0.8,
                          zorder=3)
    for reference, level in zip(ordered, heights):
        axis.axvline(reference["value"], color="0.6", lw=0.6, ls=(0, (3, 2)), zorder=1)
        axis.annotate(reference["label"], (reference["value"], 1), xycoords=("data", "axes fraction"),
                      xytext=(0, 2 + level * 7), textcoords="offset points", ha="center",
                      va="bottom", fontsize=plot["font_size"] - 2.5, color="0.35")
    axis.set_ylim(len(rows) - 0.5, -0.5)        # the first sample on top
    axis.set_yticks(range(len(rows)))
    axis.set_yticklabels([s.replace("$", r"\$") for s in rows])
    axis.tick_params(axis="y", length=0)
    axis.set_xlim(low, high)
    axis.xaxis.set_major_locator(MaxNLocator(nbins=5))
    axis.ticklabel_format(axis="x", style="plain", useOffset=False)
    axis.set_xlabel(label)
    keep_inside(figure)
    files = save_figure(figure, stem, plot, int(plot["dpi"]))
    plt.close(figure)
    return {"font": font, "files": files}


def html(spec: dict[str, Any], fig_id: str) -> str:
    """The same figure as an inline SVG with its controls."""
    rows, points = spec["samples"], spec["points"]
    references = sorted(spec.get("reference_lines") or [], key=lambda r: r["value"])
    left = max(60, 8 + 6.8 * max((len(s) for s in rows), default=4))
    right, size = ck.WIDTH - 24, 11
    low, high = ck.extent(_numbers(spec)) if points else (0.0, 1.0)
    heights = ck.levels([r["value"] for r in references], high - low)
    top = 8 + (14 * (1 + max(heights)) if references else 0)
    ticks, step = ck.nice_ticks(low, high)

    def sx(value: float) -> float:
        return left + (value - low) / (high - low) * (right - left)
    row_heights = [max(26, size * len([p for p in points if p["sample"] == s]) + 12) for s in rows]
    bottom_of_rows = top + sum(row_heights)
    label = _axis_label(spec).replace("\\$", "$").replace("\n", " ")
    total = bottom_of_rows + 44
    out = [ck.svg_open(ck.WIDTH, total, spec.get("title") or spec["name"])]
    for tick in ticks:
        out.append(f'<line class=gr x1="{ck.number(sx(tick))}" x2="{ck.number(sx(tick))}" '
                   f'y1="{ck.number(top)}" y2="{ck.number(bottom_of_rows)}"/>'
                   f'<text class=m x="{ck.number(sx(tick))}" y="{ck.number(bottom_of_rows + 16)}" '
                   f'text-anchor=middle>{ck.tick_text(tick, step)}</text>')
    out.append(f'<line class=ax x1="{ck.number(left)}" x2="{ck.number(right)}" '
               f'y1="{ck.number(bottom_of_rows)}" y2="{ck.number(bottom_of_rows)}"/>'
               f'<text x="{ck.number((left + right) / 2)}" y="{ck.number(bottom_of_rows + 36)}" '
               f'text-anchor=middle>{ck.esc(label)}</text>')
    for reference, level in zip(references, heights):
        x = ck.number(sx(reference["value"]))
        out.append(f'<line class=rf x1="{x}" x2="{x}" y1="{ck.number(top)}" '
                   f'y2="{ck.number(bottom_of_rows)}"/><text class=m x="{x}" '
                   f'y="{ck.number(top - 4 - level * 14)}" text-anchor=middle>'
                   f'{ck.esc(reference["label"])}</text>')
    y0 = top
    for sample, row_height in zip(rows, row_heights):
        centre = y0 + row_height / 2
        out.append(f'<text x="{ck.number(left - 8)}" y="{ck.number(centre + 4)}" '
                   f'text-anchor=end>{ck.esc(sample)}</text>')
        mine = [p for p in points if p["sample"] == sample]
        for n, point in enumerate(mine):
            y = centre + (n - (len(mine) - 1) / 2) * size
            x, cy = ck.number(sx(point["value"])), ck.number(y)
            sigma, tip = point.get("sigma"), ck.value_text(point["value"])
            parts = []
            if ck.finite(point.get("nominal")):
                nx = sx(point["nominal"])
                parts.append(f'<path class=nm d="M{ck.number(nx)} {ck.number(y - 4.5)}l4.5 4.5l-4.5 '
                             f'4.5l-4.5 -4.5z"/>')
                tip += f", nominal {ck.value_text(point['nominal'])}"
            if sigma is not None:
                a, b = ck.number(sx(point["value"] - sigma)), ck.number(sx(point["value"] + sigma))
                parts.append(f'<path class=eb d="M{a} {cy}H{b}M{a} {ck.number(y - 3)}v6M{b} '
                             f'{ck.number(y - 3)}v6"/>')
                tip += f" ± {ck.value_text(sigma)}"
            parts.append(f'<circle class=dk cx="{x}" cy="{cy}" r="3.6"/>')
            unit = f" {point['unit']}" if point.get("unit") else ""
            parts.insert(0, f'<title>{ck.esc(point["measurement_id"])}: {ck.esc(tip + unit)}</title>')
            out.append(ck.mark(point["measurement_id"], sample, "".join(parts)))
        y0 += row_height
    out.append("</svg>")
    items = [(s, m, None) for s, m in inventory(spec)]
    return ck.wrap(fig_id, "dots", "".join(out) + ck.legend(items))
