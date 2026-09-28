"""Deterministic validation: structure, integrity, examples, rules, custom validator and tests."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..models import Check, ValidationReport
from ..spec import SkillPackage
from ..spec import rules as rules_mod
from .runner import SkillRunner


def _diff(expected: Any, actual: Any, path: str = "") -> str | None:
    """Return a short description of the first difference, or None if equal (floats with tolerance)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in expected:
            if k not in actual:
                return f"{path}.{k} missing".lstrip(".")
            d = _diff(expected[k], actual[k], f"{path}.{k}")
            if d:
                return d
        extra = set(actual) - set(expected)
        return f"unexpected keys at {path or '$'}: {sorted(extra)}" if extra else None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path or '$'} length {len(actual)} != {len(expected)}"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            d = _diff(e, a, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(expected, float | int) and isinstance(actual, float | int) and not isinstance(expected, bool):
        return (
            None
            if abs(float(expected) - float(actual)) <= 1e-9 * max(1.0, abs(expected))
            else (f"{path or '$'} = {actual!r}, expected {expected!r}")
        )
    return None if expected == actual else f"{path or '$'} = {actual!r:.80}, expected {expected!r:.80}"


class Validator:
    def __init__(self, runner: SkillRunner | None = None):
        self.runner = runner or SkillRunner()

    def check_output(self, pkg: SkillPackage, inp: Any, out: Any, prefix: str = "") -> list[Check]:
        checks: list[Check] = []
        err = self.runner.check_output(pkg, out)
        checks.append(Check(name=f"{prefix}schema:output", passed=err is None, message=err or "output schema ok"))
        for c in rules_mod.evaluate(pkg.rules(), inp, out):
            c.name = prefix + c.name
            checks.append(c)
        res = self.runner.run_validator(pkg, inp, out)
        if not res.ok:
            checks.append(Check(name=f"{prefix}validate.py", passed=False, message=res.error or "validator failed"))
        else:
            verdict = res.output if isinstance(res.output, dict) else {}
            errs = verdict.get("errors") or []
            passed = bool(verdict.get("passed", not errs))
            checks.append(
                Check(
                    name=f"{prefix}validate.py",
                    passed=passed,
                    message="custom validator ok" if passed else "; ".join(map(str, errs))[:500],
                )
            )
        return checks

    def run_tests(self, pkg: SkillPackage, timeout: float = 300.0) -> Check | None:
        tests = list((pkg.path / "tests").glob("test_*.py"))
        if not tests:
            return None
        if importlib.util.find_spec("pytest") is None:
            return Check(
                name="tests:pytest",
                passed=True,
                severity="warning",
                message="pytest not installed; package tests skipped",
            )
        with tempfile.TemporaryDirectory(prefix="av-test-") as tmp:
            work = Path(tmp) / "pkg"
            shutil.copytree(pkg.path, work, ignore=shutil.ignore_patterns("__pycache__"))
            env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))}
            try:
                proc = subprocess.run(
                    [self.runner.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return Check(name="tests:pytest", passed=False, message=f"tests timed out after {timeout}s")
        summary = (proc.stdout.strip().splitlines() or ["no output"])[-1]
        return Check(name="tests:pytest", passed=proc.returncode == 0, message=summary[:300])

    def validate(self, pkg: SkillPackage, run_tests: bool = True) -> ValidationReport:
        report = ValidationReport(skill_name=pkg.name, version=pkg.version, content_hash=pkg.content_hash())
        report.checks += pkg.validate_structure()
        problems = pkg.verify_manifest()
        report.checks.append(
            Check(
                name="integrity:manifest",
                passed=not problems,
                message="manifest matches files" if not problems else "; ".join(problems)[:500],
            )
        )
        if not pkg.entrypoint.exists():
            report.checks.append(Check(name="entrypoint", passed=False, message="run.py missing"))
            return report

        for ex in pkg.examples():
            if "input" not in ex:
                continue
            prefix = f"example[{ex['name']}]:"
            res = self.runner.run(pkg, ex["input"], check_schemas=False)
            if not res.ok:
                report.checks.append(Check(name=prefix + "run", passed=False, message=res.error or "failed"))
                continue
            report.checks.append(Check(name=prefix + "run", passed=True, message=f"ran in {res.duration_ms}ms"))
            if "expected_output" in ex:
                d = _diff(ex["expected_output"], res.output)
                report.checks.append(
                    Check(
                        name=prefix + "expected",
                        passed=d is None,
                        message=d or "matches expected output",
                    )
                )
            report.checks += self.check_output(pkg, ex["input"], res.output, prefix=prefix)

        if run_tests:
            t = self.run_tests(pkg)
            if t:
                report.checks.append(t)
        return report
