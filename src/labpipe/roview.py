"""`labpipe readonly-view --source PATH`: the commands that give labpipe a read-only
view of the raw data on this OS, with the paths filled in. Prints only; the user
runs them (they need admin rights). See docs/SAFETY.md for the why.
"""

from __future__ import annotations

import os
import platform
import shlex
from pathlib import Path
from typing import Any


def _fstab_escape(path: str) -> str:
    """fstab fields use octal escapes for whitespace and backslashes."""
    return (path.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")
            .replace("\n", "\\012"))


def _mount_point_of(path: str) -> str:
    best = "/"
    try:
        with open("/proc/self/mounts", encoding="utf-8") as stream:
            for line in stream:
                mount = line.split()[1].replace("\\040", " ")
                if (path == mount or path.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
                    best = mount
    except OSError:
        pass
    return best


def _ps(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


def _linux(source: str, target: str) -> dict[str, Any]:
    s, t = shlex.quote(source), shlex.quote(target)
    fstab = (f"{_fstab_escape(source)}  {_fstab_escape(target)}  none  "
             f"bind,ro,nofail,x-systemd.requires-mounts-for={_fstab_escape(_mount_point_of(source))}  0  0")
    return {"steps": [
        {"title": "Create the read-only view (until the next reboot)", "run_as": "admin (sudo)",
         "commands": [f"sudo mkdir -p {t}", f"sudo mount --bind {s} {t}",
                      f"sudo mount -o remount,bind,ro {t}"],
         "explain": "The same folder appears a second time at the target; that copy refuses "
                    "every write. New files written into the source appear in the view at once."},
        {"title": "Check it", "run_as": "you",
         "commands": [f"findmnt {t}", f"ls {t}", f"touch {t}/labpipe-write-test"],
         "explain": "findmnt OPTIONS must start with 'ro'; the touch must FAIL with "
                    "'Read-only file system'."},
        {"title": "Make it permanent", "run_as": "admin (sudo)",
         "commands": ["sudoedit /etc/fstab", "# add this line:", fstab],
         "explain": "nofail: the computer still boots if the source is missing. "
                    "requires-mounts-for: mount only after the source's filesystem is up."},
        {"title": "Test the fstab line without rebooting", "run_as": "admin (sudo)",
         "commands": ["sudo systemctl daemon-reload", f"sudo umount {t}", f"sudo mount {t}",
                      f"findmnt {t}"], "explain": "Again OPTIONS must start with 'ro'."},
    ], "alternative": {
        "title": "Without admin rights after installation: bindfs (FUSE)",
        "commands": [f"mkdir -p {shlex.quote(str(Path.home() / 'labdata-ro'))}",
                     f"bindfs -r {s} {shlex.quote(str(Path.home() / 'labdata-ro'))}"],
        "explain": "Install bindfs once from your distribution. The view lasts until logout; "
                   "start it at login (e.g. a systemd user service) to keep it."}, "tested": True}


def _macos(source: str, target: str) -> dict[str, Any]:
    s, t = shlex.quote(source), shlex.quote(target)
    return {"steps": [
        {"title": "Install macFUSE and bindfs (once)", "run_as": "admin",
         "commands": ["brew install --cask macfuse", "brew install gromgit/fuse/bindfs-mac"],
         "explain": "macOS has no bind mounts; bindfs provides a read-only mirror. macFUSE "
                    "needs a one-time approval in System Settings → Privacy & Security."},
        {"title": "Create the read-only view", "run_as": "you",
         "commands": [f"mkdir -p {t}", f"bindfs -r {s} {t}"], "explain": ""},
        {"title": "Check it", "run_as": "you",
         "commands": [f"ls {t}", f"touch {t}/labpipe-write-test"],
         "explain": "The touch must FAIL."},
        {"title": "Make it permanent", "run_as": "you",
         "commands": [f"# run `bindfs -r {s} {t}` at login (login item or launchd agent)"],
         "explain": ""},
    ], "tested": False}


def _windows(source: str, share: str) -> dict[str, Any]:
    return {"steps": [
        {"title": "Share the folder read-only", "run_as": "administrator PowerShell",
         "commands": [f"New-SmbShare -Name {share} -Path {_ps(source)} -ReadAccess \"$env:USERNAME\""],
         "explain": "labpipe reads through the share, which grants read access only; the "
                    "original folder stays writable for you (and a sync client, if you use one)."},
        {"title": "Check it", "run_as": "you",
         "commands": [f"Get-ChildItem \\\\localhost\\{share}",
                      f"New-Item -Path \\\\localhost\\{share}\\labpipe-write-test.txt -ItemType File"],
         "explain": "The New-Item must FAIL with 'access denied'. If the folder is synced "
                    "with files-on-demand, mark it 'always keep on this device'."},
    ], "alternative": {"title": "WSL2", "commands": [],
                       "explain": "Inside WSL2 the Linux instructions apply; the Windows folder "
                                  "is under /mnt/<drive>/..."}, "tested": False}


def commands(source: str, target: str | None = None, system: str | None = None) -> dict[str, Any]:
    system = (system or platform.system()).lower()
    system = {"darwin": "macos"}.get(system, system)
    here = {"darwin": "macos"}.get(platform.system().lower(), platform.system().lower())
    if system == here:  # only resolve paths that belong to this computer
        source = str(Path(os.path.expanduser(source)).resolve())
    if system == "linux":
        target = target or "/srv/labdata-ro"
        plan = _linux(source, target)
        raw_path = target
    elif system == "macos":
        target = target or str(Path.home() / "labdata-ro")
        plan = _macos(source, target)
        raw_path = target
    elif system == "windows":
        share = target or "labdata-ro"
        plan = _windows(source, share)
        raw_path = f"//localhost/{share}"
    else:
        raise ValueError(f"unsupported system {system!r} (linux, macos, windows)")
    exists = Path(source).is_dir() if system == here else None
    return {"system": system, "source": source, "source_exists": exists, "view": raw_path,
            **plan,
            "then": {"config/local.yaml": {"raw_roots": [{"id": "lab", "path": raw_path}],
                                           "protected_paths": [source.replace("\\", "/")]},
                     "check": "labpipe doctor"},
            "note": "labpipe never runs these commands; the user does."}
