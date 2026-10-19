# Settings: one page per thing you can change, plots and report layout (design, draft)

Status: draft, awaiting approval. Nothing is implemented yet.

## Goal

- Everything a user can change has one short page that says what it does and how to change
  it.
- Plots and the report become adjustable: style for all plots, per method and per
  measurement; report grouped by sample, by method, or not grouped.
- The lab's own preferences live in its own files and are never committed. The defaults
  that come with labpipe stay untouched, so updating labpipe never conflicts with them.

## One set of pages, for people and agents

Each page is written for people and ends with a **You say → setting** table, as the method
pages already do. Agents use the same pages: `AGENTS.md` says "to change anything, find its
page in `docs/settings/`". How an agent applies a change (smallest scope, `note:` with the
date and the user's words, rerun, show before and after) stays once, in `AGENTS.md` →
Steering. There are no separate agent copies that could drift from the human ones.

| Page | What it covers | Files |
|---|---|---|
| `docs/settings/README.md` | index: "I want to change … → page → file" | |
| `data.md` | data folder, which files are recognised or ignored, sample IDs | `local.yaml`, `methods.yaml`, `ignore.yaml`, `sample_ids.yaml`, `pipeline.yaml` → `sample_id` |
| `samples.md` | presets, settings per sample, nominal composition, sample properties | `samples.yaml` |
| `docs/methods/<m>.md` | the analysis of each method (exists) | `<method>.yaml`, `samples.yaml`, `measurements/` |
| `plots.md` | the look of all plots, per method, per measurement | `lab.yaml` → `plots`, method `plot:` blocks |
| `report.md` | layout and content of the report, extra reports | `lab.yaml` → `report` |
| `agents.md` | agent tool, model per role (with the role descriptions) | `local.yaml` → `agents` |
| `automation.md` | watcher, nightly run, batch size, notifications page, pop-ups, cost limit | `local.yaml` → `automation`, `container` |
| `context.md` | the postdoc's context, documents, instructions | `context/` |
| `container.md` | engine, port, allowed hosts, login | `local.yaml` → `container` |

Each page is short:
- one paragraph on what the thing is;
- the settings, with their defaults;
- the "You say → setting" table;
- what happens after a change (nothing, re-plot, re-analysis, or the AI notes being rewritten),
  so the user knows the cost.

## The lab's preferences: `config/lab.yaml` (new, git-ignored)

At the moment, a lab-wide change (e.g. "always use background order 3") means editing a
tracked file such as `config/xrd.yaml`. That makes updating labpipe a merge.

New: one git-ignored file, `config/lab.yaml`, with one section per element. It overrides the
defaults that come with labpipe:

```yaml
plots:   {...}        # look of all plots
report:  {...}        # report layout
xrd:     {...}        # lab-wide XRD settings (same keys as config/xrd.yaml)
eds:     {...}
```

The analysis settings then layer as follows (one new level, the second):

```
config/<method>.yaml          labpipe's defaults (tracked, not edited)
← config/lab.yaml <method>:   your lab's defaults
← samples.yaml presets / sample
← config/measurements/<method>/<id>.yaml
```

- Unknown keys are refused, as now.
- `lab.yaml` is recorded with its sha256 in provenance, as the other files are.
- `labpipe init` creates it with every section commented out.
- With the planned code/project split, it moves into each project folder.

## Plots

**All plots**: `lab.yaml` → `plots`. Defaults (ACS, as now):

```yaml
plots:
  preset: acs                  # the only preset for now
  font: Arial                  # falls back to Liberation Sans, as now
  font_size: 9
  label_size: 10
  line_width: 1.0
  palette: okabe-ito           # or a list of colours
  single_column_in: 3.25
  double_column_in: 7.0
  dpi: 300
  formats: [png, pdf]          # svg also possible; the report uses png
```

**Per method**: the method's `plot:` block, i.e. `config/<method>.yaml`, overridden by
`lab.yaml` → `<method>.plot`. It holds the existing keys (width, height, dpi) plus a few
method-specific ones:

| Method | New keys |
|---|---|
| xrd | `y_scale: linear \| sqrt \| log`, `residual_panel: true`, `phase_ticks: true`, `zoom_deg: null` |
| eds | `panels: [image, spectrum, composition]` |
| sem | `scale_bar: true` |

- **`zoom_deg: [x1, x2]`** gives an *extra* figure (`zoom.png`, shown in the report's figure
  carousel). The main plot keeps the full measured range, as the conventions require.
- **Per measurement or per sample**: the same keys, in `config/measurements/<method>/<id>.yaml`
  or `samples.yaml`, as with any other setting.

**Changing a plot setting never re-analyses and never costs AI.**
- Plot settings are left out of the analysis fingerprint. A plot change no longer makes a
  measurement stale; it marks it **"plot out of date"**.
- **`labpipe replot [--entry ID | --method M | --all]`** redraws the figures:
  - It reruns the analysis code; the plotting is part of it, and it takes seconds.
  - It checks that `results.json` comes out byte-identical. If not, it stops and reports this
    as a reproducibility problem, which a human should look at.
  - The old figures go to `_superseded/<id>/<time>-plot/`; nothing is deleted.
  - Because `results.json` is unchanged, the notes stay valid.
- **Automatic runs** redraw plots that are out of date on their own (code only, no AI).
- **Provenance** records the resolved plot settings, so `labpipe replay` still regenerates the
  same figure.

## Report

`lab.yaml` → `report` (the defaults are today's layout):

```yaml
report:
  group_by: sample             # sample | method | none
  sections: [postdoc, overview, measurements]   # order; leave one out to hide it
  cards: full                  # full | compact (plot and key numbers only)
  methods: all                 # or [xrd, eds]
  samples: all                 # or a list / pattern, e.g. ["S-1*"]
  extra:                       # more reports, same keys
    # xrd-only: {methods: [xrd], group_by: none}
```

- **`group_by: sample`**: as now. Each sample has its measurements and its image gallery.
- **`group_by: method`**: one part per method. Cards are ordered by sample, and the image
  galleries go under their method.
- **`group_by: none`**: all measurements in one list, ordered by sample, then method.
- **`sections`**: the postdoc's analysis, the overview tables, the measurement cards.
- **`extra`**: further reports with their own keys, e.g. `analysis/report-xrd-only.html`.
  They are rebuilt with the main one; the notifications page links to all of them.
- A layout change only rebuilds the report: no re-analysis, no AI.

## Changes to existing files

- `src/labpipe/settings.py`: the `lab.yaml` layer; plot keys left out of the fingerprint; a
  separate plot fingerprint per entry in the manifest.
- `src/labpipe/plotting/`: the style is read from the resolved `plots` settings, no longer
  from constants; ACS stays the default preset.
- **xrd, eds, sem plot code**: the new keys. The figures look the same with the defaults
  (checked by comparing images before and after).
- `src/labpipe/report.py`: grouping, sections, cards, filters, extra reports.
- `src/labpipe/cli.py`: `labpipe replot`; status "plot out of date" in `labpipe status` and
  in the review queue.
- **automation**: redraw out-of-date plots in each pass.
- `docs/settings/*.md` (new); `AGENTS.md` points to them; `docs/methods/*.md` keep their
  method settings and link back; the README links the index.
- **Tests**:
  - layering with `lab.yaml`, and unknown keys refused;
  - a plot change → "plot out of date", not stale; replot keeps `results.json` and the notes;
  - the default figures unchanged;
  - the report in each grouping, sections, cards, filters, extra reports.
