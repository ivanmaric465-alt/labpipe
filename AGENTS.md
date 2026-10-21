# AGENTS.md — labpipe

Instructions for any AI agent working in this repository, whatever the harness (Claude Code reads them through `CLAUDE.md`, which adds the Claude-specific safety setup).

labpipe turns raw instrument files into reproducible analyses:
- `results.json`, a plot (`plot.png` + `plot.pdf`) and `provenance.json` per measurement;
- a human-readable `notes.md` written by an agent.

The numerics are deterministic Python. **Agents run commands, read results, write notes and edit settings files. They never compute numbers themselves.** Human-facing overview: `README.md`. The code's skeleton (modules, data flow, what is written where): `docs/ARCHITECTURE.md`. Read `docs/SAFETY.md` before changing anything that touches paths or permissions.

Everything specific to one method (its settings, how to word its results, its conventions) is in `docs/methods/<method>.md` (the lab's own methods: `lab/methods/<method>/README.md`, listed in the generated section "This lab's own methods"). Read it before steering or explaining that method's results. To explain a figure, use its "Reading the plot" section. Everything else the user can change (data, samples, plots, report, agents, automation, context, container) has a page in `docs/settings/`, with a "You say → setting" table: find the page there before changing anything.

## Start of every session: `labpipe doctor`

Run `labpipe doctor` first (it only reads). If `ready` is true, go to what the user asks. Otherwise lead the user through setup, one item at a time, as described in `docs/SETUP.md`.

- **`onboarding`** (a warning, never blocking): when `ready` is true and doctor shows it, or when the user says "start", "tour" or "show me around", follow `docs/ONBOARDING.md`: one step at a time, short, with their own data, skipping what they do not want. `labpipe onboarding done <id>` after each step (it resumes where it stopped); "skip the onboarding" is `labpipe onboarding skip all`; "tour" is `labpipe onboarding restart --tour`.

- If `labpipe` itself is not found, the user has not installed it yet: show them the one-line install (README → Install); they run it. It asks its questions itself and creates the first project.
- **`settings_migrations`** (a warning): an update renamed or moved settings, and a file of the user still has the old names (they keep working for one release, with a warning). Show the user `labpipe migrate` (it changes nothing: files, the migrations, the diff, whether comments in the file are kept). **Apply it (`labpipe migrate --apply`, which makes a backup in `backups/migrate-<time>/`) only with the user's OK**, then tell them where the backup is. `docs/settings/migrations.md`.
- **`project_release`** (a warning): this project was set up or updated with another labpipe release than the installed one. Show the user `labpipe update --check` (what changed) and tell them to run `labpipe update` in their terminal (below); do not run it yourself.
- **Optional dependencies** (e.g. pymatgen for `labpipe xrd refs build`) are never installed by you. When a command reports one missing, show the user the exact command it prints (it names this installation's own Python and the pinned version), say what the package is for, and let them run it.
- **The user runs anything that needs admin rights or asks questions in a terminal** (`labpipe setup`, `labpipe automation setup`, mounts, shares, `sudo`). Show the exact commands and explain them; never run them yourself.

## Rules (non-negotiable)

1. **Raw data is read-only.** Never write, move, rename or delete anything under a raw root or a `protected_paths` entry (`config/local.yaml`). Read raw files only through `labpipe` commands. The same holds for the user's documents in `context/documents/`.
2. **No numbers from the agent.**
   - Every number in `notes.md` or in a reply must be copied from a `results.json` (cite its key path), or come from a `labpipe` command's output.
   - No mental arithmetic, no unit conversions done by hand.
   - If a number you need isn't in `results.json`, say so. Don't compute it.
3. **No invented interpretations.** Name only what the results contain (phases, elements, features), worded as the method's doc says (`docs/methods/<method>.md` → Interpretation): "consistent with", "closest reference", never "is". How to word claims in notes, analyses and replies (observation vs interpretation, no cause from a trend, zero vs missing vs below detection): `docs/WRITING.md`.
4. **Uncertainty is explicit.** Report σ where `results.json` has it. Every `review`-severity check is mentioned in the notes ("Check before using").
5. **Code is not changed during analysis work.** To change how a sample is analysed, edit settings (below). Code changes are separate tasks, with tests and a commit.
6. **Nothing is deleted.** Reruns move old outputs to `_superseded/`; failures go to `_failed/`.

## Commands

All commands print JSON.

| Command | Purpose |
|---|---|
| `labpipe --version` | Version of labpipe and the release tag (or commit) of the code folder |
| `labpipe doctor` | Setup state and the next setup step (read-only) |
| `labpipe onboarding [status] \| done ID \| skip ID\|all \| restart [--tour]` | The first conversation (`docs/ONBOARDING.md`): the steps and the next one / mark a step done or skipped / start again (writes only `logs/onboarding.json`) |
| `labpipe scan` | Update `manifest.json` from the raw roots (idempotent) |
| `labpipe pending` | Entries waiting for analysis, with their subagent |
| `labpipe status [ID]` | Manifest summary, or one entry |
| `labpipe <method> analyze --entry ID [--rerun]` | Analyse one entry (`--rerun` after steering) |
| `labpipe <method> summary --entry ID` | Key numbers and checks in plain words; read this before results.json |
| `labpipe <method> check --entry ID` | The deterministic checks of an analysed entry |
| `labpipe analyze --pending [--method M]` | Analyse every pending entry |
| `labpipe requeue --stale \| --baseline \| --entry ID` | Put entries back to pending |
| `labpipe review` | Write `logs/review_queue.md` (not recognised, needs a decision, failed, flagged, stale) |
| `labpipe report` | Write `analysis/report.html` (and the extra reports in `lab.yaml` → `report.extra`), the overview pages for people |
| `labpipe replot [--entry ID \| --method M \| --all]` | Redraw plots after a plot-settings change (no re-analysis, no AI) |
| `labpipe notes todo` / `verify --entry ID` / `retire --entry ID` | Notes missing or outdated / check a `notes.md` (a pass is recorded with the rules' version) / move it aside to be rewritten (only when the user asks) |
| `labpipe inventory` | What labpipe recognises in the data folder(s) |
| `labpipe dataset` / `dataset compare --quantity Q (--reference S \| --against P) [--run RUN]` | All samples with the named quantities of their measurements / differences and trends across samples, done by code |
| `labpipe context init \| index` | Create `context/` from the templates / extract the text of `context/documents/` |
| `labpipe postdoc start "Q" \| verify RUN \| list \| run "Q"` | A run for the postdoc / check its `analysis.md` / past runs / the whole thing without a conversation |
| `labpipe update [--check\|--preview\|--to vX.Y.Z]` | New release, preview or rollback: `--check` says what is available and what the changelog says (read-only); `--check --preview` also analyses copies of the measurements with the new code and says what would change in the numbers (changes nothing); `labpipe update` itself backs up, switches the code, migrates settings, regenerates, restarts and lists what is out of date: **the user runs it**, never you |
| `labpipe migrate` / `migrate --apply [--yes]` | Settings files that use names an update changed: show the change (read-only) / make it, with a backup (only with the user's OK, after showing the diff) |
| `labpipe replay PROVENANCE.json` | Regenerate outputs and compare hashes (needs the same analysis version as the record; a comparison figure needs the same compare version) |
| `labpipe compare [--method M]` | (Re)draw the comparison figures of the samples, `analysis/_compare/<method>/<quantity>/plot.png` (code only; also done by `labpipe report`). You may look at and cite them by path; never compute from them: numbers come from `labpipe dataset compare` |
| `labpipe project new FOLDER [--data PATH]... [--name NAME]` / `project sync` | Create a project folder (the user runs it) / regenerate its generated files (`AGENTS.md`, `CLAUDE.md`, `.labpipe/`, harness files) after `lab/LAB.md`, `lab/agents/` or a labpipe update changed |
| `labpipe agents sync` | Generate the harness's agent files from `agents/` and `lab/agents/` (in a project: the whole `project sync`) |
| `labpipe automation run \| status` | One automatic pass (by hand; in container mode the container's watcher does this) / settings and last events. `automation setup` (timings, container at login, removes old native units) is run by the user; `automation watch` is the container's own entry point |
| `labpipe methods new \| check` | New method (only in a method-design task with the user): in a project into `lab/` (the lab's own), in the repository into the code |
| `labpipe methods versions` | Per method: analysis version, plot version, the modules in each (read-only) |
| `labpipe container setup \| start [--replace] \| stop \| status \| logs \| login \| popups` | Container mode (`docs/settings/container.md`); the user runs `setup`, `login` and `popups` (they ask questions or install a service) |
| `labpipe shell` | An agent session inside the container (the user runs it) |
| `labpipe roots rename OLD NEW` | Re-key manifest entries after a raw root's `id` changed in `local.yaml` |
| `labpipe setup --first [--answers F] [--no-start]` | The installer's questions (tools, models, login, first project); the user runs it, or the installer does |
| `labpipe init`, `labpipe readonly-view --source PATH` | Create this machine's `config/local.yaml`; print the commands for a read-only view (runs nothing) |

Method-specific commands (e.g. `labpipe xrd refs`) are in the method's doc.

## Updates (the user runs them)

`labpipe update` changes the installed code and the project's settings, so **you never run it** (the harness asks if you try; unattended runs refuse it). When the user asks for an update or `doctor` says `project_release`:

1. Run `labpipe update --check` (it changes nothing) and read the user its four parts: changes results (per method), settings migrated, new, needs your action. If results change, say that `labpipe update --check --preview` shows what would change in their own numbers, by code (quote its lines; no numbers of your own).
2. Show the command, `labpipe update`, in the project folder, and what it does: asks first, backs up `labpipe.yaml`, `config/`, `manifest.json` and `lab/methods.yaml` to `backups/<release>/`, shows each settings diff and asks, regenerates the generated files, restarts the container, lists the out-of-date analyses. Back: `labpipe update --to vX.Y.Z`. The user runs it in their terminal. Details: `docs/settings/updates.md`.
3. **After it**: tell the user what the changelog said under *Needs your action*, which methods have out-of-date analyses and how many (the update printed it; they stay valid), and where a migrated file's original is (`backups/<release>/migrated/`). Redo the out-of-date analyses (`labpipe requeue --stale`, `labpipe analyze --pending`, then the notes) only when the user asks: it costs AI time, and the results change.
4. If it stopped, the error names `labpipe update --resume <file>`; the user runs that after fixing what it says.

## Agents

Roles are defined once, harness-neutral, in `agents/` (in a project they are read-only copies in `.labpipe/agents/`). `labpipe agents sync` turns them into the files the chosen harness reads (Claude Code `.claude/agents/`, Codex `.codex/agents/*.toml`, OpenCode `.opencode/agents/`; generated, git-ignored), with the models from `config/local.yaml` → `agents`. Never edit the generated files. The lab's own instructions go in `lab/LAB.md` (all agents) and `lab/agents/<role>.md` (appended to that role; a file with full frontmatter is an extra role); then `labpipe project sync`. Edit `agents/*.md` only in a code task.

| Agent | Role | Writes |
|---|---|---|
| main session (you) | talks with the user: setup, steering, questions | settings files (with the user), notes |
| `supervisor` | one pipeline pass: doctor → scan → delegate pending entries → notes → review → report | `logs/runs/*-summary.md` |
| `<method>-analyst` | analyse one entry, check the plot, write and verify short `notes.md` | `analysis/**/notes.md` |
| `postdoc` | only when the user asks: the whole project with its context, one question per run | `analysis/_postdoc/<run>/analysis.md` |

- Interactive: delegate to `supervisor` for "process the new data", to the method's analyst for notes after a rerun, to `postdoc` for "ask the postdoc …" (below).
- Extra safety layers (permissions, guard hook, sandbox) depend on the harness: Claude Code `CLAUDE.md`; Codex `.codex/` (`docs/SAFETY.md` → Codex). OpenCode has only the read-only view and labpipe's own write checks: do not run it unattended.

## Project context and the postdoc

The postdoc answers the user's questions about the whole project, with its context. It runs only when the user asks; automatic runs never start it. Design: `docs/design/postdoc.md`.

- **Context** (`context/`, the user's, never committed):
  - `context.md`: the project in plain words;
  - `instructions.md`: how the postdoc uses the context;
  - `documents/`: work plan, papers; read-only.

  If it does not exist, offer `labpipe context init`. After documents are added, run `labpipe context index`.
- **"Let's write the project context"**:
  1. Ask about the aims, hypotheses, the samples and what they should show.
  2. Read the documents (`context/.text/`).
  3. Draft `context.md` and show it.
  4. Write it only with the user's approval. Same for `instructions.md`.
- **"Ask the postdoc: <question>"**:
  1. `labpipe postdoc start "<question>"`.
  2. Delegate to `postdoc` with `Write the analysis for run <run>.`
  3. Check `labpipe postdoc verify <run>` yourself, then run `labpipe report`.
  4. Give the user the summary and say where the analysis is.

  It costs more than notes: say so if the user asks for several at once. Without a conversation, the user runs `labpipe postdoc run "<question>"`.
- A postdoc analysis, like a note, that the report marks "checked with the rules of labpipe X" passed the checks of an older release and is valid: never rewrite it for that reason.
- Never edit `analysis.md` of a run yourself. To change an answer, the user asks again: a new run.

## Layout

A project is a folder with `labpipe.yaml`; labpipe finds it from the folder you are in. The code (defaults, `docs/`, agent roles, `methods.yaml`) is elsewhere: in a project, what you may read of it is copied to `.labpipe/` (`.labpipe/docs/`, `.labpipe/config/<method>.yaml`, `.labpipe/methods.yaml`), and `AGENTS.md`, `CLAUDE.md`, `.labpipe/` and the harness files are **generated: never edit them**, add to `lab/LAB.md` or `lab/agents/` and run `labpipe project sync`. In the labpipe repository itself (dev mode) the sources are the files themselves.

```
analysis/<sample_id>/<method>/<measurement_id>/   results.json, plot.png, plot.pdf, provenance.json, notes.md
analysis/<sample_id>/<method>/_superseded|_failed/ older or failed outputs (never deleted)
analysis/_compare/<method>/<quantity>/              comparison figure of the samples: plot.png, plot.pdf, provenance.json (the plotted data)
analysis/_postdoc/<run>/                           the postdoc's runs: request.md, inputs.json, dataset.json, compare-N.json, analysis.md, verify.json
context/                                           the user's project context and documents (git-ignored)
lab/                                               the lab's own additions: LAB.md (all agents), agents/<role>.md, methods.yaml + methods/<name>/ (its own methods)
manifest.json                                      one entry per raw file: hash, status, output, versions; unrecognised files
logs/                                              automation.log, runs/, review_queue.md
config/                                            settings (docs/settings/): local.yaml = this machine; lab.yaml = the lab's preferences; ignore.yaml = files to leave alone
methods.yaml                                       file patterns → method → analyst → CLI (from the code)
docs/methods/<method>.md                           per method: design, settings, interpretation, conventions
```

- **Sample ID**: the file stem up to the first separator (`config/pipeline.yaml` → `sample_id`), if it matches the pattern there. Otherwise the entry is `needs_review`: ask the user and record the answer in `config/sample_ids.yaml` (keyed by relative path).
- **Measurement ID**: the file stem made filesystem-safe (`S-17_3.2.26_long scan + Si` → `S-17_3.2.26_long_scan_Si`).
- **Status**: `pending`; `done` (its `review` flag: a human should look); `failed` (reason recorded; retried when the file or the method's calculation changes, at most 3 times); `needs_review` (a human decision); `duplicate`; `ignored`; `stale` (the method's calculation or the analysis settings changed; the reason says which; redone only on `requeue --stale`); `missing` (file gone; outputs kept); `baseline` (already there at setup; not analysed unless asked).

## Recognising the user's files

Do this after the data folder is set, and whenever doctor or the review queue reports unrecognised files. **Show, ask, then act; never guess.**

1. Run `labpipe inventory`. Show the user, in plain words: which files each method recognised (counts, two examples), which file types no method reads (with examples), and which names give no sample ID.
2. Ask: *"Is this right?"* Then go through what is off, one thing at a time:
   - **recognised but wrong**: ask what it is; narrow that method's `patterns` in `methods.yaml` or add an `ignore` pattern, with the user's OK;
   - **not recognised**: ask what these files are. Not data (reports, photos, slides): with the user's OK, add them to `config/ignore.yaml` (`ignore:` globs relative to the data folder, e.g. `"TEM/**"`, with a `#` comment saying why). Exports of a format a method already reads: propose a pattern. A technique labpipe cannot analyse yet: offer to design a new method with the user (below);
   - **no sample ID**: show the names, ask how their files are named, adapt `sample_id.pattern` or add overrides.
3. Rerun `labpipe inventory` and show the difference. Repeat until the user says it is right.
4. If the manifest is still empty for this folder, ask whether to analyse everything already there, or only files added from now on (`labpipe scan --baseline`). The user decides; analysis is free, AI notes cost money.

### When the user comes from a notification

| The user says | Do |
|---|---|
| "check the new files" | `labpipe review`, read `logs/review_queue.md` → "Not recognised", then the steps above for those files. |
| "check the review queue" | `labpipe review`; for each "needs a human decision" entry, show the reason and ask. |
| "check the failed analyses" | `labpipe review`; for each failed entry show `labpipe status ID`, say what went wrong in plain words, propose a fix (a setting, a sample ID, or a bug report for the code). Rerun only with the user's OK. |
| "check the last automatic run" | Read the last `run_finished` / `run_failed` event in `logs/automation.log` and the end of its `transcript`; explain what stopped it and what would fix it. The next automatic run retries. |

Out-of-date (stale) analyses need nothing unless the user asks: `labpipe requeue --stale`.

## Steering an analysis (how the user changes an analysis)

The user describes a problem. You translate it into settings, rerun, and show the result. Each method's settings, with examples of what users say, are in `docs/methods/<method>.md` → Settings; plots, the report and the rest in `docs/settings/`. Each page says what a change costs (a report rebuild, a redraw, a re-analysis, new AI notes): tell the user before.

1. Find the entry and its `measurement_id` with `labpipe status`. The current settings are in that measurement's `provenance.json` → `parameters.resolved`, and the files they came from in `parameters.sources`.
2. **Surface, then wait.** Report problems; change an analysis only after the user explicitly asks for that change. The default scope is the single measurement:
   - one measurement → `config/measurements/<method>/<measurement_id>.yaml`;
   - one sample → `config/samples.yaml` → `samples.<id>.<method>`;
   - a group (e.g. all samples on one support) → a preset in `config/samples.yaml`;
   - the whole lab → `config/lab.yaml` → `<method>`, `plots`, `report`. Never edit labpipe's defaults (`config/<method>.yaml`, `plots.yaml`, `report.yaml`).
3. Write only keys that exist in `config/<method>.yaml`; unknown keys are rejected. Start the file with a `note:` giving the date and the user's request in their words.
4. Rerun with `labpipe <method> analyze --entry ID --rerun` (plot-only changes: `labpipe replot --entry ID`), show the new plot, and quote the changed numbers from the new `results.json` next to the old ones from `_superseded/`.
5. If several settings are plausible, show the alternatives and **let the user choose**. Don't keep iterating on your own.
6. Settings files are part of the record (hashed in every `provenance.json`) and the lab's own (git-ignored): remind the user to back them up.

## Changing the code

A separate task with the user, never during analysis work, never unattended.
- Tests for every change. They run as `PYTHONPATH=src python -m pytest -q`, and CI runs them on Linux and Windows.
- **Docs move with the code.** `docs/docmap.yaml` says which docs belong to which code, and which docs repeat each other. In the same change:
  - update the docs of every area you touched, and the docs linked to them;
  - update `docs/ARCHITECTURE.md` when a module, command, file or flow is added, removed or moved.
  - a new document goes into `docs/docmap.yaml` too: the area of the code it describes, or a linked group of the docs it repeats (design notes: `not_kept_in_sync`).
- Check before committing:
  - `python scripts/check_docs.py` must pass (CI runs it too). If an area's docs truly need no change, add the commit message line `Docs-checked: <area>`.
  - A renamed, moved or removed settings key needs a `Migration` in `migrations.MIGRATIONS` (`src/labpipe/migrations.py`) with a test, and a line under "Settings migrated" in `CHANGELOG.md`; CI refuses the change without it (`scripts/check_settings_migrations.py`).
  - Stricter rules in `notes.py` or `postdoc.py` need a `CHECKS_VERSION` bump in that module; `tests/test_checks_versions.py` fails when the rule code changed (then update its fingerprint).
  - `tests/test_docs.py` checks what can be checked mechanically: commands, the module map, every document in the docmap, links, settings keys, method pages.

## Adding a new analysis method

A code change: a separate task with the user, never during analysis work, never unattended. Design it with the user, write the design note, **stop until the user approves it**, then implement and check. The steps: `docs/ADDING_A_METHOD.md`.

- **In a project** (the usual case): a method of the lab's own goes in `lab/` (`lab/methods.yaml`, `lab/methods/<name>/`, its page `lab/methods/<name>/README.md`, its role `lab/agents/<name>-analyst.md`), made by `labpipe methods new`. Updates never touch it. Then `labpipe methods check`, `labpipe project sync`.
- **In the labpipe repository** (dev mode): a method for everyone, with tests and a commit.

## Conventions

- **Units**: `results.json` → `units` is authoritative; never convert by hand.
- **Plots**: ACS journal style, 3.25 in single column (7 in double), 300 dpi, Arial or a metric-compatible fallback; the full measured range. Method specifics: its doc.
- **Checks**: `review` = a human must look before the result is used; `warning` = mention it in the notes; `info` = context.
- **Notes and the checks' versions**: `labpipe notes verify` records the version of the rules a note passed. A note the report marks "checked with the rules of labpipe X" (or whose verify says `checked_with`) passed the rules of an older release and is valid: do not rewrite or retire it for that reason, and `labpipe notes todo` does not list it. Only text you write now must pass the current rules.
- **Platforms**: Linux is the reference; Windows runs natively (CI). Forward slashes in YAML paths. Never put the venv, `analysis/` or `manifest.json` in a synced folder. The installation: `~/.local/share/labpipe/` (`app/` the code, `venv/`; Windows `%LOCALAPPDATA%\labpipe\`), your defaults for new projects `~/.config/labpipe/defaults.yaml`. The host needs only Python 3.10+ and PyYAML; the analysis runs in the container (native analysis: `install.sh --full`, Python 3.14).
