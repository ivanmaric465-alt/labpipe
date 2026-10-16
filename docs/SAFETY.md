# Safety model

Raw data is irreplaceable. Everything in this repository can be regenerated. Several independent layers protect the raw data; each one covers gaps in the others.

| Layer | Enforced by | Protects against |
|---|---|---|
| 1. Read-only view of the raw data | OS: bind mount (Linux) or read-only share (Windows) | Any write from any process the pipeline starts |
| 2. Claude Code sandbox (unattended runs) | OS isolation (bubblewrap on Linux, Seatbelt on macOS; on Windows use WSL2) | Shell commands writing outside the repository, network access from shell commands |
| 3. Permission rules (`.claude/settings.json`, `.claude/unattended.json`) | Claude Code | Agents running other commands or editing other files without your approval |
| 4. PreToolUse hook (`labpipe guard`, via `.claude/hooks/guard.py`) | Claude Code + this code | Any agent write inside raw or protected paths, shell commands naming them, agent edits of pipeline outputs and the manifest; in unattended mode anything but plain `labpipe` commands and notes/log writes |
| 5. `labpipe.safety` in the code | This code | Writing inside raw roots, protected paths, or anywhere outside `analysis/`, `logs/` and `manifest.json` |
| 6. Independent backup or snapshot | You | Mistakes outside the pipeline; deletions spread by a sync client, if you use one |

## Agent modes

**Interactive** (you talk to the agent): `.claude/settings.json` pre-approves `labpipe` commands and edits of steering files and notes. Everything else asks you first. Destructive commands (`rm`, `mv`, `sudo`, `chmod`, forced git operations…) are denied outright. Edits of `config/local.yaml`, `config/pipeline.yaml` and `.claude/` always ask.

**Unattended** (automatic): `labpipe automation setup` installs a watcher and a nightly run (Linux: systemd user services; you confirm before anything is installed). Each pass scans first and starts the AI only if there is work. The command it uses is:

```bash
LABPIPE_AGENT_MODE=unattended claude -p "Run one pipeline pass." \
  --agent supervisor --setting-sources project --strict-mcp-config \
  --permission-mode dontAsk --settings .claude/unattended.json < /dev/null
```

- `--agent supervisor` gives the session only the supervisor's tools (Bash, Read, Write, Agent).
- `--setting-sources project --strict-mcp-config` keeps your personal settings, plugins and MCP servers out of the run, so it depends only on what is in the repository.
- `dontAsk` refuses anything not pre-approved, instead of waiting for an answer nobody gives.
- `.claude/unattended.json` is self-contained: it pre-approves `labpipe` commands and notes/log edits, turns on the sandbox (startup fails if it is unavailable), and blocks network commands, web access and edits of settings, code and references. Allow rules in `.claude/settings.json` are ignored by Claude Code until the folder is trusted in an interactive session, so unattended runs do not rely on them.
- `LABPIPE_AGENT_MODE=unattended` tells the guard hook to allow only plain `labpipe ...` commands and writes of `notes.md` and `logs/`.
- `labpipe` must be on `PATH` for the run (`labpipe doctor` checks).

**Do not add `Edit(...)` deny rules for files labpipe writes** (`manifest.json`, `analysis/`, `logs/`). In a sandboxed run, Claude Code turns `Edit` deny rules into OS-level write blocks for every process, `labpipe` included, so the pipeline could no longer write its own state. Agents are kept away from those files by the guard hook instead. `Edit` denies on settings, code and references in `.claude/unattended.json` are fine, because labpipe never writes there during a run.

The hook needs a Python 3 on `PATH` as `python3` (it then uses the labpipe venv itself). A hook that cannot start does not block anything, so layers 1 and 5 remain the guarantee.

## Where the paths are configured

`config/local.yaml` is per machine and git-ignored. Create it with `labpipe init`.

- **`raw_roots`**: the read-only view. This is the only place labpipe reads raw data from.
- **`protected_paths`**: where the files really live, if that folder is writable (the original behind a read-only view). labpipe refuses to write anywhere inside them. Not needed when the raw root itself cannot be written.

## Setting up the read-only view (layer 1)

labpipe needs exactly one thing: **a folder it can only read**. How your files get into it is up to you. You can copy them by hand, export them from the instrument PC, use a network drive, or use a sync client; none of these is required.

The easiest route is `labpipe setup --only raw-data` (also offered by the installers). It asks for the folder, prints the commands below for your system, waits while you run them, checks the result and fills in `config/local.yaml`. It never runs anything that needs admin rights or changes the raw data itself.

Two ways to get there:

1. **A read-only view (recommended).** Keep the files in an ordinary folder and fill it however you like. labpipe gets a second, read-only view of that folder and is only ever pointed at the view. New files appear in the view at once. Nothing running as you can write through it, and it takes admin rights to change. Get the exact commands for your system, with your paths filled in:

   ```
   labpipe readonly-view --source "/path/to/raw/data"
   ```

   It only prints; you run the commands yourself. Put the printed `raw_roots` and `protected_paths` into `config/local.yaml`.

2. **Make the folder itself read-only (simplest, weaker).** Copy the files in, then remove write permission, e.g. on Linux/macOS `chmod -R a-w "/path/to/raw/data"`. To add files later: `chmod -R u+w`, copy, `chmod -R a-w` again. No admin rights are needed, but it only prevents accidental writes: any program running as you could make the folder writable again. A read-only view cannot be changed that way.

Either way, `labpipe doctor` checks that the raw root really is read-only.

| System | Method | Status |
|---|---|---|
| Linux | bind mount, remounted read-only, made permanent in `/etc/fstab`. Alternative without root after installation: `bindfs -r`. | tested |
| macOS | `bindfs -r` (macFUSE); no native bind mounts | untested |
| Windows | SMB share of the folder that grants read access only, read via `//localhost/<share>`. Or use WSL2 and the Linux method. | untested |

For the Claude Code sandbox layer (2): on Linux it needs `bubblewrap` and `socat` installed (`labpipe setup --only sandbox` checks); macOS has it built in; on Windows, run Claude Code and labpipe inside WSL2.

## What layer 1 does not cover

- The writable original stays writable for you and any process running as you. That is why labpipe and the agents are only ever given the read-only view, and why the original is listed in `protected_paths`.
- Keep an independent backup of the raw data (a copy on another disk, a filesystem snapshot, version history). A read-only view protects against the pipeline, not against everything else.
- If you use a sync client: it spreads deletions made on other devices or in its web interface, and files-on-demand (placeholders that download when opened) can make reads slow or fail; keep the raw folder fully downloaded on the analysis computer.
