"""Repair loop for failed executions.

Strategies, in order:
1. ``retry``     – transient failure (timeout): retry once with a doubled timeout.
2. ``fallback``  – re-run on the previous *approved* version of the skill.
3. ``llm_patch`` – ask the LLM for a patched run.py; it lands as a NEW DRAFT version that is
                   validated against all examples and must be approved by a human.
4. ``needs_human`` – escalate: nothing automatic succeeded; a correction is required.

Repairs never modify an approved version and never auto-approve anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..learning.llm import LLMError, LLMProvider, NullProvider, extract_code
from ..models import Check, ExecutionResult, SkillStatus
from ..runner.runner import SkillRunner
from ..runner.validator import Validator
from ..spec import SkillPackage
from ..util import version_key
from .registry import SkillRegistry

PATCH_SYSTEM = (
    "You repair a Python 3.11 skill entrypoint (stdin JSON -> stdout JSON, standard library only). "
    "Keep behaviour identical for inputs that already work. Reply with one ```python code block."
)


@dataclass
class RepairResult:
    status: str  # recovered | draft_created | needs_human
    strategy: str | None = None
    output: Any = None
    version: str | None = None
    new_version: str | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("status", "strategy", "output", "version", "new_version", "notes")}


class RepairEngine:
    def __init__(
        self, registry: SkillRegistry, runner: SkillRunner, validator: Validator, llm: LLMProvider | None = None
    ):
        self.registry = registry
        self.runner = runner
        self.validator = validator
        self.llm = llm or NullProvider()

    def _passes(self, pkg: SkillPackage, data: Any, res: ExecutionResult) -> tuple[bool, list[Check]]:
        if not res.ok:
            return False, [Check(name="run", passed=False, message=res.error or "failed")]
        checks = self.validator.check_output(pkg, data, res.output)
        return all(c.passed for c in checks if c.severity == "error"), checks

    def attempt(
        self, name: str, version: str, data: Any, failure: ExecutionResult, failed_checks: list[Check] | None = None
    ) -> RepairResult:
        notes: list[str] = []
        pkg = self.registry.package(name, version)

        if failure.timed_out:
            timeout = (pkg.timeout or self.runner.default_timeout) * 2
            res = self.runner.run(pkg, data, timeout=timeout)
            ok, _ = self._passes(pkg, data, res)
            if ok:
                return RepairResult("recovered", "retry", res.output, version, notes=[f"retried with {timeout}s"])
            notes.append("retry with longer timeout failed")

        older = [
            v
            for v in self.registry.versions(name)
            if v.status == SkillStatus.APPROVED and version_key(v.version) < version_key(version)
        ]
        for prev in reversed(older):
            ppkg = self.registry.package(name, prev.version)
            res = self.runner.run(ppkg, data)
            ok, _ = self._passes(ppkg, data, res)
            if ok:
                notes.append(f"{name}@{version} failed; fell back to approved {prev.version}")
                return RepairResult("recovered", "fallback", res.output, prev.version, notes=notes)
            notes.append(f"fallback to {prev.version} also failed")

        if self.llm.available:
            problem = failure.error or "; ".join(c.message for c in (failed_checks or []) if not c.passed)
            prompt = (
                f"Skill: {pkg.name} — {pkg.description}\n\nCurrent run.py:\n```python\n"
                f"{pkg.entrypoint.read_text()}```\n\nFailing input:\n{data!r}\n\nFailure: {problem}\n\n"
                f"Rules the output must satisfy:\n{pkg.rules()!r}\n"
            )
            try:
                code = extract_code(self.llm.complete(PATCH_SYSTEM, prompt))
                sv = self.registry.fork(name, version, bump="patch", actor="repair-engine")
                (SkillPackage(sv.path).entrypoint).write_text(code)
                report = self.registry.validate(name, sv.version)
                notes.append(f"llm patch -> draft {sv.version} (validation {'passed' if report.passed else 'failed'})")
                npkg = self.registry.package(name, sv.version)
                res = self.runner.run(npkg, data)
                ok, _ = self._passes(npkg, data, res)
                return RepairResult(
                    "draft_created", "llm_patch", res.output if ok else None, version, sv.version, notes
                )
            except LLMError as exc:
                notes.append(f"llm patch unavailable: {exc}")

        notes.append("automatic repair exhausted; add a correction (av correct ...) and re-learn")
        return RepairResult("needs_human", None, None, version, notes=notes)
