"""Packages of a lab method that labpipe's image does not have: `lab/requirements.txt`.

One line per package, `name==version` (a `#` starts a comment). Nothing else is accepted: no
URLs, no `-e`, no `-r`, no options, no ranges, so the file says exactly which versions made a
result. `labpipe container build` installs the file in a layer on top of labpipe's image, for
this project only (`labpipe.container.build_lab`); in native mode the user installs it into
labpipe's own environment with the command `install_command` prints (labpipe never runs it).
Everything here is plain functions: no engine, no network.
"""

from __future__ import annotations

import hashlib
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

FILE = "lab/requirements.txt"
NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
VERSION = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._!+]*[A-Za-z0-9])?")


class LabPackagesError(ValueError):
    """The file is not in the allowed form (the message says which line and why)."""


def path(project: Path | str) -> Path:
    return Path(project) / FILE


def text(project: Path | str) -> str | None:
    """The file's text, or None when the project has none (then nothing changes)."""
    try:
        return path(project).read_text(encoding="utf-8")
    except OSError:
        return None


def parse(content: str) -> list[tuple[str, str]]:
    """[(name, version)] of every line; LabPackagesError for any line that is not
    `name==version` (blank lines and comments are fine)."""
    found, seen = [], set()
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        where = f"{FILE} line {number} ({line[:60]!r})"
        if line.startswith("-"):
            raise LabPackagesError(f"{where}: options such as -e, -r or --index-url are not "
                                   "allowed; list `name==version` only")
        if "://" in line or "@" in line or "/" in line or "\\" in line or line.startswith("."):
            raise LabPackagesError(f"{where}: URLs, paths and `name @ url` are not allowed; "
                                   "list `name==version` from the package index only")
        name, equals, pinned = (part.strip() for part in line.partition("=="))
        if not equals or "=" in pinned or not NAME.fullmatch(name) or \
                not VERSION.fullmatch(pinned):
            raise LabPackagesError(f"{where}: write one exact version, `name==version` (no "
                                   "ranges, extras, markers or wildcards)")
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            raise LabPackagesError(f"{where}: {name} is listed twice")
        seen.add(key)
        found.append((name, pinned))
    return found


def listed(project: Path | str) -> list[tuple[str, str]]:
    """The packages of the project's file; [] without a file. Raises LabPackagesError."""
    content = text(project)
    return parse(content) if content is not None else []


def sha256(project: Path | str) -> str | None:
    """Hash of the file's bytes (None without a file)."""
    try:
        return hashlib.sha256(path(project).read_bytes()).hexdigest()
    except OSError:
        return None


def installed(project: Path | str) -> dict[str, str | None]:
    """{name: installed version or None} for the listed packages, in this environment. A file
    that does not parse gives {} (provenance must not fail on it; `methods check` says why)."""
    try:
        names = [name for name, _ in listed(project)]
    except LabPackagesError:
        return {}
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def missing(project: Path | str) -> list[str]:
    """The listed packages that are not installed here at the listed version, as
    `name==version (installed: x | not installed)`."""
    have = installed(project)
    return [f"{name}=={wanted} (" + (f"installed: {have[name]}" if have.get(name)
                                      else "not installed") + ")"
            for name, wanted in listed(project) if have.get(name) != wanted]


def install_command(project: Path | str) -> str:
    """The exact command that installs the file into THIS environment (labpipe's own). The
    user runs it; labpipe never does."""
    return f'"{sys.executable}" -m pip install -r "{path(project)}"'


def check(project: Path | str) -> tuple[bool, str]:
    """(ok, detail) for `labpipe methods check` and doctor, on the host, without building
    anything: the file is in the allowed form. A project without the file is fine."""
    try:
        packages = listed(project)
    except LabPackagesError as exc:
        return False, str(exc)
    if text(project) is None:
        return True, f"no {FILE}: lab methods use what labpipe's image has"
    return True, f"{FILE}: " + (", ".join(f"{n}=={v}" for n, v in packages) or "no packages")


def check_result(project: Path | str, lab_method: bool) -> tuple[bool, str]:
    """`labpipe methods check`'s "lab packages" line: (ok, detail). The file must be in the
    allowed form; for a lab method its packages must also be installed here (natively: the
    command that installs them; in the container: build the layer)."""
    ok, detail = check(project)
    gone = missing(project) if ok and lab_method else []
    if not gone:
        return ok, detail
    from labpipe.safety import in_container
    return False, (f"not installed here: {'; '.join(gone)}; " + (
        "on the host: labpipe container build, then labpipe container start --replace"
        if in_container() else f"the user runs: {install_command(project)}"))


def problem(project: Path | str, *, host_container: bool, in_container: bool
            ) -> tuple[str, str, str | None] | None:
    """What doctor says about the packages: (status, message, next) or None when all is well
    (or there is no file). `host_container`: container mode seen from the host (the layer is
    checked by `labpipe container status`, not here)."""
    ok, detail = check(project)
    if not ok:
        return "todo", detail, f"fix {FILE} (docs/ADDING_A_METHOD.md, Packages)"
    if text(project) is None or host_container:
        return None
    gone = missing(project)
    if not gone:
        return None
    message = f"lab methods need packages that are not installed: {'; '.join(gone)}"
    if in_container:
        return "todo", message, ("the user runs `labpipe container build`, then "
                                 "`labpipe container start --replace` (on the host)")
    return "todo", message, f"the user runs: {install_command(project)}"


def containerfile(base_tag: str) -> str:
    """The layer on top of labpipe's image. Installs with the image's own python, the base's
    installed versions (`pip freeze`) as constraints (a listed package cannot change them: pip
    stops instead), then `pip check`. The user stays what the base has (root until the entrypoint drops to
    the host user). It needs the network at build time only, like the base's own installs
    (the container's allow-list is for the running container, not for the build)."""
    # The image's ENV points at the in-container proxy, which only runs once the container
    # has started: the build must reach the package index directly, as the base's own did.
    py = ("env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy "
          "/usr/local/bin/python -m pip")
    return (f"FROM {base_tag}\n"
            "COPY requirements.txt /tmp/lab-requirements.txt\n"
            f"RUN {py} freeze > /tmp/base-constraints.txt \\\n"
            f" && {py} install --no-cache-dir --root-user-action=ignore -c /tmp/base-constraints.txt "
            "-r /tmp/lab-requirements.txt \\\n"
            f" && {py} check && rm /tmp/base-constraints.txt /tmp/lab-requirements.txt\n")


def lab_repository(cfg: dict) -> str:
    """`localhost/labpipe-<slug>-lab` for a project container named `labpipe-<slug>`."""
    name = str(cfg.get("name") or "labpipe")
    suffix = name[len("labpipe-"):] if name.startswith("labpipe-") else ""
    return "-".join(part for part in (str(cfg["image"]), suffix, "lab") if part).lower()


def lab_hash(project: Path | str, base_id: str) -> str:
    """12 hex chars over the file's bytes and the base image's id: a changed file or a new
    base image gives another tag."""
    digest = hashlib.sha256()
    digest.update((sha256(project) or "").encode() + b"\0" + base_id.encode())
    return digest.hexdigest()[:12]
