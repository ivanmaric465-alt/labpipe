"""The other files the EDS analysis reads (methods.yaml `companions`): its images."""

from __future__ import annotations

from pathlib import Path

from labpipe.methods.eds.analyze import _linked_images, find_image


def companions(input_path: Path, settings: dict | None = None) -> list[Path]:
    """The images the analysis reads that exist (read only), found as analyze.py finds them
    (by name, case-insensitive): those chosen with `images:` in the settings, else the ones
    the INCA report links."""
    report = Path(input_path)
    found: dict[Path, None] = {}
    for link in (settings or {}).get("images") or _linked_images(report):
        image = find_image(report, link)
        if image is not None and image.is_file():
            found[image] = None
    return list(found)
