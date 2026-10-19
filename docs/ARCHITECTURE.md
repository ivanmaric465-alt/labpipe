# Architecture

The skeleton of labpipe: what each part does, how data flows, what is written where. This page
is for people and agents changing the code. Using labpipe: `README.md`; agent rules:
`AGENTS.md`; settings: `docs/settings/`. **Keep this page current**: a change that adds,
removes or moves a module, command, file or flow updates it in the same commit. Which other
docs belong to which code: `docs/docmap.yaml` (`scripts/check_docs.py` checks it).

## Invariants (the code enforces them)

1. **Raw data is read-only.** Every write goes through `safety.assert_pipeline_writable` /
   `atomic_write_*`; raw roots and `protected_paths` are refused. In the container the data is
   also mounted read-only.
2. **Numbers come from code.** Analyses are deterministic Python. Agent text is checked by code
   (`notes verify`, `postdoc verify`): every number must be found in the outputs.
3. **Every output can be regenerated from its `provenance.json`**: input hash, resolved
   settings, the settings files used and their hashes, code version, environment, output hashes
   (`replay`).
4. **Nothing is deleted.** Reruns go to `_superseded/`, failures to `_failed/`.
5. **Failures are recorded and skipped**: one bad file never stops a run.

## Data flow

```
data folder (read-only)
  └─ scan ─────────► manifest.json          one entry per raw file: hash, method, sample, status
       └─ analyze ─► analysis/<sample>/<method>/<measurement>/
                       results.json, plot.png/pdf, provenance.json      (code)
                       notes.md                                         (analyst agent, verified)
            └─ review ─► logs/review_queue.md
            └─ report ─► analysis/report.html (+ extra reports)
  dataset ─► quantities across samples ─► postdoc run ─► analysis/_postdoc/<run>/analysis.md
```

**Automatic processing** (inside the container):
1. The watcher notices new files and waits until they settle.
2. It runs one pass: scan, then the code-only analyses and replots.
3. If there is AI work, it starts the supervisor agent, which hands entries to the analysts.
4. It rebuilds the report and notifies (`logs/notifications.jsonl`, page, pop-ups).

## Repository layout

```
src/labpipe/            the package (module map below)
  methods/<m>/          one package per analysis method (xrd, eds, sem; template)
  plotting/             shared plot style (ACS)
agents/                 agent roles, harness-neutral (→ `labpipe agents sync`)
config/                 defaults (tracked) and the lab's files (git-ignored, docs/settings/)
methods.yaml            registry: file patterns → method → agent → module
references/<m>/         reference data (XRD phases: open COD data only)
templates/              files copied into a project (context/)
container/              image: Dockerfile, entrypoint (firewall, proxy, drop privileges), locks
hooks/guard.py          launcher of `labpipe guard` for agent tools
docs/                   methods/, settings/, design/ (approved design notes), SAFETY, SETUP, …
  docmap.yaml           which docs belong to which code, and which docs repeat each other
scripts/check_docs.py   fails when code changed without its docs (CI)
tests/                  pytest; CI on Linux and Windows
install.sh, install.ps1 installers
```

Generated and git-ignored: `analysis/`, `manifest.json`, `logs/`, `context/`,
`.claude/agents/`, `.codex/agents/`, `.opencode/agents/`, the lab's settings files.

## Module map

| Area | Module | Role |
|---|---|---|
| Entry | `cli.py` | argparse commands; forwards host commands into the container in container mode |
| State | `manifest.py` | load, save and lock `manifest.json`; status changes |
| | `scanner.py` | raw roots → manifest; settle time, hashes, duplicates, stale (code or settings), plot out of date, unrecognised files |
| | `registry.py` | `methods.yaml`: patterns, `recognise:` hooks, ignore rules |
| | `naming.py` | sample ID and measurement ID from file names |
| | `inventory.py` | what is recognised in the data folder (read-only) |
| Analysis | `pipeline.py` | run a method on an entry; staging, `_superseded/`, `_failed/`; replot; review queue |
| | `settings.py` | layered settings (defaults ← `lab.yaml` ← samples ← measurement), key checks, fingerprints (analysis and plot separate) |
| | `provenance.py` | provenance records, code version, environment |
| | `replay.py` | regenerate from `provenance.json` and compare hashes |
| | `methods/<m>/analyze.py` | `run(...)`: the method contract (below) |
| | `methods/<m>/summary.py` | `summarize(results)` for report and agents; `quantities(results)` for the dataset |
| | `plotting/acs.py` | style from settings, figure saving, margins |
| Results for people | `report.py` | HTML report: layouts, cards, galleries, the postdoc section, extra reports |
| | `dataset.py` | quantities of all samples; `compare` (differences, trends) |
| | `notes.py` | `notes verify` (numbers vs results), `todo`, `retire` |
| | `postdoc.py` | postdoc runs: start, verify, freshness, list, headless run |
| | `context.py` | `context/` init and text index of documents |
| Agents | `agents.py` | `agents/*.md` → harness files, models per role |
| | `guard.py` | hook: refuses writes outside the allowed paths, raw-data access, disallowed commands |
| Automation | `automation.py` | watcher, one pass, nightly run, notifications, headless agent commands |
| | `inbox.py` | notifications page (local, token, read-only) |
| | `container.py` | build, start, stop, login, shell, pop-up proxy; forwarding of host commands |
| Setup | `doctor.py` | setup state and next step (read-only) |
| | `wizard.py` | `labpipe setup` (asks in the terminal) |
| | `roview.py` | commands for a read-only view (native mode) |
| | `methods_kit.py` | `methods new | check`: scaffold and check a method |
| Support | `safety.py` | project root, write checks, atomic writes, container path mapping |
| | `locking.py` | cross-platform file lock |

## The method contract

A method is a package in `src/labpipe/methods/<m>/`, registered in `methods.yaml`.
Details: `docs/ADDING_A_METHOD.md`, template: `methods/template/`.

- **`analyze.run(input_path, out_dir, settings, context)`** writes:
  - `results.json`: schema, status, units, checks (`info` / `warning` / `review`);
  - `plot.png` (+ formats);
  - `provenance.json`.

  Unusable input raises `AnalysisError` with a readable reason.
- **Settings**: `config/<m>.yaml` holds the defaults and every allowed key. The `plot:` block
  changes figures only.
- **Optional**:
  - `summary.summarize` / `quantities`;
  - a `recognise:` content check;
  - a `cli_extension`;
  - `report: gallery` (+ `gallery.select`);
  - `figures` in results.
- **Docs**: `docs/methods/<m>.md` covers design, settings, interpretation, quantities and
  conventions. An agent role goes in `agents/<m>-analyst.md`, unless the method is code only.

## Agents and the harness

- **Roles**: `agents/*.md` hold the roles, harness-neutral: supervisor, `<m>`-analyst, postdoc.
- **Generated files**: `labpipe agents sync` writes the harness files, with the models from
  `config/local.yaml` → `agents`.
- **Unattended runs** use the project-only settings (`.claude/unattended*.json`; Codex:
  `-c` flags). `guard.py` is the hook in both tools.
- **Postdoc mode**: `LABPIPE_AGENT_MODE=postdoc` narrows what the postdoc may run and write.

## Container mode

- **Image**:
  - Python with the locked dependencies, the agent tools at pinned versions, the labpipe code;
  - the image tag is a hash of `container/**` and `pyproject.toml`.
- **Run**: the repository is mounted read-write and the data folders read-only. A home volume
  holds the logins.
- **Network**: an iptables firewall allows only the local tinyproxy, and the proxy allows only
  the listed hosts. The entrypoint drops privileges.
- **Host side**: `labpipe` on the host forwards commands into the container (`exec`). Local-only
  commands: container, shell, setup, init, doctor, agents, guard, methods, readonly-view.

## Where things are written

| Path | Written by |
|---|---|
| `manifest.json` | scanner, pipeline (locked) |
| `analysis/<sample>/<method>/<m>/` | pipeline (code); `notes.md` by the analyst agent |
| `analysis/_postdoc/<run>/` | postdoc (code), `analysis.md` by the postdoc agent |
| `analysis/report*.html` | report |
| `logs/` | automation, review, supervisor run summaries, notifications |
| `config/` lab files | the user, or an agent with the user's OK |
| `context/` | the user; `context.md` / `instructions.md` by an agent with the user's OK; `.text/` by `context index` |
| harness agent files | `agents sync` |

## Tests

- `tests/` with pytest, as `PYTHONPATH=src python -m pytest -q`.
- CI runs on Linux and Windows.
- Numerics are tested against hand-checked values, and outputs must be byte-identical where
  promised (default figures, replay).
- Container tests are pure: they test the command lines, not a running engine.
- `tests/test_docs.py` checks the docs against the code where it can be done mechanically:
  every command documented, this module map complete, links resolve, settings keys and method
  plot keys on their pages, every agent role in `AGENTS.md`.
