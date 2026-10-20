"""Layered analysis settings: how the user steers an analysis.

For method M, sample S and measurement X the resolved settings are

    config/M.yaml                                  labpipe's defaults (the code's)
    ← config/lab.yaml  M: {...}                    the lab's defaults (the project's)
    ← config/samples.yaml  presets listed for S    (e.g. a support type)
    ← config/samples.yaml  S: M: {...}             sample-specific settings
    ← config/measurements/M/X.yaml                 measurement-specific settings

merged in that order (dicts merge key by key; lists and scalars replace).
Override files may only use keys that exist in config/M.yaml, so a typo
fails loudly instead of being silently ignored. Every file used is recorded
with its sha256 in provenance.json (`root`: "code" for labpipe's defaults, "project" for
the lab's files; `file` is relative to that root), together with the fully resolved
settings, so a steered result is reproducible without the files.

The look of all plots (font, sizes, palette, formats) is resolved separately,
config/plots.yaml ← config/lab.yaml plots:, and added to the resolved settings
under the key `plots`. Plot settings (`plots` and the method's `plot:` block)
are left out of the analysis fingerprint; they have their own (`plot_sha256`),
so a plot change redraws figures (`labpipe replot`) and never makes an
analysis stale.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from labpipe import labmethods, migrations
from labpipe.registry import shipped_methods
from labpipe.safety import CODE_ROOT, PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config"      # the project's files: lab.yaml, samples.yaml, measurements/
DEFAULTS = CODE_ROOT / "config"       # labpipe's defaults: <method>.yaml, plots.yaml


class SettingsError(ValueError):
    pass


def defaults_file(method: str) -> tuple[Path, str]:
    """A method's default settings and the root they are in: config/<method>.yaml of the code,
    or (a lab method, lab/methods.yaml) the file its `config:` names in the project."""
    spec = None if method in shipped_methods() else labmethods.lab_spec(method)
    if spec is not None:
        return labmethods.defaults_file(spec), "project"
    return DEFAULTS / f"{method}.yaml", "code"


def _source(path: Path, digest: str | None, layer: str, root: str = "project") -> dict[str, Any]:
    """The record of one settings file: its path relative to its root ("code" or "project"),
    which root, its hash."""
    base = DEFAULTS.parent if root == "code" else CONFIG.parent
    return {"file": path.relative_to(base).as_posix(), "root": root, "sha256": digest,
            "layer": layer}


def _load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    # the digest is of the file as written; old key names are understood (migrations.py)
    data = migrations.upgraded(path, yaml.safe_load(raw) or {}, CONFIG.parent)
    return data, hashlib.sha256(raw).hexdigest()


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


PLOT_FORMATS = ("png", "pdf", "svg")
# Keys the `plot:` blocks had before plot keys left the analysis fingerprint (manifest
# entries written then hold a fingerprint that includes them); other methods: all keys.
LEGACY_PLOT_KEYS = {"xrd": {"width_in", "height_in", "dpi"}, "eds": {"width_in", "dpi"},
                    "sem": {"width_in", "dpi"}}
FINGERPRINT_VERSION = 2


def measurement_file(method: str, measurement_id: str) -> Path:
    return CONFIG / "measurements" / method / f"{measurement_id}.yaml"


def _plot_style(lab: dict[str, Any]) -> tuple[dict[str, Any], list]:
    """config/plots.yaml ← lab.yaml plots:; unknown keys and bad values are refused."""
    path = DEFAULTS / "plots.yaml"
    style, digest = _load(path)
    sources = [_source(path, digest, "plot style defaults", "code")]
    layer = lab.get("plots") or {}
    if not isinstance(layer, dict):
        raise SettingsError("config/lab.yaml: plots: must be a section of settings")
    _check_keys(layer, style, "config/lab.yaml plots")
    style = _merge(style, layer)
    if style["preset"] != "acs":
        raise SettingsError(f"plots.preset: '{style['preset']}' is not a preset (only: acs)")
    palette = style["palette"]
    if not (palette == "okabe-ito" or (isinstance(palette, list) and palette
                                       and all(isinstance(c, str) for c in palette))):
        raise SettingsError("plots.palette: 'okabe-ito' or a list of colours")
    formats = style["formats"]
    if not isinstance(formats, list) or not set(formats) <= set(PLOT_FORMATS):
        raise SettingsError(f"plots.formats: a list of {', '.join(PLOT_FORMATS)}")
    return style, sources


def resolve(method: str, sample_id: str | None,
            measurement_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (resolved settings, list of sources used)."""
    base_path, base_root = defaults_file(method)
    config, digest = _load(base_path)
    sources = [_source(base_path, digest, "global defaults" if base_root == "code"
                       else "the lab method's defaults", base_root)]

    lab, lab_digest = load_lab()
    style, style_sources = _plot_style(lab)
    # A global plots.dpi of the lab is the default of every method's plot: block (the
    # method's own layers below still win).
    if "dpi" in (lab.get("plots") or {}) and "dpi" in (config.get("plot") or {}):
        config["plot"]["dpi"] = style["dpi"]
    lab_layer = lab.get(method) or {}
    if not isinstance(lab_layer, dict):
        raise SettingsError(f"config/lab.yaml: {method}: must be a section of settings")
    _check_keys(lab_layer, config, f"config/lab.yaml {method}")
    config = _merge(config, lab_layer)
    if lab_layer or lab.get("plots"):
        sources.append(_source(CONFIG / "lab.yaml", lab_digest, "lab defaults (config/lab.yaml)"))

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
            sources.append(_source(samples_path, digest, f"sample {sample_id}" + (
                f" (presets: {', '.join(entry['presets'])})" if entry.get("presets") else "")))

    if measurement_id:
        path = measurement_file(method, measurement_id)
        if path.exists():
            layer, digest = _load(path)
            layer.pop("note", None)
            _check_keys(layer, config, str(path.relative_to(CONFIG.parent)))
            config = _merge(config, layer)
            sources.append(_source(path, digest, f"measurement {measurement_id}"))
    config["plots"] = style
    sources.extend(style_sources)
    return config, sources


def load_lab() -> tuple[dict[str, Any], str | None]:
    """config/lab.yaml (the lab's own preferences, git-ignored): its sections and sha256,
    or ({}, None) without the file. Sections: plots, report, and one per method."""
    path = CONFIG / "lab.yaml"
    if not path.exists():
        return {}, None
    data, digest = _load(path)
    if not isinstance(data, dict):
        raise SettingsError("config/lab.yaml: expected sections such as plots:, report:, xrd:")
    return data, digest


def fingerprint(config: dict[str, Any]) -> str:
    """sha256 of the resolved settings (canonical JSON)."""
    text = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k not in keys}


def analysis_fingerprint(config: dict[str, Any]) -> str:
    """sha256 of the resolved settings without the plot settings: what the numbers depend on."""
    return fingerprint(_without(config, "plot", "plots"))


def plot_fingerprint(config: dict[str, Any]) -> str:
    """sha256 of the plot settings: the method's `plot:` block and the plot style."""
    return fingerprint({"plot": config.get("plot"), "plots": config.get("plots")})


def fingerprints(config: dict[str, Any]) -> dict[str, Any]:
    """The values a method's run() returns for the manifest."""
    return {"settings_sha256": analysis_fingerprint(config),
            "plot_sha256": plot_fingerprint(config),
            "fingerprint_version": FINGERPRINT_VERSION}


def _legacy_plot(method: str, plot: dict[str, Any]) -> dict[str, Any]:
    keys = LEGACY_PLOT_KEYS.get(method)
    return {k: v for k, v in plot.items() if keys is None or k in keys}


def compare_entry(entry: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Is a done entry's analysis (and its plot) still what the settings ask for?

    Returns {"analysis_current", "plot_current", "adopt"}; `adopt` holds fields to store
    when the entry predates the split (fingerprint version 1: one fingerprint over all
    settings including `plot:`). It counts as current when that old fingerprint equals
    the one computed the old way (plot block reduced to its old keys, no `plots`); its
    figures were drawn with the old keys and the default plot style (plots.yaml)."""
    if entry.get("fingerprint_version") == FINGERPRINT_VERSION:
        return {"analysis_current": entry.get("settings_sha256") == analysis_fingerprint(config),
                "plot_current": entry.get("plot_sha256") == plot_fingerprint(config),
                "adopt": {}}
    method = entry["method"]
    plot = config.get("plot") or {}
    old = _without(config, "plots")
    if "plot" in old:
        old["plot"] = _legacy_plot(method, plot)
    if entry.get("settings_sha256") != fingerprint(old):
        return {"analysis_current": False, "plot_current": False, "adopt": {}}
    base, _ = _load(defaults_file(method)[0])
    drawn_plot = {k: (v if k in _legacy_plot(method, plot) else (base.get("plot") or {}).get(k))
                  for k, v in plot.items()}
    defaults, _ = _load(DEFAULTS / "plots.yaml")
    drawn = {"plot": drawn_plot if plot else config.get("plot"), "plots": defaults}
    return {"analysis_current": True,
            "plot_current": fingerprint(drawn) == plot_fingerprint(config),
            "adopt": {"settings_sha256": analysis_fingerprint(config),
                      "plot_sha256": fingerprint(drawn),
                      "fingerprint_version": FINGERPRINT_VERSION}}
