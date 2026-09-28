"""Typed data models shared across stores, engines, API and MCP server."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .util import new_id, now


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class StepKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    PROMPT = "prompt"
    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    COMMAND = "command"
    FILE = "file"
    CODE = "code"
    NOTE = "note"
    ERROR = "error"
    VALIDATION = "validation"


class SkillStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class Run(BaseModel):
    id: str = Field(default_factory=new_id)
    goal: str
    status: RunStatus = RunStatus.RUNNING
    agent: str = "unknown"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    skill_name: str | None = None
    skill_version: str | None = None
    created_at: str = Field(default_factory=now)
    finished_at: str | None = None


class Step(BaseModel):
    id: str = Field(default_factory=new_id)
    run_id: str
    seq: int = 0
    kind: StepKind
    content: str = ""
    data: Any = None
    artifact_hash: str | None = None
    created_at: str = Field(default_factory=now)


class Artifact(BaseModel):
    hash: str
    size: int
    name: str
    media_type: str = "application/octet-stream"
    created_at: str = Field(default_factory=now)


class Correction(BaseModel):
    id: str = Field(default_factory=new_id)
    text: str
    run_id: str | None = None
    skill_name: str | None = None
    rule: dict[str, Any] | None = None
    priority: int = 100
    applied_in: str | None = None
    author: str = "human"
    created_at: str = Field(default_factory=now)


class Check(BaseModel):
    name: str
    passed: bool
    message: str = ""
    severity: str = "error"  # error | warning


class ValidationReport(BaseModel):
    id: str = Field(default_factory=new_id)
    skill_name: str
    version: str
    content_hash: str = ""
    checks: list[Check] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def score(self) -> float:
        return round(sum(c.passed for c in self.checks) / len(self.checks), 4) if self.checks else 0.0

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill": self.skill_name,
            "version": self.version,
            "passed": self.passed,
            "score": self.score,
            "checks": [c.model_dump() for c in self.checks],
            "created_at": self.created_at,
        }


class SkillVersion(BaseModel):
    name: str
    version: str
    status: SkillStatus = SkillStatus.DRAFT
    content_hash: str
    path: str
    description: str = ""
    parent_version: str | None = None
    created_at: str = Field(default_factory=now)
    approved_by: str | None = None
    approved_at: str | None = None


class ExecutionResult(BaseModel):
    ok: bool
    output: Any = None
    error: str | None = None
    stderr: str = ""
    duration_ms: int = 0
    exit_code: int | None = None
    timed_out: bool = False


class RouteMatch(BaseModel):
    name: str
    version: str
    score: float
    status: SkillStatus
    reasons: list[str] = Field(default_factory=list)
