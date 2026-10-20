"""Start the project's container by itself at login (host only; the standard library).

Automatic processing runs only in the container (docs/design/container.md). The container
is started with `--restart unless-stopped`, so the engine brings it back by itself:

* Podman on Linux: the user unit `podman-restart.service` (shipped with Podman, off by
  default) runs `podman start --all --filter should-start-on-boot=true` at login. Starting
  without logging in needs `loginctl enable-linger $USER`, an admin-level step that is
  shown, never run.
* Docker: the Docker daemon restarts the container when it starts (Docker Desktop: turn on
  "Start Docker Desktop when you sign in").
* Podman on macOS and Windows runs in a podman machine; its autostart is untested.

Also here: the old native units (labpipe-watch.service, labpipe-nightly.service/.timer) that
earlier versions installed. They are removed only after the user's yes. The pop-up proxy
unit (labpipe-dbus-proxy.service) belongs to container pop-ups and is never touched.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Callable

PODMAN_UNIT = "podman-restart.service"
OLD_UNITS = ("labpipe-watch.service", "labpipe-nightly.service", "labpipe-nightly.timer")
LINGER = "loginctl enable-linger $USER"


def kind(engine: str, system: str | None = None) -> str:
    """podman-linux | docker | podman-machine (Podman on macOS or Windows)."""
    if engine == "docker":
        return "docker"
    return "podman-linux" if (system or platform.system()) == "Linux" else "podman-machine"


def _systemctl(*words: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *words], capture_output=True, text=True)


def podman_restart_enabled() -> bool | None:
    """Is podman-restart.service enabled for this user? None if systemctl cannot say."""
    try:
        result = _systemctl("is-enabled", PODMAN_UNIT)
    except OSError:
        return None
    state = result.stdout.strip()
    if state == "enabled":
        return True
    return False if state else None


def enable_podman_restart() -> tuple[bool, str]:
    try:
        result = _systemctl("enable", PODMAN_UNIT)
    except OSError as exc:
        return False, str(exc)
    return result.returncode == 0, result.stderr.strip()


def check(engine: str, system: str | None = None) -> tuple[str, str, str | None]:
    """For doctor: (status, message, next step)."""
    what = kind(engine, system)
    if what == "docker":
        return ("ok", "Docker restarts the container when Docker starts (Docker Desktop: "
                "'Start Docker Desktop when you sign in')", None)
    if what == "podman-machine":
        return ("warn", "autostart of the container on macOS/Windows is untested: Podman runs "
                "in a podman machine, which must start at login",
                "labpipe automation setup shows the guidance")
    enabled = podman_restart_enabled()
    if enabled:
        return ("ok", f"{PODMAN_UNIT} is enabled: the container starts when you log in", None)
    return ("warn", "the container does not start by itself at login",
            "labpipe automation setup")


def guidance(engine: str, system: str | None = None) -> list[str]:
    """What to tell the user about autostart for this engine and system."""
    what = kind(engine, system)
    if what == "docker":
        return ["Docker restarts the container by itself whenever Docker starts. Docker Desktop: "
                "turn on 'Start Docker Desktop when you sign in' (Settings > General). "
                "Nothing else to do."]
    if what == "podman-machine":
        return ["Podman runs in a podman machine here. The container has the restart policy "
                "'unless-stopped', so it comes back when the machine starts: start the machine "
                "at login (Podman Desktop: 'Start at login', or `podman machine start` from a "
                "login task). UNTESTED on macOS and Windows: tell us if it works."]
    return [f"Podman: `systemctl --user enable {PODMAN_UNIT}` makes the container start at "
            f"login. To start it at boot without logging in, an administrator runs "
            f"`{LINGER}` (labpipe never runs that)."]


def offer(engine: str, yes: Callable[[str], bool], say: Callable[[str], None] = print,
          system: str | None = None) -> str:
    """Show the autostart steps for this engine; Podman on Linux: enable after a yes.
    Returns enabled | already | declined | failed | guidance."""
    what = kind(engine, system)
    for line in guidance(engine, system):
        say(line)
    if what != "podman-linux":
        return "guidance"
    if podman_restart_enabled():
        say(f"{PODMAN_UNIT} is already enabled.")
        return "already"
    say(f"\nWill run: systemctl --user enable {PODMAN_UNIT}")
    if not yes(f"Enable {PODMAN_UNIT} now?"):
        say(f"Not enabled. Later: labpipe automation setup, or systemctl --user enable "
            f"{PODMAN_UNIT}")
        return "declined"
    ok, detail = enable_podman_restart()
    if not ok:
        say(f"It did not work ({detail or 'systemctl failed'}). Run yourself: "
            f"systemctl --user enable {PODMAN_UNIT}")
        return "failed"
    say(f"Enabled. To start without logging in, an administrator runs: {LINGER}")
    return "enabled"


# ---- the old native units ------------------------------------------------------------

def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def old_units() -> list[Path]:
    """The old native unit files that are installed."""
    return [path for name in OLD_UNITS if (path := unit_dir() / name).exists()]


def removal_commands(units: list[Path] | None = None) -> list[str]:
    units = old_units() if units is None else units
    if not units:
        return []
    names = [path.name for path in units]
    return [f"systemctl --user disable --now {' '.join(names)}",
            f"rm {' '.join('~/.config/systemd/user/' + name for name in names)}",
            "systemctl --user daemon-reload"]


def remove_old_units(units: list[Path]) -> bool:
    """Disable, delete and reload; True if every systemctl call worked."""
    ok = True
    try:
        ok &= _systemctl("disable", "--now", *[path.name for path in units]).returncode == 0
        for path in units:
            path.unlink(missing_ok=True)
        ok &= _systemctl("daemon-reload").returncode == 0
    except OSError:
        return False
    return ok
