"""The other files the SEM analysis reads (methods.yaml `companions`): the JEOL .txt."""

from __future__ import annotations

from pathlib import Path

from labpipe.methods.sem.analyze import metadata_file


def companions(input_path: Path, settings: dict | None = None) -> list[Path]:
    """The JEOL .txt of the image, if there is one (read only)."""
    sidecar = metadata_file(Path(input_path))
    return [sidecar] if sidecar else []
