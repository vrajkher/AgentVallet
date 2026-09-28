"""End-to-end execution: Route -> Run -> Validate -> Repair -> (record everything)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Check, RunStatus
from ..recorder import WorkRecorder
from ..runner.runner import SkillRunner
from ..runner.validator import Validator
from .registry import SkillRegistry
from .repair import RepairEngine
from .router import SkillRouter


@dataclass
class PipelineResult:
    status: str  # success | failed | invalid_input | no_skill | needs_human | repaired
    task: str
    run_id: str | None = None
    skill: str | None = None
    version: str | None = None
    output: Any = None
    checks: list[Check] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    repair: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("success", "repaired")

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "task": self.task,
            "run_id": self.run_id,
            "skill": self.skill,
            "version": self.version,
            "output": self.output,
            "checks": [c.model_dump() for c in self.checks],
            "candidates": self.candidates,
            "repair": self.repair,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class Pipeline:
    def __init__(
        self,
        registry: SkillRegistry,
        router: SkillRouter,
        runner: SkillRunner,
        validator: Validator,
        repair: RepairEngine,
        recorder: WorkRecorder,
        graph: Any = None,
        threshold: float = 0.5,
    ):
        self.registry = registry
        self.router = router
        self.runner = runner
        self.validator = validator
        self.repair = repair
        self.recorder = recorder
        self.graph = graph
        self.threshold = threshold

    def execute(
        self,
        task: str,
        data: Any,
        skill: str | None = None,
        version: str | None = None,
        auto_repair: bool = True,
        include_drafts: bool = False,
        agent: str = "agentvallet",
    ) -> PipelineResult:
        result = PipelineResult(status="failed", task=task)
        if skill is None:
            matches = self.router.route(task, include_drafts=include_drafts)
            result.candidates = [m.model_dump() for m in matches]
            if not matches or matches[0].score < self.threshold:
                result.status = "no_skill"
                result.error = f"no skill above confidence {self.threshold}" + (
                    f" (best: {matches[0].name} {matches[0].score})" if matches else ""
                )
                return result
            skill, version = matches[0].name, matches[0].version

        sv = self.registry.get(skill, version, include_drafts=True)
        pkg = self.registry.package(sv.name, sv.version)
        result.skill, result.version = sv.name, sv.version

        with self.recorder.start(
            task, tags=["execution", sv.name], agent=agent, skill=f"{sv.name}@{sv.version}"
        ) as run:
            result.run_id = run.id
            self.recorder.runs.link_skill(run.id, sv.name, sv.version)
            run.input(data)
            input_error = self.runner.check_input(pkg, data)
            if input_error:
                # The caller's input is wrong, not the skill: report it, don't try to repair.
                result.status, result.error = "invalid_input", input_error
                run.error(input_error)
                run.finish(RunStatus.FAILED)
                return result
            res = self.runner.run(pkg, data, check_schemas=False)
            if res.ok and (out_err := self.runner.check_output(pkg, res.output)):
                res.ok, res.error = False, out_err
            result.duration_ms = res.duration_ms
            run.tool_call(
                f"skill:{sv.name}@{sv.version}",
                args=None,
                result={"ok": res.ok, "error": res.error, "duration_ms": res.duration_ms},
                ok=res.ok,
            )
            checks: list[Check] = []
            if res.ok:
                checks = self.validator.check_output(pkg, data, res.output)
                result.checks = checks
                run.validation([c.model_dump() for c in checks])
                if all(c.passed for c in checks if c.severity == "error"):
                    result.status, result.output = "success", res.output
                    run.output(res.output)
            if result.status != "success":
                result.error = res.error or "; ".join(
                    f"{c.name}: {c.message}" for c in checks if not c.passed and c.severity == "error"
                )
                run.error(result.error or "failed")
                if auto_repair:
                    rep = self.repair.attempt(sv.name, sv.version, data, res, checks)
                    result.repair = rep.summary()
                    run.note(f"repair: {rep.status} ({rep.strategy}); " + "; ".join(rep.notes))
                    if rep.status == "recovered":
                        result.status, result.output, result.version = "repaired", rep.output, rep.version
                        run.output(rep.output, note=f"repaired via {rep.strategy}")
                    else:
                        result.status = "needs_human"
                run.finish(RunStatus.SUCCESS if result.ok else RunStatus.FAILED)
        if self.graph is not None:
            self.graph.add_edge(f"run:{result.run_id}", "used_skill", f"skill:{sv.name}")
        return result
