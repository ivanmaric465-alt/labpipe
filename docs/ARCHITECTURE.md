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
   settings, the settings files used and their hashes (each with its root, `code` or
   `project`), git commit, the method's analysis and plot version, environment, output hashes
   (`replay`).
4. **Only a changed calculation marks an analysis out of date.** Each method has an
   *analysis version* and a *plot version* (`versions.py`): hashes of the source that computes
   the results, and of the drawing code. A new git commit, a doc, the report, an agent, another
   method or a local edit changes neither. Details below.
5. **Nothing is deleted.** Reruns go to `_superseded/`, failures to `_failed/`.
6. **Failures are recorded and skipped**: one bad file never stops a run.

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

## Code and projects

Two roots (`safety.py`). The code is replaced by updates and never holds anyone's data; a
project is one lab folder and is never written by an update.

| Root | Is | Found by |
|---|---|---|
| `CODE_ROOT` | the code: defaults, `methods.yaml`, references, agent roles, templates, docs, container | `LABPIPE_CODE`, else the package's repository |
| `PROJECT_ROOT` | one project: settings, context, results, the lab's additions | `LABPIPE_PROJECT` (`LABPIPE_ROOT` is the old name), else the nearest folder from the current one upwards that holds `labpipe.yaml` |

- **Dev mode**: with no project around, `PROJECT_ROOT` is `CODE_ROOT` itself: the repository is
  its own project, as it was before projects existed. Only there are `AGENTS.md`, `docs/` and
  `.claude/*.json` both the sources and what the agents read.
- **An installation** (marked by `.labpipe-installed`, or `LABPIPE_HOME/app`) is never
  its own project: commands there say *"not in a labpipe project; create one with
  `labpipe project new <folder>`"*.
- **Read from the code**: `config/<method>.yaml`, `plots.yaml`, `report.yaml`, `pipeline.yaml`,
  `*.example.yaml`; `methods.yaml`; `references/`; `agents/`; `templates/`; `docs/`; the sources
  of `AGENTS.md`, `CLAUDE.md`, `.claude/*.json`, `.codex/`, `.agents/`; `hooks/guard.py`; `container/`.
- **Read from the project**: `labpipe.yaml`, `config/local.yaml`, `lab.yaml`, `samples.yaml`,
  `sample_ids.yaml`, `ignore.yaml`, `config/measurements/`, `context/`, `lab/`; written: `analysis/`,
  `logs/`, `manifest.json`.

## Install: the host and the container

```
$LABPIPE_HOME/              default ~/.local/share/labpipe (Windows %LOCALAPPDATA%\labpipe)
  app/                      the code: a git clone, marked `.labpipe-installed`; install.sh is run from it
  venv/                     the host environment
~/.local/bin/labpipe        link to venv/bin/labpipe (LABPIPE_BIN: the folder)
~/.config/labpipe/          defaults.yaml (the installer's choices for new projects), claude.env (Docker only)
                            (LABPIPE_CONFIG_HOME)
```

- **The host is slim.** The base package needs PyYAML only and Python 3.10+. It starts the
  container and forwards commands into it, creates projects, generates agent files, runs the
  guard and `doctor`, and `xrd refs` (with the optional pymatgen). numpy, scipy and matplotlib
  are the `analysis` extra and are in the image (`container/requirements.lock`). A test blocks
  them and runs the host commands; CI installs the host part on Python 3.10.
- **A full native install** (`./install.sh --full`, Python 3.14) adds the analysis libraries
  from `requirements.lock`: analysis without a container. CI runs the main tests that way.
- **`labpipe setup --first`** (`installer.py`) is what `install.sh` / `install.ps1` call after the
  environment exists: checks Podman/Docker and the agent tool (shows install commands, never runs
  them), asks the agent tool, models per role and pop-ups, writes `defaults.yaml`, stores the
  login, creates the first project (`project new` takes `agents:` and `container:` engine and
  pop-ups from `defaults.yaml`), starts the container, records existing files as baseline if
  asked. `--answers FILE.yaml` replaces the questions, `--no-start` skips what needs the tools.
- **doctor** in an installation also reports the install location, host Python, container
  engine, whether the image is current and the defaults file.

## Repository layout (the code)

```
src/labpipe/            the package (module map below)
  methods/<m>/          one package per analysis method (xrd, eds, sem; template)
  plotting/             shared plot style (ACS)
agents/                 agent roles, harness-neutral (→ `labpipe agents sync`)
config/                 defaults (`<method>.yaml`, plots, report, pipeline) and `*.example.yaml`
methods.yaml            registry: file patterns → method → agent → module
references/<m>/         reference data (XRD phases: open COD data only)
templates/              context/ and project/ (LAB.md, lab/agents README): files copied into a project
container/              image: Dockerfile, entrypoint (firewall, proxy, drop privileges), locks
hooks/guard.py          launcher of `labpipe guard` for agent tools
.claude/ .codex/ .agents/   sources of the harness settings, rules, hook and skills
AGENTS.md, CLAUDE.md    sources of the agent instructions
docs/                   methods/, settings/, design/ (approved design notes), SAFETY, SETUP, …
  docmap.yaml           which docs belong to which code, and which docs repeat each other
scripts/check_docs.py   fails when code changed without its docs (CI)
tests/                  pytest; CI on Linux and Windows
install.sh, install.ps1 installers (below: Install)
```

## A project (the lab's folder)

```
labpipe.yaml            marks the folder; name, created, labpipe version
config/                 local.yaml (this machine), lab.yaml, samples.yaml, sample_ids.yaml, ignore.yaml, measurements/
context/                the user's project context and documents
lab/LAB.md              the lab's conventions for all agents
lab/agents/<role>.md    appended to that role; <new>.md with frontmatter = an extra role
lab/references/xrd/     the lab's own XRD reference phases (`labpipe xrd refs add`)
analysis/  manifest.json  logs/       results and state
AGENTS.md  CLAUDE.md                  generated: the code's text + "This lab's additions" (LAB.md)
.labpipe/                             generated: docs/, config/ (defaults), methods.yaml, references/,
                                      agents/, hooks/guard.py, generated.json
.claude/*.json  .codex/  .agents/     generated: permissions, hook, rules, skills
.claude/agents/  .codex/agents/  .opencode/agents/   generated: the harness's agent files
```

- **Generated files** come from `labpipe project sync` (`project.py`; `agents sync` calls it in a
  project). Each starts with "Generated by labpipe <version> from <source>. Do not edit; your
  additions go in lab/LAB.md (or lab/agents/)" (JSON has no comments).
- `.labpipe/generated.json` records every generated file with its hash and a hash of their
  sources: `doctor` says "run `labpipe project sync`" when the code or `LAB.md` changed, a file is
  missing or was edited by hand; the guard and the harness permissions refuse agent writes to them.
- Backticked code-side paths in the generated texts (`docs/…`, `methods.yaml`, default
  `config/<method>.yaml`, `references/`, `agents/`) point to the copies in `.labpipe/`.
- In dev mode `project sync` writes only the harness's agent files (as `agents sync` always did).
- `labpipe project new` creates a project (not in a synced folder, never over a non-empty
  folder) and runs the first sync.

## Module map

| Area | Module | Role |
|---|---|---|
| Entry | `cli.py` | argparse commands; forwards host commands into the container in container mode |
| State | `manifest.py` | load, save and lock `manifest.json`; status changes |
| | `scanner.py` | raw roots → manifest; settle time, hashes, duplicates, stale (analysis version or settings), plot out of date (plot version or settings), unrecognised files |
| | `registry.py` | `methods.yaml`: patterns, `recognise:` hooks, ignore rules |
| | `naming.py` | sample ID and measurement ID from file names |
| | `inventory.py` | what is recognised in the data folder (read-only) |
| Analysis | `pipeline.py` | run a method on an entry; staging, `_superseded/`, `_failed/`; replot; review queue |
| | `settings.py` | layered settings (defaults ← `lab.yaml` ← samples ← measurement), key checks, fingerprints (analysis and plot separate) |
| | `provenance.py` | provenance records, git commit, environment |
| | `versions.py` | analysis version and plot version per method (hash of the source), `labpipe methods versions` |
| | `replay.py` | regenerate from `provenance.json` and compare hashes |
| | `methods/<m>/analyze.py` | `run(...)`: the method contract (below) |
| | `methods/<m>/summary.py` | `summarize(results)` for report and agents; `quantities(results)` for the dataset |
| | `plotting/acs.py` | style from settings, figure saving, margins |
| Results for people | `report.py` | HTML report: layouts, cards, galleries, the postdoc section, extra reports |
| | `dataset.py` | quantities of all samples; `compare` (differences, trends) |
| | `notes.py` | `notes verify` (numbers vs results), `todo`, `retire` |
| | `postdoc.py` | postdoc runs: start, verify, freshness, list, headless run |
| | `context.py` | `context/` init and text index of documents |
| Agents | `agents.py` | `agents/*.md` (+ the lab's overlays and extra roles) → harness files, models per role |
| | `project.py` | `project new`; `project sync`: the generated files of a project, `generated.json`, status for doctor |
| | `guard.py` | hook: refuses writes outside the allowed paths, to generated files, raw-data access, disallowed commands |
| Automation | `automation.py` | watcher, one pass, nightly run, notifications, headless agent commands |
| | `inbox.py` | notifications page (local, token, read-only) |
| | `container.py` | build (staged context), start (name, port, mounts per project), stop, login, shell, pop-up proxy; forwarding of host commands |
| Setup | `doctor.py` | setup state and next step (read-only) |
| | `wizard.py` | `labpipe setup` (asks in the terminal) |
| | `installer.py` | `labpipe setup --first`: the installer's questions (tools present, agent tool, models, login, first project, start) |
| | `roview.py` | commands for a read-only view (native mode) |
| | `methods_kit.py` | `methods new | check`: scaffold and check a method |
| Support | `safety.py` | code and project roots, install and config folders, dev mode, write checks, atomic writes, container path mapping |
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
  - `figures` in results;
  - `plot_modules` (drawing code in its own modules, not in the analyze module: a change marks "plot
    out of date" instead of stale) and `analysis_data` (data files the numbers depend on).
- **Docs**: `docs/methods/<m>.md` covers design, settings, interpretation, quantities and
  conventions. An agent role goes in `agents/<m>-analyst.md`, unless the method is code only.

## Versions: what marks an analysis out of date

`versions.py` hashes source (line endings normalised, so Windows and Linux agree). Both are 16 hex
characters, stored per entry in `manifest.json` and in `provenance.json` → `code`.

- **Analysis version** of a method: the modules its `module` in `methods.yaml` imports
  (`labpipe.*`, followed through the AST, also imports inside functions), plus the files in
  `analysis_data:` (globs in the code folder; `lab/…` ones in the project. XRD:
  `references/xrd/patterns/*.json` and `lab/references/xrd/patterns/*.json`: a rebuilt or added phase can change
  which phase matches, so XRD analyses become out of date; the reference hashes are in
  provenance too). Left out:
  - the plot modules (`plot_modules:` in `methods.yaml`) and `labpipe.plotting`;
  - infrastructure that computes no numbers: `safety`, `provenance`, `settings` (the resolved
    settings are fingerprinted instead), `manifest`, `locking`, `registry`
    (`versions.INFRASTRUCTURE`).

  Everything else stays in, for example `naming` (XRD uses it for a check in results.json).
  eds and sem draw inside their analyze module, so their drawing code is part of their analysis
  version: a change to it marks their analyses out of date.
- **Plot version**: the method's `plot_modules` plus `labpipe.plotting`. A change marks done
  entries "plot out of date" (`labpipe replot`), never stale.
- **Replay** needs the same analysis and plot version as the record, not the same commit (a
  different commit is noted). Records from before per-method versions need the recorded
  commit, clean.
- **Scanner**: a done entry whose analysis version differs is stale ("the <method> calculation
  changed (version a → b)"). A failed entry is retried when it changes. An entry from before
  versions existed adopts the current ones if its `code_version` is the current clean commit,
  otherwise it is stale ("analysed before per-method versions; redo to be sure").
- **Replay** still compares the git commit; the versions show whether a commit changed anything.

## Agents and the harness

- **Roles**: `agents/*.md` hold the roles, harness-neutral: supervisor, `<m>`-analyst, postdoc.
  The lab's `lab/agents/<role>.md` is appended to the role; `lab/agents/<new>.md` with full
  frontmatter is an extra role. An overlay that names no role is reported (`problems`), never dropped.
- **Generated files**: `labpipe agents sync` writes the harness files, with the models from
  `config/local.yaml` → `agents`; in a project it runs the whole `project sync`.
- **Unattended runs** use the project-only settings (`.claude/unattended*.json`; Codex:
  `-c` flags). `guard.py` is the hook in both tools.
- **Postdoc mode**: `LABPIPE_AGENT_MODE=postdoc` narrows what the postdoc may run and write.

## Container mode

One container per project.

- **Image**:
  - Python with the locked dependencies, the agent tools at pinned versions, and the labpipe
    **code at `/opt/labpipe`** (`PYTHONPATH=/opt/labpipe/src`, `LABPIPE_CODE`);
  - `container.py` stages the build context: exactly the files the image hash covers (the code
    listed under "Read from the code", the default settings but never the lab's files, and the
    commit of the code as `.labpipe-code.json`, because the image has no `.git`);
  - the image tag is the hash of those files.
- **Run**:
  - the project is mounted read-write at `/project` (the working folder, `LABPIPE_PROJECT`), the
    data folders read-only at `/data/<id>`;
  - dev mode (the repository is its own project) and `labpipe container start --dev` also mount
    the code folder at `/opt/labpipe`, so code edits need no rebuild;
  - the container is named `labpipe-<project name>` with the volume `<name>-home` (logins), and
    its port is the first free one from 8765, saved in the project's `config/local.yaml`.
- **Network**: an iptables firewall allows only the local tinyproxy, and the proxy allows only
  the listed hosts. The entrypoint drops privileges.
- **Host side**: `labpipe` on the host, anywhere inside a project, forwards commands into that
  project's container (`exec`, the working folder mapped under `/project`). Local-only
  commands: container, shell, setup, init, doctor, agents, project, guard, methods, readonly-view,
  and method subcommands listed in `methods.yaml` → `run_on_host` (`xrd refs`: building
  references needs the optional pymatgen, which the image does not have).

## Where things are written

| Path | Written by |
|---|---|
| `labpipe.yaml`, `lab/`, `config/` lab files | `project new` creates them; then the user, or an agent with the user's OK |
| `AGENTS.md`, `CLAUDE.md`, `.labpipe/`, `.claude/*.json`, `.codex/`, `.agents/` | `project sync` (generated; agents never write them) |
| `manifest.json` | scanner, pipeline (locked) |
| `analysis/<sample>/<method>/<m>/` | pipeline (code); `notes.md` by the analyst agent |
| `analysis/_postdoc/<run>/` | postdoc (code), `analysis.md` by the postdoc agent |
| `analysis/report*.html` | report |
| `logs/` | automation, review, supervisor run summaries, notifications |
| `config/` lab files | the user, or an agent with the user's OK |
| `context/` | the user; `context.md` / `instructions.md` by an agent with the user's OK; `.text/` by `context index` |
| harness agent files | `agents sync` / `project sync` |

## Tests

- `tests/` with pytest, as `PYTHONPATH=src python -m pytest -q`.
- CI runs on Linux and Windows.
- Numerics are tested against hand-checked values, and outputs must be byte-identical where
  promised (default figures, replay).
- Container tests are pure: they test the command lines, the staged files and the image hash,
  not a running engine.
- `tests/test_project.py`: finding the project, `project new` and its refusals, the two roots in
  settings, the generated files and overlays, dev mode, the guard, and a scan, analysis, report
  and replay in a project made by `project new`.
- `tests/test_encoding.py`: every text read and write names UTF-8 (Windows would otherwise use
  another code page).
- `tests/test_docs.py` checks the docs against the code where it can be done mechanically:
  every command documented, this module map complete, links resolve, settings keys and method
  plot keys on their pages, every agent role in `AGENTS.md`.
