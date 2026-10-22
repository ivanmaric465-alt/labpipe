"""`bars`: one horizontal stacked bar per measurement, one colour per category.

Spec (the method supplies values that already sum to its basis, e.g. 100 %; nothing is
normalised here):

    {"kind": "bars", "method", "name", "title", "unit_label": axis label with its unit,
     "categories": [{"name", "color"}, ...]         fixed order, one colour each
     "rows": [{"sample", "measurement_id", "entry_id",
               "segments": [{"category", "value"}, ...]         drawn in this order, from 0
               "nominal_segments": [{"category", "value"}, ...] | None,
               "results_path", "results_sha256"}, ...]
     "plot": the resolved plot style (added by labpipe.compare)}

The value is printed inside a segment that is at least 8 % of its bar; a nominal bar is thinner,
grey-toned, labelled "nominal" and sits right under its measurement's bar. A legend names the
categories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from labpipe import comparekinds as ck

MIN_LABEL = 0.08            # a segment shows its value from this share of its bar
NOMINAL_SCALE = 0.55        # the nominal bar's thickness relative to the measured one


def check(spec: dict[str, Any]) -> None:
    ck.require(spec, ("categories", "rows"), "bars")
    names = {c["name"] for c in spec["categories"]}
    for row in spec["rows"]:
        for key in ("sample", "measurement_id", "segments"):
            if key not in row:
                raise ValueError(f"a bars row needs {key}: {row!r}")
        for segment in row["segments"] + (row.get("nominal_segments") or []):
            if segment["category"] not in names or not ck.finite(segment["value"]):
                raise ValueError(f"bad segment {segment!r} of {row['measurement_id']}")


def inventory(spec: dict[str, Any]) -> list[tuple[str, str]]:
    return [(r["sample"], r["measurement_id"]) for r in spec["rows"]]


def caption(spec: dict[str, Any]) -> str:
    rows = spec["rows"]
    text = (f"{spec.get('title') or spec['name']}: {len(rows)} measurement"
            f"{'s' if len(rows) != 1 else ''} of {len(ck.counts(inventory(spec)))} samples, one bar "
            f"each, colours by {', '.join(c['name'] for c in spec['categories'])}; values of 8 % "
            f"of a bar or more are printed in it.")
    if any(r.get("nominal_segments") for r in rows):
        text += " The thinner grey bar under a measurement is its nominal composition."
    return text


def _colours(spec: dict[str, Any]) -> dict[str, str]:
    return {c["name"]: c["color"] for c in spec["categories"]}


def _scale(spec: dict[str, Any]) -> float:
    totals = [sum(s["value"] for s in segs) for r in spec["rows"]
              for segs in (r["segments"], r.get("nominal_segments") or []) if segs]
    return max(totals, default=1.0) or 1.0


def _shown(value: float) -> str:
    return f"{value:.1f}".replace("-", "−")


def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    from labpipe.plotting.acs import apply_acs, keep_inside, save_figure, style_of, width_in
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    plot = style_of({"plots": spec["plot"]})
    row_in = (spec["plot"].get("compare") or {}).get("height_per_sample_in", 0.3)
    font = apply_acs(plot)
    rows, colours, scale = spec["rows"], _colours(spec), _scale(spec)
    counts = ck.counts(inventory(spec))
    slots = sum(1 + (NOMINAL_SCALE if r.get("nominal_segments") else 0) for r in rows)
    width = width_in({"width_in": "double"}, plot)
    legend_in = 0.22 * -(-len(spec["categories"]) // 6)     # one line per 6 categories
    height = max(1.4, slots * row_in * 1.1 + 0.5 + legend_in)
    figure, axis = plt.subplots(figsize=(width, height))
    figure.subplots_adjust(left=0.7 / width, right=1 - 0.12 / width, bottom=0.5 / height,
                           top=1 - (legend_in + 0.08) / height)
    y, ticks, names = 0.0, [], []
    small = plot["font_size"] - 2.5
    for row in rows:
        nominal = row.get("nominal_segments") or []
        for segments, centre, thick in ((row["segments"], y, 0.8),
                                        (nominal, y + 0.62, 0.8 * NOMINAL_SCALE)):
            if not segments:
                continue
            grey, start = segments is nominal, 0.0
            total = sum(s["value"] for s in segments)
            for segment in segments:
                colour = colours[segment["category"]]
                colour = ck.grey_of(colour) if grey else colour
                axis.barh(centre, segment["value"], left=start, height=thick, color=colour,
                          edgecolor="white", linewidth=0.5)
                if segment["value"] >= MIN_LABEL * total:
                    axis.text(start + segment["value"] / 2, centre, _shown(segment["value"]),
                              ha="center", va="center", fontsize=small - (1 if grey else 0),
                              color=ck.text_on(colour))
                start += segment["value"]
            if grey:
                axis.text(start + scale * 0.01, centre, "nominal", ha="left", va="center",
                          fontsize=small - 0.5, color="0.4")
        ticks.append(y)
        names.append(ck.label_of(row["sample"], row["measurement_id"], counts))
        y += 1.35 if nominal else 1.0
    axis.set_ylim(y - 0.5, -0.5)
    axis.set_xlim(0, scale * 1.08 if any(r.get("nominal_segments") for r in rows) else scale)
    axis.set_yticks(ticks)
    axis.set_yticklabels([n.replace("$", r"\$") for n in names])
    axis.tick_params(axis="y", length=0)
    axis.set_xlabel(spec.get("unit_label", "").replace("$", r"\$"))
    figure.legend(handles=[Patch(facecolor=c["color"], label=c["name"]) for c in spec["categories"]],
                  loc="upper center", ncol=min(len(spec["categories"]), 6), frameon=False,
                  fontsize=small, handlelength=1, columnspacing=1.0,
                  bbox_to_anchor=(0.5, 1.0))
    keep_inside(figure)
    files = save_figure(figure, stem, plot, int(plot["dpi"]))
    plt.close(figure)
    return {"font": font, "files": files}


def html(spec: dict[str, Any], fig_id: str) -> str:
    rows, colours, scale = spec["rows"], _colours(spec), _scale(spec)
    counts = ck.counts(inventory(spec))
    left = max(60, 8 + 6.8 * max((len(ck.label_of(r["sample"], r["measurement_id"], counts))
                                  for r in rows), default=4))
    has_nominal = any(r.get("nominal_segments") for r in rows)
    right, bar, nominal = ck.WIDTH - (70 if has_nominal else 24), 20, 20 * NOMINAL_SCALE
    out, y = [], 6.0
    ticks, step = ck.nice_ticks(0, scale)

    def sx(value: float) -> float:
        return left + value / scale * (right - left)
    body = []
    for row in rows:
        label = ck.label_of(row["sample"], row["measurement_id"], counts)
        parts = [f'<text x="{ck.number(left - 8)}" y="{ck.number(y + bar / 2 + 4)}" '
                 f'text-anchor=end>{ck.esc(label)}</text>']
        for segments, thick, grey in ((row["segments"], bar, False),
                                      (row.get("nominal_segments") or [], nominal, True)):
            if not segments:
                continue
            start, total = 0.0, sum(s["value"] for s in segments)
            if grey:
                y += bar + 3
            for segment in segments:
                colour = colours[segment["category"]]
                colour = ck.grey_of(colour) if grey else colour
                width = segment["value"] / scale * (right - left)
                tip = (f'{segment["category"]}: {ck.value_text(segment["value"])}'
                       f'{" (nominal)" if grey else ""}')
                parts.append(f'<rect x="{ck.number(sx(start))}" y="{ck.number(y)}" '
                             f'width="{ck.number(width, 2)}" height="{ck.number(thick)}" '
                             f'fill="{colour}" stroke="#ffffff" stroke-width=".6">'
                             f'<title>{ck.esc(tip)}</title></rect>')
                if segment["value"] >= MIN_LABEL * total:
                    parts.append(f'<text x="{ck.number(sx(start) + width / 2)}" '
                                 f'y="{ck.number(y + thick / 2 + 4)}" text-anchor=middle '
                                 f'style="fill:{ck.text_on(colour)}{";font-size:10px" if grey else ""}">'
                                 f'{_shown(segment["value"])}</text>')
                start += segment["value"]
            if grey:
                parts.append(f'<text class=m x="{ck.number(sx(start) + 6)}" '
                             f'y="{ck.number(y + nominal / 2 + 4)}" style="font-size:10.5px">nominal</text>')
        body.append(ck.mark(row["measurement_id"], row["sample"], "".join(parts)))
        y += (nominal if row.get("nominal_segments") else bar) + 12
    height = y + 40
    # legend of the categories inside the figure (under the axis)
    legend_y, x = height + 2, left
    key = []
    for category in spec["categories"]:
        width = 22 + 7 * len(category["name"])
        if x + width > right:
            x, legend_y = left, legend_y + 18
        key.append(f'<rect x="{ck.number(x)}" y="{ck.number(legend_y)}" width="12" height="12" '
                   f'fill="{category["color"]}"/><text x="{ck.number(x + 17)}" '
                   f'y="{ck.number(legend_y + 10.5)}">{ck.esc(category["name"])}</text>')
        x += width
    out.append(ck.svg_open(ck.WIDTH, legend_y + 22, spec.get("title") or spec["name"]))
    for tick in ticks:
        out.append(f'<line class=gr x1="{ck.number(sx(tick))}" x2="{ck.number(sx(tick))}" y1="0" '
                   f'y2="{ck.number(y)}"/><text class=m x="{ck.number(sx(tick))}" '
                   f'y="{ck.number(y + 14)}" text-anchor=middle>{ck.tick_text(tick, step)}</text>')
    out.append(f'<line class=ax x1="{ck.number(left)}" x2="{ck.number(right)}" y1="{ck.number(y)}" '
               f'y2="{ck.number(y)}"/><text x="{ck.number((left + right) / 2)}" '
               f'y="{ck.number(y + 32)}" text-anchor=middle>{ck.esc(spec.get("unit_label", ""))}</text>')
    out += body + key + ["</svg>"]
    items = [(s, m, None) for s, m in inventory(spec)]
    return ck.wrap(fig_id, "bars", "".join(out) + ck.legend(items))
