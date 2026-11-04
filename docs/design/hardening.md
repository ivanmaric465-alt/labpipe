# Hardening after an external review (design)

Status: approved 2026-10-22 (user: "write the design note, start with A"). Groups A, B and C are
merged (B on 2026-11-03, C on 2026-11-04).

An external review (2026-10-22) checked the code and ran parts of it in isolation. Each
finding below was checked against the code before it went in here. Its main point stands: the
numbers are computed by code, but the plumbing around them has gaps:
- how a measurement maps to its folder;
- what the guard lets through;
- what invalidates an analysis;
- what "checked" means.

## A. Before relying on unattended runs

### A1. One output folder per measurement

**Today:** `analysis/<sample>/<method>/<measurement_id>/` with `measurement_id` taken from
the file name only. Two different files with the same name in different folders (or data
roots) share one folder. The second analysis moves the first one's outputs aside, and both
manifest entries point at the same place.

**Change:**
- An entry's output folder is chosen once, at its first analysis, and kept in the manifest
  (`output_dir`, already recorded).
- The name stays readable: `<measurement_id>`, or `<measurement_id>~<6 characters of the
  entry id>` when another entry already holds that name.
- **Publication refuses** a folder whose `provenance.json` names another entry. The
  analysis fails with the reason; nothing is moved.
- **Existing projects:** `labpipe scan` (and `doctor`) finds entries that share an
  `output_dir`. The entry whose provenance is in the folder keeps it. The others get their
  own folder name and go back to `pending`, with the reason "shared an output folder with
  <path>; analysed again into its own folder". Nothing is deleted.
- **Changelog:** "Needs your action": the affected measurements are listed and redone.
- **A moved or renamed file** (the same content at a new place, its old entry `missing`)
  takes its folder along instead of getting a `~` folder (added while building).
- **Found while building:** a moved file was marked a duplicate of its own old place, so it
  was never analysed again and dropped out of the report. Files that are gone are now marked
  `missing` before duplicates are looked for.

### A2. The guard allows only reads it can check

**Today:** unattended and postdoc runs may use `cat head tail sed ls wc grep rg nl`, and only
the options are checked. These pass:
- `sed -n 'w file'` (writes a file);
- `sed -n '1e cmd'` (runs a command);
- `rg --pre cmd` (runs a command);
- `cat $HOME/…` (reads outside the project).

**Change:** a strict form per command; anything else is refused with the allowed forms in
the message:

| Command | Allowed |
|---|---|
| `cat`, `nl`, `wc` | only files |
| `head`, `tail` | `-n N` / `-N`, then files |
| `sed` | only `-n 'N,Mp'` / `'Np'` / `'N,$p'`, then files |
| `ls` | `-l -a -h -1 -R -t -r`, then paths |
| `grep`, `rg` | a short list of options (`-n -i -l -c -F -w -v -r -A/-B/-C N --no-heading -g GLOB`; for `grep` also `--include`), one pattern, then paths |

Rules for every command:
- **Operands:** every path, existing or not, must resolve inside the project.
- **`$`** anywhere in the command is refused (shell expansion). A pattern that needs `$` is
  written without it, or the agent uses a `labpipe` command.
- **Tests:** the review's commands are refused; normal reads still work.

### A3. Data and project never overlap

**Today:** `project new` refuses a project inside its data folder, but not a data folder
inside the project. The container mounts the project read-write and the data read-only, so
the data stays writable through `/project`.

**Change:** one check, "no data folder inside the project, the code or the other way
round", on resolved real paths:
- in `project new`: refused;
- in `labpipe doctor`: an error;
- before the container starts (`container start`, setup): refused with the reason;
- in dev mode, also against the code mount.

### A4. The guard hook does not fail open in a configured project

**Today:** `hooks/guard.py` looks for labpipe only in the default installation. With another
`LABPIPE_HOME` it finds nothing and allows every tool call.

**Change:**
- `labpipe project sync` writes the Python of the installation to `.labpipe/python` (a path),
  and the launcher reads it first. Then `LABPIPE_PYTHON`, `LABPIPE_HOME`, the defaults.
- If none is found and the project has `config/local.yaml`, the call is **blocked**, with
  how to fix it (`labpipe project sync`).
- Without `config/local.yaml` (labpipe not set up yet), the call is allowed as now.

### A5. Review flags of code-only methods reach the user

**Today:** the SEM gallery shows neither the check code nor its message. SEM has no analyst,
so a `scale_bar_unreadable` flag is seen by no one. Code-only results flagged `review` are
not notified.

**Change:**
- **Report:** a gallery shows a "Check before using" list (image, code, message) under
  it. Each flagged image gets a mark on its caption. The overview row counts them as
  "N to check", as the other methods do.
- **Automation:** a code-only measurement that finishes with `review: true` is notified once
  ("N SEM image(s) to check"). It is listed in `logs/review_queue.md`.
- The same applies to every code-only method, including the lab's own.

### A6. Edited lab-method code is never run stale

**Today:** the watcher (one long process) analyses code-only methods in that process. It
imports `labpipe_lab.*` once and restarts only when labpipe's own code changes. After an edit
of `lab/methods/…` it runs the old code but records the version of the new source.

**Change:**
- The watcher's code-only analyses run **one measurement per fresh process** (`labpipe
  <method> analyze --entry ID`, as the AI's analyses already do). The version recorded is
  always that of the code that ran.
- The watcher also restarts when `lab/methods/` or `lab/methods.yaml` changes (cheap: their
  hashes). That keeps the recogniser caches current too.

## B. Next

- **B1. Every input of a measurement is tracked.** A method names its companion files (the
  SEM `.txt`, the images an EDS report links). Their hashes go into the manifest and
  provenance. A companion that changes, appears or disappears makes the measurement out of
  date, as for your own analyses.

  **Built:** `companions: <module>:<function>` in methods.yaml (shipped and lab methods;
  `registry.input_companions` hashes them). SEM: `methods/sem/inputs.py` (the JEOL `.txt`);
  EDS: `methods/eds/inputs.py` (the images that exist, found with analyze's own
  `_linked_images` and `find_image`: those chosen with `images:` in the settings, else the
  linked ones; the function gets the measurement's resolved settings for that). The scanner records `companions` in the
  entry, the pipeline stores `analysed_companions` on `done`; a done or stale entry whose
  companions differ goes to pending ("companion <name> changed / appeared / is gone"), ahead of
  the stale and settings checks. An entry analysed before: its provenance `companions` (base
  names) are compared; equal → adopted silently; different → pending. When the provenance
  lists no companions (or cannot be read) there is nothing to compare, so the current ones are
  adopted. A companion that cannot be read during a scan leaves the entry as it was.
- **B2. Publication is one step.**
  - a lock per measurement, so the watcher and a manual run never do the same one;
  - the method's return value and every output are checked while still in staging;
  - just before publishing, the input hashes are compared again;
  - an interrupted publication is recovered on the next scan.

  **Built:** `pipeline.analyze_entry` and `replot_entry` hold `logs/locks/<entry_id>.lock`
  (`try_exclusive`) for the whole run; a second one returns `skipped`, "being analysed by another
  run", and changes nothing. `_check_staged` runs before anything moves: results.json parses, every
  output in the summary exists in staging with that sha256, provenance.json names the entry and
  lists the same outputs, the summary has `settings_sha256`, `status`, `checks`, `outputs["results.json"]`;
  a failure fails the analysis (staging to `_failed/`, no traceback). `_changed_input` hashes the main
  file and the companions again (same resolved settings) just before publishing: a difference ends
  as `pending` ("the input changed while it was analysed (<file>); analysed again on the next scan"),
  the attempt is not counted, staging goes to `_failed/`. A journal `logs/publishing/<entry_id>.json`
  (staging, final, aside, stamp) is written before the first rename and removed after the manifest
  update. `recover_publications(config)` (start of `scanner.scan`, before the manifest lock, listed
  in `changes`; and for its own entry at the start of an analysis or replot) rolls back each journal
  whose entry is not locked. Decided while building: "final holds the new outputs" is read from where
  things are, not from hashes: the staging folder is gone only after the second rename, so then final
  is new; otherwise final is the old outputs (crash before the first rename) or missing (the old ones
  are in the aside folder). The new outputs, or what staging still holds, go to
  `_failed/<folder>/<stamp>-interrupted/`, the aside folder moves back, the entry is `pending`
  ("publication was interrupted; analysed again"). A hash comparison would be wrong when a rerun
  gives byte-identical results. The manifest update records the stamp (`published_stamp`): a journal
  whose stamp it records was complete (the crash came just after) and is only removed. Replot has
  no journal (its figure moves are not covered).
- **B3. "Notes checked" says what it is.** "Numbers checked against the results", not
  validation:
  - `docs/SETUP.md` is corrected;
  - inline code is no longer exempt;
  - the report keeps the automatic check apart from a human's review.
  
  Structured claims (result keys, units, σ) are a later option.

  **Built:** every description of the two verify commands (SETUP, ARCHITECTURE, AGENTS, CLI help,
  setup wizard, analyst and postdoc texts, docstrings) now says "numbers checked against the
  results", not that they are used in the right place; a person reads the notes. `notes.CHECKS_VERSION`
  and `postdoc.CHECKS_VERSION` are 2 (the postdoc shares the number rules): numbers in `inline code`
  are checked; identifiers (S-9, `xrd-1a2b3c`, `xrd.a.fcc`) stay out through the number pattern, and
  the only added exemption is an index glued to a name inside backticks (`peaks[3]`). Notes and
  analyses that passed rules 1 stay valid ("checked with the rules of labpipe X"). In the report a
  passing card carries "numbers checked against results.json by labpipe; not a review" (the postdoc
  section likewise), and "Check before using" is described as needing a person. Added while building:
  the how-to-read text says the same.
- **B4. Replay compares everything it can.**
  - every recorded output, `thumb.jpg` included;
  - a warning when the recorded package versions differ from the current ones;
  - the README names the exception for your own analyses (checked, not redrawn).

  **Built:** `replay._compared` is now every recorded output except `provenance.json` and
  `notes.md` (no method lists them; they are not regenerated). `thumb.jpg` is byte-identical
  run to run (tested on real SEM, XRD and EDS entries: all outputs identical twice), so nothing
  is excluded. `replay.environment_notes` compares python, each tracked library,
  `requirements_lock_sha256` and `runtime.mode` with the current ones and puts "recorded numpy
  2.3.1, now 2.3.2: outputs may differ" lines in `notes` (the CLI prints the whole result as JSON);
  records without an environment field are skipped. Comparison figures (`compare.replay`) already
  compared every output; they record the environment too, so `compare.replay` gives the same warnings.

- **B5. A moved file keeps its analysis.** Today a moved file is analysed again (into its
  old folder), AI notes included. Better: the new entry takes over the old one's results and
  notes, with replay finding the input at its new place by sha256.

  **Built:** `scanner._keep_moved_analyses` (after the pass that marks gone files `missing`,
  before duplicates): an entry that became pending "new file" in this scan takes over the one
  `missing` entry that has the same method, sha256, sample ID and measurement ID, was `done` when
  it went missing (its events), holds a folder with `provenance.json`, has the method's current
  analysis version and settings (`settings.compare_entry` for the new entry) and the same
  companions (base name and sha256 of `analysed_companions` and `companions`). Zero or several
  candidates (or one claimed by two new entries): nothing is guessed, the file is analysed again
  as before. `manifest.take_over_analysis` copies `ANALYSIS_FIELDS` (what `analyze_entry` and
  `replot_entry` write, plus `notes_check`) and sets `done` "moved from <path>; its analysis is
  kept"; the old entry loses `output_dir`, gets `output_moved_to`, stays `missing`. Provenance is
  not edited: `other_owner` already counts the same input sha256 as the same owner, and replay
  looks in the manifest for a non-missing entry of the method with the recorded sha256 when the
  recorded path and raw root + relative path do not exist (`replay._moved_input`; note "input
  moved: found at <path> by its sha256"). `notes verify`, `notes todo` and the report read the
  manifest entry, so they work unchanged; the card shows the current path.
  - A **renamed** file has another measurement ID, and its results.json and notes name the old
    one: it is analysed again (into the old folder, A1), AI notes included.
  - Files of an imported kind are excluded: their provenance lists several source paths.
  - `index.json` is not touched by the scan (it never was for status changes); the next analysis
    in the sample rewrites it.
  - The A1 test of a moved file now renames the file: a plain move no longer needs the folder rule.

## C. Later

- **Context documents:** symlinks out of `context/documents/` are refused, and unpacking and
  tables are bounded.

  **Built:** `context.documents()` walks without following links and lists links as entries;
  `link_problem` refuses a link anywhere on the path or a resolved path outside `documents/`
  (no read, no hash; `postdoc.current_inputs` uses the same rule). Limits are constants at the
  top of `context.py`: 50 MB per file, 20 MB unpacked `word/document.xml` (header checked, then a
  capped read, since a header can lie), 1000 DOCX entries, 120 s and 500 pages for `pdftotext`,
  1,000,000 characters of text (marker + `truncated: true`). "Tables": `dataset.json` holds only
  numbers and paths per measurement (the data itself, not free text), so nothing there was cut;
  the unbounded table was a DOCX table, which went into the text as every cell, unlimited. Now one
  line per row (cells joined by ` | `), at most 200 rows and 500 characters per cell, with a marker.
  This changes how DOCX tables read in `.text/` (before: one line per cell).
- **Setup** says plainly that context read by the postdoc goes to the AI provider.

  **Built:** SETUP.md (Project context), AGENTS.md (Project context and the postdoc),
  docs/settings/context.md, both context templates and the `note` of `labpipe context init` name
  the agent tool's provider generically and say not to put documents there that may not leave the lab.
- **Agent tools:** a short capability table (Claude Code, Codex, OpenCode: install,
  container, unattended runs, cost cap), shown when choosing one.
  **Built:** rows defined once in `agents.CAPABILITIES` (six columns: install, container,
  automatic runs, guard hook, cost cap, models per role). `labpipe setup --only agents` and the
  installer print it as plain text before asking (the installer only the two tools it offers);
  the same table is in docs/settings/agents.md, and `tests/test_docs.py` compares it cell by
  cell. Facts come from the code: OpenCode is not installed, not in the container, not run
  unattended and has no guard hook; Codex has no USD cap (tokens logged); its models line is
  marked "untested" because only the file rendering is tested.
- **Generic agent text** follows the registry (`subagent: null`, `notes: false`).
  **Built:** the generated supervisor already delegates only to methods with a `subagent`
  (tested). The onboarding steps `first-results` and `tour-agents` end with a list generated from
  `load_methods()` (`onboarding.methods_text`); AGENTS.md, SETUP.md, ONBOARDING.md, the
  supervisor text and the `notes todo` help are worded conditionally ("for methods with an
  analyst"; code-only methods: show their checks).
- **Lab methods** (decisions 2026-11-03, user):
  - **Disable a shipped method per project:** `disable: [eds]` in the project's
    `lab/methods.yaml`. Nothing is recognised, analysed or reported for it; `labpipe methods
    list` shows it as off.
    **Built:** `disable:` is read by `labmethods.disabled()` (errors for an unknown name, a lab
    method's name, a non-list) and applied in `load_methods()`, so versions, agents sync, doctor,
    report, compare, dataset and the scanner never see the method. Files it would have claimed
    (asked of `registry.disabled_methods()` only when no enabled method claims them) are *left
    out silently* (scan count `disabled`; `inventory` "left out: method eds is disabled in
    lab/methods.yaml"), not "not recognised": the user chose it, so no notification. Existing
    entries stay in the manifest untouched (scan skips them, and does not mark them missing while
    the file exists); `pending`, `analyze --pending`, `requeue`, the report, the review queue,
    notes todo and the automation's work and notifications skip them; `analyze_entry` and
    `replot_entry` answer `skipped` with the reason. New `labpipe methods list` (shipped, lab,
    imported, and the off ones). The commented `# disable: [eds]` line is in the header of a
    `lab/methods.yaml` that `methods new|import` creates.
  - **Known-answer test:** `labpipe methods new NAME` also writes `lab/methods/NAME/known_answer/`
    (an example input and `expected.yaml`: values with tolerances); `labpipe methods check` runs
    it and fails when a number drifts.
    **Built:** the scaffold writes `known_answer/{README.md,expected.yaml,example<ext>}`; the
    example is the template's two-column file (10 points) and the expected values are what the
    template gives (`summary.n_points|y_max|x_at_y_max`), so a new method passes at once.
    `methods check NAME` (in the container, as before) runs `run()` on it into
    `logs/method-check/NAME-known-answer-<time>/` and compares dotted keys (`a.b[0].c`) with
    `value ± tolerance` (a number needs `tolerance`; other values are compared for equality);
    failures list key, expected, got. `--example` is now optional: the known-answer input
    stands in. No `known_answer/`: a warning. Shipped methods and dev mode are untouched.
  - **Packages:** `lab/requirements.txt` (pinned versions) in the project; `labpipe container
    build` adds a layer on top of labpipe's image for that project only. Recorded in provenance;
    never in labpipe's own image.
    **Built:** `labpackages.py` (parse: only `name==version`, comments allowed, anything else
    refused with the line and why; hash; installed versions; the layer's Containerfile; native
    install command), `container.build_lab` / `project_image`: the layer is
    `localhost/labpipe-<slug>-lab:<hash of the file + base image id>`, built by `container
    build`, and by `start` when missing (so `labpipe update`, whose last step is `container start
    --replace`, rebuilds it on the new base). The layer installs with the image's own
    `/usr/local/bin/python -m pip`, the base's installed versions (`pip freeze`) as constraints
    (a lab package cannot change labpipe's), then `pip check`; the user is the base's. Decided
    while building: the image's `ENV` points pip at the in-container proxy, which runs only in
    the started container, so the RUN unsets the proxy variables (the build uses the engine's
    network; the allow-list is for the running container). `container status` has
    `lab_packages_up_to_date` / `lab_packages` (label `labpipe.lab_requirements` on the
    container vs the file; doctor warns). Provenance `environment` gets
    `lab_requirements_sha256` and `lab_packages`; replay compares them only for records that
    have the keys. `methods check` has a "lab packages" line (form on the host; for a lab method
    also installed here, with the `pip install -r` command natively or "labpipe container build"
    inside the container), doctor the same. Not done: `labpipe update --preview` runs the
    release in a throwaway container from the base image, without the project's layer.
- **CI:** GitHub actions pinned to commit hashes.
  **Built:** `actions/checkout` v4.4.0 and `actions/setup-python` v5.6.0 by commit hash, the
  version in a comment (Dependabot keeps both up to date); docs/RELEASING.md says so. Moving to
  newer majors stays with Dependabot's pull requests.

## Not taken

- **One definition for commands and capability tables, generated into the docs:** docmap and
  `check_docs` already keep the docs in step. Revisit if they drift.
