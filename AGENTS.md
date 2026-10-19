# AGENTS.md — labpipe

Instructions for any AI agent working in this repository, whatever the harness (Claude Code reads them through `CLAUDE.md`, which adds the Claude-specific safety setup).

labpipe turns raw instrument files into reproducible analyses:
- `results.json`, a plot (`plot.png` + `plot.pdf`) and `provenance.json` per measurement;
- a human-readable `notes.md` written by an agent.

The numerics are deterministic Python. **Agents run commands, read results, write notes and edit settings files. They never compute numbers themselves.** Human-facing overview: `README.md`. Read `docs/SAFETY.md` before changing anything that touches paths or permissions.

## Start of every session: `labpipe doctor`

Run `labpipe doctor` first (it only reads). If `ready` is true, skip to what the user asks. Otherwise you are onboarding: **lead the user through setup, one item at a time**.

- Take `next` from the doctor output, explain in two or three plain sentences what it is and why it matters, do it together with the user, rerun `labpipe doctor`, repeat.
- If `labpipe` itself is not found, the user has not installed it yet: have them run `./install.sh` (Linux/macOS) or `.\install.ps1` (Windows) from the repository folder, and explain what it does (README → Install). It creates the virtual environment outside synced folders and never needs admin rights.
- **`labpipe setup`** is the guided alternative for the raw-data folder, the sandbox check, models per role and automation (the installers offer it). It asks questions, so the user runs it in their own terminal: suggest it, you cannot run it. Single steps: `labpipe setup --only raw-data|files|sandbox|agents|automation`. If the user prefers to do it with you, follow the items below.
- **The user runs anything that needs admin rights** (mounts, shares, `sudo`, `New-SmbShare`). Get the exact commands with `labpipe readonly-view --source <their raw folder>` (it prints them for their OS, paths filled in), show them step by step, and explain each. Never run it yourself.
- **Raw data location** (`config/local.yaml`): ask where the instrument files are and how they get there (copied by hand, exported from the instrument PC, a network drive, a sync client; none is required). Explain the two folders in plain words: their **data folder**, where they put files, and the **read-only view** labpipe reads from (README → Your data folder). The two ways to get a read-only folder are in `docs/SAFETY.md`: a read-only view (recommended) or making the folder itself read-only. Record the data folder as `source:` of the raw root and list it under `protected_paths`. Editing `local.yaml` always asks the user for approval; that is intended.
- **Recognising the user's files**: see the section of that name below. Do it right after the data folder is set.
- **Sample IDs**: doctor shows file names that give no sample ID. Show them, ask the user how their files are named, then adapt `config/pipeline.yaml` → `sample_id.pattern` (or add overrides in `config/sample_ids.yaml`). Rerun doctor until the examples look right to the user.
- **References**: for XRD, ask which phases they expect and whether references are missing (see "Adding a reference phase").
- **Instrument specifics**: ask about internal standards (certificate values), sample holders that give peaks, supports or substrates. Put the answers into settings, never code.
- **First analysis**: `labpipe scan`, pick one file with the user, `labpipe <method> analyze --entry ID`, show `plot.png`, walk through `results.json` and its checks, write the notes (delegate to the method's analyst agent). Then explain steering (below) with their own plot.
- **Agents and models**: ask which agent harness the user works with and which model each role should use (`supervisor`, `analyst`). Explain the trade-off: the supervisor only runs commands and keeps the books, so a small, fast model is enough; the analyst looks at plots and writes for people, so a stronger model writes better notes. `labpipe notes verify` rejects invented numbers whatever the model. Write the answer into `config/local.yaml` → `agents`, then run `labpipe agents sync`.
- **Automatic processing** (optional, last): explain what `labpipe automation` does (a watcher that checks for new files, waits until they stop changing, then runs the pipeline in batches of at most 10 measurements per AI run; a nightly safety-net run; notifications when work starts and ends and about new unrecognised files, files needing a decision, failed and out-of-date analyses; the AI starts only when there is work). Doctor's `automation` item reports whether the last run succeeded; if it warns, show the user the reason and the log. The user installs it by running `labpipe automation setup` in their own terminal: it asks for the timings and shows the service files before installing. Agents cannot run it.

Keep a short record of the setup decisions (paths chosen, naming rule, standards) in the `note:` fields of the settings files you change, so the next person knows why.

## Rules (non-negotiable)

1. **Raw data is read-only.** Never write, move, rename or delete anything under a raw root or a `protected_paths` entry (`config/local.yaml`). Read raw files only through `labpipe` commands.
2. **No numbers from the agent.**
   - Every number in `notes.md` or in a reply must be copied from a `results.json` (cite its key path), or come from a `labpipe` command's output.
   - No mental arithmetic, no unit conversions done by hand.
   - If a number you need isn't in `results.json`, say so. Don't compute it.
3. **No invented interpretations.**
   - Phase names come only from `phases.fcc_indexed[*].closest_references`, `phases.reference_candidates` or `internal_standard`, always worded as candidates ("consistent with", "closest reference").
   - A pure-element match on an alloy is not an identification. The `reference_match_isostructural` check says so.
   - Don't name a crystal phase that isn't in the results.
4. **Uncertainty is explicit.** Report σ where `results.json` has it (`a_sigma_angstrom`, `two_theta_sigma_deg`, …). Every `review`-severity check is mentioned in the notes ("Check before using").
5. **Code is not changed during analysis work.** To change how a sample is analysed, edit settings (below). Code changes are separate tasks, with tests and a commit.
6. **Nothing is deleted.** Reruns move old outputs to `_superseded/`; failures go to `_failed/`.

## Commands

All commands print JSON.

| Command | Purpose |
|---|---|
| `labpipe scan` | Update `manifest.json` from the raw roots (idempotent) |
| `labpipe pending` | Entries waiting for analysis, with their subagent |
| `labpipe status [ENTRY]` | Manifest summary, or one entry |
| `labpipe <method> analyze --entry ID [--rerun]` | Analyse one entry (`--rerun` after steering) |
| `labpipe <method> check --entry ID` | The deterministic checks of an analysed entry |
| `labpipe analyze --pending` | Analyse every pending entry |
| `labpipe requeue --stale \| --entry ID` | Put entries back to pending |
| `labpipe review` | Write `logs/review_queue.md` |
| `labpipe replay PROVENANCE.json` | Regenerate outputs and compare hashes |
| `labpipe xrd refs list \| add \| build` | XRD reference phases |
| `labpipe inventory` | What labpipe recognises in the data folder(s): files per method, unrecognised types with examples, names without a sample ID |
| `labpipe scan --baseline` / `labpipe requeue --baseline` | Record existing files as `baseline` (not analysed) / queue them later; only when the user asks |
| `labpipe methods new NAME --example F --description D` / `labpipe methods check NAME --example F` | Scaffold a new method / verify its contract (only in a method-design task with the user) |
| `labpipe init` | Create `config/local.yaml` on a new machine |
| `labpipe setup [--only STEP]` | Guided setup; interactive, for the user's terminal only |
| `labpipe readonly-view --source PATH` | Print this OS's commands for a read-only view of the raw data (runs nothing) |
| `labpipe automation run \| status` | One automatic pass (scan; AI only if there is work) / state of the watcher and nightly run. `setup` and `watch` are for the user's terminal and services |
| `labpipe agents sync` | Generate the harness's agent files from `agents/` and the models in `config/local.yaml` |
| `labpipe doctor` | Setup state and the next setup step (read-only) |
| `labpipe <method> summary --entry ID` | Compact, formatted key numbers and plain-language checks; read this before results.json |
| `labpipe report` | Write `analysis/report.html`, the overview page for people (run after analyses or notes change) |
| `labpipe notes todo` | Analysed entries whose `notes.md` is missing or outdated |
| `labpipe notes verify --entry ID` | Check `notes.md`: every number is in `results.json`/settings, every `review` code is mentioned, the results sha256 matches |
| `labpipe notes retire --entry ID` | Move `notes.md` to `_superseded/` so the analyst rewrites it (only when the user asks) |

## Agents

Roles are defined once, harness-neutral, in `agents/`. `labpipe agents sync` turns them into the files the chosen harness reads (Claude Code: `.claude/agents/`, Codex: `.codex/agents/*.toml`, OpenCode: `.opencode/agents/`; all generated and git-ignored), with the models from `config/local.yaml` → `agents`. Edit `agents/*.md`, never the generated files.

| Agent | Role | Writes |
|---|---|---|
| main session (you) | talks with the user: onboarding, steering, questions | settings files (with the user), notes |
| `supervisor` (`agents/supervisor.md`) | one pipeline pass: doctor → scan → delegate pending entries → notes todo → review → report → run summary | `logs/runs/*-summary.md` |
| `xrd-analyst` (`agents/xrd-analyst.md`) | analyse one XRD entry, check the plot, write and verify short `notes.md` | `analysis/**/notes.md` |

- Interactive: delegate to `supervisor` for "process the new data", to the method's analyst for notes after a rerun.
- Unattended runs and the extra safety layers (permissions, guard hook, sandbox) depend on the harness. Claude Code: see `CLAUDE.md`. Codex: `.codex/` (config, rules, hooks; `docs/SAFETY.md` → Codex). OpenCode: only the read-only view and labpipe's own write checks apply, so do not run it unattended.

The venv lives outside the repo:
- Linux/macOS: `~/.local/share/labpipe/venv/bin/labpipe`
- Windows: `%LOCALAPPDATA%\labpipe\venv\Scripts\labpipe.exe`

## Layout

```
analysis/<sample_id>/<method>/<measurement_id>/   results.json, plot.png, plot.pdf, provenance.json, notes.md
analysis/<sample_id>/<method>/index.json          every measurement of that sample and method
analysis/<sample_id>/<method>/_superseded|_failed/ older or failed outputs (never deleted)
manifest.json                                      one entry per raw file: hash, status, output, code + settings version
logs/                                              run summaries, review_queue.md, errors/, replay/
config/                                            settings (see "Steering"); local.yaml = this machine's paths
methods.yaml                                       file patterns → method → subagent → CLI
references/xrd/                                    phases.yaml (curated), cif/, patterns/ (computed)
src/labpipe/                                       code; methods/<name>/ per method; methods/template/ to copy
```

- **Sample ID**: the file stem up to the first `_`, if it matches `config/pipeline.yaml` → `sample_id.pattern`. Otherwise the entry is `needs_review`. Ask the user, and record the answer in `config/sample_ids.yaml` (keyed by relative path).
- **Measurement ID**: the file stem made filesystem-safe. For example `S-17_3.2.26_long scan + Si` → `S-17_3.2.26_long_scan_Si`.

## Manifest status

- `pending`: waiting.
- `done`: analysed. Its `review` flag says whether a human should look.
- `failed`: the reason is recorded. Retried only when the file or the code changes, at most 3 times.
- `needs_review`: needs a human decision (e.g. sample ID).
- `duplicate`: the same measurement in a secondary format.
- `ignored`: matches an ignore pattern.
- `stale`: the code or settings changed since the analysis. Redone only on request (`requeue --stale`).
- `missing`: the file has gone from the raw root. Its outputs are kept.
- `baseline`: already in the data folder when labpipe was set up, and the user chose "only new files". Not analysed until its content changes or the user asks (`requeue --baseline` or `--entry ID`).

## Recognising the user's files

Do this after the data folder is set, and whenever doctor reports unrecognised files. **Show, ask, then act; never guess.**

1. Run `labpipe inventory`. Show the user, in plain words: which files each method recognised (counts, two examples), which file types no method reads (with examples), and which names give no sample ID.
2. Ask: *"Is this right?"* Then go through what is off, one thing at a time:
   - **recognised but wrong** (e.g. a `.txt` that is not what the method thinks): ask what it is; narrow that method's `patterns` in `methods.yaml` or add an `ignore` pattern, with the user's OK;
   - **not recognised**: ask what these files are. Not data (reports, photos, slides): with the user's OK, add them to `config/ignore.yaml` (`ignore:` globs relative to the data folder, e.g. `"TEM/**"`, with a `#` comment saying why) so they are never reported again. Exports of a format a method already reads: propose a pattern. A technique labpipe cannot analyse yet: offer to design a new method with the user ("Adding a new analysis method");
   - **no sample ID**: as in "Sample IDs" above.
3. Rerun `labpipe inventory` and show the difference. Repeat until the user says it is right.
4. If the manifest is still empty for this folder, ask whether to analyse everything already there, or only files added from now on (`labpipe scan --baseline` records the existing ones without analysing them). This is the user's decision; explain that analysis is free but AI notes cost money.

### When the user comes from a notification

Automatic processing notifies the user about anything new that needs them, ending with what to say to you:

| The user says | Do |
|---|---|
| "check the new files" | Read `logs/review_queue.md` → "Not recognised" (run `labpipe review` first), then the steps above for those files. |
| "check the review queue" | `labpipe review`; for each "needs a human decision" entry, show the reason and ask (sample IDs: "Sample IDs" above). |
| "check the last automatic run" | Read the last `run_finished` / `run_failed` event in `logs/automation.log` and the end of its `transcript`; explain in plain words what stopped it and what would fix it (a setting, the harness setup, `labpipe doctor`). Nothing to rerun yourself: the next automatic run retries. |
| "check the failed analyses" | `labpipe review`; for each failed entry show `labpipe status --entry ID` (reason, history), say what went wrong in plain words, and propose a fix (a setting, a sample ID, or a bug report for the code). Rerun with `labpipe <method> analyze --entry ID` only with the user's OK. |

Out-of-date (stale) analyses need nothing from you unless the user asks: `labpipe requeue --stale` puts them back in the queue.

## Steering an analysis (how the user changes an analysis)

The user describes a problem. You translate it into settings, rerun, and show the result.

1. Find the entry and its `measurement_id` with `labpipe status`. The current settings are in that measurement's `provenance.json` → `parameters.resolved`, and the files they came from are in `parameters.sources`.
2. **Surface, then wait.** Agents report problems; they change an analysis only after the user explicitly asks for that change. The default scope is the single measurement (case by case). Use a sample, preset or global scope only when the user says so.
   Choose the scope:
   - one measurement → `config/measurements/<method>/<measurement_id>.yaml`;
   - one sample → `config/samples.yaml` → `samples.<id>.<method>`;
   - a group (e.g. all samples on one support) → a preset in `config/samples.yaml`.
3. Write only keys that exist in `config/<method>.yaml`; unknown keys are rejected. Start the file with a `note:` giving the date and the user's request in their words.
4. Rerun:
   ```
   labpipe <method> analyze --entry ID --rerun
   ```
   Then show the new plot. Quote the changed numbers from the new `results.json`, side by side with the old ones from `_superseded/`.
5. If several settings are plausible, show the alternatives and **let the user choose**. Don't keep iterating on your own.
6. Settings files are part of the record (their hashes are in every `provenance.json`). They are the lab's own and git-ignored (`config/samples.yaml`, `config/sample_ids.yaml`, `config/measurements/`); remind the user to back them up or keep them in a private repo.

XRD steering keys (defaults in `config/xrd.yaml`):

| User says | Setting |
|---|---|
| "background too low/high around X" | `background.anchor_ranges_deg: [[lo, hi], …]` (peak-free ranges); lower `background.order` if it bends at the scan ends |
| "there's a peak at X you missed" / "that's not a peak" | `peaks.add_deg: [X]` / `peaks.remove_deg: [X]` |
| "ignore X–Y (holder, artefact)" | `exclude_ranges_deg: [[X, Y]]` |
| "the support bands are at …" / "no broad bands" | `broad_components_deg: [..]` / `[]` (`null` = automatic) |
| "only compare with Pt, Pd" | `phases: [Pt, Pd]` |
| "no internal standard here" | `internal_standard.phase: null` |
| "our Si standard has a = …" | `internal_standard.lattice_a_angstrom: …` (from the certificate the user gives you) |

SEM keys (`config/sem.yaml`; no analyst, analysed by code):

| User says | Setting |
|---|---|
| "show 5k and 200k images in the report" | `gallery.magnifications: [5000, 200000]` (per sample in `config/samples.yaml`) |
| "always show / never show that image" | `gallery.include: [<measurement_id>]` / `gallery.exclude: [...]` |

EDS keys (`config/eds.yaml`):

| User says | Setting |
|---|---|
| "S-9 should be equimolar Ru, Rh, Pd, Ir, Pt" | `samples.S-9.eds.nominal_at_percent: {Ru: 20, Rh: 20, Pd: 20, Ir: 20, Pt: 20}` (copy the user's numbers; never compute them) |
| "count Si as a metal" / "leave out Cu (grid)" | `non_metals: [...]` |
| "the images of S-14 are s-140.jpg and s-141.jpg" (report links the wrong ones) | `config/measurements/eds/S-14.yaml`: `images: [s-140.jpg, s-141.jpg]` plus a `note:` |

## Adding a reference phase (XRD)

```
labpipe xrd refs add path/to/file.cif --id NAME --structure-type fcc|rocksalt|hcp|bcc|diamond|rutile|other [--cod-id N] --note "why"
```

- Check the CIF first: cell edges in Å, not kX; ambient conditions; not a theoretical or hydrogen-loaded structure. `references/xrd/phases.yaml` → `excluded` lists examples of what to reject.
- COD is open data. ICSD/ICDD CIFs are licensed and must not be committed.
- `fcc` and `rocksalt` references also take part in the lattice-parameter comparison.

## Adding a new analysis method (designed with the user)

A new method is a code change: a separate task with the user, tests and a commit. Never during analysis work, never unattended. Details: `docs/ADDING_A_METHOD.md`.

1. **Design conversation.** Ask, one topic at a time, and look at real example files with the user:
   - technique, instrument, software; which files belong together (e.g. image + metadata `.txt`); what is in them, and what only the user knows (areas, loadings, certificates);
   - which numbers they want, with units and uncertainty; which plot; what goes into the report;
   - sensible defaults and why; what should be flagged for review;
   - existing code to reuse (record source and commit; report its bugs to the user instead of silently fixing them);
   - whether the analyst agent writes notes for every measurement, or only on request (cost).
2. **Design note.** `labpipe methods new <name> --example <file> --description "..."` scaffolds everything (module, settings, `methods.yaml` entry, analyst role, test stub) and creates `docs/methods/<name>.md`. Fill in the design note from the conversation and **stop: the user approves it before any analysis code is written.**
3. **Implement** the reading and numerics in `src/labpipe/methods/<name>/analyze.py`, every tunable number in `config/<name>.yaml`, a synthetic known-answer test in `tests/test_<name>.py`. If the file type is shared with another method (`.txt`, `.jpg`), add a content check (`recognise:` in `methods.yaml`). Adapt `agents/<name>-analyst.md` (what to look at in the plot, what the notes say).
4. **Check** `labpipe methods check <name> --example <file>` until every item passes (outputs, results shape, provenance hashes, deterministic and regenerable, raw file untouched). Then `pytest`.
5. **First real result with the user**: `labpipe agents sync`, `labpipe scan`, analyse one real file, show the plot and results, adjust defaults with them. Then commit.

## Conventions

- **Units**:
  - 2θ in degrees;
  - d and lattice parameters in Å;
  - intensity in counts (as recorded);
  - peak areas in counts·degree;
  - crystallite size in nm;
  - strain dimensionless (also given in %).
  - `results.json` → `units` is authoritative.
- **Plots**:
  - ACS journal style, 3.25 in single column, 300 dpi, Arial or a metric-compatible fallback;
  - **full measured range**;
  - axis labels `2θ (°)` and `Intensity (counts)`, or `Intensity (10ⁿ counts)` when scaled;
  - fit in red, background dashed grey, background + broad components dotted blue, one tick row per phase, residual panel.
- **XRD defaults**:
  - Kα1/Kα2 modelled as doublets using the ratio from the file;
  - pseudo-Voigt crystalline peaks (FWHM ≤ 4°), Gaussian broad components (5–10°);
  - Chebyshev background;
  - Scherrer K = 0.9 on the Kα1 FWHM;
  - sizes are lower bounds until an instrument calibration exists (`sizing.calibration_file`).
- **Checks**:
  - `review` means a human must look before the result is used;
  - `warning` means mention it in the notes;
  - `info` is context.

## Platforms

- Linux is the reference platform.
- On Windows the code runs natively (cross-platform paths and locking, CI on Windows).
- Use forward slashes in YAML paths on every platform.
- Never put the venv, `analysis/` or `manifest.json` in a synced folder.
