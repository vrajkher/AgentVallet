from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Event:
    event_type: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ValidationResult:
    name: str
    passed: bool
    details: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class RunRecord:
    run_id: str
    goal: str
    source: str
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    status: str = "running"
    approved: bool = False
    final_result: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    validations: list[ValidationResult] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
