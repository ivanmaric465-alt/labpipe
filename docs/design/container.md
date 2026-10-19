# Container mode for automatic processing

Status: DRAFT, for approval. No code until approved.

## Goal
Run labpipe's automatic processing (watcher, nightly pass, AI runs) in a container (Podman or Docker), the same way on Linux, macOS and Windows, and offer interactive agent sessions in the same container. The container replaces the OS-specific parts: the read-only view (`sudo mount` / SMB share), the agent tool's own sandbox, and systemd / Task Scheduler.

**Native automation is retired** once container mode works on this machine (user's decision, 2026-10-19): the systemd units, the Task Scheduler plan and the native-notification code go; `labpipe automation setup` installs the container only.

Interactive work: in the container (`labpipe shell`, the same protections as automatic runs), or with the user's installed apps on the host (Claude desktop, Codex app, an IDE), which work on the same repository folder.

## What runs where

| | Host | Container |
|---|---|---|
| Interactive agent, terminal (Claude Code, Codex CLI) | possible, as today | `labpipe shell` (recommended) |
| Interactive agent, desktop apps and IDEs | yes | no (GUI apps cannot run inside) |
| Watcher, nightly pass, code-only analyses | no | `labpipe automation watch` |
| AI runs (supervisor, analysts) | no | `claude -p …` / `codex exec …`, Linux builds inside the image |
| Report and notifications page | browser tab | `labpipe serve` (port published on 127.0.0.1 only) |
| Desktop pop-ups (Linux) | `xdg-dbus-proxy` (notifications only) | `notify-send` through the proxy |

## Image
- `container/Dockerfile` in the repository; the user's machine builds it (`labpipe container build`); nothing is pulled from a registry except the base image.
- Base image pinned by digest (Debian-based `python:3.14-slim`). Inside: the Python dependencies with exact versions (as in `pyproject.toml`, plus Pillow pinned), fonts for the ACS plot style, git, Node.js, Claude Code and Codex CLIs at fixed versions, `notify-send`. Auto-updates of the CLIs are off; a version change is a commit.
- The **code is not in the image**: the repository is mounted and installed in development mode at start. `git pull` on the host takes effect as today (the watcher restarts on a new code version). Only a change of the files in `container/` needs a rebuild; `labpipe doctor` says when the image is older than them.
- Provenance gains `runtime: {mode: container, engine, image_id}`. With the same image, the same raw file gives byte-identical outputs on every machine.

## Mounts

| Host | Container | Mode |
|---|---|---|
| the repository | `/labpipe` | read-write (as for native agents; the guard and permissions keep agents to notes and logs) |
| each raw root's data folder (`raw_roots[].source`, else `path`) | `/data/<root id>` | **read-only** (`:ro`, enforced by the kernel or the VM) |
| labpipe's credential store | `/home/labpipe/.claude`, `/home/labpipe/.codex` | named volume, never the user's own `~/.claude` / `~/.codex` |

- Inside, raw roots are found by id: `LABPIPE_ROOT_PATHS=lab=/data/lab` overrides the host paths of `config/local.yaml`. Manifest entries are keyed by root id and relative path, so host and container share one manifest.
- **SELinux (Fedora, Bazzite):** the container runs with `--security-opt label=disable`. The usual `:z` / `:Z` options would relabel the raw files (a metadata change on raw data) and are never used.
- Files are written as the user: Podman rootless with `--userns=keep-id`, Docker with `--user <uid>:<gid>`.
- With container mode, the raw folder on the host does not need a read-only view for the automatic runs. Interactive host sessions still read it directly: the guard and `protected_paths` protect it there, and on Linux the read-only view remains recommended.

## Agents inside the container
- The container is the boundary: only the mounts above are visible, and outgoing network is limited to the AI provider (below). Claude Code's and Codex's own sandboxes are not needed there.
- Claude Code: `.claude/unattended-container.json`, the same as `unattended.json` but without the sandbox requirement (Claude Code's sandbox needs namespaces a rootless container usually lacks; its documented "weaker nested sandbox" is tried while building and kept if it works). The guard hook runs as today (`LABPIPE_AGENT_MODE=unattended`).
- Codex: `codex exec` with its sandbox off inside the container (OpenAI documents this for externally sandboxed environments); guard hook as today.
- **Logins** (the user's subscription, no API key needed):
  - Claude Code: the user runs `claude setup-token` on the host (a one-year token); `labpipe container setup` stores it as a Podman/Docker secret, passed to the container as `CLAUDE_CODE_OAUTH_TOKEN`.
  - Codex: `labpipe container login codex` runs `codex login --device-auth` inside the container once (open a link, enter a code); the login stays in the labpipe volume.

## Interactive sessions and personal settings
- `labpipe shell [claude|codex]` starts the agent tool inside the running container, in `/labpipe`, with the raw data read-only and the same network limits. On every OS this is the safe way to talk to the agent.
- Skills, agents and settings for labpipe live in the repository (`.claude/`, `.codex/`, `.agents/skills/`), so they work on the host and in the container alike; develop them there.
- **Importing personal settings** (offered by `labpipe automation setup`, per harness): the user's own skills, agents, instructions (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) and settings are listed, and the ones the user picks are **copied** into the container's labpipe home. Never copied: logins and tokens. Flagged before copying: MCP servers and plugins that need network (blocked by the firewall unless their hosts are added) and hooks that call programs on the host. `labpipe container import` repeats it later.
- Imported settings apply to interactive sessions in the container only. Automatic runs keep using the repository's settings alone (`--setting-sources project` for Claude Code, the same for Codex), so their results do not depend on anyone's personal setup.
- Host apps with the raw folder: there the raw folder is writable (no read-only view any more), and only the guard and `protected_paths` protect it. The README says so and recommends `labpipe shell` for work that touches raw data.

## Updates
What can change, and how each change reaches a machine:

| What | Pinned in | Who proposes updates |
|---|---|---|
| labpipe's code | git | the maintainer (commits) |
| Python libraries | `pyproject.toml` (exact versions) | Dependabot (pip) |
| Base image | `container/Dockerfile` (digest) | Dependabot (docker) |
| Codex CLI | `container/package.json` + lock file | Dependabot (npm) |
| Claude Code | `container/versions.env` | a weekly GitHub workflow (Claude Code is no longer published to npm as its main channel, so Dependabot cannot see it): compares with the latest release and opens a pull request |
| GitHub Actions | workflow files | Dependabot (github-actions) |

- The container file is named `Dockerfile` (Podman reads it too): Dependabot does not recognise `Containerfile`.
- Every update is a pull request; CI builds the image and runs the tests in it (the agent tools only as far as `--version`; CI has no AI logins). Nothing updates on its own on a user's machine; the agent tools' self-updates are off.
- On a machine: `labpipe update` = `git pull --ff-only`, rebuild the image if the container files changed, restart the container, show what changed. The watcher notices when the repository on GitHub is ahead (a check once a day; needs `github.com` in the allowed hosts) and says so on the notifications page: "update available: run `labpipe update`". The user decides when.
- After an update, analyses become out of date (stale) as today, and the notification says so; nothing is redone without the user.

## Network
- Outgoing connections allowed only to the hosts of the AI tool in use and `github.com` (update check) (`config/pipeline.yaml` → `container.allowed_hosts`, defaults for Claude Code and Codex). Enforced by firewall rules set when the container starts, before labpipe drops to an unprivileged user (the approach of Anthropic's reference dev container).
- The notifications page is published on `127.0.0.1` only, so other computers cannot reach it.

## Notifications
1. **Browser page** (every OS): `http://localhost:8765` (`container.port`) shows the report and a "needs you" list (the same news as the notifications: not recognised, needs a decision, failed, out of date, finished runs, with what to say to the agent). The page asks once for permission to show notifications and then shows each new item as a desktop notification while the tab is open (pin it). Served by Python's standard library (no new dependency), read-only (no buttons that change anything). The messages are kept in `logs/notifications.jsonl` (also written in native mode).
2. **Desktop pop-ups on Linux**, as today: a user service on the host runs `xdg-dbus-proxy`, which lets the container talk to the desktop's notification service and nothing else on the desktop bus (giving the container the whole bus would let it start programs on the host). On Windows and macOS only the browser page.

## Starting and stopping
- `labpipe automation setup`: detects Podman or Docker, builds the image, stores the logins, offers the settings import, shows what it will install, and installs after the user confirms. If the old native automation is installed, it removes it (with the user's OK).
- Linux with Podman: a Quadlet file (`~/.config/containers/systemd/labpipe.container`), so systemd starts the container at login like the native watcher; plus the `xdg-dbus-proxy` user service.
- Docker, and Podman on Windows/macOS: restart policy `unless-stopped`; Docker Desktop / Podman Desktop must start at login (setup checks and says how).
- The nightly pass becomes part of the watcher (one process, no timer).
- `labpipe container status | logs | stop | start | import`, `labpipe shell`, `labpipe update`; `labpipe doctor` checks: engine present, container running, image up to date, last run, notifications page reachable.

## Considered, not chosen
- Repository read-only in the container with only `analysis/` and `logs/` writable: `manifest.json` sits in the repository root and is replaced atomically, which a single-file mount does not allow. Possible later by moving the manifest into a state folder.
- Using the host's Claude Code / Codex programs: they are built for the host system; the container has its own Linux copies at fixed versions.
- Sharing the user's `~/.claude` / `~/.codex`: would bring personal settings and plugins into automatic runs, and on macOS the login is in the keychain.
- ntfy, email, chat platforms (user's decision, 2026-10-19).
- Keeping native automation next to the container: two ways to maintain and test (user's decision, 2026-10-19).
- A third-party base image with the agent tools preinstalled: less control over versions.

## Testing
- Linux with Podman and Docker on this machine and in CI (Ubuntu): build, start, read-only raw mount (a write must fail), firewall (a non-allowed host must fail), a full pass with a fake agent tool, files owned by the user, notifications page, pop-ups through the proxy (here).
- Windows and macOS: Podman Desktop / Docker Desktop on a real machine (to be arranged); CI cannot run Linux containers on its Windows machines.

## Decisions
- 2026-10-19, user: notifications by browser page (port 8765) and Linux pop-ups; no ntfy, email or chat. Containers replace native automation. Personal settings may be imported per harness. Updates proposed by Dependabot.

## Order of work
1. Image, mounts, firewall, `labpipe shell`, automatic runs in the container (Linux, Podman and Docker).
2. Notifications page and Linux pop-ups.
3. Settings import; `labpipe update`; Dependabot and the Claude Code version workflow.
4. Retire native automation (code, docs, doctor checks) once 1–3 run on this machine.
5. Windows and macOS on a real machine.

## To verify while building
- Claude Code's weaker nested sandbox in rootless Podman, and its exact setting name.
- The hosts Claude Code and Codex need (login refresh, telemetry that can be switched off).
- Podman Desktop on Windows: whether the machine starts at login, and how fast reads from `C:\` are for hashing large files.
