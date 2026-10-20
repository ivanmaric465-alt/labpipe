"""Settings migrations: a release that renames a settings key, moves a section or changes what
a value means ships a migration (docs/settings/migrations.md, docs/design/install.md).

    labpipe migrate            what would change in the project's settings files (changes nothing)
    labpipe migrate --apply    changes them, after a question, with a backup in backups/migrate-<UTC>/

A migration names the files it concerns (globs relative to the project), the key paths it
replaces and what to do: `rename` a key, `move` it to another section, `transform` a value.
Whether it is pending is decided from the file's content (does applying it change anything?),
so nothing records which migrations were applied. A transform's function must return a value
that is already new unchanged.

* Comments are kept: a rename of a key and a transform of a plain value edit the text of the
  file in place, changing only the key's name or the value. Anything else (a move) is done on
  the parsed data and written back with yaml.safe_dump, which drops the comments: the plan says so
  (`comments_kept: False`). After every textual edit the file is parsed again and must equal what
  the operation does to the parsed data, else the safe_dump way is taken.
* Old keys keep working for one release: the loaders of the project's settings files pass what
  they read through `upgrade_in_memory`, which applies the migrations of the newest release (and
  "Unreleased") to the parsed data and warns on stderr, once per file and process. Unknown keys
  are still refused by the loaders afterwards.
* If a file has both the old and the new key, the new one wins and the old one is dropped.
* Paths are dotted key names; `*` stands for any one key (`samples.*.xrd.dpi`).

Host side: Python 3.10, standard library and PyYAML only.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

from labpipe import __version__

UNRELEASED = "Unreleased"


class MigrationError(RuntimeError):
    pass


# ---- the migrations ----------------------------------------------------------------------

@dataclass(frozen=True)
class Op:
    """One operation of a migration; made with rename(), move() or transform()."""
    kind: str                                   # rename | move | remove | transform
    old: str                                    # dotted key path ("*" = any key)
    new: str | None = None                      # rename, move: the new path
    fn: Callable[[Any], Any] | None = None      # transform: old value -> new value
    note: str = ""                              # transform, remove: what changed, in plain words


def _parts(path: str) -> tuple[str, ...]:
    return tuple(path.split("."))


def rename(old_path: str, new_path: str) -> Op:
    """A key gets another name in the same place: rename("plots.dpi", "plots.resolution")."""
    old, new = _parts(old_path), _parts(new_path)
    if old[:-1] != new[:-1] or old[-1] == new[-1] or "*" == new[-1]:
        raise ValueError(f"rename changes only the last name ({old_path} -> {new_path}); "
                         "use move() for another section")
    return Op("rename", old_path, new_path)


def move(old_path: str, new_path: str) -> Op:
    """A key goes to another section: move("report.dpi", "plots.dpi"). A `*` of the new path
    takes the key the same-numbered `*` of the old path matched."""
    if _parts(new_path).count("*") > _parts(old_path).count("*"):
        raise ValueError(f"move: {new_path} has more '*' than {old_path}")
    return Op("move", old_path, new_path)


def remove(path: str, note: str = "") -> Op:
    """A key is not used any more and is deleted from the files (with what it holds)."""
    return Op("remove", path, note=note)


def transform(path: str, fn: Callable[[Any], Any], note: str) -> Op:
    """A value changed its meaning: fn(old value) is the new value (and returns a value that is
    already new unchanged). `note` says what changed, e.g. "now in nm, was in Å"."""
    return Op("transform", path, fn=fn, note=note)


@dataclass(frozen=True)
class Migration:
    id: str                          # "0.3.0-plots-dpi"
    release: str                     # "v0.3.0", or "Unreleased" until the release is made
    description: str                 # plain words, shown to the user
    files: tuple[str, ...]           # globs relative to the project
    old_keys: tuple[str, ...] = ()   # key paths it replaces (CI check, warnings); default: the
    #                                  old paths of its renames, moves and removals
    operations: tuple[Op, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "operations", tuple(self.operations))
        keys = tuple(self.old_keys) or tuple(op.old for op in self.operations
                                             if op.kind in ("rename", "move", "remove"))
        object.__setattr__(self, "old_keys", keys)


def method_setting(id: str, release: str, description: str, method: str, *operations: Op
                   ) -> list[Migration]:
    """The migrations of a setting of one method (paths relative to the method's section, as in
    config/<method>.yaml): the same change in lab.yaml, samples.yaml (samples and presets) and
    config/measurements/<method>/."""
    def prefixed(prefix: str) -> tuple[Op, ...]:
        return tuple(replace(op, old=prefix + op.old, new=op.new and prefix + op.new)
                     for op in operations)
    return [
        Migration(id, release, description, ("config/lab.yaml",),
                  operations=prefixed(f"{method}.")),
        Migration(f"{id}-samples", release, description, ("config/samples.yaml",),
                  operations=prefixed(f"samples.*.{method}.") + prefixed(f"presets.*.{method}.")),
        Migration(f"{id}-measurements", release, description,
                  (f"config/measurements/{method}/*.yaml",), operations=tuple(operations)),
    ]


# Every settings change of a release is listed here (with a test in tests/test_migrations.py);
# scripts/check_settings_migrations.py makes CI refuse a removed key without one.
MIGRATIONS: list[Migration] = []


# ---- the operations on parsed data -------------------------------------------------------

def _found(data: Any, parts: tuple[str, ...]) -> list[tuple[tuple, tuple]]:
    """Where `parts` is present in `data`: (the keys of the way there, what the `*` matched)."""
    found: list[tuple[tuple, tuple]] = []

    def walk(node: Any, i: int, path: tuple, stars: tuple) -> None:
        if i == len(parts):
            found.append((path, stars))
        elif isinstance(node, dict):
            if parts[i] == "*":
                for key in list(node):
                    walk(node[key], i + 1, path + (key,), stars + (key,))
            else:
                for key in list(node):
                    if str(key) == parts[i]:
                        walk(node[key], i + 1, path + (key,), stars)
                        break
    walk(data, 0, (), ())
    return found


def _at(data: Any, path: tuple) -> Any:
    for key in path:
        data = data[key]
    return data


def _prune(data: dict, path: tuple) -> None:
    """Delete the sections on the way to `path` that are empty now (not the top level)."""
    for depth in range(len(path) - 1, 0, -1):
        if _at(data, path[:depth]):
            break
        del _at(data, path[:depth - 1])[path[depth - 1]]


def _apply_op(data: dict, op: Op) -> int:
    """Do `op` on `data` in place; the number of places changed."""
    changed = 0
    for path, stars in _found(data, _parts(op.old)):
        parent, key = _at(data, path[:-1]), path[-1]
        if op.kind == "transform":
            new = op.fn(parent[key])
            if new != parent[key]:
                parent[key] = new
                changed += 1
        elif op.kind == "remove":
            del parent[key]
            _prune(data, path)
            changed += 1
        elif op.kind == "rename":
            name = _parts(op.new)[-1]
            items = list(parent.items())
            parent.clear()
            for k, v in items:      # keeps the position; a key that is there already wins
                if k == key:
                    if not any(str(other) == name for other, _ in items):
                        parent[name] = v
                else:
                    parent[k] = v
            changed += 1
        else:
            stars = list(stars)
            target = [stars.pop(0) if p == "*" else p for p in _parts(op.new)]
            value = parent.pop(key)
            _prune(data, path)
            node = data
            for name in target[:-1]:
                node = node.setdefault(name, {})
                if not isinstance(node, dict):
                    raise MigrationError(f"cannot move {op.old} to {op.new}: "
                                         f"{'.'.join(target[:-1])} is not a section")
            node.setdefault(target[-1], value)
            changed += 1
    return changed


# ---- in-place text edits (comments stay) ---------------------------------------------------

_KEY = re.compile(r"""^(?P<indent> *)(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'"""
                  r"""|[^\s\-?\[\]{},#&*!|>'"%@`][^:#]*?|-[^\s:#][^:#]*?) *:(?=\s|$)""")
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")


@dataclass
class _Key:
    path: tuple | None      # the keys leading here; None inside a list
    line: int
    start: int              # the key's text in the line (with its quotes)
    end: int
    rest: int               # the value's text starts after here
    flow: bool = False      # inside a `{a: 1, b: 2}` on one line


def _flow_depth(text: str) -> int:
    """Open brackets minus closed ones on a line, ignoring quoted text and comments."""
    depth, quote, i = 0, None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1].isspace()):
            break
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth


_FLOW_KEY = re.compile(r"""(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s,{}\[\]:#"'][^,{}\[\]:]*?)"""
                       r""" *:(?=[ ,}]|$)""")


def _skip_flow(line: str, i: int) -> int | None:
    """The index after the bracket that closes the one at line[i], or None (not on this line)."""
    n, quote, j, depth = len(line), None, i, 0
    while j < n:
        c = line[j]
        if quote:
            if c == "\\" and quote == '"':
                j += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def _flow_map(line: str, i: int, path: tuple, number: int, out: list) -> int | None:
    """The keys of the `{...}` at line[i] (all on this line), added to `out`; the index after it."""
    n, i = len(line), i + 1
    while True:
        while i < n and line[i] in " ,":
            i += 1
        if i >= n:
            return None
        if line[i] == "}":
            return i + 1
        match = _FLOW_KEY.match(line, i)
        if not match:
            return None
        name = _unquote(match.group("key"))
        out.append(_Key(path + (name,), number, match.start("key"), match.end("key"),
                        match.end(), True))
        i = match.end()
        while i < n and line[i] == " ":
            i += 1
        if i < n and line[i] == "{":
            i = _flow_map(line, i, path + (name,), number, out)
        elif i < n and line[i] == "[":
            i = _skip_flow(line, i)
        elif i < n and line[i] in "\"'":
            span = _value_span(line, i, True)
            i = span[1] if span else None
        else:
            while i < n and line[i] not in ",}":
                i += 1
        if i is None:
            return None


def _unquote(key: str) -> str:
    if key[:1] == '"':
        return yaml.safe_load(key)
    return key[1:-1].replace("''", "'") if key[:1] == "'" else key


def _scan(lines: list[str]) -> list[_Key]:
    """The `key:` lines of a block-style YAML text, with the path of keys leading to each."""
    keys: list[_Key] = []
    stack: list[tuple[int, str | None]] = []    # (indent, key); None: a list item
    block_indent = None                         # inside a | or > scalar
    flow = 0                                    # inside a [ ] or { } spanning lines
    for number, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if flow > 0:
            flow += _flow_depth(line)
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not stripped or indent > block_indent:
                continue
            block_indent = None
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("--- ", "...")) or stripped in ("---", "..."):
            stack.clear()
            continue
        if stripped == "-" or stripped.startswith("- "):
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, None))
            continue
        match = _KEY.match(line)
        if not match:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        names = [name for _, name in stack]
        name = _unquote(match.group("key"))
        keys.append(_Key(None if None in names else tuple(names) + (name,), number,
                         match.start("key"), match.end("key"), match.end()))
        stack.append((indent, name))
        value = line[match.end():].strip()
        if value[:1] in ("|", ">"):
            block_indent = indent
        elif value[:1] in ("[", "{"):
            flow = max(0, _flow_depth(value))
            if value[:1] == "{" and not flow and keys[-1].path is not None:
                inner: list[_Key] = []
                if _flow_map(line, line.index("{", match.end()), keys[-1].path, number,
                             inner) is not None:
                    keys.extend(inner)
    return keys


def _same_path(path: tuple, parts: tuple[str, ...]) -> bool:
    return len(path) == len(parts) and all(p == "*" or p == q for p, q in zip(parts, path))


def _value_span(line: str, rest: int, flow: bool = False) -> tuple[int, int] | None:
    """Where the one-line plain or quoted value of a `key: value` line is (None: it is
    something else: a section, a list, a block scalar, an anchor, a multi-line string)."""
    start = rest
    while start < len(line) and line[start] in " \t":
        start += 1
    text = line[start:].rstrip("\r\n")
    if not text or text[0] in "#|>[{&*!%@`":
        return None
    if text[0] in "\"'":
        quote, i = text[0], 1
        while i < len(text):
            if quote == '"' and text[i] == "\\":
                i += 2
                continue
            if text[i] == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                return start, start + i + 1
            i += 1
        return None
    end = re.search(r"[,}]|\s#" if flow else r"\s#", text)
    return start, start + len((text[:end.start()] if end else text).rstrip())


def _scalar_text(new: Any, old_text: str) -> str | None:
    """The new value as YAML on one line, in the old one's quote style; None: not a scalar."""
    if isinstance(new, (dict, list)):
        return None
    if isinstance(new, str) and old_text[:1] == '"':
        return json.dumps(new, ensure_ascii=False)
    if isinstance(new, str) and old_text[:1] == "'":
        return "'" + new.replace("'", "''") + "'"
    lines = yaml.safe_dump(new, default_flow_style=True, allow_unicode=True,
                           width=10 ** 6).splitlines()
    return lines[0] if lines and lines[1:] in ([], ["..."]) else None


def _edit_text(text: str, before: dict, after: dict, op: Op) -> str | None:
    """`text` with `op` done in place, or None when that cannot be done textually."""
    if op.kind == "move":
        return None
    lines = text.splitlines(keepends=True)
    parts = _parts(op.old)
    places = [k for k in _scan(lines) if k.path is not None and _same_path(k.path, parts)]
    found = _found(before, parts)
    if len(places) != len(found) or (op.kind == "remove" and any(k.flow for k in places)):
        return None
    if op.kind == "remove":
        for place in reversed(places):
            indent = len(lines[place.line]) - len(lines[place.line].lstrip(" "))
            end = place.line + 1
            while end < len(lines):
                text = lines[end].strip()
                if text and len(lines[end]) - len(lines[end].lstrip(" ")) <= indent:
                    break
                end += 1
            while end > place.line + 1 and not lines[end - 1].strip():
                end -= 1            # the blank lines before the next key stay
            del lines[place.line:end]
        return "".join(lines)
    if op.kind == "rename":
        name = _parts(op.new)[-1]
        if not _PLAIN_NAME.fullmatch(name) or yaml.safe_load(name) != name:
            return None
        for path, _ in found:       # the new name is there already: that is not a rename
            if any(str(k) == name for k in _at(before, path[:-1])):
                return None
        for place in places:
            line = lines[place.line]
            quote = line[place.start] if line[place.start] in "\"'" else ""
            lines[place.line] = line[:place.start] + quote + name + quote + line[place.end:]
        return "".join(lines)
    for place in places:            # transform
        line = lines[place.line]
        span = _value_span(line, place.rest, place.flow)
        try:
            new = _at(after, place.path)
            old = _at(before, place.path)
        except (KeyError, TypeError):
            return None
        if span is None:
            if new != old:
                return None
            continue
        if new == old:
            continue
        written = _scalar_text(new, line[span[0]:span[1]])
        if written is None:
            return None
        tail = line[span[1]:]
        gap = len(tail) - len(tail.lstrip(" "))
        if tail.lstrip(" ").startswith("#"):      # the comment stays in its column
            tail = " " * max(1, gap - (len(written) - (span[1] - span[0]))) + tail.lstrip(" ")
        lines[place.line] = line[:span[0]] + written + tail
    return "".join(lines)


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)


def _parse(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


# ---- the plan ----------------------------------------------------------------------------

def _glob_match(path: str, pattern: str) -> bool:
    """Whole-path glob; `**` stands for any number of folders (also none)."""
    def match(parts: tuple[str, ...], pats: tuple[str, ...]) -> bool:
        if not pats:
            return not parts
        if pats[0] == "**":
            return any(match(parts[i:], pats[1:]) for i in range(len(parts) + 1))
        return bool(parts) and PurePosixPath(parts[0]).match(pats[0]) and match(parts[1:], pats[1:])
    return match(PurePosixPath(path).parts, PurePosixPath(pattern).parts)


def _upgrade_text(text: str, data: dict, migrations: list[Migration]
                  ) -> tuple[str, dict, bool, list[Migration]]:
    """(new text, new data, comments kept, the migrations that changed something)."""
    kept_text, current, applied, textual = text, data, [], True
    for migration in migrations:
        start = current
        for op in migration.operations:
            expected = copy.deepcopy(current)
            if not _apply_op(expected, op):
                continue
            edited = _edit_text(kept_text, current, expected, op) if textual else None
            if edited is not None and _parse(edited) == expected:
                kept_text = edited
            else:
                textual = False
            current = expected
        if current != start:
            applied.append(migration)
    if textual:
        return kept_text, current, True, applied
    return _dump(current), current, not re.search(r"(^|\s)#", text, re.M), applied


def plan(project: Path, migrations: list[Migration] | None = None) -> list[dict]:
    """One item per settings file of `project` that the migrations would change:
    {"file", "migrations": [{"id", "release", "description"}], "before", "after", "diff",
    "comments_kept"}. Changes nothing."""
    project = Path(project)
    migrations = MIGRATIONS if migrations is None else migrations
    per_file: dict[str, list[Migration]] = {}
    for migration in migrations:
        for pattern in migration.files:
            for path in project.glob(pattern):
                if path.is_file():
                    rel = path.relative_to(project).as_posix()
                    if migration not in per_file.setdefault(rel, []):
                        per_file[rel].append(migration)
    items = []
    for rel in sorted(per_file):
        try:
            text = (project / rel).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        data = _parse(text)
        if not isinstance(data, dict):
            continue
        after, _, kept, applied = _upgrade_text(text, data, per_file[rel])
        if not applied:
            continue
        diff = "\n".join(difflib.unified_diff(text.splitlines(), after.splitlines(),
                                              f"a/{rel}", f"b/{rel}", lineterm=""))
        items.append({"file": rel, "before": text, "after": after, "diff": diff,
                      "comments_kept": kept,
                      "migrations": [{"id": m.id, "release": m.release,
                                      "description": m.description} for m in applied]})
    return items


def _write_atomic(path: Path, text: str) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def apply(project: Path, planned: list[dict], backup_dir: Path) -> list[str]:
    """Write the planned files: each is first copied to backup_dir/<file> (an existing backup is
    never overwritten). Refuses, before writing anything, if a file changed since the plan was
    made. Returns the files written (posix, relative to the project)."""
    project, backup_dir = Path(project), Path(backup_dir)
    for item in planned:
        path = project / item["file"]
        try:
            now = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise MigrationError(f"{item['file']} cannot be read: {exc}") from exc
        if now != item["before"]:
            raise MigrationError(f"{item['file']} changed since the plan was made: "
                                 "run `labpipe migrate` again")
        if (backup_dir / item["file"]).exists():
            raise MigrationError(f"{backup_dir / item['file']} exists already; "
                                 "a backup is never overwritten")
    written = []
    for item in planned:
        path, backup = project / item["file"], backup_dir / item["file"]
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        _write_atomic(path, item["after"])
        written.append(item["file"])
    return written


# ---- old keys keep working for one release ---------------------------------------------

def _version(release: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", release)
    return tuple(int(n) for n in numbers) if release != UNRELEASED and numbers else ()


def _in_window(migrations: list[Migration]) -> list[Migration]:
    """The migrations of the newest release (and Unreleased): older ones are not applied in
    memory any more, the user had a release to run `labpipe migrate`."""
    newest = max([_version(m.release) for m in migrations] + [_version(__version__)])
    return [m for m in migrations if m.release == UNRELEASED or _version(m.release) == newest]


def _warning(rel_file: str, op: Op, migration: Migration) -> str:
    when = "an unreleased labpipe" if migration.release == UNRELEASED else \
        f"labpipe {migration.release}"
    if op.kind == "transform":
        what = f"'{op.old}' {op.note}"
    elif op.kind == "remove":
        what = f"'{op.old}' is not used any more" + (f" ({op.note})" if op.note else "")
    else:
        what = f"'{op.old}' is now '{op.new}'"
    return f"{rel_file}: {what} ({when}); run labpipe migrate"


def upgrade_in_memory(rel_file: str, data: Any, migrations: list[Migration] | None = None
                      ) -> tuple[Any, list[str]]:
    """For loaders: `data` parsed from the project file `rel_file` (posix, relative to the
    project) with the pending migrations of the newest release applied; the warnings to show."""
    pool = _in_window(MIGRATIONS if migrations is None else migrations)
    pool = [m for m in pool if any(_glob_match(rel_file, p) for p in m.files)]
    if not pool or not isinstance(data, dict):
        return data, []
    data, warnings = copy.deepcopy(data), []
    for migration in pool:
        for op in migration.operations:
            if _apply_op(data, op):
                warnings.append(_warning(rel_file, op, migration))
    return data, warnings


_WARNED: set[tuple[str, str]] = set()


def upgraded(path: Path | str, data: Any, project: Path | str) -> Any:
    """What a loader of the project's settings file `path` calls with the parsed `data`: the
    data with old names understood, the warnings on stderr (once per file and process).
    Files outside `project` are returned as they are."""
    if not MIGRATIONS:
        return data
    try:
        rel = Path(os.path.realpath(path)).relative_to(
            Path(os.path.realpath(project))).as_posix()
    except ValueError:
        return data
    data, warnings = upgrade_in_memory(rel, data)
    if (str(project), rel) not in _WARNED:
        _WARNED.add((str(project), rel))
        for warning in warnings:
            print(warning, file=sys.stderr)
    return data
