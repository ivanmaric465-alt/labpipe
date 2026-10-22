"""`labpipe report`: one self-contained HTML page for people (analysis/report.html).

Built only from results.json, plot.png and notes.md (deterministic apart from
the agent's plain-words summary, which `labpipe notes verify` has checked).
Each method may provide a summary module (methods.yaml → `summary`) with
`summarize(results) -> {"overview": {...}, "check_before_using": [...], ...}`;
methods without one get a generic card. Methods with `report: gallery` (SEM) get
one gallery per sample instead of a card per measurement; their `gallery` module
chooses the images (`select(items, settings["gallery"])`). A method that lists
`figures` in results.json ([{"file", "caption"}], files in its output folder) gets
those, one at a time, instead of plot.png; galleries are shown the same way
(arrows, keys or side scrolling). By default the page is grouped by sample. It opens with
the postdoc's latest verified analysis (analysis/_postdoc/), or says that there is none,
that it is out of date or that the newest one failed its checks.
Layout and content come from config/report.yaml (the defaults) and config/lab.yaml →
`report` (the lab's own): grouping, sections, card size, method and sample filters, and
`extra` reports written beside the main one as analysis/report-<name>.html.

Tables: a summary module may also provide `table(results)` (the card's key results) and
`overview_tables(items)` (the overview: one row per measurement); both return table specs
({"columns", "rows", "notes"}, see `_spec_table`) and only lay numbers out. Without them the
card shows the `overview` lines and the overview table one column per line. `CAPTION` in the
summary module is what a method's plot shows (caption and alt text of its figure). Samples
are ordered by the `sample_order` setting (`sample_sorter`), everywhere in the report.

The section "Compare samples" (`sections: compare`, `compare:` chooses the quantities and the
methods' own figures) shows the figures of `labpipe.compare` (analysis/_compare/), drawn when the
report is built; matplotlib is imported only then, and a failure to draw is said in the section,
not raised. Each figure is its `figure.html` (inline SVG with checkboxes; the script below
switches measurements and samples on and off, nothing is stored), its caption and links to the
PNG, PDF and provenance; the PNG is the fallback.
"""

from __future__ import annotations

import base64
import hashlib
import html
import importlib
import json
import posixpath
import re
from fnmatch import fnmatchcase
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe import settings as settings_mod
from labpipe.notes import verify_entry
from labpipe.provenance import code_version, utc_now
from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT, atomic_write_text

CSS = """
:root{--bg:#fbfbf8;--fg:#1c1c1a;--mut:#66665f;--line:#deded6;--card:#ffffff;--warn:#9a5b00;
--bad:#a3262a;--ok:#2f6b3a;--chip:#f1f0ea;--bar:#e4e9ee;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161614;--fg:#ebebe6;
--mut:#a3a39b;--line:#383833;--card:#1f1f1c;--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;
--chip:#2a2a26;--bar:#2c3540;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#161614;--fg:#ebebe6;--mut:#a3a39b;--line:#383833;--card:#1f1f1c;
--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;--chip:#2a2a26;--bar:#2c3540;color-scheme:dark}
html{scroll-padding-top:3rem}body{background:var(--bg);color:var(--fg);margin:0;padding-inline:16px;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding-block:28px 60px}
h1{font-size:1.6rem;margin:0}.sub{color:var(--mut);margin:.2rem 0 1.4rem}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:.45rem .7rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0} td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:inherit}.flag{display:inline-block;border-radius:99px;padding:0 .55rem;font-size:.8rem;
font-weight:600;background:var(--chip)}.flag.r{color:var(--bad)}.flag.o{color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-top:22px;
padding:18px 20px;display:grid;grid-template-columns:minmax(0,480px) minmax(0,1fr);gap:22px}
@media (max-width:760px){.card{grid-template-columns:1fr}}
.card img{width:100%;border-radius:6px;background:#fff}
.card h2{margin:0 0 .2rem;font-size:1.15rem}.meta{color:var(--mut);font-size:.82rem;margin-bottom:.7rem}
.card h3{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
margin:1rem 0 .3rem}.card ul{margin:.2rem 0;padding-left:1.1rem}.card li{margin:.15rem 0}
.check li::marker{color:var(--bad)}.know{color:var(--mut);font-size:.88rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.15rem .9rem;margin:0;font-size:.9rem}
dt{color:var(--mut)}dd{margin:0;font-variant-numeric:tabular-nums}
.sample{margin:2.4rem 0 0;font-size:1.3rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
.card.wide{grid-template-columns:1fr}.card.wide .car{max-width:680px;margin:0 auto}
.track{display:flex;overflow-x:auto;scroll-snap-type:x mandatory}
.track figure{flex:0 0 100%;margin:0;scroll-snap-align:start;text-align:center}
.track img{max-height:540px;object-fit:contain}
figcaption{font-size:.8rem;color:var(--mut);margin-top:.25rem}
.nav{display:flex;justify-content:center;align-items:center;gap:.8rem;margin-top:.35rem;
font-size:.85rem;color:var(--mut)}.nav button{width:2.2rem;height:2.2rem;border-radius:99px;
border:1px solid var(--line);background:var(--card);color:var(--fg);font-size:1.1rem;cursor:pointer}
.nav button:disabled{opacity:.3;cursor:default}
@media print{.track{display:block;overflow:visible}.nav{display:none}}
details.howto{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:.55rem 1rem;margin:0 0 1rem;font-size:.9rem}details.howto summary{cursor:pointer;
font-weight:600}details.howto ul{margin:.5rem 0 .2rem;padding-left:1.1rem}details.howto li{margin:.25rem 0}
.postdoc{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-top:22px;
padding:18px 20px}.postdoc h2{margin:0 0 .2rem;font-size:1.25rem}.postdoc .notes{font-size:.95rem}
.plain{font-size:.97rem}details{margin-top:1rem}summary{cursor:pointer;color:var(--mut);font-size:.85rem}.notes{font-size:.88rem}.notes h4{margin:.8rem 0 .2rem}.warnbox{border-left:3px solid var(--warn);padding:.2rem .7rem;margin:.6rem 0}
nav.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;overflow-x:auto;
white-space:nowrap;margin:0 -16px;padding:0 16px;min-height:2.9rem;background:var(--bg);
background:color-mix(in srgb,var(--bg) 90%,var(--card));border-bottom:1px solid var(--line);
box-shadow:0 3px 10px -6px rgba(0,0,0,.28);font-size:.95rem;scrollbar-width:auto;
scrollbar-color:var(--line) transparent}
nav.top::-webkit-scrollbar{height:10px}nav.top::-webkit-scrollbar-track{background:transparent}
nav.top::-webkit-scrollbar-thumb{background:var(--line);border-radius:5px}
nav.top::-webkit-scrollbar-thumb:hover{background:var(--mut)}
nav.top .g{display:flex;align-items:center;gap:.1rem}nav.top .sep{flex:none;width:1px;height:1.3rem;
background:var(--line);margin:0 .8rem}nav.top .gl{color:var(--mut);font-size:.72rem;
text-transform:uppercase;letter-spacing:.05em;margin-right:.35rem}
nav.top a{padding:.3rem .65rem;border-radius:6px;text-decoration:none;color:var(--mut);
transition:background .15s,color .15s}nav.top a:hover{color:var(--fg);background:var(--chip)}
nav.top a.on{color:var(--fg);background:var(--chip);box-shadow:inset 0 -2px 0 var(--fg)}
nav.top .fold{margin-left:auto;padding-left:.8rem;display:flex;gap:.4rem}
nav.top button{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--card);
color:var(--fg);border-radius:6px;padding:.15rem .6rem;cursor:pointer}
nav.top button:hover{background:var(--chip)}
details.blk{margin-top:0}details.blk>summary{list-style:none;color:var(--fg);font-size:inherit}
details.blk>summary::-webkit-details-marker{display:none}
details.blk>summary h2.sample{display:block;margin-top:2.4rem}
details.blk>summary h2.sample::before{content:"▾ ";color:var(--mut)}
details.blk:not([open])>summary h2.sample::before{content:"▸ "}
section.mt h3{margin:1.4rem 0 .4rem}
h2.sec{margin:2.4rem 0 .3rem;font-size:1.3rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
details.cmp>summary h3{display:block;margin:1.2rem 0 .5rem;font-size:1.05rem}
details.cmp>summary h3::before{content:"▾ ";color:var(--mut)}
details.cmp:not([open])>summary h3::before{content:"▸ "}
.cgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
.cgrid>figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 12px}.cgrid>figure>img{width:100%;background:#fff;border-radius:4px}
.cgrid .links{font-size:.8rem;color:var(--mut)}.cgrid .links a{color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:.35rem;align-items:center;margin:.6rem 0 .2rem}
.chips .gl{color:var(--mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.05em}
.chips button{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--card);
color:var(--fg);border-radius:99px;padding:.1rem .7rem;cursor:pointer}
.chips button[aria-pressed=false]{color:var(--mut);text-decoration:line-through;opacity:.6}
.chips button:hover{background:var(--chip)}section.mt h4{margin:1rem 0 .3rem;font-size:.9rem;color:var(--mut)}
.tw.st{max-height:80vh;overflow:auto}.tw.st th{position:sticky;top:0;background:var(--card);z-index:1;
box-shadow:0 1px 0 var(--line)}
th .u{display:block;font-weight:400;text-transform:none;letter-spacing:0}th.r,td.r{text-align:right}
td.num{font-variant-numeric:tabular-nums}td.bar{background-repeat:no-repeat}
td.nd{color:var(--warn);font-style:italic}td.low{color:var(--mut)}td.missing{color:var(--mut);opacity:.7}
tr.nominal td{color:var(--mut);font-style:italic;background-color:rgba(128,128,128,.07)}
tr.sub td:first-child{padding-left:1.4rem}p.tnote{color:var(--mut);font-size:.8rem;margin:.3rem 0 0}
.card table{font-size:.85rem}.card .tw{margin-top:.2rem}
@media print{nav.top,.chips{display:none}body{background:#fff;color:#000;padding-inline:0}
.card,.postdoc{break-inside:avoid;break-after:page;border-color:#bbb}
.tw.st{max-height:none;overflow:visible}.tw.st th{position:static;box-shadow:none}
details.blk>summary h2.sample::before,details.cmp>summary h3::before{content:""}
.cgrid>figure{break-inside:avoid}}
"""


def _summary_module(spec: dict[str, Any]):
    name = spec.get("summary")
    return importlib.import_module(name) if name else None


def _notes_summary(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", path.read_text(encoding="utf-8"),
                      re.S | re.M)
    return match.group(1).strip() if match else None


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)


def _title(entry: dict) -> str:
    """The measurement ID, prefixed with the sample only if it does not already start with it."""
    sample, measurement = entry.get("sample_id") or "?", entry["measurement_id"]
    return measurement if measurement.startswith(sample) else f"{sample} · {measurement}"


def _markdown(text: str) -> str:
    """Minimal Markdown (headings, bullets, tables, paragraphs) for the embedded notes."""
    lines, parts, i = text.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            parts.append("<div class=tw><table>" + "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows)
                + "</table></div>")
            continue
        heading = re.match(r"^(#+)\s+(.*)", line)
        if heading:
            parts.append(f"<h4>{_inline(heading.group(2))}</h4>")
        elif re.match(r"^\s*[-*]\s", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s", "", lines[i]))
                i += 1
            parts.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
            continue
        elif line.strip() and line.strip() != "---":
            parts.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(parts)


def _settings_used(out: Path) -> list[str]:
    """Each settings layer beyond the defaults, with the `note:` of measurement files."""
    path = out / "provenance.json"
    if not path.exists():
        return []
    sources = json.loads(path.read_text(encoding="utf-8")).get("parameters", {}).get("sources", [])
    lines = []
    for source in sources:
        if source.get("layer") == "global defaults":
            continue
        text = source.get("layer") or source.get("file", "")
        file = PROJECT_ROOT / source.get("file", "")
        if file.suffix == ".yaml" and "measurements" in file.parts and file.exists():
            note = (yaml.safe_load(file.read_text(encoding="utf-8")) or {}).get("note")
            changed = hashlib.sha256(file.read_bytes()).hexdigest() != source.get("sha256")
            if note:
                text += f": {' '.join(str(note).split())}"
            if changed:
                text += " (file changed since this analysis)"
        lines.append(text)
    return lines


EMBED_MAX_BYTES = 400_000   # larger plots (photos, e.g. EDS) are embedded as a smaller JPEG
EMBED_WIDTH_PX = 1100


SCRIPT = """
document.querySelectorAll('[data-car]').forEach(c=>{
const t=c.querySelector('.track'),n=t.children.length,p=c.querySelector('[data-prev]'),
x=c.querySelector('[data-next]'),s=c.querySelector('[data-status]');
const at=()=>Math.round(t.scrollLeft/t.clientWidth);
const show=()=>{const k=at();p.disabled=k<=0;x.disabled=k>=n-1;s.textContent=(k+1)+' / '+n};
const go=d=>{t.scrollTo({left:(at()+d)*t.clientWidth});show()};
p.onclick=()=>go(-1);x.onclick=()=>go(1);t.addEventListener('scroll',show,{passive:true});
c.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){e.preventDefault();go(-1)}
else if(e.key==='ArrowRight'){e.preventDefault();go(1)}});show()});
document.querySelectorAll('[data-fold]').forEach(b=>b.onclick=()=>
document.querySelectorAll('details.blk').forEach(d=>d.open=b.dataset.fold==='open'));
const reveal=()=>{let e=document.getElementById(decodeURIComponent(location.hash.slice(1)));
for(;e;e=e.parentElement)if(e.tagName==='DETAILS')e.open=true};
addEventListener('hashchange',reveal);reveal();
(()=>{const nav=document.querySelector('nav.top');if(!nav||!window.IntersectionObserver)return;
const target=a=>{const e=document.getElementById(decodeURIComponent(a.hash.slice(1)));
return e&&(e.tagName==='A'?e.nextElementSibling:e.closest('details.blk')||e)};
const links=[...nav.querySelectorAll('a[href^="#"]')],of=new Map(links.map(a=>[a,target(a)])),live=new Set();
const mark=()=>{links.forEach(a=>a.classList.toggle('on',live.has(of.get(a))));
const a=nav.querySelector('a.on');if(a&&nav.scrollWidth>nav.clientWidth)
nav.scrollTo({left:a.offsetLeft-nav.clientWidth/2,behavior:'smooth'})};
const io=new IntersectionObserver(es=>{es.forEach(e=>e.isIntersecting?live.add(e.target):live.delete(e.target));mark()},
{rootMargin:'-56px 0px -62% 0px'});
new Set(of.values()).forEach(t=>t&&io.observe(t))})();
(()=>{const S=document.getElementById('compare');if(!S)return;
const figs=[...S.querySelectorAll('.cf')],boxes=f=>[...f.querySelectorAll('input[data-cm]')];
const apply=f=>{const on=new Set(boxes(f).filter(c=>c.checked).map(c=>c.dataset.cm));
f.querySelectorAll('[data-m]').forEach(e=>e.closest('svg')?e.style.display=on.has(e.dataset.m)?'':'none'
:e.style.visibility=on.has(e.dataset.m)?'':'hidden');const cs=new Set(boxes(f).filter(c=>c.checked).map(c=>c.dataset.cs));
f.querySelectorAll('[data-row]').forEach(r=>r.style.display=cs.has(r.dataset.row)?'':'none');
f.querySelectorAll('svg').forEach(v=>{const st=+v.dataset.step;if(!st)return;
v.querySelectorAll('.v').forEach(g=>{let k=0;g.querySelectorAll('g[data-i]').forEach(t=>{
const here=on.has(t.dataset.m);t.setAttribute('transform','translate(0 '+(here?(+t.dataset.i-k)*st:0)+')');if(here)k++})})})};
const view=f=>{const s=f.dataset.view||'s',n=f.querySelector('[data-norm]');
const key=s+(n&&n.checked?1:0);f.querySelectorAll('svg .v').forEach(g=>g.style.display=g.dataset.v===key?'':'none')};
const chips=[...S.querySelectorAll('[data-chip]')],all=()=>figs.forEach(apply);
const setSample=(s,on)=>figs.forEach(f=>{boxes(f).filter(c=>c.dataset.cs===s).forEach(c=>c.checked=on)});
const mark=()=>chips.forEach(b=>b.setAttribute('aria-pressed',figs.some(f=>boxes(f).some(
c=>c.dataset.cs===b.dataset.chip&&c.checked))));
S.addEventListener('change',e=>{const f=e.target.closest('.cf');if(!f)return;
if(e.target.dataset.norm!==undefined)view(f);else{apply(f);mark()}});
S.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const f=b.closest('.cf');
if(b.dataset.chip!==undefined){setSample(b.dataset.chip,b.getAttribute('aria-pressed')!=='true');all();mark()}
else if(b.dataset.chips!==undefined){chips.forEach(c=>setSample(c.dataset.chip,b.dataset.chips==='all'));all();mark()}
else if(f&&b.dataset.all!==undefined){boxes(f).forEach(c=>c.checked=b.dataset.all==='1');apply(f);mark()}
else if(f&&b.dataset.view){f.dataset.view=b.dataset.view;f.querySelectorAll('[data-view]').forEach(x=>
x.setAttribute('aria-pressed',x===b));view(f)}});
let saved=[];addEventListener('beforeprint',()=>{saved=figs.map(f=>boxes(f).map(c=>c.checked));
figs.forEach(f=>boxes(f).forEach(c=>c.checked=true));all()});
addEventListener('afterprint',()=>{figs.forEach((f,i)=>boxes(f).forEach((c,j)=>c.checked=saved[i][j]));all()});
all()})();
addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>{d.dataset.was=d.open?1:'';d.open=true}));
addEventListener('afterprint',()=>document.querySelectorAll('details').forEach(d=>{d.open=!!d.dataset.was}));
"""


def _carousel(figures: list[str]) -> str:
    """Figures one at a time: arrows, arrow keys, or side scrolling (all shown when printed)."""
    if len(figures) == 1:
        return f'<div class=track>{figures[0]}</div>'
    return (f'<div class=car data-car tabindex=0 aria-label="figures">'
            f'<div class=track>{"".join(figures)}</div><div class=nav>'
            f'<button type=button data-prev aria-label="previous figure">&larr;</button>'
            f'<span data-status>1 / {len(figures)}</span>'
            f'<button type=button data-next aria-label="next figure">&rarr;</button></div></div>')


def _figure(path: Path, caption: str) -> str:
    """A figure with its caption under it; the caption is also the image's alt text."""
    return (f"<figure>{_embedded(path, caption)}"
            f"<figcaption>{html.escape(caption)}</figcaption></figure>")


def _embedded(png: Path, alt: str = "plot") -> str:
    """The plot as an <img> data URI; plot.png itself stays untouched (publication copy)."""
    data = png.read_bytes()
    kind = "jpeg" if png.suffix.lower() in (".jpg", ".jpeg") else "png"
    if len(data) > EMBED_MAX_BYTES:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            if image.width > EMBED_WIDTH_PX:
                image = image.resize((EMBED_WIDTH_PX, round(image.height * EMBED_WIDTH_PX
                                                            / image.width)), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        data, kind = buffer.getvalue(), "jpeg"
    return (f'<img alt="{html.escape(alt)}" '
            f'src="data:image/{kind};base64,{base64.b64encode(data).decode()}">')


def _spec_rows(spec: dict) -> list[dict]:
    return [row if isinstance(row, dict) else {"cells": row} for row in spec["rows"]]


def _spec_states(spec: dict) -> set[str]:
    """The cell states used in a table spec (nd, low, missing, nominal) and its row classes."""
    states = {c.get("state") for row in _spec_rows(spec) for c in row["cells"]
              if isinstance(c, dict)}
    states |= {row.get("class") for row in _spec_rows(spec)}
    if any(isinstance(c, dict) and c.get("bar") is not None
           for row in _spec_rows(spec) for c in row["cells"]):
        states.add("bar")
    return {x for x in states if x}


def _spec_cell(cell: Any, column: dict) -> str:
    c = cell if isinstance(cell, dict) else {"text": cell}
    classes = ["num"] + (["r"] if column.get("align") == "r" else [])
    if c.get("state"):
        classes.append(c["state"])
    attributes = ""
    if c.get("title"):
        attributes += f' title="{html.escape(c["title"])}"'
    if c.get("bar") is not None:
        width = round(100 * min(max(c["bar"], 0.0), 1.0), 1)
        classes.append("bar")
        attributes += (f' style="background-image:linear-gradient(90deg,var(--bar) {width}%,'
                       f'transparent {width}%)"')
    return f'<td class="{" ".join(classes)}"{attributes}>{html.escape(str(c.get("text", "")))}</td>'


def _spec_table(spec: dict, lead: tuple[str, Any] | None = None,
                trail: tuple[str, Any] | None = None, sticky: bool = False) -> str:
    """A table spec as HTML. spec = {"columns": [{"label", "unit"?, "align": "l"|"r"}],
    "rows": [cells | {"cells", "item"?, "label"?, "class"?}], "notes": [text]}; a cell is a
    text or {"text", "bar": 0..1 (a faint bar behind the number), "state": nd | low | missing
    | nominal, "title"}. `lead` and `trail` = (header, fn(item) -> HTML) add a first and a
    last column for the rows with an `item` (rows without one show their `label`)."""
    columns = spec["columns"]
    head = "".join(f'<th class="{"r" if c.get("align") == "r" else ""}">{html.escape(c["label"])}'
                   + (f'<span class=u>{html.escape(c["unit"])}</span>' if c.get("unit") else "")
                   + "</th>" for c in columns)
    body = ""
    for row in _spec_rows(spec):
        item, cells = row.get("item"), "".join(_spec_cell(c, col)
                                               for c, col in zip(row["cells"], columns))
        first = last = ""
        if lead:
            first = f"<td>{lead[1](item)}</td>" if item is not None else (
                f'<td>{html.escape(row.get("label") or "")}</td>')
        if trail:
            last = f"<td>{trail[1](item)}</td>" if item is not None else "<td></td>"
        body += f'<tr class="{html.escape(row.get("class") or "")}">{first}{cells}{last}</tr>'
    head = (f"<th>{html.escape(lead[0])}</th>" if lead else "") + head \
        + (f"<th>{html.escape(trail[0])}</th>" if trail else "")
    notes = "".join(f"<p class=tnote>{html.escape(n)}</p>" for n in spec.get("notes") or [])
    return (f'<div class="tw{" st" if sticky else ""}"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>{notes}")


def _rules_line(labpipe_version: str | None) -> str:
    """The small line under notes or an analysis that passed the checks of an older release."""
    which = f"labpipe {html.escape(labpipe_version)}" if labpipe_version else "an older labpipe"
    return f"<p class=meta>checked with the rules of {which}</p>"


def _card(entry: dict, results: dict, summary: dict, notes_text: str | None,
          notes_ok: bool | None, checked_with: dict | None = None,
          caption: str = "plot") -> tuple[str, str]:
    """(full card, compact card: plot, key numbers and checks only). `summary["table"]`, when
    the method has one, replaces the key-result lines; `caption` is for the plot (figures
    listed in results.json bring their own)."""
    out = PROJECT_ROOT / entry["output_dir"]
    figures = [_figure(out / f["file"], f.get("caption") or caption)
               for f in results.get("figures") or [] if (out / f["file"]).exists()]
    if not figures and (out / "plot.png").exists():
        figures = [_figure(out / "plot.png", caption)]
    image = _carousel(figures) if figures else ""
    lines = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>"
                    for k, v in summary.get("overview", {}).items()
                    if not summary.get("table"))
    for extra in ("internal_standard", "broad_bands"):
        if summary.get(extra):
            label = extra.replace("_", " ").capitalize()
            lines += f"<dt>{label}</dt><dd>{html.escape(summary[extra])}</dd>"
    rows = (_spec_table(summary["table"]) if summary.get("table") else "") \
        + (f"<dl>{lines}</dl>" if lines else "")
    checks = summary.get("check_before_using") or []
    check_html = ("<h3>Check before using</h3><ul class=check>" + "".join(
        f"<li>{html.escape(c['text'])}</li>" for c in checks) + "</ul>") if checks else ""
    know = summary.get("worth_knowing") or []
    know_html = ("<h3>Worth knowing</h3><ul class=know>" + "".join(
        f"<li>{html.escape(t)}</li>" for t in know) + "</ul>") if know else ""
    if notes_text is None:
        plain = "<p class=know>No notes yet.</p>"
    else:
        plain = "".join(f"<p class=plain>{_inline(p)}</p>" for p in notes_text.split("\n\n"))
        if notes_ok is False:
            plain = ('<p class=warnbox>These notes did not pass <code>labpipe notes verify'
                     '</code>; read them with care.</p>') + plain
        elif checked_with:
            plain += _rules_line(checked_with.get("labpipe"))
    settings_used = _settings_used(out)
    steer_html = ("<h3>Settings beyond the defaults</h3><ul class=know>" + "".join(
        f"<li>{html.escape(line)}</li>" for line in settings_used) + "</ul>") if settings_used else ""
    anchor = html.escape(entry["measurement_id"])
    notes_path = out / "notes.md"
    full_notes = (f'<details><summary>Full notes</summary><div class=notes>'
                  f'{_markdown(notes_path.read_text(encoding="utf-8"))}</div></details>'
                  if notes_path.exists() else "")
    compact = (f'<section class=card id="{anchor}"><div>{_carousel(figures[:1]) if figures else ""}'
               f'</div><div><h2>{html.escape(_title(entry))}</h2>{rows}{check_html}'
               f'</div></section>')
    return (f'<section class=card id="{anchor}"><div>{image}</div><div>'
            f'<h2>{html.escape(_title(entry))}</h2>'
            f'<div class=meta>Raw file: {html.escape(entry["relative_path"])}<br>'
            f'Outputs (results.json, plot.pdf, provenance.json): '
            f'<code>{html.escape(entry["output_dir"])}/</code></div>'
            f'<h3>In plain words</h3>{plain}<h3>Key results</h3>{rows}'
            f'{check_html}{know_html}{steer_html}{full_notes}</div></section>', compact)


def _sample_order(sample: str) -> tuple:
    """Natural order by name: S-2 before S-10."""
    number = re.search(r"(\d+)", sample)
    return (re.sub(r"\d+.*", "", sample), int(number.group(1)) if number else 0, sample)


def _samples_config() -> dict:
    """config/samples.yaml as the dataset reads it ({} without the file)."""
    from labpipe import migrations
    path = PROJECT_ROOT / "config" / "samples.yaml"
    if not path.exists():
        return {}
    return migrations.upgraded(path, yaml.safe_load(path.read_text(encoding="utf-8")) or {},
                               PROJECT_ROOT)


def sample_sorter(order: str, samples: dict | None = None):
    """The one place that decides the order of samples: a key function for sample IDs.

    `order` is the `sample_order` setting: `name` (natural: S-2 before S-10), `samples` (the
    order in config/samples.yaml, unlisted samples after, by name) or `property:<name>` (by
    the number `samples.<id>.properties.<name>`, samples without it last, by name).
    `samples`: the parsed samples.yaml (read from the project when None and needed)."""
    if order == "name":
        return _sample_order
    samples = (_samples_config() if samples is None else samples).get("samples") or {}
    if order == "samples":
        place = {name: i for i, name in enumerate(samples)}
        return lambda sample: (place.get(sample, len(place)), *_sample_order(sample))
    name = order.split(":", 1)[1]

    def key(sample: str) -> tuple:
        value = ((samples.get(sample) or {}).get("properties") or {}).get(name)
        known = isinstance(value, (int, float)) and not isinstance(value, bool)
        return (0, value, *_sample_order(sample)) if known else (1, 0, *_sample_order(sample))
    return key


def _sample_key(entry: dict) -> tuple:
    return (*_sample_order(entry.get("sample_id") or ""), entry["measurement_id"])


def _gallery(method: str, spec: dict, sample: str, items: list[dict]) -> tuple[str, str, dict]:
    """One card (full, compact) with the chosen images of a sample, and its overview-table row."""
    chosen_settings = settings_mod.resolve(method, sample, None)[0]
    chosen = importlib.import_module(spec["gallery"]).select(items, chosen_settings["gallery"])
    figures = []
    for item in chosen:
        out = PROJECT_ROOT / item["entry"]["output_dir"]
        thumb = out / "thumb.jpg"
        acquisition = item["results"].get("acquisition") or {}
        caption = " · ".join(part for part in (
            f"×{acquisition['magnification']:,}" if acquisition.get("magnification") else None,
            f"{acquisition['accelerating_voltage_kV']:g} kV"
            if acquisition.get("accelerating_voltage_kV") is not None else None,
            f"WD {acquisition['working_distance_mm']:g} mm"
            if acquisition.get("working_distance_mm") is not None else None,
            item["entry"]["measurement_id"]) if part)
        if thumb.exists():
            figures.append(_figure(thumb, caption))
    targets = ", ".join(f"×{m:,}" for m in chosen_settings["gallery"].get("magnifications") or [])
    magnifications = sorted((i["results"].get("acquisition") or {}).get("magnification") or 0
                            for i in items)
    card = (f'<section class="card wide" id="{html.escape(sample)}-{method}"><div>'
            f"<h2>{html.escape(sample)} · {method.upper()}</h2>"
            f"<div class=meta>{len(chosen)} of {len(items)} image(s) shown: closest to {targets}"
            f" (both series compete); change in config/{method}.yaml → gallery, per sample in "
            f"config/samples.yaml. All outputs: <code>analysis/{html.escape(sample)}/{method}/</code>"
            f"</div>{_carousel(figures) if figures else ''}</div></section>")
    compact = (f'<section class="card wide" id="{html.escape(sample)}-{method}"><div>'
               f"<h2>{html.escape(sample)} · {method.upper()}</h2>"
               f"{_carousel(figures[:1]) if figures else ''}</div></section>")
    row = {"Sample": (f"{html.escape(sample)}-{method}", html.escape(sample)),
           "Images": str(len(items)),
           "Magnifications": (f"×{magnifications[0]:,} – ×{magnifications[-1]:,}"
                              if magnifications and magnifications[-1] else "?"),
           "Shown": str(len(chosen))}
    return card, compact, row


def _date(run: dict) -> str:
    return html.escape(str(run.get("utc") or run["run"])[:16].replace("T", " "))


def _link_measurements(rendered: str, anchors: dict[str, str]) -> str:
    """Sample and measurement IDs in the postdoc's text become links (text only, not tags)."""
    if not anchors:
        return rendered
    escaped = {html.escape(name): target for name, target in anchors.items()}
    pattern = re.compile(r"(?<![\w.\-])(" + "|".join(
        re.escape(name) for name in sorted(escaped, key=len, reverse=True)) + r")(?![\w\-])")
    parts = re.split(r"(<[^>]+>)", rendered)
    return "".join(part if part.startswith("<") else pattern.sub(
        lambda m: f'<a href="#{html.escape(escaped[m.group(1)])}">{m.group(1)}</a>', part)
        for part in parts)


def _postdoc_section(config: dict, anchors: dict[str, str]) -> str:
    """The postdoc's analysis: the latest verified run, its freshness, a newer run that did
    not pass, earlier runs; or how to ask when there is none."""
    from labpipe import automation, postdoc
    title = "<h2>The postdoc's analysis</h2>"
    runs = postdoc.list_runs(config)
    name = automation.HARNESS_NAMES.get((config.get("agents") or {}).get("harness"))
    how = (f"open {name} in {html.escape(automation._where())} and say: "
           f"<em>ask the postdoc: &lt;your question&gt;</em>" if name else
           f"run <code>labpipe postdoc run \"&lt;your question&gt;\"</code> in "
           f"{html.escape(automation._where())}")
    if not runs:
        return (f"<section class=postdoc id=postdoc>{title}<p class=know>The postdoc has not been "
                f"asked anything yet. To ask, {how}.</p></section>")
    latest = postdoc.latest(config)
    verified, newer = latest["verified"], latest["newer"]
    parts = [title]
    if newer:
        problems = (postdoc._read_json(PROJECT_ROOT / newer["path"] / "verify.json") or {}).get(
            "problems") or []
        what = ("did not pass its checks: " + html.escape("; ".join(problems))
                if newer["state"] == "failed" else
                "has not been checked yet" if newer["state"] == "unverified" and not problems else
                "is being checked or corrected: " + html.escape("; ".join(problems)))
        parts.append(f"<p class=warnbox>The postdoc's analysis of {_date(newer)} "
                     f"(<em>{html.escape(newer.get('question') or '')}</em>) {what}."
                     + (" The last one that passed is below." if verified else "") + "</p>")
    if verified:
        fresh = postdoc.freshness(config, verified["run"])
        meta = " · ".join(html.escape(str(x)) for x in (
            verified.get("model") and f"model {verified['model']}", verified.get("harness"),
            f"run {verified['run']}") if x)
        parts.append(f"<p class=meta>{_date(verified)} · {meta}</p>")
        if verified.get("older_rules"):
            parts.append(_rules_line(verified.get("labpipe")))
        if not fresh["up_to_date"]:
            changes = []
            if fresh["changed_results"]:
                changes.append(f"{len(fresh['changed_results'])} result(s) changed")
            if fresh["new_measurements"]:
                listed = ", ".join(f"{m['sample_id']} {m['method']}"
                                   for m in fresh["new_measurements"][:4])
                more = len(fresh["new_measurements"]) - 4
                changes.append(f"{len(fresh['new_measurements'])} new measurement(s) ({listed}"
                               + (f" and {more} more" if more > 0 else "") + ")")
            if fresh["removed"]:
                changes.append(f"{len(fresh['removed'])} measurement(s) gone")
            if fresh["changed_notes"]:
                changes.append(f"{len(fresh['changed_notes'])} note(s) rewritten")
            for path in fresh["changed_context"] + fresh["changed_settings"]:
                changes.append(f"{Path(path).name} changed")
            parts.append("<p class=warnbox><strong>Out of date:</strong> "
                         + html.escape(", ".join(changes)) + f". To update, {how}.</p>")
        text = (PROJECT_ROOT / verified["path"] / "analysis.md").read_text(encoding="utf-8")
        parts.append(f"<div class=notes>{_link_measurements(_markdown(text), anchors)}</div>")
    elif not newer:
        parts.append(f"<p class=know>No analysis of the postdoc has passed its checks yet. "
                     f"To ask, {how}.</p>")
    shown = {r["run"] for r in (verified, newer) if r}
    earlier = [r for r in runs if r["run"] not in shown]
    if earlier:
        parts.append("<details><summary>Earlier questions</summary><ul>" + "".join(
            f"<li>{_date(r)}: <em>{html.escape(r.get('question') or '')}</em> "
            f"({html.escape(r['state'])}, <code>{html.escape(r['path'])}/analysis.md</code>)</li>"
            for r in earlier) + "</ul></details>")
    return "<section class=postdoc id=postdoc>" + "".join(parts) + "</section>"


REPORT_DEFAULTS = "report.yaml"
GROUPS, CARDS = ("sample", "method", "none"), ("full", "compact")
SECTIONS = ("postdoc", "compare", "overview", "measurements")
ORDER_WORDS = "name, samples or property:<name>"


HOOK_ITEM = "(compare hook)"        # the name of a skipped item that is a failed method hook


def _offers_quantities(spec: dict) -> bool:
    """Does the method name quantities to compare (`quantities` in its summary module)?"""
    try:
        return hasattr(_summary_module(spec), "quantities")
    except ImportError:
        return False


def _check_compare(value: Any, fail, methods: dict) -> None:
    """`compare`: all, or {method: [quantity or figure names or globs] | true | false}."""
    if value == "all":
        return
    offering = sorted(m for m, spec in methods.items()
                      if spec.get("compare") or _offers_quantities(spec))
    if not isinstance(value, dict):
        fail(f"compare must be 'all' or a mapping such as {{xrd: [xrd.a.fcc], eds: false}}, "
             f"not {value!r}")
    for method, wanted in value.items():
        if method not in methods:
            fail(f"compare: unknown method {method!r}; known: {', '.join(sorted(methods))}")
        if method not in offering:
            fail(f"compare: method {method!r} names no quantities or figures to compare; "
                 f"methods that do: {', '.join(offering) or 'none'}")
        if isinstance(wanted, bool) or wanted == "all":
            continue
        if not (isinstance(wanted, list) and all(isinstance(x, str) for x in wanted)):
            fail(f"compare.{method} must be a list of quantity or figure names (or globs such "
                 f"as {method}.*), true or false, not {wanted!r}")
        for name in wanted:     # a method with figures of its own names them freely
            if not methods[method].get("compare") and not name.startswith(method + "."):
                fail(f"compare.{method}: {name!r} is not a {method} quantity; its names start "
                     f"with '{method}.' (list them with `labpipe dataset`)")


def compare_wanted(setting: Any, method: str, name: str, covered: bool = False) -> bool:
    """Does the report's `compare` setting ask for this figure (a method's figure or a quantity)?
    `all` (also for a method that is only named, or true) is every figure of a method and every
    quantity a figure of its own does not cover (`covered`); a list asks for what it names, a
    covered quantity too. A method not named in a mapping keeps all of its figures."""
    if setting == "all":
        return not covered
    wanted = setting.get(method, True)
    if isinstance(wanted, bool):
        return wanted and not covered
    if wanted == "all":
        return not covered
    return any(fnmatchcase(name, pattern) for pattern in wanted)


def _check_values(settings: dict, where: str, methods: dict) -> None:
    known_methods = set(methods)
    def fail(message: str):
        raise settings_mod.SettingsError(f"{where}: {message}")

    if not isinstance(settings["how_to_read"], bool):
        fail(f"how_to_read must be true or false, not {settings['how_to_read']!r}")
    for key, allowed in (("group_by", GROUPS), ("cards", CARDS)):
        if settings[key] not in allowed:
            fail(f"{key} must be one of {', '.join(allowed)}, not {settings[key]!r}")
    order = settings["sample_order"]
    if not (order in ("name", "samples") or (isinstance(order, str) and re.fullmatch(
            r"property:\S+", order))):
        fail(f"sample_order must be {ORDER_WORDS} (the name of a number under "
             f"samples.<id>.properties in config/samples.yaml), not {order!r}")
    _check_compare(settings["compare"], fail, methods)
    sections = settings["sections"]
    if not isinstance(sections, list) or any(x not in SECTIONS for x in sections) \
            or len(set(sections)) != len(sections):
        fail(f"sections must be a list made of {', '.join(SECTIONS)}, each at most once; "
             f"got {sections!r}")
    for key in ("methods", "samples"):
        value = settings[key]
        if value != "all" and not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            fail(f"{key} must be 'all' or a list of names, not {value!r}")
    unknown = [m for m in (settings["methods"] if settings["methods"] != "all" else [])
               if m not in known_methods]
    if unknown:
        fail(f"unknown method(s) {', '.join(unknown)}; known: {', '.join(sorted(known_methods))}")


def extra_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(name)).strip("-")


def report_settings(methods: dict | None = None) -> dict[str, dict]:
    """{file stem: settings} for the main report ("report") and each `extra` one
    ("report-<name>"). config/report.yaml, then config/lab.yaml → report; an extra report
    starts from the main report's resolved settings and applies its own keys on top.
    Unknown keys and bad values raise SettingsError."""
    path = settings_mod.DEFAULTS / REPORT_DEFAULTS
    defaults = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    defaults.setdefault("extra", {})
    base = {k: v for k, v in defaults.items() if k != "extra"}
    lab = (settings_mod.load_lab()[0].get("report")) or {}
    if not isinstance(lab, dict):
        raise settings_mod.SettingsError("config/lab.yaml: report: must be a mapping of settings")
    where = "config/lab.yaml → report"
    settings_mod._check_keys({k: v for k, v in lab.items() if k != "extra"}, base, where)
    if not isinstance(lab.get("extra") or {}, dict):
        raise settings_mod.SettingsError(f"{where}: extra: must be a mapping of report name to settings")
    known = methods if methods is not None else load_methods()
    main = settings_mod._merge(base, {k: v for k, v in lab.items() if k != "extra"})
    _check_values(main, where, known)
    result = {"report": main}
    extras = settings_mod._merge(defaults["extra"] or {}, lab.get("extra") or {})
    for name, own in extras.items():
        stem, here = "report-" + extra_name(name), f"{where} → extra.{name}"
        if stem == "report-" or stem in result:
            raise settings_mod.SettingsError(f"{where}: extra report name {name!r} is empty or "
                                             f"repeats another one once made safe for a file name")
        own = own or {}
        if not isinstance(own, dict):
            raise settings_mod.SettingsError(f"{here}: expected settings (group_by, methods, ...)")
        if "extra" in own:
            raise settings_mod.SettingsError(f"{here}: 'extra' is not allowed inside an extra report")
        settings_mod._check_keys(own, base, here)
        merged = settings_mod._merge(main, own)
        _check_values(merged, here, known)
        result[stem] = merged
    return result


def _wanted(sample: str, method: str, settings: dict) -> bool:
    return (settings["methods"] == "all" or method in settings["methods"]) and (
        settings["samples"] == "all" or any(fnmatchcase(sample, p) for p in settings["samples"]))


MARKS = {
    "bar": "a faint bar behind a number shows its size (at%: out of 100; wt%: relative to the "
           "largest of its column)",
    "nd": "<i>n.d.</i> (not detected): INCA reported a negative value, taken as 0 (hover for the "
          "recorded value)",
    "low": "grey numbers are near the detection limit",
    "missing": "<b>&ndash;</b> or <i>no σ</i>: not in this measurement, or no uncertainty available",
    "nominal": "a grey italic row <i>nominal</i> is the sample's nominal composition"}


def _how_to_read(settings: dict, records: list[dict], banner: str,
                 states: set[str] = frozenset(), nav: bool = False, folds: bool = False,
                 compare: bool = False) -> str:
    """The collapsed "How to read this report" box: only what this report shows, from its
    settings (layout, sections, card size) and from what it holds (stale analyses, galleries,
    several figures per card, table marks in `states`, the navigation bar `nav`, folding
    `folds`). `how_to_read: false` leaves it out."""
    sections, compact = settings["sections"], settings["cards"] == "compact"
    cards = "measurements" in sections
    layout = {"sample": "grouped by sample, each sample with its measurements",
              "method": "grouped by method, cards ordered by sample",
              "none": "one list, ordered by sample, then method"}[settings["group_by"]]
    bullets = []
    if cards:
        bullets.append(
            f"<b>Cards</b>: one per measurement, {layout}" + (
                "; a gallery of images per sample replaces the cards of imaging methods"
                if any(r["gallery"] for r in records) else "") + ". "
            + ("Compact cards show the figure, the key numbers and what to check; the notes "
               "are left out." if compact else
               "Each card has the figure, the key numbers, what to check, the notes and where "
               "the files are."))
    if compare:
        bullets.append(
            "<b>Compare samples</b>: the samples in the order of the report, one point, trace or "
            "bar per measurement (several measurements of a sample sit side by side; nothing is "
            "averaged). In a dot plot the bars are the &sigma; the analysis gives (none where it "
            "has none) and a grey diamond is a nominal value. Tick measurements off under a "
            "figure, or a sample off in the row of sample buttons; nothing is saved. Not drawn: "
            "a quantity that only one sample has.")
    if "overview" in sections:
        stale = any(r["stale"] for r in records)
        bullets.append(
            "<b>Overview tables</b>: one row per measurement. The last column says "
            "<span class=\"flag o\">ok</span> (nothing to check) or "
            "<span class=\"flag r\">N to check</span> (N points under "
            "&ldquo;Check before using&rdquo;)"
            + (", and <span class=flag>stale</span> when the calculation or settings changed "
               "since the analysis, so the numbers are from an older version until it is redone"
               if stale else "") + ".")
    if cards:
        bullets.append("<b>Check before using</b>: points the analysis itself flagged for a "
                       "human to look at; read them before using a number."
                       + ("" if compact else " <b>Worth knowing</b> lists lesser remarks."))
    if cards and not compact:
        bullets.append(
            "<b>In plain words</b>: the notes of an AI analyst. They are checked by code "
            "(<code>labpipe notes verify</code>): numbers must match the results. A warning box "
            "means they failed that check; &ldquo;checked with the rules of labpipe X&rdquo; means "
            "they passed the checks of that older release.")
    if states & set(MARKS):
        bullets.append("<b>Marks in the tables</b>: " + "; ".join(
            MARKS[k] for k in MARKS if k in states) + ".")
    if nav:
        bullets.append("<b>Navigation</b>: the bar at the top jumps to the parts, tables and "
                       "samples and stays in view"
                       + ("; each sample folds (<i>Fold all</i> / <i>Unfold all</i>)"
                          if folds else "")
                       + ". Printing the page gives one card per page, everything open.")
    if cards and (any(r["gallery"] or r["figures"] for r in records)):
        bullets.append("<b>Figures</b>: where a card has several, they are shown one at a time "
                       "(arrows, keys or side scrolling).")
    if "postdoc" in sections:
        bullets.append("<b>The postdoc's analysis</b> (top): the latest answer that passed its "
                       "checks, across measurements. It says when it is out of date.")
    if banner:
        bullets.append("<b>Line under the title</b>: also counts what is not in the report yet "
                       "(waiting, failed, needing a decision, not recognised); the list is in "
                       "<code>logs/review_queue.md</code>.")
    bullets.append("<b>Where numbers come from</b>: only from each measurement's "
                   "<code>results.json</code> (next to its plot, with <code>provenance.json</code>, "
                   "which records the raw file, settings and code); "
                   "<code>labpipe replay</code> regenerates them from that record. Nothing here is "
                   "calculated by AI.")
    return ("<details class=howto><summary>How to read this report</summary><ul>"
            + "".join(f"<li>{b}</li>" for b in bullets)
            + "</ul><p class=know>Hide this box: <code>how_to_read: false</code> under "
              "<code>report</code> in <code>config/lab.yaml</code>.</p></details>")


def _filter_note(settings: dict) -> str:
    parts = []
    if settings["methods"] != "all":
        parts.append("showing " + ", ".join(settings["methods"]))
    if settings["samples"] != "all":
        parts.append("samples " + ", ".join(settings["samples"]))
    return "; ".join(parts)


def _plain_table(rows: list[dict], linked: bool) -> str:
    """One row per measurement; the columns are the method's `overview` lines."""
    headers = list(rows[0])

    def cell(h, r):
        value = r.get(h, "")
        if isinstance(value, tuple):
            value = f'<a href="#{value[0]}">{value[1]}</a>' if linked else value[1]
        return f'<td class={"num" if h not in ("Measurement", "Sample") else ""}>{value}</td>'

    return ("<div class=tw><table><thead><tr>"
            + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(cell(h, r) for h in headers) + "</tr>" for r in rows)
            + "</tbody></table></div>")


def _table(method: str, rows: list[dict], linked: bool) -> str:
    return (f"<section id=table-{html.escape(method)} class=mt><h3>{html.escape(method.upper())}"
            f"</h3>{_plain_table(rows, linked)}</section>")


def _method_tables(method: str, recs: list[dict], linked: bool) -> tuple[str, set[str]]:
    """The overview of one method: the method's own tables when its summary module has
    `overview_tables`, else one row per measurement with the `overview` lines as columns.
    A measurement whose results do not fit the method's tables (an older results.json) goes
    into that plain table instead, under the others. Also the table marks used."""
    hook = recs[0]["overview_tables"]
    fits, rest = [], []
    for rec in recs:
        try:
            if hook and not rec["gallery"]:
                hook([{"results": rec["results"]}])
                fits.append(rec)
                continue
        except (KeyError, TypeError, ValueError, IndexError):
            pass
        rest.append(rec)
    body, used = "", set()
    if fits:
        def link(i: int) -> str:
            anchor, text = fits[i]["row"]["Measurement"]
            return f'<a href="#{anchor}">{text}</a>' if linked else text
        for spec in hook([{"results": r["results"]} for r in fits]):
            body += (f'<h4>{html.escape(spec["title"])}</h4>' if spec.get("title") else "") \
                + _spec_table(spec, lead=("Measurement", link),
                              trail=("Review", lambda i: fits[i]["row"]["Review"]), sticky=True)
            used |= _spec_states(spec)
    if rest:
        body += ("<h4>Other measurements (results without the details the tables above need)</h4>"
                 if fits else "") + _plain_table([r["row"] for r in rest], linked)
    return (f"<section id=table-{html.escape(method)} class=mt><h3>{html.escape(method.upper())}"
            f"</h3>{body}</section>"), used


def _compare_section(settings: dict, built: dict | None, folder: str = "analysis") -> tuple[str, set[str]]:
    """"Compare samples": the comparison figures (`labpipe.compare`) of the methods this report
    shows, one block per method that folds; above them the sample chips; below, what was not
    drawn and why. `built` is what `compare.build` returned (an `error` when it could not draw);
    `folder` is the report's own folder (the links are relative to it)."""
    built = built or {}

    def mine(item: dict) -> bool:
        wanted = settings["compare"]
        if item["name"] == HOOK_ITEM:
            asked = wanted == "all" or wanted.get(item["method"], True) is not False
        else:
            asked = compare_wanted(wanted, item["method"], item["name"], item.get("covered", False))
        return asked and (settings["methods"] == "all" or item["method"] in settings["methods"])

    figures = [f for f in built.get("figures", []) if mine(f)]
    body = ("<p class=tnote>The samples in the order above, one point or trace per measurement "
            "(nothing averaged). Tick measurements off in a figure, or a sample off here; "
            "nothing is saved. Drawn by code from <code>results.json</code> (or the raw file, "
            "read only), kept in <code>analysis/_compare/</code> with their provenance.</p>")
    if built.get("error"):
        body += (f'<p class=warnbox>The comparison figures could not be drawn: '
                 f'{html.escape(built["error"])}</p>')
    present = {s for f in figures for s in f["samples"]}
    order = [s for s in built.get("sample_order", []) if s in present]
    if order:
        body += ('<div class=chips><span class=gl>Samples</span>' + "".join(
            f'<button type=button aria-pressed=true data-chip="{html.escape(s)}">'
            f'{html.escape(s)}</button>' for s in order)
            + '<button type=button data-chips=all>All</button>'
              '<button type=button data-chips=none>None</button></div>')
    methods: dict[str, list[dict]] = {}
    for figure in figures:
        methods.setdefault(figure["method"], []).append(figure)
    for method, items in methods.items():
        cells = "".join(_compare_figure(f, folder) for f in items)
        body += (f'<details class="blk cmp" open id="compare-{html.escape(method)}"><summary>'
                 f'<h3>{html.escape(method.upper())} <span class=know>({len(items)} '
                 f'figure{"s" if len(items) != 1 else ""})</span></h3></summary>'
                 f'<div class=cgrid>{cells}</div></details>')
    if not figures and not built.get("error"):
        body += ("<p class=know>Nothing to compare yet: a quantity needs measurements of at "
                 "least 2 samples.</p>")
    left = [f'{html.escape(i["name"])} ({html.escape(i["reason"])})'
            for i in built.get("skipped", []) + built.get("unmatched", []) if mine(i)]
    if left:
        body += f"<p class=tnote>Not drawn: {'; '.join(left)}.</p>"
    return (f'<section id=compare class=mt><h2 class=sec>Compare samples</h2>{body}</section>',
            set(methods))


def _compare_figure(item: dict, folder: str) -> str:
    """One comparison figure: its interactive `figure.html` (the PNG when that is missing), the
    caption and the links to its files."""
    try:
        shown = (PROJECT_ROOT / item["dir"] / "figure.html").read_text(encoding="utf-8")
    except OSError:
        shown = ""
    if not shown:
        shown = _embedded(PROJECT_ROOT / item["png"], item["caption"])
    where = posixpath.relpath(item["dir"], folder)
    links = [f'<a href="{html.escape(where)}/plot.png">PNG</a>']
    if "plot.pdf" in item.get("files", []):
        links.append(f'<a href="{html.escape(where)}/plot.pdf">PDF</a>')
    links.append(f'<a href="{html.escape(where)}/provenance.json">provenance</a>')
    return (f'<figure>{shown}<figcaption>{html.escape(item["caption"])}</figcaption>'
            f'<div class=links>{" · ".join(links)}</div></figure>')


def _fold(summary: str, cards: str) -> str:
    """A block (sample or method) that folds; open by default."""
    return f"<details class=blk open><summary>{summary}</summary>{cards}</details>"


def _render(config: dict, settings: dict, name: str | None, entries: list[dict],
            records: list[dict], banner: str, samples: dict | None = None,
            compared: dict | None = None) -> str:
    """One report page from the collected records (cards and rows are already built)."""
    key = sample_sorter(settings["sample_order"], samples)
    shown = sorted((r for r in records if _wanted(r["sample"], r["method"], settings)),
                   key=lambda r: key(r["sample"]))        # stable: a sample's own order stays
    count = sum(1 for e in entries if _wanted(e.get("sample_id") or "?", e["method"], settings))
    note = _filter_note(settings)
    with_cards = "measurements" in settings["sections"]
    compact = settings["cards"] == "compact"
    card = "compact" if compact else "card"
    tables: dict[str, list[dict]] = {}
    for rec in shown:
        tables.setdefault(rec["method"], []).append(rec)
    by_sample: dict[str, list[dict]] = {}
    for rec in shown:
        by_sample.setdefault(rec["sample"], []).append(rec)
    group = settings["group_by"]
    body = ""
    if group == "sample":
        for sample in by_sample:             # already in sample order
            body += _fold(f'<h2 class=sample id="sample-{html.escape(sample)}">'
                          f'{html.escape(sample)}</h2>',
                          "".join(r[card] for r in by_sample[sample]))
    else:
        order = (lambda r: (key(r["sample"]), r["method"], r["mid"])) if group == "none" \
            else (lambda r: (key(r["sample"]), r["mid"]))
        done: set[str] = set()

        def put(rec: dict) -> str:   # a sample anchor at the sample's first card
            anchor = "" if rec["sample"] in done else f'<a id="sample-{html.escape(rec["sample"])}"></a>'
            done.add(rec["sample"])
            return anchor + rec[card]
        if group == "none":
            body = "".join(put(r) for r in sorted(shown, key=order))
        else:
            for method in tables:
                body += _fold(f'<h2 class=sample id="method-{html.escape(method)}">'
                              f"{html.escape(method.upper())}</h2>",
                              "".join(put(r) for r in sorted(tables[method], key=order)))
    anchors = {}
    if with_cards:
        anchors = {**{r["mid"]: r["mid"] for r in shown if r["mid"]},
                   **{sample: f"sample-{sample}" for sample in by_sample}}
    overview, states = "", set()
    if "overview" in settings["sections"]:
        for method, recs in tables.items():
            text, used = _method_tables(method, recs, with_cards)
            overview += text
            states |= used
        overview = f"<section id=overview>{overview}</section>"
    if with_cards:
        for rec in shown:
            states |= rec["states"]
    compare_html, compared_methods = (_compare_section(settings, compared, config["analysis_dir"])
                                      if "compare" in settings["sections"] else ("", set()))
    parts = {"postdoc": lambda: _postdoc_section(config, anchors),
             "compare": lambda: compare_html,
             "overview": lambda: overview,
             "measurements": lambda: body}
    folds = with_cards and group != "none"
    groups = []        # the bar: sections | methods | samples
    jump = [(name, label) for name, label in (("postdoc", "Postdoc"), ("compare", "Compare"),
                                              ("overview", "Overview"))
            if name in settings["sections"] and (name != "overview" or tables)]
    if jump:
        groups.append("".join(f'<a href="#{n}">{label}</a>' for n, label in jump))
    shown_methods = ([m for m in tables] if "overview" in settings["sections"] else
                     [m for m in tables if m in compared_methods])
    if shown_methods:
        prefix = "table" if "overview" in settings["sections"] else "compare"
        groups.append('<span class=gl>Methods</span>' + "".join(
            f'<a href="#{prefix}-{html.escape(m)}">{html.escape(m.upper())}</a>'
            for m in shown_methods))
    if with_cards and by_sample:
        groups.append('<span class=gl>Samples</span>' + "".join(
            f'<a href="#sample-{html.escape(s)}">{html.escape(s)}</a>' for s in by_sample))
    nav = ""
    if groups:
        nav = ('<nav class=top aria-label="jump to">'
               + "<span class=sep></span>".join(f"<span class=g>{g}</span>" for g in groups)
               + ('<span class=fold><button type=button data-fold=close>Fold all</button>'
                  '<button type=button data-fold=open>Unfold all</button></span>' if folds else "")
               + "</nav>")
    title = "Analysis report" + (f" · {html.escape(name)}" if name else "")
    return (f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>labpipe {html.escape(name or 'report')}</title><style>{CSS}</style><main>"
            f"<h1>{title}</h1><p class=sub>{count} analysed measurement(s)"
            + (f" · {html.escape(note)}" if note else "")
            + f" · generated {utc_now()} · code {html.escape(code_version())}{banner}</p>{nav}"
            + (_how_to_read(settings, shown, banner, states, bool(nav), folds, bool(compare_html))
               if settings["how_to_read"] else "")
            + "".join(parts[s]() for s in settings["sections"])
            + f"</main><script>{SCRIPT}</script>")


def build(config: dict) -> dict[str, Any]:
    """Write analysis/report.html and one analysis/report-<name>.html per `extra` report."""
    methods = load_methods()
    configured = report_settings(methods)
    data = mf.load(config)
    entries = sorted((e for e in data["entries"].values()
                      if e.get("status") in ("done", "stale") and e.get("output_dir")),
                     key=_sample_key)
    statuses = Counter(e["status"] for e in data["entries"].values())
    gallery_methods = {name for name, spec in methods.items() if spec.get("report") == "gallery"}
    records: list[dict] = []
    gallery_items: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        out = PROJECT_ROOT / entry["output_dir"]
        if not (out / "results.json").exists():
            continue
        results = json.loads((out / "results.json").read_text(encoding="utf-8"))
        if entry["method"] in gallery_methods:
            gallery_items.setdefault((entry.get("sample_id") or "?", entry["method"]), []).append(
                {"entry": entry, "results": results})
            continue
        module = _summary_module(methods.get(entry["method"], {}))
        title = _title(entry)
        caption = (f"{title}: {module.CAPTION}" if hasattr(module, "CAPTION")
                   else f"{entry['method'].upper()} plot of {title}")
        summary = module.summarize(results) if module else {
            "overview": {"Status": results.get("status")},
            "check_before_using": [{"code": c["code"], "text": c["message"]}
                                   for c in results.get("checks", []) if c["severity"] == "review"]}
        notes_text = _notes_summary(out / "notes.md")
        checked = verify_entry(config, entry["entry_id"]) if notes_text is not None else {}
        spec = None
        if hasattr(module, "table"):
            try:
                spec = module.table(results)
            except (KeyError, TypeError, ValueError, IndexError):
                spec = None          # an older results.json: the lines of `overview` instead
        card, compact = _card(entry, results, {**summary, "table": spec}, notes_text,
                              checked["ok"] if checked else None, checked.get("checked_with"),
                              caption)
        figures = len(results.get("figures") or [])
        n_checks = len(summary.get("check_before_using") or [])
        flag = (f'<span class="flag r">{n_checks} to check</span>' if n_checks
                else '<span class="flag o">ok</span>')
        stale = ' <span class="flag">stale</span>' if entry["status"] == "stale" else ""
        row = {"Measurement": (html.escape(entry["measurement_id"]), html.escape(title))}
        row |= {k: html.escape(str(v)) for k, v in summary.get("overview", {}).items()}
        row["Review"] = flag + stale
        records.append({"sample": entry.get("sample_id") or "?", "method": entry["method"],
                        "mid": entry["measurement_id"], "card": card, "compact": compact,
                        "row": row, "gallery": False, "figures": figures > 1,
                        "stale": entry["status"] == "stale", "results": results,
                        "states": _spec_states(spec) if spec else set(),
                        "overview_tables": getattr(module, "overview_tables", None)})
    for (sample, method), items in sorted(gallery_items.items(),
                                          key=lambda kv: (_sample_order(kv[0][0]), kv[0][1])):
        card, compact, row = _gallery(method, methods[method], sample, items)
        records.append({"sample": sample, "method": method, "mid": "", "card": card,
                        "compact": compact, "row": row, "gallery": True, "figures": False,
                        "stale": False, "results": None, "states": set(),
                        "overview_tables": None})
    pending = statuses.get("pending", 0) + statuses.get("needs_review", 0) + statuses.get("failed", 0)
    unrecognised = len(data.get("unrecognised", {}))
    banner = ((f" · {pending} waiting, failed or needing a decision (logs/review_queue.md)"
               if pending else "")
              + (f" · {unrecognised} file(s) not recognised (logs/review_queue.md)"
                 if unrecognised else ""))
    compared = None
    if any("compare" in s["sections"] for s in configured.values()):
        from labpipe import compare       # draws figures: needs matplotlib (the container)
        try:
            compared = compare.build(config)
        except Exception as exc:      # the report must still come out
            compared = {"error": f"{type(exc).__name__}: {exc}"}
    written = []
    samples = _samples_config() if any(s["sample_order"] != "name"
                                       for s in configured.values()) else None
    for stem, settings in configured.items():
        page = _render(config, settings, stem[len("report-"):] if stem != "report" else None,
                       entries, records, banner, samples, compared)
        target = PROJECT_ROOT / config["analysis_dir"] / f"{stem}.html"
        atomic_write_text(target, page, config)
        written.append(target.relative_to(PROJECT_ROOT).as_posix())
    return {"report": written[0], "measurements": len(entries), "reports": written}
