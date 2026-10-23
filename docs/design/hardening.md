# Hardening after an external review (design)

Status: approved 2026-10-22 (user: "write the design note, start with A"). Group A is being
built (branch `hardening`); B and C follow, each stopping for review.

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
  EDS: `methods/eds/inputs.py` (the linked images that exist, found with analyze's own
  `_linked_images` and `find_image`; images chosen with `images:` in the settings are not
  included, the settings are tracked on their own). The scanner records `companions` in the
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

- **B5. A moved file keeps its analysis.** Today a moved file is analysed again (into its
  old folder), AI notes included. Better: the new entry takes over the old one's results and
  notes, with replay finding the input at its new place by sha256.

## C. Later

- **Context documents:** symlinks out of `context/documents/` are refused, and unpacking and
  tables are bounded.
- **Setup** says plainly that context read by the postdoc goes to the AI provider.
- **Agent tools:** a short capability table (Claude Code, Codex, OpenCode: install,
  container, unattended runs, cost cap), shown when choosing one.
- **Generic agent text** follows the registry (`subagent: null`, `notes: false`).
- **Lab methods:**
  - an explicit enable/disable of shipped methods per project;
  - a known-answer test scaffold for a lab method;
  - a supported way to add packages.
- **CI:** GitHub actions pinned to commit hashes.

## Not taken

- **One definition for commands and capability tables, generated into the docs:** docmap and
  `check_docs` already keep the docs in step. Revisit if they drift.
