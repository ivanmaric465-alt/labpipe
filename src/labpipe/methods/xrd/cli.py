"""xrd-analyze <input> --out <dir>: analyse one diffraction file.

Standalone use (outside the manifest). Settings are resolved exactly as in
the pipeline (config/xrd.yaml, config/samples.yaml, config/measurements/xrd/).
Output may go anywhere except a raw root or protected path; inside the
pipeline use `labpipe xrd analyze --entry`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from labpipe.methods.xrd.analyze import AnalysisError, run
from labpipe.naming import measurement_id
from labpipe.safety import UnsafePathError
from labpipe.settings import SettingsError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xrd-analyze", description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-id", default=None)
    parser.add_argument("--overwrite", action="store_true",
                        help="allow writing into a non-empty output directory")
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()) and not args.overwrite:
        print(f"error: {args.out} is not empty (use --overwrite)", file=sys.stderr)
        return 2
    try:
        summary = run(args.input, args.out,
                      context={"sample_id": args.sample_id,
                               "measurement_id": measurement_id(args.input.name),
                               "relative_path": args.input.name},
                      pipeline=False)
    except (AnalysisError, UnsafePathError, SettingsError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
