# Adding an analysis method

A method is one package, one settings file, one registry entry, and (for agent use) one subagent. Nothing else in the pipeline changes.

Here `sem` is the example name.

## 1. Code: `src/labpipe/methods/sem/`

1. Copy the template:
   ```bash
   cp -r src/labpipe/methods/template src/labpipe/methods/sem        # Linux/macOS
   ```
   On Windows (PowerShell):
   ```powershell
   Copy-Item -Recurse src\labpipe\methods\template src\labpipe\methods\sem
   ```
2. In `analyze.py`, set `METHOD = "sem"` and `RESULTS_SCHEMA = "labpipe.sem.results/1"`.
3. Replace the reading and numerics sections with your analysis.
4. Keep the rest of the contract described in the template's docstring:
   - check the output location before writing anything;
   - open raw files read-only;
   - make every number someone might quote available in `results.json`, with units;
   - write all outputs with `atomic_write_bytes`;
   - build provenance with `labpipe.provenance.build_record`;
   - add deterministic checks (`info`, `warning` or `review`);
   - raise `AnalysisError` for unusable input.
5. If you reuse existing code from another project, port it into the package. Record the source commit and every behaviour change, as `docs/PORTING.md` does for XRD.

The pipeline only talks to `run(...)`, so you can organise the inside freely. The XRD package splits it into `io_*`, `fitting`, `plot`, and so on.

## 2. Settings: `config/sem.yaml`

Start from `src/labpipe/methods/template/config.example.yaml`. Put every tunable number here, with a comment.

Users steer the analysis by overriding these keys:
- per sample: `config/samples.yaml` → `samples.<id>.sem`, or a preset;
- per measurement: `config/measurements/sem/<measurement_id>.yaml`.

Keys that don't exist in `config/sem.yaml` are rejected.

## 3. Registry: `methods.yaml`

```yaml
  sem:
    description: SEM images (Zeiss TIFF with metadata)
    patterns: ["**/SEM/**/*.tif"]          # glob relative to a raw root; ** = any depth
    ignore: []
    subagent: sem-analyst
    cli: "labpipe sem analyze --entry {entry_id}"
    module: labpipe.methods.sem.analyze
    config: config/sem.yaml
    outputs: [results.json, plot.png, plot.pdf, provenance.json, notes.md]
```

- The first method whose `ignore` or `patterns` matches a file claims it, so make the patterns specific.
- After the next `labpipe scan`, the files appear in `manifest.json`, and `labpipe sem analyze --entry ID` works.

## 4. Agent role: `agents/sem-analyst.md`

Copy `agents/xrd-analyst.md` and adapt:
- what the method measures;
- which summary or results.json keys hold what;
- which checks matter;
- what `notes.md` should contain.

Keep the rules: the agent runs only `labpipe` commands, writes only `notes.md`, and never computes or invents numbers. Keep the `labpipe notes verify` step: it works for any method, because it compares the notes with that entry's `results.json`.

Name the agent in `methods.yaml` → `subagent`, then run `labpipe agents sync`. That writes the agent file for the configured harness, and the supervisor may then delegate to the new agent (its allowlist comes from `methods.yaml`). `labpipe doctor` reports an agent named in `methods.yaml` that has no role file.

Optional: give the method a summary module (`methods.yaml` → `summary`, like `labpipe.methods.xrd.summary`). The report and the agents then get compact, plain-language key results instead of the raw `results.json`.

## 5. Tests: `tests/test_sem.py`

- Use synthetic data with a known answer. The XRD tests show the pattern.
- If you have real files, add a regression test that skips when they aren't present. Raw data must not be committed.

## 6. Document

- Add the method to the table in `README.md`.
- Add its conventions (units, axis labels) to `CLAUDE.md`.
