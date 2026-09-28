"""Portable regression tests: every example must reproduce its expected output."""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example(path):
    case = json.loads(path.read_text())
    proc = subprocess.run(
        [sys.executable, str(ROOT / "run.py")],
        input=json.dumps(case["input"]), capture_output=True, text=True, timeout=60, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    if "expected_output" in case:
        assert out == case["expected_output"]
