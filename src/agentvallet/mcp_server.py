from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .recorder import WorkRecorder
from .skills import SkillRegistry


DATA_DIR = Path(os.environ.get("AGENTVALLET_DATA_DIR", "data"))
SKILLS_DIR = Path(os.environ.get("AGENTVALLET_SKILLS_DIR", "skills"))
DB_PATH = DATA_DIR / "agentvallet.db"
ARTIFACT_ROOT = DATA_DIR / "artifacts"

mcp = MCPServer("AgentVallet")
recorder = WorkRecorder(DB_PATH, ARTIFACT_ROOT)
registry = SkillRegistry(SKILLS_DIR)


def _current_run_id() -> str | None:
    return recorder.current.run_id if recorder.current else None


@mcp.tool()
def system_status() -> dict[str, Any]:
    """Return lightweight AgentVallet status and current recording state."""
    return {
        "name": "AgentVallet",
        "mode": "mcp-first",
        "data_dir": str(DATA_DIR),
        "skills_dir": str(SKILLS_DIR),
        "active_run_id": _current_run_id(),
        "skill_count": len(registry.list()),
    }


@mcp.tool()
def list_skills(trusted_only: bool = False) -> list[dict[str, Any]]:
    """List portable AgentVallet skills available to this AI client."""
    result: list[dict[str, Any]] = []
    for skill in registry.list():
        if trusted_only and not skill.trusted:
            continue
        result.append(
            {
                "name": skill.name,
                "version": skill.version,
                "status": skill.manifest.get("status", "unknown"),
                "goal": skill.manifest.get("goal", ""),
                "portable": skill.manifest.get("portable", True),
            }
        )
    return result


@mcp.tool()
def get_skill(name: str) -> dict[str, Any]:
    """Get a portable skill manifest plus readable workflow/rules/corrections."""
    skill = registry.find(name)
    if not skill:
        return {"found": False, "name": name}

    def read_optional(filename: str) -> str | None:
        path = skill.path / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    return {
        "found": True,
        "manifest": skill.manifest,
        "workflow": read_optional("workflow.md"),
        "rules": read_optional("rules.json"),
        "corrections": read_optional("corrections.md"),
        "exceptions": read_optional("exceptions.md"),
    }


@mcp.tool()
def start_work(goal: str, source: str = "mcp", tags: list[str] | None = None) -> dict[str, Any]:
    """Start an observable work record before an AI begins a task."""
    run = recorder.start(goal, source=source, tags=tags)
    return {"run_id": run.run_id, "status": run.status, "goal": run.goal}


@mcp.tool()
def record_work(event_type: str, content: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record an observable action, output, decision, script reference, or tool result."""
    item = recorder.event(event_type, content, **(metadata or {}))
    return {
        "run_id": _current_run_id(),
        "event_type": item.event_type,
        "recorded": True,
    }


@mcp.tool()
def record_correction(text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record a human correction as high-priority learning evidence."""
    recorder.correction(text, **(metadata or {}))
    return {"run_id": _current_run_id(), "recorded": True, "priority": "high"}


@mcp.tool()
def record_validation(name: str, passed: bool, details: str = "") -> dict[str, Any]:
    """Record a deterministic or human validation result for the active run."""
    result = recorder.validate(name, passed, details)
    return {
        "run_id": _current_run_id(),
        "name": result.name,
        "passed": result.passed,
        "details": result.details,
    }


@mcp.tool()
def snapshot_artifact(path: str) -> dict[str, Any]:
    """Snapshot a user-selected file into the active run with SHA-256 integrity metadata."""
    return recorder.snapshot(path)


@mcp.tool()
def finish_work(final_result: dict[str, Any] | None = None, approved: bool = False) -> dict[str, Any]:
    """Finish the active run. Approval does not automatically make a skill trusted."""
    run = recorder.finish(final_result, approved=approved)
    return run.to_dict()


@mcp.tool()
def create_skill_candidate(run_id: str, name: str, version: str = "0.1.0") -> dict[str, Any]:
    """Create a portable candidate skill from a recorded run; candidate code remains non-trusted."""
    run = recorder.load(run_id)
    package = registry.create_from_run(run.to_dict(), name, version=version)
    return {
        "name": package.name,
        "version": package.version,
        "status": package.manifest.get("status"),
        "path": str(package.path),
        "manifest": package.manifest,
    }


@mcp.resource("agentvallet://skills")
def skills_resource() -> str:
    """Machine-readable index of portable skills."""
    skills = [
        {
            "name": skill.name,
            "version": skill.version,
            "status": skill.manifest.get("status", "unknown"),
            "goal": skill.manifest.get("goal", ""),
        }
        for skill in registry.list()
    ]
    return json.dumps(skills, ensure_ascii=False, indent=2)


@mcp.prompt()
def reuse_prior_work(goal: str) -> str:
    """Guide an AI client to prefer validated prior skills before reasoning from zero."""
    return (
        "You are using AgentVallet. For this goal, first call list_skills and inspect the most "
        "relevant skill with get_skill. Prefer a trusted validated skill when available. If no "
        "suitable skill exists, call start_work, perform the task, record observable important "
        "actions/corrections/validations, finish_work, and only create a skill candidate after a "
        "successful validated run. Goal: " + goal
    )


def main() -> None:
    """Run AgentVallet locally over stdio, the lightweight desktop/CLI MCP transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
