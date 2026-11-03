# Adding an analysis method

A method is one package, one settings file, one registry entry and one analyst agent. Nothing else in the pipeline changes. With an agent, say *"I want to analyse <technique> files"*; it follows these steps. A new method is a code change: a separate task, never during analysis work, never unattended.

**Not code, but your own analysis made elsewhere** (an Origin fit exported as PNG and CSV, a table from the instrument software)? That needs no method: `labpipe methods import` takes the files into the report as they are, with their own analyst agent, and writes no Python. See [docs/methods/imported.md](methods/imported.md). The rest of this page is for a method that computes.

Two places, the same steps:

| | Your own method (**project mode**, the usual case) | A method for labpipe itself (**dev mode**: you work in the labpipe repository) |
|---|---|---|
| code | `lab/methods/<name>/` | `src/labpipe/methods/<name>/` |
| registry | `lab/methods.yaml` | `methods.yaml` |
| defaults | `lab/methods/<name>/config.yaml` | `config/<name>.yaml` |
| design and settings page | `lab/methods/<name>/README.md` | `docs/methods/<name>.md` |
| analyst role | `lab/agents/<name>-analyst.md` | `agents/<name>-analyst.md` |
| test | none scaffolded: `labpipe methods check` | `tests/test_<name>.py` |
| import name | `labpipe_lab.<name>` | `labpipe.methods.<name>` |

Your own methods live in your project: a labpipe update never touches them, and they are yours (not shared with other labs). To give a method to everyone, contribute it to labpipe (dev mode).

## 1. Design first

Agree with the people who will use it, in the page (created in step 2 from a template). **No analysis code until they approve it.** The agent asks one topic at a time and looks at real example files with the user:
- technique, instrument, software; which files belong together (e.g. image + metadata `.txt`); what is in them, and what only the user knows (areas, loadings, certificates);
- which numbers they want, with units and uncertainty; which plot; what goes into the report;
- sensible defaults and why; what users will want to change (the Settings table); what should be flagged for review;
- what notes may name and how (the Interpretation section);
- existing code to reuse (record source and commit; report its bugs to the user instead of silently fixing them);
- whether the analyst agent writes notes for every measurement, or only on request (cost).

## 2. Scaffold

```bash
labpipe methods new sem --example "/path/to/S-1 a001.tif" --description "SEM images (JEOL)"   # add --no-analyst for code-only
```

Creates (never overwrites). In a project, only inside `lab/`, never in the code:

| File | What to do |
|---|---|
| `lab/methods/sem/analyze.py` (dev: `src/labpipe/methods/sem/analyze.py`) | replace the reading and numerics sections |
| `lab/methods/sem/config.yaml` (dev: `config/sem.yaml`) | every tunable number, with a comment |
| `lab/methods.yaml` → `sem` (dev: `methods.yaml`) | patterns from the example's extension; narrow them if needed |
| `lab/agents/sem-analyst.md` (dev: `agents/sem-analyst.md`) | what to look at in the plot, what the notes say (worded by `docs/WRITING.md`, which the role already points to) |
| `lab/methods/sem/README.md` (dev: `docs/methods/sem.md`) | the design note |
| project only: `lab/methods/sem/known_answer/` | `expected.yaml`, an example input and a README: replace them with one file whose result you know (step 4) |
| dev only: `tests/test_sem.py` | a synthetic example with a known answer |

In a project the entry's `module` is `labpipe_lab.sem.analyze` and its `config` is `lab/methods/sem/config.yaml`. Add more modules (`summary.py`, `plot.py`) to the same folder and name them `labpipe_lab.sem.summary` in the entry. A lab method may import labpipe's own modules (`labpipe.naming`, `labpipe.plotting.acs`, …) and its own (`labpipe_lab.sem.…`). Rules for names: lower-case letters, digits and `_`; not a shipped method's name (an error names both files) and not a labpipe command (`scan`, `report`, …).

Patterns: a file goes to the first method whose pattern matches, shipped methods first, then yours in the order of `lab/methods.yaml`. To claim a file type a shipped method also uses, add a `recognise:` content check (below), or narrow the patterns.

**Packages**: a lab method can use what labpipe's image has: numpy, scipy, matplotlib, Pillow, PyYAML and their dependencies (`container/requirements.lock`). For anything else, list it in `lab/requirements.txt`, one exact version per line:

```
# lab/requirements.txt
tabulate==0.9.0      # comments are fine
```

Only `name==version` is accepted (no ranges, URLs, `-e`, `-r`, options); `labpipe methods check` says which line is wrong, without building. Its own dependencies are installed at whatever versions fit (never changing labpipe's), and only the listed packages are recorded in provenance.
- **Container**: `labpipe container build` adds a layer on top of labpipe's image for this project only (`localhost/labpipe-<project>-lab:<hash>`; the build needs the internet, the container's allow-list applies only once it runs). `labpipe container start` builds it when missing, `labpipe update` rebuilds it on the new image. After you edit the file, `labpipe container status` and `labpipe doctor` say "lab/requirements.txt changed: labpipe container build"; then `labpipe container start --replace`. Old layers stay (remove them with `podman image rm`).
- **Native** (no container): `labpipe methods check` and `labpipe doctor` print the exact command, `"<venv python>" -m pip install -r lab/requirements.txt` (into labpipe's own environment); you run it, labpipe never does.
- **Provenance**: `environment` records `lab_requirements_sha256` and the installed version of each listed package; `labpipe replay` warns when they differ.
- No file: nothing changes, and labpipe's own image never contains lab packages.

## 3. Implement

The contract `run(...)` fulfils is in the docstring of `src/labpipe/methods/template/analyze.py`. In short:
- check the output location before writing; open raw files read-only;
- every number someone may quote goes into `results.json`, with units;
- checks `{code, severity: info|warning|review, message}`; status `review` if any check is `review`;
- outputs via `atomic_write_bytes`, provenance via `labpipe.provenance.build_record`;
- plots: draw with the user's style (`acs.apply_acs(acs.style_of(settings))`, save with `acs.save_figure`, so `config/lab.yaml` → `plots` applies); method plot options go under `plot:` in `config/<method>.yaml` and only change the figure, never `results.json` (they are left out of the analysis fingerprint, so changing them means `labpipe replot`, not a re-analysis). Document them in the method page (and, for a method of labpipe, `docs/settings/plots.md`);
- optional: `"figures": [{"file", "caption"}]` in `results.json` (files in the output folder, hashed in provenance) makes the report show those one at a time instead of `plot.png`, e.g. the panels of a multi-panel plot;
- declare the numbers worth comparing across samples as `quantities(results)` in the method's summary module (name `<method>.<what>[.<id>]`, `{value, sigma, unit, key, note?, label?}`; `label` is the axis label of the comparison figure, unit included, e.g. "Lattice parameter a, fcc (Å)", copied from `results.json`), and list them under "Quantities" in the design note; a method with none says so.
- optional, comparison figures of your own: `compare: <module>` in the method's entry (methods.yaml; a lab method names `labpipe_lab.<name>.compare`). The module provides `figures(measurements, context) -> [spec, ...]` and, optionally, `COVERS = ["<method>.<quantity>.*"]` (the generic dot plots your figures replace: they are drawn only when the report setting names them). `measurements`: one dict per analysed measurement in the report's sample order: `sample, entry_id, measurement_id, status, results` (the loaded `results.json`), `results_path, results_sha256, output_dir, input` (`results["input"]` or None), `raw_path` (absolute path of the raw file if it resolves here, else None; read it, never write it), `raw_root` (the id of its data root). `context`: `{"plot": the resolved plot style, "config": the method's resolved settings}`. A spec is plain JSON holding **everything drawn**: copy values from `results.json`; for data from a raw file store the plotted points plus `{"raw_root": …, "path": <relative to that root, as in results["input"]>, "sha256": …}` (never the absolute path: it differs between the host and the container). Such a dict, a project-relative `path`/`sha256`, or `results_path`/`results_sha256`, anywhere in the spec, is checked by `labpipe replay`. Never compute scientific numbers there (a display offset or thinning of a drawn line is fine, the spec records the result). Every spec has `kind`, `name` (letters, digits, `_ . + -`; unique in the method; it is the folder `analysis/_compare/<method>/<name>/`), `title` and optionally `caption`. A hook that raises is reported as skipped with its reason. Keep the module small and free of numerics; its source is part of the comparison version, not of the analysis version. The kinds (`labpipe.comparekinds`; each docstring has the full schema):
  - `dots`: `{kind, name, title, label, unit, samples: [rows], points: [{sample, entry_id, measurement_id, value, sigma|None, nominal|None, unit, status, results_path, results_sha256, key}], reference_lines?: [{value, label}]}`: one dark marker per measurement, σ bar where given, nominal as a grey diamond, reference lines dashed.
  - `lines`: `{kind, name, title, x_label, y_label, traces: [{sample, measurement_id, entry_id, x, y, source: {path, sha256}}], tick_rows: [{label, positions}], y_scale: linear|sqrt|log, layout: "stacked", normalised?: false}`: stacked patterns, one colour per trace, labelled at the right; the report offers overlaid and normalised views.
  - `bars`: `{kind, name, title, unit_label, categories: [{name, color}], rows: [{sample, measurement_id, entry_id, segments: [{category, value}], nominal_segments|None, results_path, results_sha256}]}`: one stacked bar per measurement, values from 8 % printed inside, the nominal as a thinner grey bar under it.
  - `images`: `{kind, name, title, columns: [{key, label}], rows: [{sample, cells: {column_key: {measurement_id, entry_id, path, sha256, label}}}]}`: a grid of small images (project-relative `path`, sha256 checked; a changed or missing image is said in its cell).
  Check with `labpipe compare --method <name>`; look at `plot.png` and `figure.html`. The design note's section "Comparison figures" (a `### Reading the plot` under it in the method page, 2-3 bullets) says how to read them.
- optional, for the report (layout only, never new numbers; keep them in the summary module so they stay outside the analysis version): `table(results) -> {"columns", "rows", "notes"}` for the card's key results, `overview_tables(items) -> [table, ...]` for the overview (`items` = `[{"results"}]`, one row per item), and `CAPTION` (what the figure shows, also its alt text). Cells are text or `{"text", "bar", "state", "title"}`; see `labpipe.methods.eds.summary` and `report._spec_table`. Without them the report shows the lines of `summarize()["overview"]`;
- fill in "Reading the plot" in the design note (3-5 bullets: axes, lines, colours, good versus look again, which `plot:` settings change it); agents explain the figure from it, and `methods check` warns while it is empty;
- raise `AnalysisError` for unusable input.

Versions (`labpipe methods versions`): the analysis version covers `module` and what it imports (`labpipe.*` and `labpipe_lab.*`; a lab method's source is read from the project), so changing it marks the method's analyses out of date. To keep drawing out of it, put the drawing in its own module and list it (the template draws inside `analyze.py`, so it counts as analysis):

```yaml
    plot_modules: [labpipe.methods.sem.plot]     # drawing only: a change means "plot out of date", not stale
    analysis_data: ["references/sem/*.json"]     # data the numbers depend on (globs in the code folder; "lab/…": in the project)
    run_on_host: [refs]                           # subcommands of `cli_extension` that never go into the container
```

A lab method names its own modules the same way (`labpipe_lab.sem.plot`) and keeps data under `lab/` (`analysis_data: ["lab/methods/sem/data/*.json"]`).

If other methods use the same file type (`.txt`, `.jpg`), add a content check:

```yaml
    recognise: labpipe.methods.sem.analyze:recognise   # recognise(path) -> bool, read-only
```

If the analysis reads other files besides the main one (a metadata file, linked images), name them, so that a change to one of them makes the measurement out of date:

```yaml
    companions: labpipe.methods.sem.inputs:companions   # companions(path, settings) -> list of Paths, read-only
```

`companions(input_path, settings)` returns the other files the analysis reads that exist now (`settings`: the measurement's resolved settings, for a method whose settings choose files). labpipe hashes them with the main file (`companions` in the manifest) and again after each analysis (`analysed_companions`); a companion that changes, appears or disappears sends the done or stale entry back to pending ("companion S-1 a001.txt changed"). Put the function in its own module (it may import from `analyze`, never the other way round, so the analysis version does not change). Also allowed in a lab method (`labpipe_lab.<name>…` or `labpipe.`).

Reused code from another project: port it into the package and record the source commit and every behaviour change (as `docs/PORTING.md` does for XRD).

## 4. Check

```bash
labpipe methods check sem                                      # a lab method: runs its known_answer/ too
labpipe methods check sem --example "/path/to/S-1 a001.tif"    # the contract, on another file
pytest                  # dev mode
```

In container mode `methods check` runs in the container (it needs the analysis packages); `methods new` and `methods versions` run on the host.

`methods check` runs the method twice on the example (outputs in `logs/method-check/`) and verifies:
- registry entry, settings file, analyst role, `run()` signature;
- the example is recognised as this method;
- `results.json`, `plot.png`, `plot.pdf`, `provenance.json` exist; `results.json` has schema, status, units and well-formed checks;
- provenance hashes match, and a second run from the recorded settings gives identical files;
- the raw file and its folder are untouched;
- a lab method: the known answer (below).

### Known-answer test (lab methods)

`labpipe methods new` writes `lab/methods/<name>/known_answer/`: one input file and `expected.yaml`, the values the method must give for it. `methods check` runs `run()` on that file (into `logs/method-check/`, never `analysis/` or your data folder) and compares each value with `value ± tolerance`; a drifting number fails the check and names the key, what was expected and what came out.

```yaml
input: example.dat          # a file in known_answer/
sample_id: S-1
expect:                     # key: a dotted path in results.json; peaks[0].center reaches into lists
  - {key: results.a, value: 3.61, tolerance: 0.01}
```

The scaffold's example fits the template code, so a new method passes out of the box. When you replace the analysis, replace the file and the numbers with a measurement whose answer you know independently (a published value, a hand calculation), and keep them: any later change to the code is checked against them. A number without `tolerance` is compared for equality (text, `status`); a method without `known_answer/` gets a warning, not an error. Shipped methods have their tests in `tests/`.

### Switching off a shipped method

A project that never measures EDS can switch it off. In `lab/methods.yaml`:

```yaml
disable: [eds]
```

Only shipped methods can be named (an unknown name, a lab method or something that is not a list is an error naming the file; remove a lab method from the file instead). A disabled method is left out everywhere: its files are not recognised and are not reported as unrecognised (`labpipe inventory` lists them as left out, "method eds is disabled in lab/methods.yaml"), nothing is scanned, analysed, reported, compared or given to an analyst for it, and `labpipe methods list` shows it as `off`. Entries it already has stay in `manifest.json` and its results on disk stay untouched; remove the line and they are back in the report.

## 5. Use

```bash
labpipe project sync        # dev mode: labpipe agents sync
labpipe scan
labpipe sem analyze --entry <entry-id>
```

Analyse one real file with the user, show the plot and results, adjust the defaults with them. Then, for a method of labpipe: add it to the table in `README.md` and commit. For your own: `labpipe project sync` lists it for the agents (`AGENTS.md` → "This lab's own methods"); keep `lab/` in your project's backup or git.
