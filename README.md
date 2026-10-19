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

Details: [docs/SAFETY.md](docs/SAFETY.md) (safety), [docs/ADDING_A_METHOD.md](docs/ADDING_A_METHOD.md) (new methods), [docs/methods/](docs/methods/) (method designs).

## Requirements

- Python ≥ 3.14 and git
- Claude Code or Codex, for the agents and automatic processing
- Linux only, for automatic processing: bubblewrap and socat

```bash
# Fedora
sudo dnf install python3.14 git bubblewrap socat
# Debian / Ubuntu
sudo apt install python3.14 python3.14-venv git bubblewrap socat
# macOS
brew install python@3.14 git
# Claude Code (Linux / macOS)
curl -fsSL https://claude.ai/install.sh | bash
# or Codex
npm install -g @openai/codex
```

```powershell
# Windows
winget install Python.Python.3.14
winget install Git.Git
irm https://claude.ai/install.ps1 | iex
```

## Install

Clone to a folder that is not synced (not OneDrive, Dropbox, …). No admin rights needed.

```bash
git clone https://github.com/ivanmaric465-alt/labpipe.git
cd labpipe
./install.sh
```

```powershell
git clone https://github.com/ivanmaric465-alt/labpipe.git
cd labpipe
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer creates a venv outside the repo, puts `labpipe` on your PATH, then offers the guided setup.

## Projects

One installation, several projects. A project is a folder (suggestion: `~/labpipe-projects/<name>`, never in a synced folder) with its own data, settings, context, results and container. labpipe finds the project from the folder you run it in.

```bash
labpipe project new ~/labpipe-projects/pd-films --data ~/labdata/pd
cd ~/labpipe-projects/pd-films
labpipe doctor
```

- The code (defaults, docs, agent roles) stays where it is; the project holds only your files. `AGENTS.md`, `CLAUDE.md` and `.labpipe/` in the project are generated: do not edit them. Your additions go in `lab/LAB.md` (for all agents) and `lab/agents/` (one role, or a role of your own), then `labpipe project sync`. Details: [docs/settings/project.md](docs/settings/project.md).
- Working in the labpipe repository itself (to change the code) is "dev mode": the repository is its own project.

## Your data folder

Two folders, the same files:

| Folder | Example | Who writes |
|---|---|---|
| your **data folder** | `~/labdata` | you, the instrument PC or a sync client |
| its **read-only view** | `/srv/labdata-ro` (Linux), `~/labdata-ro` (macOS), `//localhost/labdata-ro` (Windows) | nobody; labpipe only reads here |

Put new files into the data folder; they appear in the view at once. Any layout works, for example:

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

## Set up

```bash
labpipe setup          # data folder, what it recognises, sandbox, models per agent, automation
labpipe doctor         # what is still missing
```

- Run a single step: `labpipe setup --only raw-data` (or `files`, `sandbox`, `agents`, `automation`).
- For the read-only view, setup prints the commands for your system (e.g. a bind mount). **You run them**; setup waits, checks the result and saves it.
- Then open the folder in Claude Code or Codex (trust the folder when Codex asks) and say *"set this up for me"*. The agent goes through file naming, unrecognised files, references and a first analysis.

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
labpipe replay analysis/<sample>/<method>/<measurement>/provenance.json   # regenerate and compare hashes
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

```bash
labpipe automation setup         # asks the timings, shows the service files, installs after you confirm
labpipe automation status
loginctl enable-linger           # Linux: keep it running when you are logged out
```

A watcher checks for new files; once they have stopped changing, it analyses them, at most 10 per AI run (the rest follow in the next runs). A nightly run catches anything missed. `labpipe doctor` reports whether the last run succeeded. Log: `logs/automation.log`.

You get a desktop notification when analyses start and finish, and once for each new thing that needs you: files no method recognises, files needing a decision (e.g. no sample ID), failed analyses, analyses out of date after a code or settings change. Each message says what to do, e.g. *Open Claude Code in ~/labpipe and say: check the new files.* All of them are listed in `logs/review_queue.md`. Files you never want to hear about go into `config/ignore.yaml`:

```yaml
ignore: ["TEM/**", "EDS/*.docx"]
```

Test the notification: `labpipe automation notify-test`.

Stop it:

```bash
systemctl --user disable --now labpipe-watch.service labpipe-nightly.timer
```

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

Do not commit licensed CIFs (ICSD, ICDD).

## Update

```bash
git pull
./install.sh                     # Windows: .\install.ps1
labpipe agents sync
labpipe doctor
```

An update marks `stale` only the analyses of methods whose calculation changed (`labpipe methods versions` shows each method's version); docs, report, agent or other-method changes leave them alone. Stale analyses stay valid. Redo them only if you want to:

```bash
labpipe requeue --stale
labpipe analyze --pending
```

## Uninstall (Linux)

```bash
systemctl --user disable --now labpipe-watch.service labpipe-nightly.timer
rm ~/.config/systemd/user/labpipe-*
rm -r ~/.local/share/labpipe/venv ~/.local/bin/labpipe
```

A read-only view: `sudo umount <view>`, then remove its line from `/etc/fstab`. Your raw data is never changed.

## Troubleshooting

| Problem | Fix |
|---|---|
| `labpipe: command not found` | Open a new terminal, or `export PATH="$HOME/.local/bin:$PATH"` |
| doctor: raw root is writable | `labpipe setup --only raw-data` |
| automatic runs fail (sandbox) | `labpipe setup --only sandbox` |
| doctor: agent files out of date | `labpipe agents sync` |
| doctor: generated files out of date | `labpipe project sync` |
| `not in a labpipe project` | `cd` into your project folder, or `labpipe project new <folder>` |

## Tests

```bash
~/.local/share/labpipe/venv/bin/python -m pip install pytest
~/.local/share/labpipe/venv/bin/python -m pytest
```

## License

MIT, see [LICENSE](LICENSE).
