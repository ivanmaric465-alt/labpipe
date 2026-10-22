# labpipe

Reproducible analysis of lab data, driven by AI agents.

- Code computes every number; agents run commands and write notes.
- Every figure is regenerable from its `provenance.json`.
- Raw data is only ever read, from a read-only folder.

| Method | Input | Output |
|---|---|---|
| `xrd` | PANalytical `.xrdml` (`.xy`) | whole-pattern fit, lattice parameter, phase candidates, crystallite size |
| `sem` | JEOL `.tif` + `.txt` | metadata, pixel size, field of view; a gallery per sample |
| `eds` | Oxford INCA `.htm` + linked images | composition as recorded, metal-only composition, vs. nominal |
| yours | a figure, table or note you made elsewhere | shown in the report as yours ([docs/methods/imported.md](docs/methods/imported.md)) |

The methods are one lab's formulation, for its instruments: use them, steer them with settings,
or start your own from them ([docs/ADDING_A_METHOD.md](docs/ADDING_A_METHOD.md)).

## Requirements

- Python 3.10 or newer, git
- Podman (or Docker)
- Claude Code or Codex

The installer checks these and shows the commands for your system when one is missing; you run
them (some need admin rights). For example:

```bash
sudo dnf install git podman                       # Fedora (Debian/Ubuntu: sudo apt install git podman python3-venv)
curl -fsSL https://claude.ai/install.sh | bash    # Claude Code (or: npm install -g @openai/codex)
```

## Install

One line, no admin rights, not in a synced folder (OneDrive, Dropbox):

```bash
git clone https://github.com/ivanmaric465-alt/labpipe ~/.local/share/labpipe/app && ~/.local/share/labpipe/app/install.sh
```

```powershell
git clone https://github.com/ivanmaric465-alt/labpipe "$env:LOCALAPPDATA\labpipe\app"; & "$env:LOCALAPPDATA\labpipe\app\install.ps1"
```

It installs the newest release and asks a few questions:
- the agent tool and its models;
- the login;
- your first project and its data folder.

Then it starts the container. To run the analysis on your computer, without a container, see
[docs/NATIVE.md](docs/NATIVE.md).

## Three folders

| Folder | Default | What is in it | Who writes there |
|---|---|---|---|
| **code** | `~/.local/share/labpipe/app` | labpipe itself | updates only |
| **data** | yours, e.g. `~/labdata/pd` | your instrument files and your own analyses | you; labpipe only reads it |
| **project** | `~/labpipe-projects/<name>` | one study: its settings, context and all results | you and labpipe |

- One code folder per computer, one project per study. A project only points at its data
  folder(s), nothing is copied.
- You work in the project folder: open your agent tool there.
- Back up `config/`, `context/` and `lab/` of each project: they are only yours. The code comes
  back with a reinstall.

What is in a project: [docs/settings/project.md](docs/settings/project.md).

## Start

Open Claude Code or Codex in the project folder and say **"start"**.

The agent checks the setup with you, then shows you around with your own data: one
measurement per method, the report, and how to steer an analysis. After that, just say what
you want:

```
process the new data
the background is too low above 60° in S-9
include my Raman fits from Raman/fits
compare the lattice parameters of S-1 to S-4
```

Results: `analysis/report.html` in the project. It holds:
- a card per measurement, with its plot, numbers and notes;
- tables;
- comparison figures across samples.

## Settings

Everything you can change has a short page: [docs/settings/README.md](docs/settings/README.md).
Usually you just tell your agent ("bigger fonts", "order the samples by loading").

## Update

You run it in your terminal, in a project folder (agents never do):

```bash
labpipe update --check     # what is new, and what it would change; changes nothing
labpipe update             # asks, backs up, updates
```

What changed in each release: [CHANGELOG.md](CHANGELOG.md). All about updates, previews and
going back: [docs/settings/updates.md](docs/settings/updates.md).

## Uninstall

```bash
~/.local/share/labpipe/app/uninstall.sh      # shows what it removes, asks once
```

It removes:
- labpipe's containers, volumes and images;
- the stored login;
- the installation, the `labpipe` link and your defaults (`--keep-config` keeps them).

Your projects and data are never touched; delete a project folder yourself if you want it gone.
`--dry-run` only shows.

Windows (no script yet): remove the containers, the installation and your defaults, then
remove `%LOCALAPPDATA%\labpipe\venv\Scripts` from your user PATH:

```powershell
podman rm -f labpipe-<name>; podman volume rm labpipe-<name>-home      # each project (podman ps -a)
podman image rm localhost/labpipe; podman secret rm labpipe-claude-token
Remove-Item -Recurse "$env:LOCALAPPDATA\labpipe", "$HOME\.config\labpipe"
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `labpipe: command not found` | open a new terminal, or `export PATH="$HOME/.local/bin:$PATH"` |
| something is not set up | `labpipe doctor` in the project folder says what and how |
| nothing is analysed by itself | `labpipe container status`; if stopped, `labpipe container start` |
| `not in a labpipe project` | `cd` into your project folder |

## More

| | |
|---|---|
| all commands | `labpipe --help`; what the agents follow: [AGENTS.md](AGENTS.md) |
| safety | [docs/SAFETY.md](docs/SAFETY.md) |
| how it is built | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| the methods | [docs/methods/](docs/methods/) |
| releases | [docs/RELEASING.md](docs/RELEASING.md) |

## License

MIT, see [LICENSE](LICENSE).
