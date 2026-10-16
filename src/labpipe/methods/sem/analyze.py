"""SEM images (JEOL, TIFF with .txt metadata): method `sem` (design: docs/methods/sem.md).

Scaffolded from the template; replace the reading and numerics sections.
Original template text follows.

Copy this package to src/labpipe/methods/<name>/, rename METHOD, replace the
reading and the "numerics" sections with your analysis, and follow
docs/ADDING_A_METHOD.md for the registry entry, settings file and subagent.

The contract every method's ``run`` fulfils (the pipeline relies on it):

* signature ``run(input_path, out_dir, *, settings=None, settings_sources=None,
  context=None, pipeline=True) -> dict``;
* check the output location first (assert_pipeline_writable / assert_not_protected);
* resolve settings with ``labpipe.settings.resolve(METHOD, sample_id, measurement_id)``
  unless they are passed in (replay passes the recorded ones);
* open the raw file read-only, never modify it;
* write results.json, plot.png, plot.pdf and provenance.json into out_dir with
  ``atomic_write_bytes``; the plot uses ``labpipe.plotting.acs``;
* put every number a human or agent may quote into results.json, with units;
* add deterministic checks: {"code", "severity": info|warning|review, "message"};
  status is "review" if any check has severity "review", else "ok";
* raise ``AnalysisError`` with a readable reason for unusable input;
* return {"status", "out_dir", "checks" (review-level only), "outputs" (name ->
  sha256), "settings_sha256"}.

This example reads a two-column text file (x, y) and reports its range and
the position of the maximum.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from labpipe import provenance as prov
from labpipe import settings as settings_mod
from labpipe.plotting.acs import apply_acs
from labpipe.safety import assert_not_protected, assert_pipeline_writable, atomic_write_bytes

METHOD = "sem"
RESULTS_SCHEMA = "labpipe.sem.results/1"


class AnalysisError(RuntimeError):
    """Unusable input; the message is recorded as the failure reason."""


def run(input_path: Path, out_dir: Path, *, settings: dict | None = None,
        settings_sources: list | None = None, context: dict[str, Any] | None = None,
        pipeline: bool = True) -> dict[str, Any]:
    context = context or {}
    out_dir = Path(out_dir)
    (assert_pipeline_writable if pipeline else assert_not_protected)(out_dir)
    if settings is None:
        settings, settings_sources = settings_mod.resolve(
            METHOD, context.get("sample_id"), context.get("measurement_id"))
    input_path = Path(input_path)
    input_sha = prov.sha256_file(input_path)
    checks: list[dict[str, Any]] = []

    # --- reading (read-only) ------------------------------------------------------
    try:
        data = np.loadtxt(input_path, comments=("#", ";"), usecols=(0, 1), ndmin=2)
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"cannot read {input_path.name}: {exc}") from exc
    if data.shape[0] < settings["min_points"]:
        raise AnalysisError(f"only {data.shape[0]} points (min_points = "
                            f"{settings['min_points']})")
    x, y = data[:, 0], data[:, 1]

    # --- numerics -------------------------------------------------------------------
    index = int(np.argmax(y))
    summary = {"n_points": int(x.size), "x_range": [float(x.min()), float(x.max())],
               "y_max": float(y[index]), "x_at_y_max": float(x[index])}
    if not np.all(np.isfinite(data)):
        checks.append({"code": "non_finite_values", "severity": "review",
                       "message": "data contain NaN or infinite values"})
    status = "review" if any(c["severity"] == "review" for c in checks) else "ok"
    results = {"schema": RESULTS_SCHEMA, "status": status,
               "sample_id": context.get("sample_id"),
               "measurement_id": context.get("measurement_id"),
               "input": {"file_name": input_path.name, "sha256": input_sha},
               "units": {"x": settings["x_unit"], "y": settings["y_unit"]},
               "summary": summary, "checks": checks}

    # --- outputs ------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    font = apply_acs()
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(3.25, 2.5))
    axis.plot(x, y, color="0.15")
    axis.set_xlabel(f"x ({settings['x_unit']})")
    axis.set_ylabel(f"y ({settings['y_unit']})")
    figure.tight_layout()
    staging = out_dir / ".plot-staging"
    staging.mkdir(exist_ok=True)
    figure.savefig(staging / "plot.png", dpi=300, metadata={"Software": None})
    figure.savefig(staging / "plot.pdf",
                   metadata={"Creator": None, "Producer": None, "CreationDate": None})
    plt.close(figure)
    for name in ("plot.png", "plot.pdf"):
        atomic_write_bytes(out_dir / name, (staging / name).read_bytes(), pipeline=pipeline)
        (staging / name).unlink()
    staging.rmdir()
    atomic_write_bytes(out_dir / "results.json",
                       (json.dumps(results, indent=1, allow_nan=False) + "\n").encode(),
                       pipeline=pipeline)
    outputs = {name: prov.sha256_file(out_dir / name)
               for name in ("results.json", "plot.png", "plot.pdf")}
    record = prov.build_record(
        method=METHOD, entry_point="labpipe.methods.sem.analyze.run",
        input_path=input_path, input_sha256=input_sha, context=context, settings=settings,
        settings_sources=settings_sources, outputs=outputs, out_dir=out_dir,
        extra={"plot": {"style": "ACS", "font": font}})
    atomic_write_bytes(out_dir / "provenance.json",
                       (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode(),
                       pipeline=pipeline)
    return {"status": status, "out_dir": str(out_dir),
            "checks": [c for c in checks if c["severity"] == "review"], "outputs": outputs,
            "settings_sha256": settings_mod.fingerprint(settings)}
