# Adding an analysis method

A method is one package, one settings file, one `methods.yaml` entry and one analyst agent. Nothing else in the pipeline changes. With an agent, say *"I want to analyse <technique> files"*; it follows the same steps (`AGENTS.md` → Adding a new analysis method).

## 1. Design first

Agree with the people who will use it, in `docs/methods/<name>.md` (created in step 2 from a template): input files, wanted numbers with units, plot, defaults, checks, what the analyst agent writes. No analysis code until this is agreed.

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

Add the method to the table in `README.md`.
