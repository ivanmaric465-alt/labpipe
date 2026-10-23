"""The other files the EDS analysis reads (methods.yaml `companions`): the images a report links."""

from __future__ import annotations

from pathlib import Path

from labpipe.methods.eds.analyze import _linked_images, find_image


def companions(input_path: Path) -> list[Path]:
    """The images the INCA report links that exist next to it (read only), found as
    analyze.py finds them (by name, case-insensitive). Images chosen with `images:` in the
    settings are not included: the settings are tracked on their own."""
    report = Path(input_path)
    found: dict[Path, None] = {}
    for link in _linked_images(report):
        image = find_image(report, link)
        if image is not None and image.is_file():
            found[image] = None
    return list(found)
