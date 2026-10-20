# Releasing (for maintainers)

Users install releases (tags `vX.Y.Z`), never whatever is on `main`. `install.sh` / `install.ps1` in the installation's own clone switch to the newest tag (`--main` stays on the clone's current state: for developers). `labpipe update` goes from release to release (and back) and shows the changelog first (`docs/settings/updates.md`); its "Changes results" lines are what users read before they agree, and what the preview (`labpipe update --preview`) confirms with their own numbers.

## The changelog

`CHANGELOG.md` has a fixed format; `labpipe.release.parse_changelog` reads it and CI refuses anything else.

```
# Changelog
## Unreleased
### Changes results
### Settings migrated
### New
### Needs your action
## v0.1.0 — 2026-10-16
```

- Every change that users notice gets a bullet (`- `) under **Unreleased**, in the commit that makes the change.
- Only these four `###` parts, in this order. An empty part may be left out or say `None.`.
- **Changes results**: numbers a method gives for the same data. Each bullet starts with the method in backticks and says why: ``- `xrd`: background fit starts at 15° (the old start cut into the first peak).``
- **Settings migrated**: what an update changes in a project's settings files.
- **New**: new commands, methods, settings, documents.
- **Needs your action**: what the user must do (reinstall, rebuild, answer a question). Keep it rare.
- Release headings: `## vX.Y.Z — YYYY-MM-DD`, newest first.
- Dependency bumps (Python packages, the image, the agent tool) that change an analysis version need a "Changes results" bullet like any other change; the CI check enforces it.

## Make a release

From a clean `main` with CI green:

```bash
python scripts/release.py 0.2.0 --dry-run    # shows the changelog change
python scripts/release.py 0.2.0              # commits "Release v0.2.0"; tags and pushes nothing
```

It refuses unless the tree is clean, the branch is `main`, Unreleased has entries and the version is greater than `__version__`. It moves Unreleased under the new heading, sets `__version__` (`src/labpipe/__init__.py`, the single source) and `version` in `pyproject.toml` (a test keeps them equal), and prints the commands to run yourself:

```bash
git tag -a v0.2.0 -m "labpipe v0.2.0"
git push origin main v0.2.0
gh release create v0.2.0 --title "labpipe v0.2.0" --notes-file - <<'EOF'
(the printed notes)
EOF
```

## What CI checks

- `scripts/check_docs.py`: code changed, its docs did not (docs/docmap.yaml). Excuse: commit line `Docs-checked: <area>`.
- `scripts/check_release.py [BASE]`: CHANGELOG.md parses; for every shipped method whose analysis version differs from BASE, Unreleased → Changes results has a bullet naming it in backticks. A method new since BASE needs a bullet naming it in any part (New); a removed one in Changes results. BASE's versions come from a temporary `git worktree`. Run it yourself before pushing: `python scripts/check_release.py origin/main`.
- Excuse when the analysis version changed but the numbers provably did not (a refactor): commit message line `Results-unchanged: <method> <reason>`. The check prints what it let pass; say in the pull request how you know.

## Update bots

They open pull requests; CI tests them; nothing reaches users before a release.

- `.github/dependabot.yml`: weekly, the image's base (`container/Dockerfile`), the npm packages (`container/package.json`, Codex), the actions in `.github/workflows`.
- `.github/workflows/updates.yml` (weekly, or run it by hand): `scripts/check_updates.py --apply` looks up the latest PyPI version of every pin in `requirements.lock`, `container/requirements.lock` (identical sets) and `pyproject.toml` (`analysis`, `refbuild`), and of `@anthropic-ai/claude-code` against `CLAUDE_CODE_VERSION` in `container/versions.env`, and opens one pull request listing old -> new. `python scripts/check_updates.py` alone only reports. pymatgen stays out of both lock files. A pull request opened with the workflow's token does not start CI by itself: push an empty commit to it, or close and reopen it.
