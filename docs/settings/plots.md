# Plots

Every measurement gets a plot (`plot.png`, plus `plot.pdf` and others you choose), drawn by code in ACS journal style. You can change the look for all plots, per method, per sample and per measurement.

## Settings

**All plots**: defaults in `config/plots.yaml`; yours in `config/lab.yaml`:

```yaml
plots:
  preset: acs                  # the only preset for now
  font: Arial                  # falls back to Liberation Sans
  font_size: 9                 # pt, ticks and text
  label_size: 10               # pt, axis labels
  line_width: 1.0
  palette: okabe-ito           # or a list of colours, e.g. ["#0072B2", "#D55E00"]
  single_column_in: 3.25
  double_column_in: 7.0
  dpi: 300
  formats: [png, pdf]          # png is always written (the report uses it); svg possible
```

**Per method**: the `plot:` block of the method, in `config/lab.yaml` → `<method>: {plot: …}` (all samples), `config/samples.yaml` (one sample) or `config/measurements/<method>/<id>.yaml` (one measurement):

| Method | Keys (default) |
|---|---|
| all | `width_in` (inches, or `single` / `double`), `dpi` |
| xrd | `height_in` (3.0), `y_scale: linear \| sqrt \| log`, `residual_panel: true`, `phase_ticks: true`, `zoom_deg: null` |
| eds | `panels: [image, spectrum, composition]` |
| sem | `scale_bar: true` (`false` cuts off the instrument's info bar, which carries the scale bar) |

`zoom_deg: [35, 45]` adds a second figure (`zoom.png`) next to the full-range plot; the main plot always shows the full measured range.

```bash
labpipe replot             # redraw every plot whose settings changed
labpipe replot --entry ID  # one measurement (also --method M, --all)
```

## You say → setting

| You say | Setting |
|---|---|
| "bigger fonts everywhere" | `plots: {font_size: 11, label_size: 12}` |
| "I need SVG for Inkscape" | `plots: {formats: [png, pdf, svg]}` |
| "the small peaks are invisible" (XRD) | `xrd: {plot: {y_scale: sqrt}}` |
| "zoom into 35–50° for S-9" | `samples.S-9.xrd.plot.zoom_deg: [35, 50]` |
| "full page width" | `<method>: {plot: {width_in: double}}` |
| "only the spectrum and composition" (EDS) | `eds: {plot: {panels: [spectrum, composition]}}` |

## After a change

Plots become "plot out of date" (not re-analysed). The same happens when an update changes the method's drawing code (its plot version). The next automatic pass, or `labpipe replot`, redraws them: the analysis code runs again, `results.json` must come out the same (otherwise nothing is replaced and the review queue says so), old figures go to `_superseded/`. No AI: the notes stay valid. Provenance records the plot settings, so `labpipe replay` redraws the same figure.
