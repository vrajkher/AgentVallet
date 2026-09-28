"""MCP server exposing AgentVallet to any MCP client (Claude, Cursor, IDEs, local agents).

Run with ``av mcp`` (stdio). Example client config::

    {"mcpServers": {"agentvallet": {"command": "av", "args": ["mcp"]}}}
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2
    from mcp.server import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef,attr-defined,unused-ignore]

from ..core import AgentVallet
from ..spec import SkillPackage

INSTRUCTIONS = """AgentVallet is a portable skill brain.
Before solving a task from scratch, call route_task to find a trusted skill and run_skill to use it.
When you complete new work successfully, call record_run with the input, output and (ideally) the
Python code you used (stdin JSON -> stdout JSON) so it can be learned into a reusable skill.
When a skill's result is wrong, call add_correction. Skills are only trusted after human approval."""


def build_server(av: AgentVallet | None = None) -> Any:
    av = av or AgentVallet()
    server = _Server("agentvallet", instructions=INSTRUCTIONS)

    @server.tool()
    def list_skills() -> list[dict[str, Any]]:
        """List all skills with their trusted (approved) and latest versions."""
        return av.registry.list_skills()

    @server.tool()
    def get_skill(name: str, version: str | None = None) -> dict[str, Any]:
        """Get a skill's metadata, input/output schemas, rules and workflow."""
        sv = av.registry.get(name, version)
        pkg = SkillPackage(sv.path)
        return {
            "name": sv.name,
            "version": sv.version,
            "status": sv.status.value,
            "description": sv.description,
            "input_schema": pkg.input_schema(),
            "output_schema": pkg.output_schema(),
            "rules": pkg.rules(),
            "workflow": pkg.read_text("workflow.md"),
            "corrections": pkg.read_text("corrections.md"),
        }

    @server.tool()
    def route_task(task: str, include_drafts: bool = False, limit: int = 5) -> list[dict[str, Any]]:
        """Rank skills that can handle a natural-language task (score 0..1)."""
        return [m.model_dump(mode="json") for m in av.route(task, limit=limit, include_drafts=include_drafts)]

    @server.tool()
    def run_skill(name: str, input: dict[str, Any], version: str | None = None) -> dict[str, Any]:
        """Run a skill on JSON input. The output is validated against its schema and rules and the run is recorded."""
        return av.run_skill(name, input, version=version).summary()

    @server.tool()
    def execute_task(task: str, input: dict[str, Any], include_drafts: bool = False) -> dict[str, Any]:
        """Route a task to the best trusted skill, run it, validate the output and auto-repair on failure."""
        return av.execute(task, input, include_drafts=include_drafts).summary()

    @server.tool()
    def record_run(
        goal: str,
        input: Any = None,
        output: Any = None,
        code: str | None = None,
        notes: list[str] | None = None,
        success: bool = True,
        tags: list[str] | None = None,
        agent: str = "mcp-client",
    ) -> dict[str, Any]:
        """Record observable work (input, output, the code used, notes) as a run for later learning."""
        h = av.record(goal, tags=tags or [], agent=agent)
        if input is not None:
            h.input(input)
        for n in notes or []:
            h.note(n)
        if code:
            h.code(code)
        if output is not None:
            h.output(output)
        run = h.finish("success" if success else "failed")
        return {"run_id": run.id, "status": run.status.value}

    @server.tool()
    def add_correction(
        text: str, skill: str | None = None, run_id: str | None = None, rule: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Record a correction for a skill or run. Rules use rules.json syntax, e.g.
        {"type": "range", "path": "total", "min": 0}. Corrections are applied on the next learn."""
        c = av.correct(text, skill=skill, run_id=run_id, rule=rule, author="mcp-client")
        return c.model_dump()

    @server.tool()
    def learn_skill(
        goal: str | None = None, name: str | None = None, run_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """Learn a DRAFT skill version from recorded runs + corrections and validate it.
        Drafts must be approved by a human (dashboard or `av skill approve`) before they are trusted."""
        res = av.learn(goal=goal, name=name, run_ids=run_ids)
        out = res.summary()
        rep = av.validate(res.version.name, res.version.version)
        out["validation"] = {
            "passed": rep.passed,
            "score": rep.score,
            "failures": [c.model_dump() for c in rep.failures()],
        }
        return out

    @server.tool()
    def search_memory(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search the graph memory (runs, skills, corrections, concepts)."""
        return av.graph.search(query, limit=limit)

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
