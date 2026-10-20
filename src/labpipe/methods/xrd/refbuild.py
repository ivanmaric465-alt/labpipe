"""XRD reference phases: add a CIF, (re)build reflection lists, list what is there.

    labpipe xrd refs list
    labpipe xrd refs add path/to/file.cif --id NAME --structure-type fcc [--role reference]
                         [--cod-id N] [--note "why this entry"]
    labpipe xrd refs build [--id NAME]

In a project, references you add go to lab/references/xrd/ (cif/, patterns/, phases.yaml),
which updates never touch; the ones that come with labpipe stay in the code folder. In the
repository itself (dev mode) `add` and `build` write the shipped ones.

Building needs the optional 'refbuild' dependency (pymatgen, pinned in pyproject.toml);
the error shows the exact install command for this installation.
The analysis itself never imports pymatgen; it reads patterns/<id>.json and
converts d-spacings to 2θ with the wavelength recorded in each measurement.

Intensities: pymatgen XRDCalculator (kinematic, atomic scattering factors,
Lorentz-polarisation factor, no Debye-Waller factor, no preferred
orientation) at Cu Kα1 = 1.540598 Å, strongest line in 0-150° 2θ = 100.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from importlib.metadata import version
from pathlib import Path

import yaml

from labpipe import safety
from labpipe.safety import CODE_ROOT, atomic_write_text

REFERENCES = CODE_ROOT / "references" / "xrd"
CATALOGUE = REFERENCES / "phases.yaml"
LAB_EMPTY = "# This lab's own XRD reference phases (`labpipe xrd refs add`).\nphases: []\n"
WAVELENGTH = 1.540598
TWO_THETA_RANGE = (0.0, 150.0)
STRUCTURE_TYPES = ("fcc", "rocksalt", "hcp", "bcc", "diamond", "rutile", "other")
ROLES = ("reference", "internal_standard")


def _cif_field(text: str, name: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "\t"):
            return line[len(name):].strip().strip("'\"")
    return None


def _authors(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = lines.index("_publ_author_name") + 1
    except ValueError:
        return []
    authors = []
    for line in lines[start:]:
        line = line.strip()
        if not line or line.startswith("_") or line.startswith("loop_"):
            break
        authors.append(line.strip("'\""))
    return authors


def _lab() -> Path | None:
    """Where this project's own references go (None in dev mode: the shipped ones)."""
    return None if safety.dev_mode() else safety.PROJECT_ROOT / "lab" / "references" / "xrd"


def _catalogue() -> dict:
    """All references: the shipped ones, then the lab's, each entry tagged with its folder."""
    phases = [{**e, "_dir": REFERENCES}
              for e in yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))["phases"]]
    lab = _lab()
    if lab is not None and (lab / "phases.yaml").exists():
        own = yaml.safe_load((lab / "phases.yaml").read_text(encoding="utf-8")) or {}
        phases += [{**e, "_dir": lab} for e in own.get("phases") or []]
    return {"phases": phases}


def _cif_path(entry: dict) -> Path:
    relative = entry.get("cif") or f"cif/{entry['cod_id']}.cif"
    return entry["_dir"] / relative


def build_one(entry: dict) -> dict:
    try:
        from pymatgen.analysis.diffraction.xrd import XRDCalculator
        from pymatgen.io.cif import CifParser
    except ImportError as exc:
        raise SystemExit(
            "building references needs the optional package pymatgen (it is not part of "
            "labpipe's base install). It installs into labpipe's own environment on this "
            "computer, not in the container; you run:\n  "
            + safety.optional_install_command("pymatgen")) from exc
    cif_path = _cif_path(entry)
    raw = cif_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    structure = CifParser(str(cif_path)).parse_structures(primitive=False)[0]
    pattern = XRDCalculator(wavelength=WAVELENGTH).get_pattern(
        structure, scaled=True, two_theta_range=TWO_THETA_RANGE)
    reflections = [{
        "hkl": [list(item["hkl"]) for item in hkls],
        "multiplicity": [int(item["multiplicity"]) for item in hkls],
        "d_angstrom": float(d), "two_theta_cu_ka1_deg": float(two_theta),
        "intensity_rel": float(intensity),
    } for two_theta, intensity, hkls, d in zip(pattern.x, pattern.y, pattern.hkls,
                                               pattern.d_hkls)]
    lattice = structure.lattice
    cod_id = entry.get("cod_id")
    return {
        "schema": "labpipe.xrd.reference/1",
        "id": entry["id"], "role": entry["role"], "structure_type": entry["structure_type"],
        "formula": structure.composition.reduced_formula,
        "space_group": _cif_field(text, "_symmetry_space_group_name_H-M")
        or _cif_field(text, "_space_group_name_H-M_alt"),
        "cell": {"a": lattice.a, "b": lattice.b, "c": lattice.c,
                 "alpha": lattice.alpha, "beta": lattice.beta, "gamma": lattice.gamma},
        "cell_as_recorded": {key: _cif_field(text, f"_cell_length_{key}") for key in "abc"},
        "source": {
            "database": "Crystallography Open Database" if cod_id else "user-supplied CIF",
            "cod_id": cod_id,
            "url": f"https://www.crystallography.net/cod/{cod_id}.cif" if cod_id else None,
            "cif_file": cif_path.relative_to(REFERENCES).as_posix(),
            "cif_sha256": hashlib.sha256(raw).hexdigest(),
            "authors": _authors(text), "journal": _cif_field(text, "_journal_name_full"),
            "year": _cif_field(text, "_journal_year"),
            "volume": _cif_field(text, "_journal_volume"),
            "first_page": _cif_field(text, "_journal_page_first"),
        },
        "curation_note": entry.get("note"),
        "computed_with": {
            "pymatgen": version("pymatgen"),
            "calculator": "pymatgen.analysis.diffraction.xrd.XRDCalculator",
            "wavelength_angstrom": WAVELENGTH, "two_theta_range_deg": list(TWO_THETA_RANGE),
            "intensity_scaling": "strongest line in range = 100",
            "excluded_effects": ["Debye-Waller", "preferred orientation", "absorption"],
        },
        "reflections": reflections,
    }


def build(only: str | None = None) -> list[str]:
    built = []
    for entry in _catalogue()["phases"]:
        if only and entry["id"] != only:
            continue
        data = build_one(entry)
        atomic_write_text(entry["_dir"] / "patterns" / f"{entry['id']}.json",
                          json.dumps(data, indent=1, ensure_ascii=False) + "\n", pipeline=False)
        built.append(entry["id"])
    if only and not built:
        raise SystemExit(f"no reference with id {only!r}")
    return built


def add(cif: Path, *, phase_id: str, structure_type: str, role: str, cod_id: int | None,
        note: str | None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", phase_id):
        raise SystemExit(f"invalid id {phase_id!r}: use letters, digits, _ . + -")
    if any(entry["id"] == phase_id for entry in _catalogue()["phases"]):
        raise SystemExit(f"a reference with id {phase_id!r} already exists")
    home = _lab() or REFERENCES
    catalogue = home / "phases.yaml"
    target = home / "cif" / f"{phase_id}.cif"
    if target.exists():
        raise SystemExit(f"{target} already exists")
    text = cif.read_text(encoding="utf-8", errors="replace")
    if "_cell_length_a" not in text:
        raise SystemExit(f"{cif} does not look like a CIF (no _cell_length_a)")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cif, target)
    block = [f"  - id: {phase_id}", f"    cif: cif/{phase_id}.cif"]
    if cod_id:
        block.append(f"    cod_id: {cod_id}")
    block += [f"    structure_type: {structure_type}", f"    role: {role}",
              f"    note: {json.dumps(note or f'added from {cif.name}', ensure_ascii=False)}"]
    content = catalogue.read_text(encoding="utf-8") if catalogue.exists() else LAB_EMPTY
    content = content.replace("phases: []", "phases:")
    marker = "\nexcluded:"
    position = content.index(marker) if marker in content else len(content)
    content = content[:position].rstrip("\n") + "\n" + "\n".join(block) + "\n" + content[position:]
    atomic_write_text(catalogue, content, pipeline=False)
    build(phase_id)
    return json.loads((home / "patterns" / f"{phase_id}.json").read_text(encoding="utf-8"))


def listing() -> list[dict]:
    rows = []
    for entry in _catalogue()["phases"]:
        pattern = entry["_dir"] / "patterns" / f"{entry['id']}.json"
        data = json.loads(pattern.read_text(encoding="utf-8")) if pattern.exists() else None
        rows.append({"id": entry["id"], "structure_type": entry["structure_type"],
                     "role": entry["role"], "cif": entry.get("cif"),
                     "cod_id": entry.get("cod_id"),
                     "a_angstrom": data["cell"]["a"] if data else None,
                     "c_angstrom": data["cell"]["c"] if data else None,
                     "built": data is not None,
                     "source": "labpipe" if entry["_dir"] == REFERENCES else "lab"})
    return rows


def register(method_sub: argparse._SubParsersAction) -> None:
    """Add `labpipe xrd refs ...` to the command line."""
    refs = method_sub.add_parser("refs", help="manage reference phases (CIFs)")
    refs_sub = refs.add_subparsers(dest="refs_action", required=True)
    refs_sub.add_parser("list").set_defaults(func=lambda args, config: listing(),
                                              needs_config=False)
    build_parser = refs_sub.add_parser("build")
    build_parser.add_argument("--id", default=None)
    build_parser.set_defaults(func=lambda args, config: {"built": build(args.id)},
                              needs_config=False)
    add_parser = refs_sub.add_parser("add")
    add_parser.add_argument("cif", type=Path)
    add_parser.add_argument("--id", required=True, dest="phase_id")
    add_parser.add_argument("--structure-type", required=True, choices=STRUCTURE_TYPES)
    add_parser.add_argument("--role", default="reference", choices=ROLES)
    add_parser.add_argument("--cod-id", type=int, default=None)
    add_parser.add_argument("--note", default=None)

    def _add(args, config):
        data = add(args.cif, phase_id=args.phase_id, structure_type=args.structure_type,
                   role=args.role, cod_id=args.cod_id, note=args.note)
        return {"added": data["id"], "formula": data["formula"], "cell": data["cell"],
                "lines": len(data["reflections"])}
    add_parser.set_defaults(func=_add, needs_config=False)
