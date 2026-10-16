"""Layered analysis settings: how the user steers an analysis.

For method M, sample S and measurement X the resolved settings are

    config/M.yaml                                  global defaults
    ← config/samples.yaml  presets listed for S    (e.g. a support type)
    ← config/samples.yaml  S: M: {...}             sample-specific settings
    ← config/measurements/M/X.yaml                 measurement-specific settings

merged in that order (dicts merge key by key; lists and scalars replace).
Override files may only use keys that exist in config/M.yaml, so a typo
fails loudly instead of being silently ignored. Every file used is recorded
with its sha256 in provenance.json, together with the fully resolved
settings, so a steered result is reproducible without the files.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from labpipe.safety import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config"


class SettingsError(ValueError):
    pass


def _load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return (yaml.safe_load(raw) or {}), hashlib.sha256(raw).hexdigest()


def _check_keys(override: dict[str, Any], base: dict[str, Any], where: str, prefix: str = ""):
    for key, value in override.items():
        path = f"{prefix}{key}"
        if key not in base:
            raise SettingsError(f"{where}: unknown setting '{path}'")
        if isinstance(value, dict) and isinstance(base[key], dict):
            _check_keys(value, base[key], where, path + ".")


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def measurement_file(method: str, measurement_id: str) -> Path:
    return CONFIG / "measurements" / method / f"{measurement_id}.yaml"


def resolve(method: str, sample_id: str | None,
            measurement_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (resolved settings, list of sources used)."""
    base_path = CONFIG / f"{method}.yaml"
    config, digest = _load(base_path)
    sources = [{"file": str(base_path.relative_to(PROJECT_ROOT)), "sha256": digest,
                "layer": "global defaults"}]

    samples_path = CONFIG / "samples.yaml"
    if sample_id and samples_path.exists():
        samples, digest = _load(samples_path)
        entry = (samples.get("samples") or {}).get(sample_id) or {}
        used = False
        for preset_name in entry.get("presets") or []:
            preset = (samples.get("presets") or {}).get(preset_name)
            if preset is None:
                raise SettingsError(f"samples.yaml: {sample_id} uses unknown preset "
                                    f"'{preset_name}'")
            layer = preset.get(method) or {}
            _check_keys(layer, config, f"samples.yaml preset {preset_name}")
            config = _merge(config, layer)
            used = True
        layer = entry.get(method) or {}
        _check_keys(layer, config, f"samples.yaml sample {sample_id}")
        config = _merge(config, layer)
        if used or layer:
            sources.append({"file": str(samples_path.relative_to(PROJECT_ROOT)),
                            "sha256": digest,
                            "layer": f"sample {sample_id}"
                                     + (f" (presets: {', '.join(entry['presets'])})"
                                        if entry.get("presets") else "")})

    if measurement_id:
        path = measurement_file(method, measurement_id)
        if path.exists():
            layer, digest = _load(path)
            layer.pop("note", None)
            _check_keys(layer, config, str(path.relative_to(PROJECT_ROOT)))
            config = _merge(config, layer)
            sources.append({"file": str(path.relative_to(PROJECT_ROOT)), "sha256": digest,
                            "layer": f"measurement {measurement_id}"})
    return config, sources


def fingerprint(config: dict[str, Any]) -> str:
    """sha256 of the resolved settings (canonical JSON)."""
    text = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
