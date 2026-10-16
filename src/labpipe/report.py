"""`labpipe report`: one self-contained HTML page for people (analysis/report.html).

Built only from results.json, plot.png and notes.md (deterministic apart from
the agent's plain-words summary, which `labpipe notes verify` has checked).
Each method may provide a summary module (methods.yaml → `summary`) with
`summarize(results) -> {"overview": {...}, "check_before_using": [...], ...}`;
methods without one get a generic card.
"""

from __future__ import annotations

import base64
import hashlib
import html
import importlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe.notes import verify_entry
from labpipe.provenance import code_version, utc_now
from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT, atomic_write_text

CSS = """
:root{--bg:#fbfbf8;--fg:#1c1c1a;--mut:#66665f;--line:#deded6;--card:#ffffff;--warn:#9a5b00;
--bad:#a3262a;--ok:#2f6b3a;--chip:#f1f0ea;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161614;--fg:#ebebe6;
--mut:#a3a39b;--line:#383833;--card:#1f1f1c;--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;
--chip:#2a2a26;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#161614;--fg:#ebebe6;--mut:#a3a39b;--line:#383833;--card:#1f1f1c;
--warn:#e0a54a;--bad:#ef8a8c;--ok:#8fcf99;--chip:#2a2a26;color-scheme:dark}
body{background:var(--bg);color:var(--fg);margin:0;padding-inline:16px;
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
padding:18px 20px;display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:22px}
@media (max-width:760px){.card{grid-template-columns:1fr}}
.card img{width:100%;border-radius:6px;background:#fff}
.card h2{margin:0 0 .2rem;font-size:1.15rem}.meta{color:var(--mut);font-size:.82rem;margin-bottom:.7rem}
.card h3{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
margin:1rem 0 .3rem}.card ul{margin:.2rem 0;padding-left:1.1rem}.card li{margin:.15rem 0}
.check li::marker{color:var(--bad)}.know{color:var(--mut);font-size:.88rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.15rem .9rem;margin:0;font-size:.9rem}
dt{color:var(--mut)}dd{margin:0;font-variant-numeric:tabular-nums}
.plain{font-size:.97rem}details{margin-top:1rem}summary{cursor:pointer;color:var(--mut);font-size:.85rem}.notes{font-size:.88rem}.notes h4{margin:.8rem 0 .2rem}.warnbox{border-left:3px solid var(--warn);padding:.2rem .7rem;margin:.6rem 0}
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


def _card(entry: dict, results: dict, summary: dict, notes_text: str | None,
          notes_ok: bool | None) -> str:
    out = PROJECT_ROOT / entry["output_dir"]
    png = out / "plot.png"
    image = (f'<img alt="plot" src="data:image/png;base64,'
             f'{base64.b64encode(png.read_bytes()).decode()}">' if png.exists() else "")
    rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>"
                   for k, v in summary.get("overview", {}).items())
    for extra in ("internal_standard", "broad_bands"):
        if summary.get(extra):
            label = extra.replace("_", " ").capitalize()
            rows += f"<dt>{label}</dt><dd>{html.escape(summary[extra])}</dd>"
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
    settings_used = _settings_used(out)
    steer_html = ("<h3>Settings beyond the defaults</h3><ul class=know>" + "".join(
        f"<li>{html.escape(line)}</li>" for line in settings_used) + "</ul>") if settings_used else ""
    anchor = html.escape(entry["measurement_id"])
    notes_path = out / "notes.md"
    full_notes = (f'<details><summary>Full notes</summary><div class=notes>'
                  f'{_markdown(notes_path.read_text(encoding="utf-8"))}</div></details>'
                  if notes_path.exists() else "")
    return (f'<section class=card id="{anchor}"><div>{image}</div><div>'
            f'<h2>{html.escape(_title(entry))}</h2>'
            f'<div class=meta>Raw file: {html.escape(entry["relative_path"])}<br>'
            f'Outputs (results.json, plot.pdf, provenance.json): '
            f'<code>{html.escape(entry["output_dir"])}/</code></div>'
            f'<h3>In plain words</h3>{plain}<h3>Key results</h3><dl>{rows}</dl>'
            f'{check_html}{know_html}{steer_html}{full_notes}</div></section>')


def _sample_key(entry: dict) -> tuple:
    sample = entry.get("sample_id") or ""
    number = re.search(r"(\d+)", sample)
    return (re.sub(r"\d+.*", "", sample), int(number.group(1)) if number else 0,
            entry["measurement_id"])


def build(config: dict) -> dict[str, Any]:
    methods = load_methods()
    data = mf.load(config)
    entries = sorted((e for e in data["entries"].values()
                      if e.get("status") in ("done", "stale") and e.get("output_dir")),
                     key=_sample_key)
    statuses = Counter(e["status"] for e in data["entries"].values())
    sections, tables = [], {}
    for entry in entries:
        out = PROJECT_ROOT / entry["output_dir"]
        if not (out / "results.json").exists():
            continue
        results = json.loads((out / "results.json").read_text(encoding="utf-8"))
        module = _summary_module(methods.get(entry["method"], {}))
        summary = module.summarize(results) if module else {
            "overview": {"Status": results.get("status")},
            "check_before_using": [{"code": c["code"], "text": c["message"]}
                                   for c in results.get("checks", []) if c["severity"] == "review"]}
        notes_text = _notes_summary(out / "notes.md")
        notes_ok = verify_entry(config, entry["entry_id"])["ok"] if notes_text is not None else None
        sections.append(_card(entry, results, summary, notes_text, notes_ok))
        n_checks = len(summary.get("check_before_using") or [])
        flag = (f'<span class="flag r">{n_checks} to check</span>' if n_checks
                else '<span class="flag o">ok</span>')
        stale = ' <span class="flag">stale</span>' if entry["status"] == "stale" else ""
        row = {"Measurement": f'<a href="#{html.escape(entry["measurement_id"])}">'
                              f'{html.escape(_title(entry))}</a>'}
        row |= {k: html.escape(str(v)) for k, v in summary.get("overview", {}).items()}
        row["Review"] = flag + stale
        tables.setdefault(entry["method"], []).append(row)

    table_html = ""
    for method, rows in tables.items():
        headers = list(rows[0])
        table_html += (f"<h3>{html.escape(method.upper())}</h3><div class=tw><table><thead><tr>"
                       + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(
                           f'<td class={"num" if h != "Measurement" else ""}>'
                           f"{r.get(h, '')}</td>" for h in headers) + "</tr>" for r in rows)
                       + "</tbody></table></div>")
    pending = statuses.get("pending", 0) + statuses.get("needs_review", 0) + statuses.get("failed", 0)
    page = (f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>labpipe report</title><style>{CSS}</style><main>"
            f"<h1>Analysis report</h1><p class=sub>{len(entries)} analysed measurement(s) · "
            f"generated {utc_now()} · code {html.escape(code_version())}"
            + (f" · {pending} waiting, failed or needing a decision (logs/review_queue.md)"
               if pending else "")
            + "</p>" + table_html + "".join(sections) + "</main>")
    target = PROJECT_ROOT / config["analysis_dir"] / "report.html"
    atomic_write_text(target, page, config)
    return {"report": str(target.relative_to(PROJECT_ROOT)), "measurements": len(entries)}
