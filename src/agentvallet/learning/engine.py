"""Learning engine: turn recorded work + human corrections into a draft skill version.

Implementation strategy (first that reproduces *every* example wins):

1. ``recorded``  – adopt code recorded in a successful run that follows the stdin/stdout contract.
2. ``synthesized`` – derive a deterministic program (copy/sum/count/mean/min/max/const per field).
3. ``llm``       – ask the configured LLM provider to write run.py (with feedback retries).
4. ``lookup``    – memorise known pairs and fail loudly on new input (flagged in exceptions.md).

Human corrections have the highest priority: rule corrections become error-severity rules and
example corrections override recorded outputs for the same input.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import Correction, Run, RunStatus, SkillVersion, StepKind
from ..runner.runner import SkillRunner
from ..runner.validator import _diff
from ..skills.registry import SkillNotFound, SkillRegistry
from ..spec import PackageContent, SkillPackage, build_package
from ..store import RunStore
from ..util import now, slugify, tokenize
from . import codegen
from .infer import infer_schema, mine_rules, synthesize_mapping
from .llm import LLMError, LLMProvider, NullProvider, extract_code

SYSTEM_PROMPT = (
    "You write small, dependency-free Python 3.11 programs for a portable skill package. "
    "The program reads ONE JSON object from stdin and prints ONE JSON object to stdout. "
    "It must reproduce every example exactly and generalise to similar inputs. "
    "Use only the standard library. Reply with a single ```python code block and nothing else."
)


class LearningError(RuntimeError):
    pass


@dataclass
class LearnResult:
    version: SkillVersion
    strategy: str
    runs_used: list[str]
    examples: int
    rules: int
    corrections_applied: list[str]
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "skill": self.version.name,
            "version": self.version.version,
            "status": self.version.status.value,
            "strategy": self.strategy,
            "runs_used": self.runs_used,
            "examples": self.examples,
            "rules": self.rules,
            "corrections_applied": self.corrections_applied,
            "notes": self.notes,
        }


def _similarity(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


class LearningEngine:
    def __init__(
        self,
        runs: RunStore,
        registry: SkillRegistry,
        runner: SkillRunner | None = None,
        llm: LLMProvider | None = None,
        graph: Any = None,
    ):
        self.runs = runs
        self.registry = registry
        self.runner = runner or SkillRunner()
        self.llm = llm or NullProvider()
        self.graph = graph

    # ---- selecting evidence -------------------------------------------------------------------------

    def select_runs(
        self,
        goal: str | None = None,
        run_ids: list[str] | None = None,
        skill_name: str | None = None,
        min_similarity: float = 0.3,
    ) -> list[Run]:
        if run_ids:
            return [self.runs.require(r) for r in run_ids]
        candidates = self.runs.list(limit=1000)
        chosen: list[Run] = []
        for r in candidates:
            if r.status == RunStatus.RUNNING:
                continue
            if skill_name and r.skill_name == skill_name or goal and _similarity(goal, r.goal) >= min_similarity:
                chosen.append(r)
        return sorted(chosen, key=lambda r: r.created_at)

    def _pair(self, run: Run) -> tuple[Any, Any] | None:
        steps = self.runs.steps(run.id)
        ins = [s.data for s in steps if s.kind == StepKind.INPUT and s.data is not None]
        outs = [s.data for s in steps if s.kind == StepKind.OUTPUT and s.data is not None]
        return (ins[0], outs[-1]) if ins and outs else None

    # ---- candidate verification ---------------------------------------------------------------------

    def _reproduces(self, name: str, run_py: str, examples: list[dict[str, Any]]) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="av-learn-") as tmp:
            pkg = build_package(
                Path(tmp) / "pkg",
                PackageContent(
                    name=name,
                    version="0.0.1",
                    description="candidate",
                    run_py=run_py,
                    examples=examples,
                ),
            )
            for ex in examples:
                res = self.runner.run(pkg, ex["input"], check_schemas=False, timeout=30)
                if not res.ok:
                    return False, f"{ex['name']}: {res.error}"
                d = _diff(ex["expected_output"], res.output)
                if d:
                    return False, f"{ex['name']}: {d}"
        return True, "ok"

    def _llm_program(
        self,
        name: str,
        description: str,
        examples: list[dict[str, Any]],
        corrections: list[Correction],
        notes: list[str],
        attempts: int = 2,
    ) -> str | None:
        if not self.llm.available:
            return None
        shown = examples[:20]
        prompt = f"Task: {description}\n\nExamples (input -> expected_output):\n" + "\n".join(
            json.dumps({"input": e["input"], "expected_output": e["expected_output"]}) for e in shown
        )
        if corrections:
            prompt += "\n\nHuman corrections (must be respected):\n" + "\n".join(f"- {c.text}" for c in corrections)
        feedback = ""
        for attempt in range(1, attempts + 1):
            try:
                code = extract_code(self.llm.complete(SYSTEM_PROMPT, prompt + feedback))
            except LLMError as exc:
                notes.append(f"llm unavailable: {exc}")
                return None
            ok, msg = self._reproduces(name, code, examples)
            if ok:
                return code
            notes.append(f"llm attempt {attempt} rejected: {msg}")
            feedback = f"\n\nYour previous program failed: {msg}\nPrevious program:\n```python\n{code}```\nFix it."
        return None

    # ---- main entry ---------------------------------------------------------------------------------

    def learn(
        self,
        name: str | None = None,
        goal: str | None = None,
        run_ids: list[str] | None = None,
        description: str | None = None,
        actor: str = "learning-engine",
        bump: str = "minor",
    ) -> LearnResult:
        notes: list[str] = []
        base: SkillPackage | None = None
        if name and self.registry.exists(name):
            try:
                base = self.registry.package(name)
            except SkillNotFound:
                base = None
        runs = self.select_runs(goal=goal, run_ids=run_ids, skill_name=name if base else None)
        if goal and base and not run_ids:
            runs += [r for r in self.select_runs(goal=goal) if r.id not in {x.id for x in runs}]
        success = [r for r in runs if r.status == RunStatus.SUCCESS]
        failed = [r for r in runs if r.status == RunStatus.FAILED]
        if not success and base is None:
            raise LearningError("no successful runs to learn from (record work first, or pass --run ids)")

        goal_text = goal or (Counter(r.goal for r in success).most_common(1)[0][0] if success else base.description)  # type: ignore[union-attr]
        skill_name = name or slugify(goal_text)
        desc = description or (base.description if base else goal_text)

        # Examples: existing package examples + recorded pairs; later evidence wins on identical input.
        examples_by_key: dict[str, dict[str, Any]] = {}
        if base:
            for ex in base.examples():
                if "input" in ex and "expected_output" in ex:
                    examples_by_key[json.dumps(ex["input"], sort_keys=True)] = {
                        "input": ex["input"],
                        "expected_output": ex["expected_output"],
                        "source": ex.get("source", "base"),
                    }
        used_runs: list[str] = []
        for r in success:
            pair = self._pair(r)
            if pair is None:
                notes.append(f"run {r.id[:8]} has no input/output data; used for workflow only")
                continue
            examples_by_key[json.dumps(pair[0], sort_keys=True)] = {
                "input": pair[0],
                "expected_output": pair[1],
                "source": f"run:{r.id}",
            }
            used_runs.append(r.id)

        # Human corrections (highest priority).
        corrections = self.runs.corrections(
            skill_name=skill_name,
            run_ids=[r.id for r in runs] or None,
            pending_only=True,
        )
        rule_corrections: list[dict[str, Any]] = []
        for c in corrections:
            if not c.rule:
                continue
            if "example" in c.rule:
                ex = c.rule["example"]
                examples_by_key[json.dumps(ex["input"], sort_keys=True)] = {
                    "input": ex["input"],
                    "expected_output": ex["expected_output"],
                    "source": f"correction:{c.id}",
                }
            elif "type" in c.rule:
                rule_corrections.append(
                    {
                        **c.rule,
                        "id": c.rule.get("id") or f"correction_{c.id[:8]}",
                        "source": "correction",
                        "severity": c.rule.get("severity", "error"),
                        "description": c.rule.get("description", c.text),
                        "priority": c.priority,
                    }
                )

        examples = [{"name": f"example_{i:03d}", **ex} for i, ex in enumerate(examples_by_key.values(), start=1)]
        pairs = [(e["input"], e["expected_output"]) for e in examples]
        if not examples:
            raise LearningError("no input/output examples available; record run.input(...) and run.output(...)")

        # Rules: corrections first (priority), then prior manual rules, then freshly mined invariants.
        rules: list[dict[str, Any]] = list(rule_corrections)
        seen = {r["id"] for r in rules}
        if base:
            for rule in base.rules():
                if rule.get("source") in ("correction", "manual") and rule["id"] not in seen:
                    rules.append(rule)
                    seen.add(rule["id"])
        for rule in mine_rules(pairs):
            if rule["id"] not in seen:
                rules.append(rule)
                seen.add(rule["id"])

        # Implementation.
        strategy, run_py = "", None
        code_candidates: list[str] = []
        for r in reversed(success):
            for s in self.runs.steps(r.id, kind=StepKind.CODE.value):
                role = (s.data or {}).get("role", "solution")
                if role == "solution" and codegen.looks_like_entrypoint(s.content):
                    code_candidates.append(s.content)
        if base and not code_candidates:
            code_candidates.append(base.entrypoint.read_text())
        for cand in code_candidates:
            ok, msg = self._reproduces(skill_name, cand, examples)
            if ok:
                strategy, run_py = "recorded", cand
                break
            notes.append(f"recorded code rejected: {msg}")
        if run_py is None:
            spec = synthesize_mapping(pairs)
            if spec:
                cand = codegen.mapping_program(skill_name, spec, len(pairs))
                ok, msg = self._reproduces(skill_name, cand, examples)
                if ok:
                    strategy, run_py = "synthesized", cand
                else:
                    notes.append(f"synthesized program rejected: {msg}")
        if run_py is None:
            cand_llm = self._llm_program(skill_name, desc, examples, corrections, notes)
            if cand_llm:
                strategy, run_py = "llm", cand_llm
        if run_py is None:
            strategy, run_py = "lookup", codegen.lookup_program(skill_name, pairs)
            notes.append("no general implementation found; using lookup table (fails on unseen input)")

        content = PackageContent(
            name=skill_name,
            version="0.0.0",
            description=desc,
            tags=sorted(
                ({t for r in runs for t in r.tags} | set(base.meta.get("tags", []) if base else []))
                - {"execution", skill_name}
            ),
            triggers=sorted({r.goal for r in success} | set(base.meta.get("triggers", []) if base else []))[:20],
            run_py=run_py,
            rules=rules,
            input_schema=infer_schema([p[0] for p in pairs], title=f"{skill_name} input"),
            output_schema=infer_schema([p[1] for p in pairs], title=f"{skill_name} output"),
            workflow_md=self._workflow(skill_name, desc, success, strategy),
            corrections_md=self._corrections_md(base, corrections),
            exceptions_md=self._exceptions_md(base, failed, strategy),
            examples=examples,
            extra_meta={
                "implementation": strategy,
                "created_from": {
                    "runs": used_runs,
                    "learned_at": now(),
                    "base_version": base.version if base else None,
                },
            },
        )
        sv = self.registry.add_from_content(content, bump=bump, actor=actor)
        applied = [c.id for c in corrections]
        self.runs.mark_corrections_applied(applied, f"{sv.name}@{sv.version}")
        self.registry.db.audit(
            "skill.learn",
            f"{sv.name}@{sv.version}",
            {"strategy": strategy, "runs": used_runs, "corrections": applied},
            actor,
        )
        if self.graph is not None:
            self.index_skill(sv, used_runs, applied)
        return LearnResult(sv, strategy, used_runs, len(examples), len(rules), applied, notes)

    # ---- documents ----------------------------------------------------------------------------------

    def _workflow(self, name: str, desc: str, runs: list[Run], strategy: str) -> str:
        lines = [
            f"# Workflow: {name}",
            "",
            desc,
            "",
            f"Learned from {len(runs)} successful run(s); implementation strategy: **{strategy}**.",
            "",
        ]
        if runs:
            best = runs[-1]
            lines += [f"## Observed procedure (run `{best.id[:8]}`)", ""]
            for i, s in enumerate(self.runs.steps(best.id), start=1):
                text = s.content.strip().splitlines()[0][:160] if s.content.strip() else ""
                if s.kind in (StepKind.INPUT, StepKind.OUTPUT) and s.data is not None:
                    text = text or json.dumps(s.data)[:160]
                lines.append(f"{i}. **{s.kind.value}** {text}".rstrip())
        lines += [
            "",
            "## Contract",
            "",
            "- Input: `inputs.schema.json`",
            "- Output: `outputs.schema.json`",
            "- Checks: `rules.json` + `validate.py`",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _corrections_md(base: SkillPackage | None, corrections: list[Correction]) -> str:
        text = (
            base.read_text("corrections.md").rstrip()
            if base
            else "# Corrections\n\nHuman corrections (highest priority)."
        )
        for c in corrections:
            text += f"\n- {c.created_at} ({c.author}, priority {c.priority}): {c.text}"
        return text + "\n"

    def _exceptions_md(self, base: SkillPackage | None, failed: list[Run], strategy: str) -> str:
        text = base.read_text("exceptions.md").rstrip() if base else "# Exceptions\n\nKnown edge cases and failures."
        for r in failed:
            errs = [s.content for s in self.runs.steps(r.id, kind=StepKind.ERROR.value)]
            text += f"\n- Run `{r.id[:8]}` failed ({r.goal}): {errs[-1][:200] if errs else 'no error recorded'}"
        if strategy == "lookup":
            text += "\n- Implementation is a lookup table: inputs not seen in examples are rejected."
        return text + "\n"

    def index_skill(self, sv: SkillVersion, run_ids: list[str], correction_ids: list[str]) -> None:
        g = self.graph
        pkg = SkillPackage(sv.path)
        g.upsert_node(f"skill:{sv.name}", "skill", sv.name, {"description": sv.description})
        g.upsert_node(
            f"skillv:{sv.name}@{sv.version}", "skill_version", f"{sv.name}@{sv.version}", {"status": sv.status.value}
        )
        g.add_edge(f"skill:{sv.name}", "has_version", f"skillv:{sv.name}@{sv.version}")
        g.link_concepts(f"skill:{sv.name}", pkg.search_text()[:2000])
        for rid in run_ids:
            g.add_edge(f"skillv:{sv.name}@{sv.version}", "learned_from", f"run:{rid}")
        for cid in correction_ids:
            g.add_edge(f"skillv:{sv.name}@{sv.version}", "applies", f"correction:{cid}")
