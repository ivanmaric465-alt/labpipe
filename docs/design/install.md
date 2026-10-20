# Installation, projects, updates and onboarding (design, draft)

Status: draft, awaiting approval. Nothing is implemented yet.

## Goal

- One command installs labpipe. A few plain questions, no YAML editing, nothing to run as
  admin except what the installer shows (e.g. installing Podman).
- One installation, several projects. Each project is its own folder with its own data,
  settings, context, results and container. Agents of one project never see another.
- Updates never break a project. The user's files are never overwritten. Results are never
  silently changed. Every update says what it changes before it does, can be previewed on
  the user's own data, and can be rolled back.
- The first conversation onboards the user: what labpipe is, setup, the first results
  explained on their own plots, a short tour.

## What can break today (and is fixed here)

| Today | Effect | Fix |
|---|---|---|
| Code and project are one folder (a git clone) | an update is a `git pull` into the user's data and settings; one project per clone | code and projects separate (below) |
| `code_version` is the git commit (`+dirty` if any tracked file is edited) | **any** update, even a typo fix in a doc, and any local edit (e.g. to `AGENTS.md`), marks every analysis stale | an analysis version per method (below) |
| The user's own agent instructions or lab conventions can only go into tracked files (`AGENTS.md`, `agents/*.md`) | `git pull` conflicts, or the update overwrites them | the lab's additions in their own files (below) |
| Renamed or removed settings keys | old settings files are refused ("unknown setting"), analyses fail | settings migrations (below) |
| Stricter checks in a new version | old notes fail the new rules and are queued for AI rewriting | checks are versioned (below) |
| Updating means `git pull` on whatever is on `main` | untested states; no list of what changed; no way back | releases, changelog, preview, rollback (below) |

## Code and projects

```
~/.local/share/labpipe/            the installation (one per computer, replaced by updates)
  app/                             the code at one release (read-only for agents)
  venv/                            a small host environment (forwards into the container)
~/labpipe-projects/<project>/      one folder per project (the user chooses where)
  labpipe.yaml                     marks the folder as a project; the release it was set up with
  config/                          local.yaml, lab.yaml, samples.yaml, sample_ids.yaml, ignore.yaml, measurements/
  context/                         context.md, instructions.md, documents/
  lab/                             the lab's own additions: LAB.md, agents/, methods/ (optional)
  analysis/  manifest.json  logs/  results (as now)
  AGENTS.md  CLAUDE.md             generated from the release + lab/LAB.md (do not edit)
  .labpipe/docs/                   the release's docs, copied for the agents (generated)
  .claude/ .codex/ .opencode/      harness files (generated)
```

- **`labpipe` finds the project** from the folder it is run in (or a parent folder). The agent
  tool is opened in the project folder, so its agents work there and read `.labpipe/docs/`.
- **Container per project**:
  - the image holds the code of one release, immutable inside;
  - the project folder is mounted read-write at `/project`;
  - the data folder is mounted read-only;
  - the project has its own name, port and home volume (logins and harness memory).

  For developers, `labpipe container start --dev` mounts the code folder instead.
- **The host needs little**: the host part only starts the container and forwards commands.
  So it needs any Python ≥ 3.10 and PyYAML; numpy, scipy and matplotlib live only in the image.
  This removes today's hardest prerequisite, Python 3.14 on the host.
- **Read-only data, simply**: in container mode the data folder is mounted read-only by the
  container, so the read-only view and its admin commands are no longer needed.
- **Existing clones**: `labpipe project adopt <clone>` turns today's clone into a project folder.
  It copies the user's files and results (nothing moved or deleted) and checks the copy with
  `labpipe replay` on a sample of measurements.

## Install (one command)

```bash
git clone https://github.com/<org>/labpipe ~/.local/share/labpipe/app && ~/.local/share/labpipe/app/install.sh
```

Windows: the same with `install.ps1`. The installer:

1. **Checks** for Podman or Docker and the agent tool. If one is missing, it prints the
   install commands for this system (with `sudo` shown, never run) and stops.
2. **Asks**:
   - which agent tool (Claude Code / Codex);
   - the models per role, with the role descriptions and recommendations (Enter takes them);
   - Linux desktop pop-ups (yes/no).
3. **Logs in**: it shows the one command to get a login token (`claude setup-token`; Codex:
   device login) and stores it in the container secret.
4. **First project**:
   - asks for a name, where to put the project folder, and the data folder;
   - asks whether to analyse what is already there or only new files;
   - creates the project (`labpipe project new`).
5. **Starts it**: builds the image, starts the container, and prints the notifications page link
   and *"Open Claude Code in ~/labpipe-projects/<name> and say: start"*.

More projects later: `labpipe project new <folder>`, which asks the same project questions.

## Updates that do not break projects

### 1. The user's files are never written by an update

- The update replaces only `~/.local/share/labpipe/app` and the image.
- Inside a project, it only regenerates the files marked as generated: `AGENTS.md`,
  `CLAUDE.md`, `.labpipe/docs/`, the harness agent files. Each starts with
  *"Generated by labpipe <release>. Your additions: lab/LAB.md"*.
- Anything a lab adapts has its own place in the project, which no update touches:

| The lab wants to … | Where |
|---|---|
| change settings, plots, report | `config/lab.yaml`, `samples.yaml`, `measurements/` (as now) |
| add conventions for all agents ("we call the support Vulcan", "answer in Croatian") | `lab/LAB.md`, included in the generated `AGENTS.md` |
| add instructions to one agent role | `lab/agents/<role>.md`, appended to that role when generating |
| add its own agent role | `lab/agents/<new-role>.md` |
| add its own method | `lab/methods/<name>/` plus its entry in `lab/methods.yaml` |
| recognise more file patterns | `lab.yaml` → `methods.<m>.patterns` |

- If an update would turn a lab addition into something unclear (e.g. a role the lab extends
  was renamed), the update says so and asks. It never drops the addition silently.

### 2. Results are never silently changed

- **Analysis version per method**: a hash of the code that computes that method's results (its
  package and the shared numerics it uses), recorded in the manifest and in provenance. The git
  commit stays in provenance for replay.
  - An update that changes only docs, the report or another method leaves the analyses as
    they are.
  - Only measurements of methods whose computation changed become "out of date".
- **Out of date keeps the old results**: they are redone only when the user asks
  (`labpipe requeue --stale`), as now. Redone results go next to the old ones (`_superseded/`),
  and the change is shown per number.
- **Update preview**: `labpipe update --preview` analyses copies of the measurements with the
  new release, by code and without AI, in a scratch folder, and shows per method what would
  change. For example: *"XRD: 12 measurements; lattice parameter changes up to 0.0004 Å
  (within σ for all); crystallite size changes up to 0.3 nm. EDS: unchanged."* Nothing in the
  project is changed.

### 3. Settings keep working

- Every settings change in a release comes with a **migration**: a rename, a moved section, or a
  changed meaning, with a test.
- `labpipe update` shows the diff of each settings file it would change, keeps a copy
  (`backups/<release>/`), and asks.
- For one release, old keys are also still understood, with a warning, so a user who
  declines is not broken.
- Unknown keys are still refused (typos fail loudly).

### 4. Checks are versioned

- Notes and postdoc analyses record the version of the checks they passed.
- New, stricter rules apply to new text only; old text is not queued for rewriting because the
  rules changed.
- The report says "checked with the rules of <release>" where it differs.

### 5. Releases, not commits

- Users install and update to **releases** (tags like `v0.3.0`), never to whatever is on `main`.
- Each release has a `CHANGELOG.md` entry in fixed parts:
  - **Changes results** (per method, why);
  - **Settings migrated**;
  - **New**;
  - **Needs your action**.
- CI refuses a release whose method code changed without a "Changes results" line, and a
  settings change without a migration.
- Dependabot and the weekly agent-tool check (container plan, step 3) feed into releases. They
  never go straight to users.

### `labpipe update`, step by step

(Built: `docs/settings/updates.md` for users, `docs/ARCHITECTURE.md` → Updates for the flow.)

1. Show the changelog between the project's release and the target (the newest, or `--to`), in
   the four parts. Ask. `--check` stops here.
2. Optional (offered when "Changes results" is not empty; `--preview`): the preview, in a scratch
   copy, by code, with the target's code (`$LABPIPE_HOME/releases/<tag>/`).
3. Before switching: record the passes of the notes (`notes verify`, with the current rules);
   back up `labpipe.yaml`, `config/`, `manifest.json` and `lab/methods.yaml` to
   `backups/<release>/` (never deleted).
4. Switch the installation's code to the release (`git checkout --detach`, refused with local
   changes) and reinstall the host part with the release's own installer (`--keep-checkout`).
   Then `labpipe update --resume` continues **in the new code**; its state is in
   `logs/update/<time>.json`. The old image is kept.
5. Migrate settings, with the diff shown and asked for (a rollback offers to restore `config/` from
   the backup made when the project left that release).
6. Regenerate the generated files (and the agent files with the lab's additions).
7. Restart the container (this builds the new image); native mode: nothing. Record the release in
   `labpipe.yaml`; run `labpipe doctor`.
8. Say what is out of date now (`labpipe scan`), and what to do.

`labpipe update --to v0.2.1` goes back: the code of that release (the image is built when the
container restarts; the old one is kept), plus the settings backup made when the project left it.
When the installation is already at the target (another project updated it), only the project
steps run.

## Onboarding (the first conversation)

The installer ends with *"say: start"*. `AGENTS.md` → Start of every session: `labpipe doctor`.
A new item `onboarding` (to do, not blocking) leads through `docs/ONBOARDING.md`. Every step is
short, uses the user's own data and can be skipped. Progress is recorded with
`labpipe onboarding done <step>`, so it resumes where it stopped; *"tour"* repeats it.

1. **Welcome** (five lines):
   - your files are only read;
   - the analysis is plain code, and every number can be traced and regenerated;
   - AI writes the notes and answers questions, but never calculates;
   - you steer in your own words;
   - the report is where you read everything.
2. **Your files**: what labpipe recognised (`labpipe inventory`), sample IDs, files to leave
   alone (as now, `docs/SETUP.md`).
3. **Method specifics**: per method found, its setup questions (e.g. XRD references and
   standard).
4. **First results**: one measurement per method.
   - The agent shows the plot and explains it with the method page's new **"Reading the
     plot"** section (three to five bullets: what each line, panel and colour is).
   - Then the key numbers, the checks, and the notes.
5. **Short tour**, about one minute each:
   - the report and its layouts;
   - steering, with one change on their own plot (only with the user's OK);
   - the agents and their models;
   - automatic processing and the notifications page;
   - the postdoc and the context folder (optional);
   - where the settings pages are;
   - updates.
6. **Done**: one line on what to say next time ("process the new data", "ask the postdoc: …").

**For people who only see the report**: a collapsed "How to read this report" box at its top
(what the cards, flags and notes mean). It is generated, so it follows the report settings.

## Order of work

1. Code/project split, the analysis version per method, `labpipe project new|adopt`. This is
   the base for everything else; tested by adopting a copy of a real clone and checking that
   nothing becomes stale and replay matches.
2. The installer (Linux; Windows script with the same questions, untested on real Windows),
   the slim host part.
3. Releases, changelog with CI checks, settings migrations, versioned checks, `labpipe update`
   with preview and rollback. **Built** (`src/labpipe/update.py`, `preview.py`; user page
   `docs/settings/updates.md`).
4. Onboarding: `docs/ONBOARDING.md`, "Reading the plot" per method, the doctor item,
   `labpipe onboarding`, the report's "How to read" box.
5. Retire native automation (container plan, step 4).

Each step is a branch with tests, and stops for review.
