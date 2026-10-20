# Updates: what changes, what never does

`labpipe update` brings the installation (the code, `~/.local/share/labpipe/app`) and one project to a release. The **user runs it** in their own terminal, in the project folder; an agent shows the command and explains it, and never runs it (the harness asks before it would, and unattended runs are refused). `labpipe migrate --apply` is the same kind of change: only with the user's OK.

```bash
cd ~/labpipe-projects/<name>
labpipe update --check                  # what is available and what the changelog says; changes nothing
labpipe update --check --preview        # also: what would change in this project's numbers
labpipe update                          # asks, then updates
labpipe update --to v0.2.0              # an older release: rollback
labpipe update --yes                    # no questions (it still prints everything)
```

Options: `--check` (only say what is available; with `--preview` also the preview), `--preview` (the preview, below), `--limit N` (preview: at most N measurements per method), `--to vX.Y.Z` (any release; older means going back), `--yes`. In a terminal it asks; without one it asks you to add `--yes` after you have read `--check`.

## What an update changes, and what it never does

| Changes | Never |
|---|---|
| the code in `~/.local/share/labpipe/app` (to the release's tag) and the host environment; the container image (a new one is built; the old image stays until you remove it) | your raw data: it is only read, also by the preview |
| the generated files of the project: `AGENTS.md`, `CLAUDE.md`, `.labpipe/`, the agent files (`labpipe project sync`) | `context/`, `lab/`, your results (`analysis/`) and notes |
| settings files whose names a release changed (a migration), only after showing the diff and your OK | an analysis: out-of-date ones are listed, redone only when you ask |
| `labpipe.yaml`: `labpipe_version` and a line in `updates:` | a backup: they are never deleted |

If the code folder has local changes, `labpipe update` refuses and names the files. In the labpipe repository itself (dev mode) it says "dev mode: use git; releases are for installations".

## The steps

1. **What is available.** The release tags are fetched (offline: the ones already here are used). It shows the changelog entries between the project's release and the target in four parts: *Changes results* (per method, why), *Settings migrated*, *New*, *Needs your action*. For a rollback: the entries that will be undone. Then it asks.
2. **Preview** (see below) when you pass `--preview`, or when you are asked because a release lists *Changes results*.
3. **Before switching.** `labpipe notes verify` for every analysed entry with notes (with the current code), so each pass is on record with the rules that applied when the notes were written (`notes_check`; a stricter rule later never sends them back to be rewritten). Then the backup: `labpipe.yaml`, `config/`, `manifest.json` and `lab/methods.yaml` go to `backups/<your release>/` in the project (`-2`, `-3` if it exists).
4. **Install the target.** `git checkout --detach <tag>` in the installation's clone, then that release's own installer without its release switching and without questions (`install.sh --no-setup --keep-checkout`), in the same way the installation was made (with or without the analysis libraries).
5. **The new code takes over.** `labpipe update --resume <state file>` starts in the new code, because the code that is being replaced must not finish the job. The state is `logs/update/<time>.json` in the project (from, to, backup, the last step done); if something fails, the message names the `--resume` command that goes on from there.
6. **Settings.** Each file the migrations would change is shown as a diff, with whether its comments are kept, and you are asked. The originals go to `backups/<release>/migrated/`. Declined: the old names keep working, with a warning, for one release; `labpipe migrate --apply` does it later ([migrations.md](migrations.md)). For a rollback you are first offered to restore `config/` from the backup made when the project left that release (the differences are shown); without such a backup it says so, and `labpipe doctor` names any setting the older release does not know.
7. **Regenerate** the generated files (`labpipe project sync`).
8. **Restart.** Container mode: `labpipe container start --replace` (builds the image if needed). Native mode: nothing.
9. **What is out of date now.** `labpipe scan`, then per method how many analyses are out of date. They stay valid and are redone only when you ask: `labpipe requeue --stale`, then `labpipe analyze --pending` (or tell your agent: *redo the out-of-date analyses*). The old results go to `_superseded/`.
10. **Record** in `labpipe.yaml`: `labpipe_version`, and `updates:` gets `{from, to, utc, backup, preview}`. Last, a `labpipe doctor` summary.

**Another project on the same computer.** One installation serves several projects. After one project updated the installation, the others still say the old release in `labpipe.yaml`; `labpipe doctor` shows `project_release` (*"this project was set up with labpipe X; the installation is Y: run labpipe update here"*). `labpipe update` there only does steps 3 and 6 to 10 (backup, settings, regenerate, restart, scan, record); no preview (the installation no longer has the old code to compare with).

## The preview

`labpipe update --preview` answers *"what would this release do to my numbers?"* before anything is changed.

- The release's code is fetched into `~/.local/share/labpipe/releases/<tag>/` (kept; reused next time). In container mode its image is built from there.
- Only methods whose **analysis version** differs between the installed code and the release can change; the others are reported "unchanged" without running anything.
- A scratch copy of the project is made in `~/.local/share/labpipe/preview/<project>-<tag>/`: `labpipe.yaml`, `config/`, `lab/`, `manifest.json` (no `analysis/`, no `context/`). The data folders are the same, read-only. There, with the release's code and by code alone (no AI, no notes): `labpipe scan` (marks the changed methods' analyses out of date), `labpipe requeue --stale`, `labpipe analyze --pending --method M [--limit N]`.
- Then `labpipe dataset` of your project (the current code) and of the scratch copy (the release's code) are compared, by code: per method the number of measurements, per named quantity the largest absolute change and whether every change is within the uncertainty, and measurements that fail now or no longer fail. Methods without named quantities (images) are compared by their `results.json`. Example:

  *xrd: 12 measurements; a.fcc changes up to 0.0004 Å (within σ for all); size.fcc changes up to 0.3 nm. eds: unchanged (the calculation is the same).*

- `preview.json` in the scratch folder has all of it. **Nothing in your project is written.** Run it again after the release changes; `--limit 3` is quicker for big projects.
- "Within σ" means the change is not larger than the uncertainty of the result you have now (the new one if the old has none); where a quantity has no uncertainty it says so.

Agents quote these numbers; they never calculate them.

## Rollback

`labpipe update --to v0.2.0` puts the installation's code and image on that release. Your analyses are not touched: results made with the newer code stay as they are (the scan then lists what the older code considers out of date), and analyses made with that release's calculation and your settings at the time are current again ("done"). Settings: see step 6. A backup of the state you leave is made in `backups/<the release you leave>/` first. Releases before `labpipe update` existed cannot be used as a target.

## Backups

`backups/<release>/` (and `backups/migrate-<time>/` from `labpipe migrate`) are plain copies in the project. Nothing deletes them; remove old ones yourself when you are sure. To undo by hand, copy the files back. Keep `backups/` out of the sync folder and out of git if the project is in one.

## You say → do

| You say | Do |
|---|---|
| "is there a new labpipe?" / "what's new?" | `labpipe update --check`; read the changelog parts to the user (results, settings, new, your action) |
| "what would the update do to my results?" | `labpipe update --check --preview`; quote its lines; never compute a number yourself |
| "update labpipe" | show `labpipe update` and what it does (this page), the changelog parts, and that it asks and backs up first; **the user runs it** in their terminal |
| "go back to the old version" | show `labpipe update --to v<old>`; the user runs it; say that settings can be restored from `backups/` |
| "the update said some analyses are out of date" | they stay valid; redo only on request: `labpipe requeue --stale`, then `labpipe analyze --pending`, then the notes for the new results |
| "labpipe doctor says my project was set up with an older release" | the installation is newer: the user runs `labpipe update` in this project |
| "the update stopped half way" | the error names `labpipe update --resume <file in logs/update/>`; the user runs it after fixing what it says |
| "restore my old settings" | after a rollback it offers it; or copy files from `backups/<release>/config/` back |

After an update, tell the user: what the changelog said under *Needs your action*, which methods have out-of-date analyses and how many (the update printed them), and, if any file was migrated, where the original is (`backups/<release>/migrated/`).

For developers: `src/labpipe/update.py` (the steps, the state, the re-exec), `src/labpipe/preview.py` (the preview); `docs/ARCHITECTURE.md` → Updates. A release that changes a method's numbers needs its *Changes results* bullet (`docs/RELEASING.md`); that bullet is what the user reads first.
