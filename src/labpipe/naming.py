"""Sample and measurement identifiers derived from file names."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

_MEASUREMENT_UNSAFE = re.compile(r"[^A-Za-z0-9.\-]+")
# Folder-name safe on Linux, macOS and Windows (no < > : " / \\ | ? * or spaces).
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def derive_sample_id(relative_path: str, *, pattern: str,
                     overrides: dict[str, str]) -> tuple[str | None, str, str | None]:
    """Return (sample_id, source, problem).

    Rule: the file stem up to the first '_' (whole stem if there is none),
    accepted only if it matches ``pattern``. A human override (config/
    sample_ids.yaml, keyed by relative path) always wins. When the rule is not
    satisfied, sample_id is None and ``problem`` says why (-> needs_review).
    """
    if relative_path in overrides:
        value = str(overrides[relative_path])
        if not _SAFE_ID.fullmatch(value):
            return None, "override (config/sample_ids.yaml)", (
                f"override {value!r} is not usable as a folder name on all platforms "
                "(letters, digits, . _ + - only)")
        return value, "override (config/sample_ids.yaml)", None
    stem = PurePosixPath(relative_path).stem
    token = stem.split("_", 1)[0].strip()
    if not token:
        return None, "filename rule", f"no sample token in file name {stem!r}"
    if not re.fullmatch(pattern, token):
        return None, "filename rule", (
            f"first token {token!r} of {stem!r} does not look like a sample ID "
            f"(expected pattern {pattern})")
    if not _SAFE_ID.fullmatch(token):
        return None, "filename rule", (
            f"{token!r} is not usable as a folder name on all platforms")
    return token, "filename rule", None


def sample_ids_agree(sample_id: str, stored: str) -> bool:
    """Does an ID stored inside a file agree with the sample ID from the file name?

    Equal, or the stored ID starts with the sample ID followed by a separator
    (S-15 and "S-15_5.2.26" agree; S-1 and "S-15" do not).
    """
    stored = stored.strip()
    if stored == sample_id:
        return True
    return stored.startswith(sample_id) and not stored[len(sample_id)].isalnum()


def measurement_id(relative_path: str) -> str:
    """File stem made filesystem-safe: 'S-17_3.2.26_long scan + Si' -> 'S-17_3.2.26_long_scan_Si'."""
    stem = PurePosixPath(relative_path).stem
    slug = _MEASUREMENT_UNSAFE.sub("_", stem).strip("_.")
    return slug or "measurement_" + hashlib.sha256(relative_path.encode()).hexdigest()[:8]


def entry_id(method: str, root_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(f"{root_id}:{relative_path}".encode()).hexdigest()[:10]
    return f"{method}-{digest}"
