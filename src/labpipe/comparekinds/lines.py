"""`lines`: one trace per measurement on a common x axis (diffraction patterns, spectra).

Spec (the data are the points to draw: a method that thins a raw file records the thinned
points and the source file with its sha256):

    {"kind": "lines", "method", "name", "title", "x_label", "y_label",
     "traces": [{"sample", "measurement_id", "entry_id", "x": [...], "y": [...],
                 "source": {"path", "sha256"}}, ...]
     "tick_rows": [{"label", "positions": [x, ...]}, ...]    reference positions under the panel
     "y_scale": "linear" | "sqrt" | "log",                   how y is drawn
     "layout": "stacked",
     "normalised": false                                     optional: start normalised
     "plot": the resolved plot style (added by labpipe.compare)}

Stacked: every trace sits one step above the previous one (a display offset made here), its
label at the right end; one colour per trace (the plot palette, Okabe-Ito without yellow,
cycling). The HTML has both views of every trace precomputed (stacked / overlaid, raw /
normalised to the trace's maximum); the report's script only switches which is visible and
re-packs the visible stacked traces. The PNG shows the view named by `normalised`, stacked.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from labpipe import comparekinds as ck

SCALES = ("linear", "sqrt", "log")
HEIGHT = 0.9                # height of a trace's maximum, in stacking steps
STEP = 46.0                 # stacking step in the HTML (viewBox units)
OVERLAY = 260.0             # plot height of the overlaid view


def check(spec: dict[str, Any]) -> None:
    ck.require(spec, ("traces",), "lines")
    if spec.get("y_scale", "linear") not in SCALES:
        raise ValueError(f"y_scale must be one of {', '.join(SCALES)}")
    for trace in spec["traces"]:
        x, y = trace.get("x"), trace.get("y")
        if not (isinstance(x, list) and isinstance(y, list) and len(x) == len(y) >= 2):
            raise ValueError(f"trace {trace.get('measurement_id')}: x and y must be lists of "
                             f"the same length (2 or more)")
        if not all(ck.finite(v) for v in x + y):
            raise ValueError(f"trace {trace.get('measurement_id')} has values that are not finite")


def inventory(spec: dict[str, Any]) -> list[tuple[str, str]]:
    return [(t["sample"], t["measurement_id"]) for t in spec["traces"]]


def caption(spec: dict[str, Any]) -> str:
    traces = spec["traces"]
    scale = spec.get("y_scale", "linear")
    text = (f"{spec.get('title') or spec['name']}: {len(traces)} trace{'s' if len(traces) != 1 else ''} "
            f"of {len(ck.counts(inventory(spec)))} samples, stacked with an offset"
            f"{'' if scale == 'linear' else f' (intensity drawn on a {scale} scale)'}"
            f"{', each normalised to its maximum' if spec.get('normalised') else ''}; "
            f"the colour only tells the traces apart (labelled at the right).")
    rows = spec.get("tick_rows") or []
    if rows:
        text += " Ticks below: " + ", ".join(r["label"] for r in rows) + "."
    return text


def _display(spec: dict[str, Any]) -> dict[str, Any]:
    """Per trace the y values on [0, 1] in both views, after the y scale: `raw` (all traces
    on one scale, the highest point of all = 1) and `norm` (each trace by its own maximum)."""
    scale = spec.get("y_scale", "linear")
    traces = spec["traces"]
    top = max((max(t["y"]) for t in traces), default=1.0) or 1.0
    positive = [v for t in traces for v in t["y"] if v > 0]
    floor = min(positive, default=1.0)

    def shape(values: list[float], high: float, low: float) -> list[float]:
        def t(v: float) -> float:
            if scale == "sqrt":
                return math.sqrt(max(v, 0.0))
            if scale == "log":
                return math.log10(max(v, low))
            return v
        base, ceiling = t(0.0 if scale != "log" else low), t(high)
        span = ceiling - base or 1.0
        return [min(1.0, max(0.0, (t(v) - base) / span)) for v in values]
    out = []
    for trace in traces:
        own = max(trace["y"]) or 1.0
        out.append({"raw": shape(trace["y"], top, floor),
                    "norm": shape([v / own for v in trace["y"]], 1.0, floor / top)})
    return {"traces": out, "top": top, "floor": floor, "scale": scale}


def _names(spec: dict[str, Any]) -> list[str]:
    counts = ck.counts(inventory(spec))
    return [ck.label_of(t["sample"], t["measurement_id"], counts) for t in spec["traces"]]


def _colours(spec: dict[str, Any]) -> list[str]:
    palette = ck.trace_colours(spec["plot"])
    return [palette[i % len(palette)] for i in range(len(spec["traces"]))]


def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    from labpipe.plotting.acs import apply_acs, keep_inside, save_figure, style_of, width_in
    import matplotlib.pyplot as plt

    plot = style_of({"plots": spec["plot"]})
    font = apply_acs(plot)
    traces, view = spec["traces"], "norm" if spec.get("normalised") else "raw"
    shown, names, colours = _display(spec), _names(spec), _colours(spec)
    rows = spec.get("tick_rows") or []
    width = width_in({"width_in": "double"}, plot)
    main = max(2.0, 0.3 * len(traces) + 0.6)
    ticks_in = 0.16 * len(rows)
    height = main + ticks_in + 0.6
    figure, axes = plt.subplots(2 if rows else 1, 1, sharex=True, figsize=(width, height),
                                gridspec_kw={"height_ratios": [main, ticks_in]} if rows else None)
    axis = axes[0] if rows else axes
    longest = max((len(n) for n in names), default=4)
    right_in = 0.15 + 0.062 * longest
    figure.subplots_adjust(left=0.55 / width, right=1 - right_in / width, bottom=0.5 / height,
                           top=1 - 0.1 / height, hspace=0.04)
    for i, (trace, colour, name) in enumerate(zip(traces, colours, names)):
        axis.plot(trace["x"], [i + HEIGHT * u for u in shown["traces"][i][view]], color=colour,
                  lw=0.7)
        axis.text(1.01, i + 0.1, name, transform=axis.get_yaxis_transform(), color=colour,
                  fontsize=plot["font_size"] - 1.5, va="bottom")
    axis.set_ylim(-0.2, len(traces) - 1 + HEIGHT + 0.2)
    axis.set_yticks([])
    axis.set_ylabel(spec.get("y_label", "") + (" (normalised, offset)" if view == "norm" else " (offset)"))
    if rows:
        ticks_axis = axes[1]
        for k, row in enumerate(rows):
            ticks_axis.vlines(row["positions"], k + 0.15, k + 0.85, color="0.25", lw=0.6)
        ticks_axis.set_ylim(len(rows), 0)
        ticks_axis.set_yticks([k + 0.5 for k in range(len(rows))])
        ticks_axis.set_yticklabels([r["label"].replace("$", r"\$") for r in rows],
                                   fontsize=plot["font_size"] - 2)
        ticks_axis.tick_params(axis="y", length=0)
        ticks_axis.tick_params(axis="x", top=False)
    xs = [v for t in traces for v in (t["x"][0], t["x"][-1])]
    (axes[-1] if rows else axis).set_xlabel(spec.get("x_label", ""))
    axis.set_xlim(min(xs), max(xs))
    axis.tick_params(top=False, right=False)
    keep_inside(figure)
    files = save_figure(figure, stem, plot, int(plot["dpi"]))
    plt.close(figure)
    return {"font": font, "files": files}


def _path(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{ck.number(x)},{ck.number(y)}" for x, y in points)


def html(spec: dict[str, Any], fig_id: str) -> str:
    traces, rows = spec["traces"], spec.get("tick_rows") or []
    n = len(traces)
    shown, names, colours = _display(spec), _names(spec), _colours(spec)
    scale = spec.get("y_scale", "linear")
    left, right = 56.0, ck.WIDTH - 24 - 7 * max((len(x) for x in names), default=4) - 12
    xs = [v for t in traces for v in t["x"]]
    low, high = min(xs), max(xs)

    def sx(value: float) -> float:
        return left + (value - low) / ((high - low) or 1.0) * (right - left)
    top = 10.0
    stack_h = (n - 1) * STEP + STEP * HEIGHT      # trace 0 at the bottom, the others above
    panel = max(stack_h, OVERLAY)
    axis_y = base_s = base_o = top + panel         # trace 0 (stacked) and the overlaid baseline
    tick_top = axis_y + 40
    total = tick_top + 16 * len(rows) + 10
    tick_xs, tick_step = ck.nice_ticks(low, high, 8)
    variants = []
    for key, stacked, view in (("s0", True, "raw"), ("s1", True, "norm"),
                               ("o0", False, "raw"), ("o1", False, "norm")):
        height_px = STEP * HEIGHT if stacked else panel
        group = []
        if not stacked:
            group.append(_y_axis(spec, view, shown, left, base_o, panel, scale))
        for i, trace in enumerate(traces):
            base = (base_s - i * STEP) if stacked else base_o
            points = [(sx(x), base - u * height_px) for x, u in zip(trace["x"], shown["traces"][i][view])]
            inner = (f'<polyline fill=none stroke="{colours[i]}" stroke-width="1" '
                     f'stroke-linejoin=round points="{_path(points)}"/>')
            if stacked:
                inner += (f'<text x="{ck.number(right + 8)}" y="{ck.number(base - 2)}" '
                          f'style="fill:{colours[i]}">{ck.esc(names[i])}</text>')
            group.append(ck.mark(trace["measurement_id"], trace["sample"], inner,
                                 f' data-i="{i}"' if stacked else ""))
        shown_now = (key == ("s1" if spec.get("normalised") else "s0"))
        variants.append(f'<g class=v data-v="{key}"{"" if shown_now else " style=display:none"}>'
                        + "".join(group) + "</g>")
    out = [ck.svg_open(ck.WIDTH, total, spec.get("title") or spec["name"],
                       f' data-step="{ck.number(STEP)}"')]
    for tick in tick_xs:
        out.append(f'<line class=gr x1="{ck.number(sx(tick))}" x2="{ck.number(sx(tick))}" '
                   f'y1="{ck.number(top)}" y2="{ck.number(axis_y)}"/>'
                   f'<text class=m x="{ck.number(sx(tick))}" y="{ck.number(axis_y + 16)}" '
                   f'text-anchor=middle>{ck.tick_text(tick, tick_step)}</text>')
    out.append(f'<line class=ax x1="{ck.number(left)}" x2="{ck.number(right)}" y1="{ck.number(axis_y)}" '
               f'y2="{ck.number(axis_y)}"/><text x="{ck.number((left + right) / 2)}" '
               f'y="{ck.number(axis_y + 34)}" text-anchor=middle>{ck.esc(spec.get("x_label", ""))}</text>')
    out += variants
    for k, row in enumerate(rows):
        y = tick_top + 16 * k
        out.append(f'<text class=m x="{ck.number(left - 6)}" y="{ck.number(y + 11)}" '
                   f'text-anchor=end style="font-size:10.5px">{ck.esc(row["label"])}</text>'
                   f'<path class=ax d="' + "".join(
                       f"M{ck.number(sx(p))} {ck.number(y + 2)}v11" for p in row["positions"]
                       if low <= p <= high) + '"/>')
    out.append("</svg>")
    start = "s1" if spec.get("normalised") else "s0"
    controls = ('<div class=cv><span><button type=button data-view=s aria-pressed=true>Stacked</button> <button type=button data-view=o '
                'aria-pressed=false>Overlaid</button></span><label><input type=checkbox data-norm'
                f'{" checked" if start == "s1" else ""}> Normalised to maximum</label></div>')
    items = [(t["sample"], t["measurement_id"], c) for t, c in zip(traces, colours)]
    return ck.wrap(fig_id, "lines", "".join(out) + controls + ck.legend(items))


def _y_axis(spec: dict[str, Any], view: str, shown: dict[str, Any], left: float, base: float,
            height: float, scale: str) -> str:
    """Ticks of the overlaid view: values of y at positions of the y scale."""
    high = 1.0 if view == "norm" else shown["top"]
    low = shown["floor"] / (shown["top"] if view == "norm" else 1.0)
    if scale == "log":
        lo, hi = math.floor(math.log10(low)), math.ceil(math.log10(high))
        values = [10.0 ** e for e in range(lo, hi + 1) if low <= 10.0 ** e <= high * 1.0001]
        step = 1.0
        place = lambda v: (math.log10(v) - math.log10(low)) / ((math.log10(high) - math.log10(low)) or 1.0)
    else:
        values, step = ck.nice_ticks(0.0, high, 5)
        place = (lambda v: math.sqrt(max(v, 0) / high)) if scale == "sqrt" else (lambda v: v / high)
    parts = []
    for v in values:
        y = base - place(v) * height
        text = f"{v:g}" if scale == "log" else ck.tick_text(v, step)
        parts.append(f'<line class=gr x1="{ck.number(left)}" x2="{ck.number(left - 4)}" '
                     f'y1="{ck.number(y)}" y2="{ck.number(y)}"/><text class=m x="{ck.number(left - 8)}" '
                     f'y="{ck.number(y + 4)}" text-anchor=end>{text}</text>')
    label = spec.get("y_label", "") + (" (normalised)" if view == "norm" else "")
    parts.append(f'<text x="14" y="{ck.number(base - height / 2)}" text-anchor=middle '
                 f'transform="rotate(-90 14 {ck.number(base - height / 2)})">{ck.esc(label)}</text>')
    return "".join(parts)
