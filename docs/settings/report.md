# Report

`analysis/report.html` is the page people read: the postdoc's answer, a comparison of the samples, overview tables per method, and one card per measurement (plot, key numbers, checks, notes). It is one self-contained HTML file, rebuilt after every pass and by `labpipe report`.

## How to read this report

At the top is a collapsed box, "How to read this report", for people who only see the report: what the comparison figures, the cards, the status flags (`ok`, `N to check`, `stale`), "Check before using", the notes and the postdoc's part mean, and that every number comes from `results.json` (with `provenance.json`, replayable). It is generated from these settings, so it describes only what this report shows (layout, sections, card size; stale analyses, galleries and the waiting line only when present). `how_to_read: false` removes it, in the main report or in one `extra` report.

## Settings

Defaults in `config/report.yaml`; yours in `config/lab.yaml`:

```yaml
report:
  group_by: sample             # sample | method | none
  sample_order: name           # name | samples | property:<name>
  sections: [postdoc, compare, overview, measurements]   # order; leave one out to hide it
  how_to_read: true            # the collapsed "How to read this report" box at the top
  compare: all                 # the comparison figures: all, or {xrd: [xrd.a.fcc, xrd.size.fcc], eds: false}
  cards: full                  # full | compact (first figure, key numbers, checks)
  methods: all                 # or [xrd, eds]
  samples: all                 # or IDs / patterns, e.g. ["S-1*"]
  extra:                       # more reports, written as analysis/report-<name>.html
    xrd-only: {methods: [xrd], group_by: none, cards: compact}
```

- `group_by: sample`: each sample with its measurements and image gallery (default).
- `group_by: method`: one part per method, cards ordered by sample.
- `group_by: none`: one list, ordered by sample, then method.
- `sample_order`: the order of the samples in the cards, tables, galleries, navigation and in `labpipe dataset`.
  - `name` (default): natural order by name, S-2 before S-10.
  - `samples`: the order in `config/samples.yaml`; samples not listed there come after, by name.
  - `property:<name>`: by the number `samples.<id>.properties.<name>` in `config/samples.yaml` (e.g. `property:loading_wt`, smallest first); samples without it come last, by name. Anything else is refused with a message.
  An extra report can have its own `sample_order`.
- `compare`: which figures the section "Compare samples" draws (below). `all` (default) is every figure a method makes itself plus every quantity that a figure of its own does not replace; a mapping names methods: a list of figure names, quantity names or globs (`eds.at.*`; a replaced quantity is drawn when you name it), `true` (the default of that method) or `false` (hide that method). A method you do not name keeps its default. Quantity names are those of `labpipe dataset` (`xrd.a.fcc`, `eds.at.Pt`); a method's own figures have their names in the folder `analysis/_compare/<method>/<name>/` (e.g. `composition`). An unknown method, a quantity name that is not that method's (`eds.at.Pt` under `xrd`) or a method with neither quantities nor figures is refused with a message; a name nothing offers is listed under the figures as "not drawn". An extra report can have its own `compare`; its figures are drawn once and shared.
- Each extra report starts from your main report settings and changes only what it lists. The notifications page links all of them.

```bash
labpipe report      # rebuild now; prints the reports written
```

## Compare samples

Figures with all samples together, so values can be compared by eye: a dot plot per quantity (`labpipe dataset` names them: the lattice parameter `xrd.a.fcc`, the Scherrer size `xrd.size.fcc`, one `eds.at.<element>` per element, any quantity a lab method names; it needs measurements of at least two samples, otherwise the line under the figures says "not drawn: xrd.size.fcc (only 1 sample)") and the figures a method makes itself (stacked patterns, composition bars, image grids). They are drawn by code from `results.json` (or the raw file, read only; nothing is computed by an agent), kept in `analysis/_compare/<method>/<name>/` (`plot.png` and `plot.pdf` for papers, `figure.html` for the report, `provenance.json`) and redrawn only when a value, the sample order, the plot style or the drawing code changed; the old figure goes to `analysis/_compare/_superseded/`, and so does a figure no report asks for any more. `labpipe report` does it as part of the report; `labpipe compare [--method M]` does only that and prints what was drawn, kept or skipped, with reasons. `labpipe replay analysis/_compare/<method>/<name>/provenance.json` redraws all outputs of a figure from the data recorded in its provenance and compares the hashes. A figure or method hook that fails is listed as "not drawn" with its reason. Each method has a heading that folds. In the report each figure is interactive: a checkbox per measurement (grouped by sample, with All and None) switches it off, and the row of sample buttons at the top of the section switches a sample off in every figure; patterns can be stacked or overlaid, and normalised to their maximum. Nothing is saved: reloading shows everything; printing shows everything. The figures always cover all samples, also in an extra report that lists only some.

Reading the plot (the dot plot of one quantity; the method pages say how to read their own figures):

- Samples are the rows, in the order of `sample_order`; the value is along the horizontal axis (it may start away from zero: these are points, not bars; read the axis).
- Every measurement is its own point; several measurements of a sample sit side by side in its row. Nothing is averaged.
- The bar is the σ the analysis gives (from the fit). A point without a bar has no σ: differences there cannot be judged against an uncertainty. A grey open diamond is the nominal value, a dashed grey line a reference (named at its top).
- All points have one colour; colour is used only where lines must be told apart (patterns), always with a label next to the line.
- Look again when a sample's points are far apart (two measurements disagree), or when a bar is much larger than the others (a poor fit; see the cards).

Style (size, palette): `plots.compare` in [plots.md](plots.md). No AI is involved; the postdoc may cite a figure by its path, never compute from it.

## The tables

- **EDS overview** (two tables): one row per measurement, one column per element in a fixed order, so a column reads down: metals by atomic number, then non-metals by atomic number (an element not in the table of symbols goes last). The first table is the metals in at% (metal basis, sum 100), the second all elements in wt% as recorded. Units are in the header; numbers are right-aligned; a faint bar in each cell shows the size (at%: out of 100; wt%: relative to the largest of the column).
  - **n.d.**: INCA reported a negative value; taken as 0 in the metal basis (a `review` check). Hover for the recorded value.
  - **grey number**: near the detection limit (the `low_concentration` check lists it).
  - **–**: the element is not in this measurement.
  - **nominal** (grey, italic): a sample with a nominal composition gets this row right under each of its measurements in the metals table.
- **XRD overview**: one row per measurement and fcc phase: lattice parameter a, ± σ in its own cell (so the numbers line up), the closest reference and Δa (a candidate, never an identification), Scherrer size ± σ (a lower bound until an instrument calibration exists), the number of reflections and Rwp. "no σ" says that the fit gave none.
- **Cards**: the key results are a small table where the method offers one (EDS: per element, metal basis, as recorded, nominal and the difference; XRD: quantity, value, ± σ); other methods keep their lines.
- Results from an older analysis that lack some details still render: EDS without `taken_as_zero` or nominal, or a result a table cannot take, goes into a plain table under the others.
- Each table's notes under it explain what is in it. The box "How to read this report" lists the marks that are in this report.

## The look

- A slim bar (its scroll bar is a little thicker) stays at the top while you scroll, in three groups: the sections (postdoc, compare, overview), the methods and the samples, only for the sections that are on. The part you are reading is highlighted. On a narrow screen the bar scrolls sideways. Table headers stay in view inside tall tables.
- Each sample (or method, with `group_by: method`) folds; open by default. "Fold all" / "Unfold all" in the bar. A link to a card in a closed block opens it.
- Every figure has a caption under it (what it shows; for EDS and XRD what the bars and ± are), and the same text is the image's alt text. A method's own wording is `CAPTION` in its summary module.
- Printing (browser, "save as PDF"): no bar or fold buttons, one card per page, everything open.
- Plain style: the same fonts and colours as before; the figure style is `config/plots.yaml`, unchanged. The page has a little plain JavaScript (figures one at a time, fold all, print) and no framework.

## You say → setting

| You say | Setting |
|---|---|
| "I want to see all XRD together" | `group_by: method` |
| "just one long list" | `group_by: none` |
| "order the samples by loading" | `sample_order: property:loading_wt` (with `properties: {loading_wt: 20}` per sample in `config/samples.yaml`) |
| "keep the samples in my own order" | `sample_order: samples` (the order in `config/samples.yaml`) |
| "compare the lattice parameters across samples" | the section is on by default: figure `xrd.a.fcc`. Only these: `compare: {xrd: [xrd.a.fcc], eds: false}` |
| "hide the EDS comparison" | `compare: {eds: false}` |
| "hide a sample (or one measurement) in the comparison" | not a setting: click it off in the report (the sample buttons at the top of "Compare samples", or the boxes under a figure); to leave a sample out of the whole report: `samples: [...]` |
| "also the per-element EDS dots, not only the composition bars" | `compare: {eds: [composition, 'eds.at.*']}` |
| "only the Pt and Pd comparison for EDS" | `compare: {eds: ['eds.at.Pt', 'eds.at.Pd']}` (or `'eds.at.*'`) |
| "no comparison section" | `sections: [postdoc, overview, measurements]` |
| "hide the how-to-read box" | `how_to_read: false` |
| "hide the postdoc's part" | `sections: [overview, measurements]` |
| "too long, only plots and numbers" | `cards: compact` |
| "a separate page for the S-1x series" | `extra: {s1x: {samples: ["S-1*"]}}` |

## Notes checked with older rules

The checks on notes and on the postdoc's analyses get stricter with some releases. Text that passed before is not failed or rewritten because of that: the card (or the postdoc's section) stays as passed and shows a small line, "checked with the rules of labpipe <version>". Notes that really fail the rules they were checked with show the warning as before.

## After a change

Only the report is rebuilt (and the comparison figures that a change touches): no re-analysis, no AI. An extra you remove from the settings keeps its old file (nothing is deleted); delete it yourself if you like.
