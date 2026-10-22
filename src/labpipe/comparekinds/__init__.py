"""The figure kinds of the comparison figures (`labpipe compare`, docs/ADDING_A_METHOD.md).

A method supplies the data, a kind draws it: `dots`, `lines`, `bars`, `images`. Every kind module
provides, for a spec (a plain, JSON-able dict that holds everything drawn; `provenance.json`
records it):

    render(spec, stem)      matplotlib, ACS style: stem.png (+ the other plot formats), deterministic;
                            returns {"font": {...}, "files": [names]}
    html(spec, fig_id)      an inline <svg> with its controls (no library, no script of its own),
                            deterministic; the report's script switches what is drawn
    check(spec)             raises ValueError saying what is missing or wrong
    caption(spec)           what the figure shows, in words (the report's caption)
    inventory(spec)         [(sample, measurement_id)] drawn, in order

Every mark of a measurement in the SVG carries `data-m="<measurement_id>"` and `data-s="<sample>"`;
the legend has one checkbox per measurement (`data-cm`, `data-cs`), grouped by sample. Colours of
text and lines are the report's variables (`--fg`, `--mut`, `--line`, `--card`, `--chip`) with
fallbacks, so the figure also reads on its own. matplotlib is imported only by `render`.
"""

from __future__ import annotations

import html as _html
import importlib
import math
from typing import Any

KINDS = {"dots": "labpipe.comparekinds.dots", "lines": "labpipe.comparekinds.lines",
         "bars": "labpipe.comparekinds.bars", "images": "labpipe.comparekinds.images"}
WIDTH = 900                     # viewBox width of every figure (text is 12 units high there)
FONT = 12
INK = "#222222"                 # the one dark colour of the static (PNG/PDF) dot plots
YELLOW = "#F0E442"              # Okabe-Ito yellow: unreadable as a thin line on white

STYLE = """<style>
.cf{margin:0;color:var(--fg,#1c1c1a);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.cf svg{display:block;width:100%;height:auto}
.cf svg text{fill:var(--fg,#1c1c1a);font:12px system-ui,-apple-system,"Segoe UI",sans-serif}
.cf svg .m{fill:var(--mut,#66665f)}.cf svg .ax{stroke:var(--mut,#66665f);fill:none;stroke-width:1}
.cf svg .gr{stroke:var(--line,#deded6);stroke-width:1;fill:none}
.cf svg .dk{fill:var(--fg,#1c1c1a);stroke:var(--fg,#1c1c1a)}.cf svg .eb{stroke:var(--fg,#1c1c1a);stroke-width:1.2}
.cf svg .nm{fill:none;stroke:var(--mut,#66665f);stroke-width:1.3}
.cf svg .rf{stroke:var(--mut,#66665f);stroke-width:1;stroke-dasharray:4 3}
.cf .cl,.cf .cv{display:flex;flex-wrap:wrap;gap:.2rem .9rem;align-items:center;margin-top:.5rem}
.cf .cl label,.cf .cv label{display:inline-flex;gap:.3rem;align-items:center;cursor:pointer}
.cf .cs{display:inline-flex;flex-wrap:wrap;gap:.1rem .7rem;align-items:center}
.cf .cn{color:var(--mut,#66665f);font-weight:600}.cf .sw{width:1rem;height:3px;display:inline-block}
.cf button{font:inherit;font-size:.85em;border:1px solid var(--line,#deded6);background:var(--card,#fff);
color:var(--fg,#1c1c1a);border-radius:6px;padding:.05rem .55rem;cursor:pointer}
.cf button:hover,.cf button[aria-pressed=true]{background:var(--chip,#f1f0ea)}
.cf button[aria-pressed=true]{box-shadow:inset 0 -2px 0 var(--fg,#1c1c1a)}
.cf .grid{display:grid;gap:8px;align-items:start}.cf .grid figure{margin:0;text-align:center}
.cf .grid img{max-width:100%;height:auto;border-radius:3px;background:#fff}
.cf .grid .na{color:var(--mut,#66665f);padding:1rem 0;font-style:italic}
.cf .grid .hd{font-weight:600;color:var(--mut,#66665f);text-align:center}
.cf .grid .rl{font-weight:600;align-self:center}
@media print{.cf .cl,.cf .cv{display:none}}
</style>"""


def esc(text: Any) -> str:
    return _html.escape(str(text), quote=True)


def module(kind: str):
    if kind not in KINDS:
        raise ValueError(f"unknown figure kind {kind!r}; known: {', '.join(KINDS)}")
    return importlib.import_module(KINDS[kind])


def number(value: float, places: int = 1) -> str:
    """A coordinate for the SVG: few digits, no '-0'."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def require(spec: dict[str, Any], keys: tuple[str, ...], kind: str) -> None:
    missing = [k for k in keys if k not in spec]
    if missing:
        raise ValueError(f"a {kind} figure needs {', '.join(missing)}")


def nice_ticks(low: float, high: float, count: int = 5) -> tuple[list[float], float]:
    """Round tick values inside [low, high] (1, 2 or 5 times a power of ten) and their step."""
    span = high - low
    if not span > 0:
        return [low], 1.0
    raw = span / max(count, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    first = math.ceil(low / step - 1e-9)
    ticks = []
    while first * step <= high + step * 1e-9:
        ticks.append(round(first * step, 12))
        first += 1
    return ticks, step


def tick_text(value: float, step: float) -> str:
    places = max(0, -math.floor(math.log10(step) + 1e-9))
    text = f"{value:.{places}f}"
    return "0" if text.lstrip("-").strip("0.") == "" else text.replace("-", "−")


def value_text(value: float) -> str:
    return f"{value:.6g}".replace("-", "−")


def extent(values: list[float], pad: float = 0.08) -> tuple[float, float]:
    """The range of the values widened by `pad` of its span (a lone value gets a little room)."""
    low, high = min(values), max(values)
    span = high - low or abs(low) * 0.1 or 1.0
    return low - span * pad, high + span * pad


def levels(positions: list[float], span: float, gap: float = 0.12) -> list[int]:
    """Label heights (0, 1, 2) so that labels of close positions do not overlap."""
    out: list[int] = []
    last: dict[int, float] = {}
    for position in positions:
        level = next((n for n in range(3) if n not in last or position - last[n] > gap * span), 2)
        last[level] = position
        out.append(level)
    return out


def label_of(sample: str, measurement_id: str, counts: dict[str, int]) -> str:
    """What names a row or trace: the sample, or the measurement when the sample has several."""
    return sample if counts.get(sample, 1) <= 1 else measurement_id


def counts(pairs: list[tuple[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for sample, _ in dict.fromkeys(pairs):
        out[sample] = out.get(sample, 0) + 1
    return out


def samples_of(pairs: list[tuple[str, str]]) -> list[str]:
    return list(dict.fromkeys(s for s, _ in pairs))


def trace_colours(plot: dict[str, Any] | None) -> list[str]:
    """The colours that tell lines apart: the plot palette (Okabe-Ito by default) without yellow."""
    from labpipe.plotting.acs import palette
    colours = palette({"palette": ((plot or {}).get("compare") or {}).get("palette", "okabe-ito")})
    return [c for c in colours if c.upper() != YELLOW]


def grey_of(colour: str) -> str:
    """A grey of about the lightness of `colour` (the nominal bar keeps its segments apart)."""
    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    level = 0.45 + 0.4 * (0.2126 * red + 0.7152 * green + 0.0722 * blue)
    return "#" + f"{round(level * 255):02x}" * 3


def text_on(colour: str) -> str:
    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return "#111111" if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.55 else "#ffffff"


def legend(items: list[tuple[str, str, str | None]]) -> str:
    """The controls: All / None and one checkbox per measurement (sample, measurement, colour),
    grouped by sample; a sample with one measurement is a single checkbox."""
    groups: dict[str, list[tuple[str, str | None]]] = {}
    for sample, measurement, colour in items:
        groups.setdefault(sample, []).append((measurement, colour))

    def box(sample: str, measurement: str, colour: str | None, text: str) -> str:
        swatch = f'<span class=sw style="background:{colour}"></span>' if colour else ""
        return (f'<label title="{esc(measurement)}"><input type=checkbox checked '
                f'data-cm="{esc(measurement)}" data-cs="{esc(sample)}">{swatch}{esc(text)}</label>')
    parts = ['<div class=cl><span><button type=button data-all=1>All</button> '
             '<button type=button data-all=0>None</button></span>']
    for sample, members in groups.items():
        if len(members) == 1:
            parts.append(box(sample, members[0][0], members[0][1], sample))
        else:
            parts.append(f'<span class=cs><span class=cn>{esc(sample)}</span>'
                         + "".join(box(sample, m, c, m) for m, c in members) + "</span>")
    return "".join(parts) + "</div>"


def svg_open(width: float, height: float, label: str, extra: str = "") -> str:
    return (f'<svg viewBox="0 0 {number(width)} {number(height)}" role="img" '
            f'aria-label="{esc(label)}"{extra}>')


def wrap(fig_id: str, kind: str, inner: str) -> str:
    """The figure's container: its (scoped) style, then the SVG and controls."""
    return f'{STYLE}<div class=cf id="{esc(fig_id)}" data-kind="{kind}">{inner}</div>'


def mark(measurement: str, sample: str, inner: str, extra: str = "", tag: str = "g") -> str:
    return f'<{tag} data-m="{esc(measurement)}" data-s="{esc(sample)}"{extra}>{inner}</{tag}>'
