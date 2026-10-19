# Container mode for automatic processing

Status: DRAFT, for approval. No code until approved.

## Goal
Run the automatic part of labpipe (watcher, nightly pass, AI runs) in a container (Podman or Docker), the same way on Linux, macOS and Windows. The container replaces the OS-specific parts of native automation: the read-only view (`sudo mount` / SMB share), the agent tool's own sandbox, and systemd / Task Scheduler. Native automation stays available; a machine uses one or the other.

Interactive work does not change: the user keeps using their installed Claude Code or Codex on the host, in the same repository folder.

## What runs where

| | Host | Container |
|---|---|---|
| Interactive agent (Claude Code, Codex) | yes, as today | no |
| Watcher, nightly pass, code-only analyses | no | `labpipe automation watch` |
| AI runs (supervisor, analysts) | no | `claude -p …` / `codex exec …`, Linux builds inside the image |
| Report and notifications page | browser tab | `labpipe serve` (port published on 127.0.0.1 only) |
| Desktop pop-ups (Linux) | `xdg-dbus-proxy` (notifications only) | `notify-send` through the proxy |

## Image
- `container/Containerfile` in the repository; the user's machine builds it (`labpipe container build`); nothing is pulled from a registry except the base image.
- Base image pinned by digest (Debian-based `python:3.14-slim`). Inside: the Python dependencies with exact versions (as in `pyproject.toml`, plus Pillow pinned), fonts for the ACS plot style, git, Node.js, Claude Code and Codex CLIs at fixed versions, `notify-send`. Auto-updates of the CLIs are off; a version change is a commit.
- The **code is not in the image**: the repository is mounted and installed in development mode at start. `git pull` on the host takes effect as today (the watcher restarts on a new code version). Only a change of the Containerfile needs a rebuild; `labpipe doctor` says when the image is older than the Containerfile.
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

## Network
- Outgoing connections allowed only to the hosts of the AI tool in use (`config/pipeline.yaml` → `container.allowed_hosts`, defaults for Claude Code and Codex). Enforced by firewall rules set when the container starts, before labpipe drops to an unprivileged user (the approach of Anthropic's reference dev container).
- The notifications page is published on `127.0.0.1` only, so other computers cannot reach it.

## Notifications
1. **Browser page** (every OS): `http://localhost:8765` (port configurable) shows the report and a "needs you" list (the same news as the notifications: not recognised, needs a decision, failed, out of date, finished runs, with what to say to the agent). The page asks once for permission to show notifications and then shows each new item as a desktop notification while the tab is open (pin it). Served by Python's standard library (no new dependency), read-only (no buttons that change anything). The messages are kept in `logs/notifications.jsonl` (also written in native mode).
2. **Desktop pop-ups on Linux**, as today: a user service on the host runs `xdg-dbus-proxy`, which lets the container talk to the desktop's notification service and nothing else on the desktop bus (giving the container the whole bus would let it start programs on the host). On Windows and macOS only the browser page.

## Starting and stopping
- `labpipe automation setup` asks: native or container. Container mode then: detects Podman or Docker, builds the image, stores the logins, shows what it will install, and installs after the user confirms. If native automation is installed, it offers to remove it (and the other way round).
- Linux with Podman: a Quadlet file (`~/.config/containers/systemd/labpipe.container`), so systemd starts the container at login like the native watcher; plus the `xdg-dbus-proxy` user service.
- Docker, and Podman on Windows/macOS: restart policy `unless-stopped`; Docker Desktop / Podman Desktop must start at login (setup checks and says how).
- The nightly pass becomes part of the watcher (one process, no timer).
- `labpipe container status | logs | stop | start`; `labpipe doctor` checks: engine present, container running, image up to date, last run, notifications page reachable.

## Considered, not chosen
- Repository read-only in the container with only `analysis/` and `logs/` writable: `manifest.json` sits in the repository root and is replaced atomically, which a single-file mount does not allow. Possible later by moving the manifest into a state folder.
- Using the host's Claude Code / Codex programs: they are built for the host system; the container has its own Linux copies at fixed versions.
- Sharing the user's `~/.claude` / `~/.codex`: would bring personal settings and plugins into automatic runs, and on macOS the login is in the keychain.
- ntfy, email, chat platforms (user's decision, 2026-10-19).
- A third-party base image with the agent tools preinstalled: less control over versions.

## Testing
- Linux with Podman and Docker on this machine and in CI (Ubuntu): build, start, read-only raw mount (a write must fail), firewall (a non-allowed host must fail), a full pass with a fake agent tool, files owned by the user, notifications page, pop-ups through the proxy (here).
- Windows and macOS: Podman Desktop / Docker Desktop on a real machine (to be arranged); CI cannot run Linux containers on its Windows machines.

## Open questions for the user
- Port for the notifications page (default 8765)?
- Should the container mode replace native automation on this machine once it works, or run only on other machines?

## To verify while building
- Claude Code's weaker nested sandbox in rootless Podman, and its exact setting name.
- The hosts Claude Code and Codex need (login refresh, telemetry that can be switched off).
- Podman Desktop on Windows: whether the machine starts at login, and how fast reads from `C:\` are for hashing large files.
