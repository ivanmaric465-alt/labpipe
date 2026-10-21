# Report: readable tables and sample comparison (design, draft)

Status: approved 2026-10-20; built step by step (Order of work); steps 1 (tables, sample order, the look) and 2 (the comparison framework, dot plots, the section, settings) are built. Built alongside it: the agents'
writing rules (`docs/WRITING.md`) and the EDS change for negative values.

## What is wrong today (a report of 13 samples: EDS, XRD, SEM)

- **The EDS table cannot be compared down a column.** Each cell is a line sorted by size
  ("Ti 92.7 · Pt 1.8 · Pd 1.7 …"), so every row has the elements in another order; the cards'
  key results are the same lines.
- **No sample comparison.** Each measurement has its card; nothing puts the same quantity of
  several samples side by side (a lattice parameter, a composition, images at one magnification).
- **The look**: one long page without navigation; tables without units in the headers and
  without aligned numbers; colours that mean nothing across figures.

## Rules the design follows

From "Ten simple rules for better figures" (Rougier et al., PLOS Comput Biol 2014), K-Dense's
`scientific-visualization` skill (MIT), ACS table conventions and Tufte's small multiples:

- the same quantity of several samples on **shared axes** (small multiples), never a separate
  figure per sample to flip through;
- **one colour and marker per sample**, the same in every comparison figure and table; colour is
  never the only cue (labels next to the marks);
- every value **with its uncertainty and its kind** (σ from the fit; "no σ" said, not hidden);
  every point shown, several measurements of one sample as several points (nothing averaged in
  the report);
- **zero, missing, below detection and not measured look different** (0, "–", "n.d.", empty);
- tables: units in the header, numbers right-aligned to the decimal point, few horizontal rules,
  no vertical ones, notes under the table;
- bars start at zero; points and lines may use a narrower range, labelled.

All of it is drawn by code from `results.json`. Agents never draw or compute; they may cite
the figures.

## 1. Tables that read down a column

- **EDS**: one row per measurement, one column per element in a **fixed order**: metals by
  atomic number, then the non-metals. Two tables: metals (at%, metal basis) and all elements
  (wt%, as recorded). A faint bar inside each cell shows the size at a glance. A sample with a
  nominal composition gets a second, grey line "nominal". "n.d." marks a value INCA reported
  negative (taken as 0, a `review` check); values under the detection threshold are grey.
- **XRD**: a ± σ aligned on the decimal point, the closest reference and Δa, crystallite size
  ± σ, the number of reflections.
- The cards' key results use the same layout (one small table instead of a line).

## 2. "Compare samples": a new report section

One block per method, between the overview tables and the cards
(`report.sections: [postdoc, compare, overview, measurements]`).

- **Generic, from the named quantities** (`labpipe dataset`): every method with
  `quantities()` gets one dot plot per quantity: samples on the vertical axis (in the sample
  order, below), the value with its σ bar, one point per measurement. The lab's own methods get
  this without code of their own.
- **Method figures** (an optional `compare(...)` in a method's plot module):
  - EDS: one horizontal 100 % bar per measurement (metal basis), the same colour per element in
    every figure, labels on the segments; with a nominal, the nominal bar right under it;
  - XRD: the patterns stacked with an offset on one 2θ axis (the plot settings' `y_scale`
    applies), sample labels at the right, the reference positions as ticks at the bottom; the
    lattice parameter per sample with the references as vertical lines;
  - SEM: one row per sample with the image closest to each gallery magnification, so the same
    magnification lines up in a column.
- **Regenerable**: comparison figures go to `analysis/_compare/<method>/` with their own
  `provenance.json` (each input `results.json` and its sha256, the plot code's version, the
  settings), so `labpipe replay` can redraw them like any other figure. They are redrawn when
  an input or the plot code changes, never by AI.
- **What to compare** is a report setting:
  `compare: {xrd: [xrd.a.fcc, xrd.size.fcc], eds: [composition]}`; default: every quantity
  and figure a method offers.

## 3. Sample order, everywhere

Default: **by name, in natural order** (S-2 before S-10). A report setting changes it, for the
cards, tables and comparisons alike:

```yaml
report:
  sample_order: name          # name | samples (the order in config/samples.yaml) | property:loading_wt
```

The order is one function in `report.py`, so a lab can also change it in its own code.

## 4. The look

- A slim bar at the top: jump to the postdoc, Compare, the overview tables, each method and
  each sample; the bar stays visible while scrolling.
- Sticky table headers; each sample's cards fold (open by default, one click to close all).
- A caption under every figure that says what the error bars are and how many measurements
  it holds; the same text is the image's alt text.
- A print style: no navigation, one card per page, so a PDF from the browser reads well.
- A restrained style: one serif or sans text face, greys and one accent colour, the ACS figure
  style unchanged. No JavaScript framework; a few lines of plain JavaScript only where the
  report has them today (the figure carousel), plus "fold all".

## 5. Settings and docs

- `config/report.yaml`: `compare`, `sample_order`, the new `compare` section; `config/plots.yaml`:
  `compare:` (figure size, offset of stacked patterns, colour palette for samples and elements;
  Okabe–Ito by default, colour-blind safe).
- `docs/settings/report.md` and `plots.md`: the new keys and "You say → setting" rows ("compare
  the lattice parameters", "order the samples by loading"). Method pages: "Reading the plot" for
  each comparison figure. `docs/WRITING.md`: the postdoc may cite comparison figures.

## Order of work

1. Sample order, the EDS and XRD tables, the cards' small tables, the look (navigation, sticky
   headers, folding, captions, print).
2. The comparison framework: `analysis/_compare/`, its provenance and replay, the generic dot
   plots from the named quantities, the report section and settings.
3. The method figures: EDS bars, XRD stacked patterns and lattice parameters, the SEM grid.
4. Docs, "Reading the plot" for the comparison figures, and a look at the rendered report on
   the test data (screenshots: Chrome connected, or the user).

Each step is a branch with tests, and stops for review.

## Decisions (2026-10-20, user)

1. EDS tables: elements by atomic number (metals, then non-metals).
2. Several measurements of one sample: every point is shown.
3. Nominal composition: a second, grey line under the measured one.
4. Negative EDS values: a `review` check, taken as 0 in the metal basis (built).
