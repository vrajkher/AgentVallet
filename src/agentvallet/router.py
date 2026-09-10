from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .skills import SkillPackage, SkillRegistry


_WORD = re.compile(r"[a-zA-Z0-9_]+")


@dataclass(slots=True)
class RouteDecision:
    action: str
    confidence: float
    skill: SkillPackage | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "skill": None
            if self.skill is None
            else {
                "name": self.skill.name,
                "version": self.skill.version,
                "status": self.skill.manifest.get("status", "unknown"),
                "goal": self.skill.manifest.get("goal", ""),
            },
        }


class SkillRouter:
    """Prefer a trusted local skill before asking an AI to solve from zero."""

    def __init__(self, registry: SkillRegistry, threshold: float = 0.34):
        self.registry = registry
        self.threshold = threshold

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.lower() for token in _WORD.findall(text) if len(token) > 1}

    def _score(self, goal: str, skill: SkillPackage) -> float:
        goal_tokens = self._tokens(goal)
        if not goal_tokens:
            return 0.0
        haystack = " ".join(
            [
                skill.name.replace("_", " "),
                str(skill.manifest.get("goal", "")),
                " ".join(skill.manifest.get("tags", []) or []),
            ]
        )
        skill_tokens = self._tokens(haystack)
        if not skill_tokens:
            return 0.0
        overlap = len(goal_tokens & skill_tokens)
        coverage = overlap / len(goal_tokens)
        precision = overlap / len(skill_tokens)
        return 0.8 * coverage + 0.2 * precision

    def route(self, goal: str, *, trusted_only: bool = True) -> RouteDecision:
        candidates = self.registry.list()
        if trusted_only:
            candidates = [skill for skill in candidates if skill.trusted]
        if not candidates:
            return RouteDecision("solve_new", 0.0, None, "No eligible trusted skills available")

        scored = sorted(
            ((self._score(goal, skill), skill) for skill in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best_skill = scored[0]
        if best_score < self.threshold:
            return RouteDecision(
                "solve_new",
                best_score,
                None,
                f"Best trusted skill match {best_score:.2f} is below threshold {self.threshold:.2f}",
            )
        return RouteDecision(
            "reuse_skill",
            best_score,
            best_skill,
            "Trusted skill matched the requested goal",
        )
