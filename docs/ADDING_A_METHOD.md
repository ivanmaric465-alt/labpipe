# Adding an analysis method

A method is one package, one settings file, one `methods.yaml` entry and one analyst agent. Nothing else in the pipeline changes. With an agent, say *"I want to analyse <technique> files"*; it follows these steps. A new method is a code change: a separate task with tests and a commit, never during analysis work, never unattended.

## 1. Design first

Agree with the people who will use it, in `docs/methods/<name>.md` (created in step 2 from a template). **No analysis code until they approve it.** The agent asks one topic at a time and looks at real example files with the user:
- technique, instrument, software; which files belong together (e.g. image + metadata `.txt`); what is in them, and what only the user knows (areas, loadings, certificates);
- which numbers they want, with units and uncertainty; which plot; what goes into the report;
- sensible defaults and why; what users will want to change (the Settings table); what should be flagged for review;
- what notes may name and how (the Interpretation section);
- existing code to reuse (record source and commit; report its bugs to the user instead of silently fixing them);
- whether the analyst agent writes notes for every measurement, or only on request (cost).

## 2. Scaffold

```bash
labpipe methods new sem --example "/path/to/S-1 a001.tif" --description "SEM images (JEOL)"
```

Creates (never overwrites):

| File | What to do |
|---|---|
| `src/labpipe/methods/sem/analyze.py` | replace the reading and numerics sections |
| `config/sem.yaml` | every tunable number, with a comment |
| `methods.yaml` → `sem` | patterns from the example's extension; narrow them if needed |
| `agents/sem-analyst.md` | what to look at in the plot, what the notes say |
| `tests/test_sem.py` | a synthetic example with a known answer |
| `docs/methods/sem.md` | the design note |

## 3. Implement

The contract `run(...)` fulfils is in the docstring of `src/labpipe/methods/template/analyze.py`. In short:
- check the output location before writing; open raw files read-only;
- every number someone may quote goes into `results.json`, with units;
- checks `{code, severity: info|warning|review, message}`; status `review` if any check is `review`;
- outputs via `atomic_write_bytes`, provenance via `labpipe.provenance.build_record`;
- plots: draw with the user's style (`acs.apply_acs(acs.style_of(settings))`, save with `acs.save_figure`, so `config/lab.yaml` → `plots` applies); method plot options go under `plot:` in `config/<method>.yaml` and only change the figure, never `results.json` (they are left out of the analysis fingerprint, so changing them means `labpipe replot`, not a re-analysis). Document them in the method page and `docs/settings/plots.md`;
- optional: `"figures": [{"file", "caption"}]` in `results.json` (files in the output folder, hashed in provenance) makes the report show those one at a time instead of `plot.png`, e.g. the panels of a multi-panel plot;
- declare the numbers worth comparing across samples as `quantities(results)` in the method's summary module (name `<method>.<what>[.<id>]`, `{value, sigma, unit, key, note?}`, copied from `results.json`), and list them under "Quantities" in the design note; a method with none says so.
- raise `AnalysisError` for unusable input.

If other methods use the same file type (`.txt`, `.jpg`), add a content check:

```yaml
    recognise: labpipe.methods.sem.analyze:recognise   # recognise(path) -> bool, read-only
```

Reused code from another project: port it into the package and record the source commit and every behaviour change (as `docs/PORTING.md` does for XRD).

## 4. Check

```bash
labpipe methods check sem --example "/path/to/S-1 a001.tif"
pytest
```

`methods check` runs the method twice on the example (outputs in `logs/method-check/`) and verifies:
- registry entry, settings file, analyst role, `run()` signature;
- the example is recognised as this method;
- `results.json`, `plot.png`, `plot.pdf`, `provenance.json` exist; `results.json` has schema, status, units and well-formed checks;
- provenance hashes match, and a second run from the recorded settings gives identical files;
- the raw file and its folder are untouched.

## 5. Use

```bash
labpipe agents sync
labpipe scan
labpipe sem analyze --entry <entry-id>
```

Analyse one real file with the user, show the plot and results, adjust the defaults with them. Then add the method to the table in `README.md` and commit.
