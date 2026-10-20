# Changelog

What changed in each release, in four fixed parts. How it is kept: [docs/RELEASING.md](docs/RELEASING.md).

## Unreleased

### Changes results

None.

### Settings migrated

None.

### New

- Code and projects are separate. The code folder (`~/.local/share/labpipe/app`) is replaced by updates; a project is your own folder (`labpipe.yaml`, `config/`, `context/`, `lab/`, results) that updates never write to. `labpipe project new` creates one, `labpipe project sync` regenerates its generated files.
- One-command installer (`install.sh`, `install.ps1`) and a slim host install (Python 3.10+, PyYAML only). The analysis runs in a container, one per project (Podman or Docker); `labpipe setup --first` asks the first questions: tools, models, login, first project. The installer installs the newest release; `--main` installs the development state.
- `labpipe --version` shows the version and the release tag.
- The postdoc: an overall analysis of the whole dataset on request (`labpipe postdoc`), with a `context/` folder for aims and documents, named quantities per sample (`labpipe dataset`, compare differences and trends), and its own section in the report.
- Settings pages (`docs/settings/`) and your own preferences file `lab.yaml`; plot and report settings with layouts, grouping, sections, filters and extra reports; `labpipe replot` redraws figures after a plot setting changed.
- Every method has its own analysis version and plot version (`labpipe methods versions`): only a change in the calculation marks analyses stale; a plot change only marks the plot out of date.
- The lab's own methods and references live in the project (`lab/methods.yaml`, `lab/methods/<name>/`, `lab/references/xrd`); updates never touch them. `labpipe methods new` scaffolds into `lab/`, `labpipe methods check` runs in the container.
- `labpipe update` goes from release to release, and back (`--to`): it shows the changelog first, can preview what the release would change in your own numbers (`--preview`, by code, nothing is changed), backs up your settings (`backups/<release>/`), switches the code, migrates renamed settings (diff shown, asked), regenerates the generated files, restarts the container and lists the analyses that are out of date (nothing is redone by itself). Settings migrations (`labpipe migrate`; old names keep working for one release) and versioned note checks (notes that passed stay valid when the rules get stricter) come with it. [docs/settings/updates.md](docs/settings/updates.md)
- First conversation: after setup the agent shows you around with your own data (`docs/ONBOARDING.md`: your files, each method's setup questions, a first result explained, a short tour; any step can be skipped, "tour" repeats it). `labpipe onboarding` keeps the record; `labpipe doctor` shows `onboarding` as a warning while steps remain.
- `labpipe doctor` says when a project was set up with another release than the installed one (`project_release`).
- `labpipe replay` needs the same analysis and plot version, not the same commit.
- Every method page has a "Reading the plot" section (what each line, panel and colour is, good versus look again, which plot settings change it), and so does the template for your own methods (`labpipe methods check` warns while it is missing). The report starts with a collapsed "How to read this report" box, generated from the report settings (`how_to_read: false` hides it).
- The container starts by itself when you log in. The installer asks ("Start the container by itself when you log in?", default yes) and `labpipe automation setup` sets it up: Podman on Linux enables the user unit `podman-restart.service` (after your yes; starting without logging in needs `loginctl enable-linger $USER`, which is shown and never run), Docker restarts the container when Docker starts, Podman on macOS/Windows needs the podman machine to start at login (untested). `labpipe doctor` has an `autostart` item.
- Automatic processing runs only in the container. The native systemd watcher and nightly timer, and the native macOS/Windows pop-ups, are removed; `labpipe automation watch` refuses outside the container. `labpipe automation run` (one pass by hand) and the notifications page stay; Linux pop-ups go through `labpipe container popups on`.
- XRD quantities use a generic fcc name in every sample, with the closest reference as "possibly X"; no AI cost limit unless you set one.

### Needs your action

- Old native automation: if you set it up with an earlier version, remove its units: `labpipe automation setup` (shows them and asks first), or `systemctl --user disable --now labpipe-watch.service labpipe-nightly.service labpipe-nightly.timer`, then `rm ~/.config/systemd/user/labpipe-watch.service ~/.config/systemd/user/labpipe-nightly.service ~/.config/systemd/user/labpipe-nightly.timer` and `systemctl --user daemon-reload` (`labpipe doctor` warns as `old_automation` until then). Then run `labpipe automation setup` once to start the container at login.
- Existing clones and test setups: reinstall with the new installer and create a project with `labpipe project new <folder> --data <data folder>`. There is no adopt of an old layout.

## v0.1.0 — 2026-10-16

### New

- First version: the pipeline (scan, analyse, provenance, replay, report) for XRD, EDS and SEM, the agent roles and the safety rules.
