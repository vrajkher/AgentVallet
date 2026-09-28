"""``AgentVallet``: the facade that wires stores, engines, graph and pipeline together."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings
from .graph import make_graph
from .learning import LearningEngine, LearnResult, get_provider
from .models import Correction, Run, RunStatus, ValidationReport
from .recorder import RunHandle, WorkRecorder
from .runner import SkillRunner, Validator
from .skills import Pipeline, PipelineResult, RepairEngine, SkillRegistry, SkillRouter
from .spec import scaffold
from .store import ArtifactStore, Database, RunStore


class AgentVallet:
    def __init__(self, settings: Settings | None = None):
        self.settings = (settings or Settings()).ensure()
        s = self.settings
        self.db = Database(s.db_path)
        self.runs = RunStore(self.db)
        self.artifacts = ArtifactStore(self.db, s.objects_dir)
        self.graph = make_graph(self.db, s.graph_backend)
        self.llm = get_provider(s.llm_provider, s.llm_model)
        self.runner = SkillRunner(default_timeout=s.run_timeout)
        self.validator = Validator(self.runner)
        self.registry = SkillRegistry(self.db, s.skills_dir, self.validator)
        self.recorder = WorkRecorder(self.runs, self.artifacts, redact_secrets=s.redact_secrets)
        self.recorder.on_finish.append(self._index_run)
        self.learner = LearningEngine(self.runs, self.registry, self.runner, self.llm, self.graph)
        self.router = SkillRouter(self.registry, self.graph)
        self.repairer = RepairEngine(self.registry, self.runner, self.validator, self.llm)
        self.pipeline = Pipeline(
            self.registry,
            self.router,
            self.runner,
            self.validator,
            self.repairer,
            self.recorder,
            self.graph,
            threshold=s.route_threshold,
        )

    # ---- recording ---------------------------------------------------------------------------------

    def record(self, goal: str, tags: list[str] | None = None, agent: str = "unknown", **meta: Any) -> RunHandle:
        return self.recorder.start(goal, tags=tags, agent=agent, **meta)

    def _index_run(self, run: Run) -> None:
        self.graph.upsert_node(f"run:{run.id}", "run", run.goal, {"status": run.status.value})
        self.graph.link_concepts(f"run:{run.id}", run.goal + " " + " ".join(run.tags))
        self.graph.upsert_node(f"agent:{run.agent}", "agent", run.agent)
        self.graph.add_edge(f"run:{run.id}", "performed_by", f"agent:{run.agent}")

    def correct(
        self,
        text: str,
        skill: str | None = None,
        run_id: str | None = None,
        rule: dict[str, Any] | None = None,
        author: str = "human",
        priority: int = 100,
    ) -> Correction:
        if not (skill or run_id):
            raise ValueError("a correction must reference a skill or a run")
        if run_id:
            run_id = self.runs.require(run_id).id
            if skill is None:
                skill = self.runs.require(run_id).skill_name
        corr = self.runs.add_correction(
            Correction(
                text=text,
                skill_name=skill,
                run_id=run_id,
                rule=rule,
                author=author,
                priority=priority,
            )
        )
        self.graph.upsert_node(f"correction:{corr.id}", "correction", text[:200], {"author": author})
        if skill:
            self.graph.add_edge(f"correction:{corr.id}", "corrects", f"skill:{skill}")
        if run_id:
            self.graph.add_edge(f"correction:{corr.id}", "corrects", f"run:{run_id}")
        return corr

    # ---- skills ------------------------------------------------------------------------------------

    def learn(self, **kwargs: Any) -> LearnResult:
        return self.learner.learn(**kwargs)

    def new_skill(self, name: str, description: str, tags: list[str] | None = None, actor: str = "human") -> Any:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            scaffold(Path(tmp) / name, name, description, tags)
            return self.registry.add_from_dir(Path(tmp) / name, actor=actor)

    def validate(self, name: str, version: str | None = None, run_tests: bool = True) -> ValidationReport:
        return self.registry.validate(name, version, run_tests=run_tests)

    def approve(self, name: str, version: str, approver: str, note: str = "") -> Any:
        sv = self.registry.approve(name, version, approver, note)
        self.graph.upsert_node(
            f"skillv:{name}@{version}",
            "skill_version",
            f"{name}@{version}",
            {"status": sv.status.value, "approved_by": approver},
        )
        self.graph.upsert_node(f"skill:{name}", "skill", name, {"description": sv.description})
        self.graph.add_edge(f"skill:{name}", "has_version", f"skillv:{name}@{version}")
        return sv

    def route(self, task: str, limit: int = 5, include_drafts: bool = False) -> list[Any]:
        return self.router.route(task, limit=limit, include_drafts=include_drafts)

    def execute(self, task: str, data: Any, **kwargs: Any) -> PipelineResult:
        return self.pipeline.execute(task, data, **kwargs)

    def run_skill(self, name: str, data: Any, version: str | None = None, **kwargs: Any) -> PipelineResult:
        return self.pipeline.execute(f"run {name}", data, skill=name, version=version, **kwargs)

    # ---- introspection -----------------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        skills = self.registry.list_skills()
        return {
            "version": __version__,
            "home": str(self.settings.home),
            "runs": {
                "total": self.runs.count(),
                "success": self.runs.count(RunStatus.SUCCESS.value),
                "failed": self.runs.count(RunStatus.FAILED.value),
                "running": self.runs.count(RunStatus.RUNNING.value),
            },
            "skills": {"total": len(skills), "trusted": sum(1 for s in skills if s["trusted"])},
            "corrections": {
                "total": len(self.runs.corrections()),
                "pending": len(self.runs.corrections(pending_only=True)),
            },
            "graph": self.graph.stats(),
            "llm": {"provider": self.llm.name, "available": self.llm.available},
        }

    def doctor(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = [
            ("python", sys.version_info >= (3, 11), sys.version.split()[0]),
            ("home writable", self.settings.home.exists(), str(self.settings.home)),
        ]
        ok, n, msg = self.db.verify_audit()
        checks.append(("audit chain", ok, f"{msg} ({n} entries)"))
        bad = self.artifacts.verify_all()
        checks.append(("artifact integrity", not bad, "all objects intact" if not bad else f"{len(bad)} corrupted"))
        tampered = []
        for s in self.registry.list_skills():
            for v in self.registry.versions(s["name"]):
                if v.status.value == "approved":
                    try:
                        self.registry.package(v.name, v.version)
                    except Exception:
                        tampered.append(f"{v.name}@{v.version}")
        checks.append(("approved skills intact", not tampered, ", ".join(tampered) or "ok"))
        for mod, extra in (("fastapi", "api"), ("mcp", "mcp"), ("pytest", "dev"), ("anthropic", "llm")):
            present = importlib.util.find_spec(mod) is not None
            checks.append((f"optional: {mod}", True, "installed" if present else f"not installed ([{extra}])"))
        checks.append(("llm provider", True, f"{self.llm.name} ({'ready' if self.llm.available else 'off'})"))
        checks.append(("graph backend", True, self.graph.backend))
        return checks
