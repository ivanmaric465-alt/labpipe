# labpipe

Reproducible analysis of lab data, driven by AI agents.

- Code computes every number; agents run commands and write notes.
- Every figure is regenerable from its `provenance.json`.
- Raw data is only ever read, from a read-only folder.

| Method | Input | Output |
|---|---|---|
| `xrd` | PANalytical `.xrdml` (`.xy`) | whole-pattern fit, lattice parameter, phase candidates, crystallite size |
| `sem` | JEOL `.tif` + `.txt` | metadata, pixel size, field of view; per-sample gallery (code only, no AI) |
| `eds` | Oxford INCA `.htm` + linked images | composition as recorded, metal-only composition, vs. nominal |

These methods are one lab's formulation, for its instruments: use them as they are, steer them with settings, or treat them as worked examples for methods of your own: `labpipe methods new` scaffolds one into your project's `lab/methods/`, where updates never touch it ([docs/ADDING_A_METHOD.md](docs/ADDING_A_METHOD.md)).

Details: [docs/SAFETY.md](docs/SAFETY.md) (safety), [docs/ADDING_A_METHOD.md](docs/ADDING_A_METHOD.md) (new methods), [docs/methods/](docs/methods/) (method designs).

## Requirements

- Python 3.10 or newer and git on your computer (the host part only starts the container; the analysis runs in it)
- Podman (or Docker)
- Claude Code or Codex, for the agents
- Linux only, optional: `xdg-dbus-proxy` for desktop pop-ups

The installer checks for Podman and the agent tool, and shows the exact commands for your system when one is missing. You run them (some need admin rights); labpipe never does. Typical ones:

```bash
# Fedora
sudo dnf install git podman
# Debian / Ubuntu
sudo apt install git podman python3-venv
# macOS
brew install git podman
# Claude Code (Linux / macOS)
curl -fsSL https://claude.ai/install.sh | bash
# or Codex
npm install -g @openai/codex
```

```powershell
# Windows
winget install Git.Git
winget install RedHat.Podman
irm https://claude.ai/install.ps1 | iex
```

## Install

One line (no admin rights, not in a synced folder like OneDrive or Dropbox):

```bash
git clone https://github.com/ivanmaric465-alt/labpipe ~/.local/share/labpipe/app && ~/.local/share/labpipe/app/install.sh
```

```powershell
git clone https://github.com/ivanmaric465-alt/labpipe "$env:LOCALAPPDATA\labpipe\app"; & "$env:LOCALAPPDATA\labpipe\app\install.ps1"
```

The installer installs the newest **release**, not whatever is on `main`; `install.sh --main` (PowerShell: `-Main`) installs the development state. What changed in each release: [CHANGELOG.md](CHANGELOG.md).

Everything is under `~/.local/share/labpipe` (`app/` the code, `venv/` a small environment) and one link, `~/.local/bin/labpipe`. Rerun the installer any time; it never replaces a `labpipe` that is somebody else's.

It then asks, in plain words, with the answer in brackets (Enter takes it):

1. Podman or Docker and the agent tool present? If not: the commands to install them, then rerun.
2. Which agent tool (Claude Code or Codex), which model for each role (supervisor, analyst, postdoc; recommendations shown), desktop pop-ups (Linux).
3. The login: it shows `claude setup-token` (Codex: a device login) and stores the token for the container. Nothing you paste is shown.
4. Your first project: name, folder (`~/labpipe-projects/<name>`), data folder (labpipe only reads it), and whether to analyse what is already there or only new files.
5. It builds and starts the container (the first build takes minutes), then prints the notifications page and: *Open Claude Code in `<project folder>` and say: start*.

Your answers are kept in `~/.config/labpipe/defaults.yaml` and become the defaults of every new project.

Not interactive (CI, tests): `install.sh --answers answers.yaml [--no-start]`; the keys are in `src/labpipe/installer.py`.

### Full native install (advanced)

Runs the analysis on your computer, without a container. Needs Python 3.14.

```bash
LABPIPE_FULL=1 ~/.local/share/labpipe/app/install.sh      # or: install.sh --full
```

Optional extras go into labpipe's own environment, and only when a command asks for one. For example `labpipe xrd refs build` needs pymatgen and prints the exact command to run (it uses the environment's own Python and the pinned version).

## Three folders

labpipe keeps three things apart:

| Folder | Default | What is in it | Who writes there |
|---|---|---|---|
| **code** | `~/.local/share/labpipe/app` | labpipe itself: methods, default settings, docs, agent roles | updates only; never edit it |
| **data** | yours, e.g. `~/labdata/pd` | your raw instrument files | you, the instrument PC or a sync client; labpipe only reads it |
| **project** | `~/labpipe-projects/<name>` | one study: its settings, context, your additions and all results | you (settings, context, `lab/`) and labpipe (results) |

- **One code folder per computer, one project per study.** Each project has its own settings, results, agents and container. Several projects can read the same data folder, and one project can read several.
- **The project only points at the data**: `config/local.yaml` → `raw_roots`. Nothing is copied or moved. Results go into the project, never next to the raw files.
- **You work in the project folder**: open Claude Code or Codex there, and run `labpipe` there (or in any folder below it).
- **In the container**, the three folders show up as `/opt/labpipe` (the code, built into the image), `/data/<id>` (each data folder, read-only) and `/project` (the project, read-write).
- **Updates** (`labpipe update`) replace the code folder and the files marked "generated" in a project. They never touch your data, `context/`, `lab/` or results; a settings file in `config/` changes only when a release renamed something in it and you agree after seeing the diff, with a backup.
- **Back up** the project folder: at least `config/`, `context/` and `lab/`, which are only yours. The code comes back with a reinstall. The data you back up as you always do.

### The project folder

```
~/labpipe-projects/pd-films/
  labpipe.yaml            marks the folder as a project: its name, the labpipe release, the updates
  backups/                what `labpipe update` and `labpipe migrate` saved before they changed something
  config/                 your settings: data folders (local.yaml), plots and report (lab.yaml), samples
  context/                what the study is about, for the postdoc: context.md, documents/
  lab/                    your additions: conventions (LAB.md), agent roles, methods, XRD references
  analysis/               results: report.html, one folder per measurement, postdoc runs
  manifest.json           every file found and its state
  logs/                   runs, notifications, replays
  AGENTS.md  CLAUDE.md    generated for the agents: do not edit
  .labpipe/  .claude/ …   generated: do not edit
```

More projects:

```bash
labpipe project new ~/labpipe-projects/pd-films --data ~/labdata/pd
cd ~/labpipe-projects/pd-films
labpipe doctor
```

- Change what the agents do with plain sentences in `lab/LAB.md` (all agents) or `lab/agents/` (one role, or a role of your own), then run `labpipe project sync`. Details: [docs/settings/project.md](docs/settings/project.md).
- If you work on labpipe's own code, the repository is its own project ("dev mode"): there the code and the project folder are the same folder.

### The data folder

Put new files into the data folder; labpipe notices them (automatic processing, or `labpipe scan`). Any layout works, for example:

```
by technique             by sample                by date
XRD/S-1_scan.xrdml       S-1/S-1_scan.xrdml       2026-03/S-1_scan.xrdml
SEM/S-1 a001.tif         S-1/S-1 a001.tif         2026-03/S-1 a001.tif
SEM/S-1 a001.txt         S-1/S-1 a001.txt         2026-03/S-1 a001.txt
```

- labpipe recognises files by type (and, where types are shared, by content), wherever they are.
- The sample ID is read from the start of the file name; the rule is yours to change (`config/pipeline.yaml` → `sample_id.pattern`, or ask the agent).
- `labpipe inventory` shows what it recognises and what it does not.
- A folder that already holds data: setup asks whether to analyse it all, or only files added from now on (the existing ones are recorded as `baseline`; `labpipe requeue --baseline` queues them later).
- **Read-only**: the container mounts the data folder read-only. With the full native install there is no container, so labpipe reads it through a **read-only view** of the same files, which you set up once (`labpipe setup --only raw-data` prints the commands): for example `/srv/labdata-ro` (Linux), `~/labdata-ro` (macOS), `//localhost/labdata-ro` (Windows).

## Set up

The installer made your first project. Open Claude Code or Codex in the project folder (trust the folder when Codex asks) and say *"start"*. The agent runs `labpipe doctor` and goes through file naming, unrecognised files and references; then the first conversation.

### First conversation

Once setup is done, the agent shows you around with your own data (`docs/ONBOARDING.md`): your files, the setup questions of each method, one measurement per method with its plot explained, and a short tour of the report, steering, the agents, automatic processing, the postdoc, the settings and updates. Every step can be skipped.

- It resumes where it stopped (`labpipe onboarding` shows where).
- Say *"tour"* to see the tour again.
- Say *"skip the onboarding"* to leave it out (`labpipe onboarding skip all`).

```bash
cd ~/labpipe-projects/<name>
labpipe doctor                 # what is still missing
labpipe project new ~/labpipe-projects/<other> --data <its data folder>     # another project
```

Without a container (full native install) the guided setup is `labpipe setup`: data folder and its read-only view, what it recognises, sandbox, models per agent, and a pointer to container mode for automatic processing.

- Run a single step: `labpipe setup --only raw-data` (or `files`, `sandbox`, `agents`, `automation`).
- For the read-only view, setup prints the commands for your system (e.g. a bind mount). **You run them**; setup waits, checks the result and saves it.

## Use

With an agent, in the project folder:

```
process the new data
the background is too low above 60° in S-9
let's write the project context
ask the postdoc: how does the lattice parameter follow the Pt content?
```

From the command line:

```bash
labpipe scan                     # find new or changed raw files
labpipe analyze --pending        # analyse them
labpipe report                   # write analysis/report.html
labpipe review                   # write logs/review_queue.md
labpipe status                   # overview of all files
```

Results:
- `analysis/report.html`: start here;
- `analysis/<sample>/<method>/<measurement>/`: `results.json`, `plot.png`, `plot.pdf`, `provenance.json`, `notes.md`.

```bash
labpipe replay analysis/<sample>/<method>/<measurement>/provenance.json   # regenerate and compare hashes (needs the same analysis version, not the same commit)
```

## The postdoc

An agent with a strong model that answers your questions about the whole project: all samples and methods, with your context. It runs only when you ask.

```bash
labpipe context init      # creates context/: context.md, instructions.md, documents/
```

1. Put your work plan, project files and papers into `context/documents/`, then run `labpipe context index`.
2. Write `context/context.md` yourself, or ask your agent: *let's write the project context*. `instructions.md` says how the postdoc uses all this; edit it as you like.
3. Ask: *ask the postdoc: <question>*. Without a conversation: `labpipe postdoc run "<question>"`.

The answer is at the top of `analysis/report.html`, with:
- the question;
- what it is based on;
- what to check before using it.

The report says when there is none yet, when it is out of date, or when it did not pass its checks. Every number in it comes from the results, a comparison done by code, or a cited page of your documents (`labpipe postdoc verify`).

`context/` is never committed. Anything in it is sent to your AI provider when the postdoc reads it.

## Automatic processing

Automatic processing lives only in the project's container, which the installer starts and which starts by itself when you log in.

```bash
labpipe automation setup         # asks the timings; starts the container at login (shows each step, asks first)
labpipe automation status
```

In the container a watcher checks for new files; once they have stopped changing, it analyses them, at most 10 per AI run (the rest follow in the next runs). A nightly run catches anything missed. `labpipe doctor` reports whether the last run succeeded and whether the container starts at login (`autostart`). Log: `logs/automation.log`.

- **Start at login**: Podman on Linux: `systemctl --user enable podman-restart.service` (setup runs it after your yes). To start without logging in, an administrator runs `loginctl enable-linger $USER` (shown, never run for you). Docker: the container restarts when Docker starts (Docker Desktop: "Start Docker Desktop when you sign in"). Podman on macOS/Windows runs in a podman machine: start the machine at login; **untested**.
- **Without a container** (full native install) nothing runs by itself: `labpipe automation run` does one pass by hand, or tell the agent "process the new data".
- **Earlier versions** installed systemd units (`labpipe-watch.service`, `labpipe-nightly.*`). They are retired; `labpipe doctor` warns while they exist (`old_automation`), and `labpipe automation setup` removes them after you confirm.

You get a notification on the notifications page when analyses start and finish, and on Linux a desktop pop-up (`labpipe container popups on`), and once for each new thing that needs you: files no method recognises, files needing a decision (e.g. no sample ID), failed analyses, analyses out of date after a code or settings change. Each message says what to do, e.g. *Open Claude Code in ~/labpipe and say: check the new files.* All of them are listed in `logs/review_queue.md`. Files you never want to hear about go into `config/ignore.yaml`:

```yaml
ignore: ["TEM/**", "EDS/*.docx"]
```

Test the notification: `labpipe automation notify-test`.

Stop it: `labpipe container stop` (it stays stopped until `labpipe container start`).

## Settings

Everything you can change (data, samples, analysis, plots, report, agents, automation, context) has a short page: [docs/settings/README.md](docs/settings/README.md). Or tell your agent in your own words ("bigger fonts", "group the report by method").

## Steer an analysis

Tell the agent what looks wrong, or write a settings file yourself, e.g. `config/measurements/xrd/S-9_1.2.26.yaml`.
These lab settings (`config/lab.yaml`, `config/samples.yaml`, `config/sample_ids.yaml`, `config/measurements/`) are git-ignored: back them up or keep them in a private repo.

```yaml
note: "background too low above 60° (2026-10-16)"
background:
  order: 3
  anchor_ranges_deg: [[10, 16], [52, 64]]
peaks:
  add_deg: [46.7]
```

```bash
labpipe xrd analyze --entry <entry-id> --rerun
```

| Scope | File |
|---|---|
| one measurement | `config/measurements/<method>/<measurement_id>.yaml` |
| one sample | `config/samples.yaml` → `samples.<id>.<method>` |
| a group of samples | preset in `config/samples.yaml` |
| the whole lab | `config/lab.yaml` → `<method>` |

Defaults and all keys: `config/<method>.yaml` (don't edit it: your changes go in `config/lab.yaml`).

## XRD references

```bash
labpipe xrd refs list
labpipe xrd refs add my_phase.cif --id NiO --structure-type rocksalt
labpipe xrd refs build
```

In a project, the references you add are kept in `lab/references/xrd/` (updates never touch them). `xrd refs` runs on your computer, not in the container; building needs the optional pymatgen; the command tells you exactly what to run if it is missing. Do not commit licensed CIFs (ICSD, ICDD).

## Update

Updates go from release to release, with the installation and every project kept in step; [CHANGELOG.md](CHANGELOG.md) says what changes (results, settings, what needs your action). You run `labpipe update` in your terminal, in a project folder; agents never run it. `labpipe --version` shows what is installed. All of it: [docs/settings/updates.md](docs/settings/updates.md).

```bash
cd ~/labpipe-projects/<name>
labpipe update --check                # what is new and what it changes; changes nothing
labpipe update --check --preview      # also: what would change in YOUR numbers (by code, nothing is changed)
labpipe update                        # asks, then updates (the preview is offered when a release changes results)
labpipe update --to v0.2.0            # back to an older release (rollback)
```

What `labpipe update` does, each step said as it happens:

1. Shows the changelog between your release and the new one, in four parts: changes results, settings migrated, new, needs your action. You say yes or no.
2. Preview (when you ask, or when a release changes results): your measurements are analysed again with the new code in a scratch copy, by code alone, and it says per method what would change, e.g. *"xrd: 12 measurements; a.fcc changes up to 0.0004 Å (within σ for all). eds: unchanged."*
3. Records the passes of your notes with today's rules, and backs up `labpipe.yaml`, `config/`, `manifest.json` and `lab/methods.yaml` to `backups/<release>/` in the project. Backups are never deleted.
4. Switches the installation's code to the release and reinstalls; the old container image stays.
5. Settings an update renamed or moved: shows each file's diff, asks, changes it (the original is kept).
6. Regenerates the generated files, restarts the container, runs `labpipe doctor`.
7. Says what is out of date now and what to do. Nothing is reanalysed by itself.

Going back (`--to` an older release) does the same for the older code, and offers to restore your settings from the backup made when the project left that release. Analyses made with that release's calculation are current again. Another project on the same computer: when the installation is already at the new release, `labpipe update` in that project only does its own steps (backup, settings, regenerate, restart, scan); `labpipe doctor` reminds you (*"this project was set up with labpipe X; the installation is Y: run labpipe update here"*). If the code folder has local changes, `labpipe update` refuses and names the files. Windows: `labpipe update` runs `install.ps1`, which is not yet tested on a real Windows machine.

Old settings names keep working, with a warning, for one release; `labpipe migrate` ([docs/settings/migrations.md](docs/settings/migrations.md)) shows them any time. Notes and postdoc analyses that passed the checks stay valid when a release makes the checks stricter; the report then says "checked with the rules of labpipe <version>".

An update marks `stale` only the analyses of methods whose calculation changed (`labpipe methods versions` shows each method's version); docs, report, agent or other-method changes leave them alone. Stale analyses stay valid. Redo them only if you want to (or tell your agent: *redo the out-of-date analyses*):

```bash
labpipe requeue --stale
labpipe analyze --pending
```

## Uninstall

Show-only: these commands remove things, so you run them yourself. Your projects and your data are never touched.

```bash
# for each project: stop and remove its container, home volume and image
cd ~/labpipe-projects/<name> && labpipe container stop
podman rm -f labpipe-<name>; podman volume rm labpipe-<name>-home   # check names: podman ps -a / podman volume ls
podman image rm localhost/labpipe                                   # all tags: podman images
podman secret rm labpipe-claude-token
# start at login (Podman, Linux), and the pop-up proxy; for installations that still have the
# old native units (labpipe-watch/-nightly, retired) the same rm removes them
systemctl --user disable --now podman-restart.service labpipe-dbus-proxy.service
systemctl --user disable --now labpipe-watch.service labpipe-nightly.timer   # only if they exist
rm -f ~/.config/systemd/user/labpipe-*
# labpipe itself: the code, the environment, the link, your defaults
rm -r ~/.local/share/labpipe
rm ~/.local/bin/labpipe
rm -r ~/.config/labpipe
```

Docker: the same with `docker`. A read-only view: `sudo umount <view>`, then remove its line from `/etc/fstab`. Your raw data is never changed. To remove a project too, delete its folder yourself.

## Troubleshooting

| Problem | Fix |
|---|---|
| `labpipe: command not found` | Open a new terminal, or `export PATH="$HOME/.local/bin:$PATH"` |
| doctor: raw root is writable | `labpipe setup --only raw-data` |
| automatic runs fail (sandbox) | `labpipe setup --only sandbox` |
| doctor: `autostart` warns (the container does not start at login) | `labpipe automation setup` |
| doctor: `old_automation` warns (old native units) | `labpipe automation setup` removes them after you confirm, or the commands it shows |
| nothing is analysed by itself | `labpipe container status`; if it is stopped, `labpipe container start` |
| doctor: agent files out of date | `labpipe agents sync` |
| doctor: generated files out of date | `labpipe project sync` |
| `not in a labpipe project` | `cd` into your project folder, or `labpipe project new <folder>` |

## Tests

```bash
~/.local/share/labpipe/venv/bin/python -m pip install pytest
~/.local/share/labpipe/venv/bin/python -m pytest         # needs the full install (LABPIPE_FULL=1)
```

## License

MIT, see [LICENSE](LICENSE).
