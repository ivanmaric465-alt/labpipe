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
- Claude Code, for the agents and automatic processing
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
- Then open the folder in Claude Code and say *"set this up for me"*. The agent goes through file naming, unrecognised files, references and a first analysis.

## Use

With an agent, in the repo folder:

```
process the new data
the background is too low above 60° in S-9
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

## Automatic processing

```bash
labpipe automation setup         # asks the timings, shows the service files, installs after you confirm
labpipe automation status
loginctl enable-linger           # Linux: keep it running when you are logged out
```

A watcher checks for new files; once they have stopped changing, it analyses them, at most 10 per AI run (the rest follow in the next runs). A nightly run catches anything missed. `labpipe doctor` reports whether the last run succeeded. Log: `logs/automation.log`.

Stop it:

```bash
systemctl --user disable --now labpipe-watch.service labpipe-nightly.timer
```

## Steer an analysis

Tell the agent what looks wrong, or write a settings file yourself, e.g. `config/measurements/xrd/S-9_1.2.26.yaml`.
These lab settings (`config/samples.yaml`, `config/sample_ids.yaml`, `config/measurements/`) are git-ignored: back them up or keep them in a private repo.

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

Defaults and all keys: `config/<method>.yaml`.

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

Old analyses become `stale` but stay valid. Redo them only if you want to:

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

## Tests

```bash
~/.local/share/labpipe/venv/bin/python -m pip install pytest
~/.local/share/labpipe/venv/bin/python -m pytest
```

## License

MIT, see [LICENSE](LICENSE).
