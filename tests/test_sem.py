"""sem: synthetic data with a known answer, and the method contract.

Scaffolded by `labpipe methods new`. Replace `make_example` with a generator of a
small synthetic .tif file whose correct result you know, and assert that
result. Raw data must not be committed; real-file regression tests must skip
when the files are absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from labpipe.methods_kit import check


def make_example(folder: Path) -> Path:
    raise NotImplementedError("write a synthetic .tif example with a known answer")


def test_sem_known_answer(tmp_path):
    example = make_example(tmp_path)
    pytest.fail("assert the known answer for " + example.name)


def test_sem_contract(tmp_path):
    result = check("sem", make_example(tmp_path), work_dir=tmp_path / "check")
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]
