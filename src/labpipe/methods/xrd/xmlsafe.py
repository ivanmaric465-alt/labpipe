"""Safe XML parsing.

Ported verbatim from upstream-eln@a3931a4
utils/scientific_reports/common.py::_parse_xml_rejecting_dtds.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import xml.parsers.expat


def parse_xml_rejecting_dtds(data: str | bytes, *,
                             invalid_message: str = "invalid XML") -> ET.Element:
    """Parse XML while refusing any document with a DTD or entity declarations.

    Expat decodes the document before the handlers run, and any doctype or
    entity declaration raises before the tree is built.
    """
    def _reject(*_args: object, **_kwargs: object) -> None:
        raise ValueError("XML document types and entities are not supported")

    if isinstance(data, str):
        data = data.encode("utf-8")
    validator = xml.parsers.expat.ParserCreate()
    validator.StartDoctypeDeclHandler = _reject
    validator.EntityDeclHandler = _reject
    try:
        validator.Parse(data, True)
    except ValueError:
        raise
    except xml.parsers.expat.ExpatError as exc:
        raise ValueError(invalid_message) from exc
    return ET.fromstring(data)
