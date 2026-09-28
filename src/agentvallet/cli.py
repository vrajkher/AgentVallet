"""Command-line interface: ``av`` / ``agentvallet``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .config import Settings
from .models import RunStatus, StepKind

app = typer.Typer(help="AgentVallet — portable AI work, memory & skill brain.", no_args_is_help=True)
record_app = typer.Typer(help="Record observable work.", no_args_is_help=True)
runs_app = typer.Typer(help="Inspect recorded runs.", no_args_is_help=True)
skill_app = typer.Typer(help="Manage skill packages and versions.", no_args_is_help=True)
graph_app = typer.Typer(help="Query the graph index.", no_args_is_help=True)
audit_app = typer.Typer(help="Inspect the append-only audit log.", no_args_is_help=True)
app.add_typer(record_app, name="record")
app.add_typer(runs_app, name="runs")
app.add_typer(skill_app, name="skill")
app.add_typer(graph_app, name="graph")
app.add_typer(audit_app, name="audit")

console = Console()
err = Console(stderr=True)
_state: dict[str, Any] = {"json": False, "home": None}


def vallet() -> Any:
    from .core import AgentVallet

    if "av" not in _state:
        settings = Settings(home=Path(_state["home"])) if _state["home"] else Settings()
        _state["av"] = AgentVallet(settings)
    return _state["av"]


def emit(data: Any, human: Any = None) -> None:
    if _state["json"] or human is None:
        console.print_json(json.dumps(data, default=str))
    elif callable(human):
        human()
    else:
        console.print(human)


def fail(msg: str, code: int = 1) -> None:
    err.print(f"[red]error:[/red] {escape(msg)}")
    raise typer.Exit(code)


def parse_json_arg(value: str | None) -> Any:
    """Accept inline JSON, ``@file.json`` or ``-`` (stdin)."""
    if value is None:
        return None
    if value == "-":
        return json.load(sys.stdin)
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text())
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")


@app.callback()
def main_callback(
    home: str | None = typer.Option(None, "--home", envvar="AV_HOME", help="AgentVallet home directory."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    _state["home"] = home
    _state["json"] = json_out


@app.command()
def version() -> None:
    """Show version."""
    console.print(f"agentvallet {__version__}")


@app.command()
def init() -> None:
    """Initialise the AgentVallet home (database, object store, skills dir)."""
    av = vallet()
    emit(
        {"home": str(av.settings.home), "db": str(av.settings.db_path)},
        f"[green]✓[/green] AgentVallet initialised at [bold]{av.settings.home}[/bold]",
    )


@app.command()
def doctor() -> None:
    """Check installation health and integrity (audit chain, artifacts, approved skills)."""
    checks = vallet().doctor()
    if _state["json"]:
        emit([{"check": c, "ok": ok, "detail": d} for c, ok, d in checks])
    else:
        t = Table("check", "status", "detail")
        for c, ok, d in checks:
            t.add_row(c, "[green]ok[/green]" if ok else "[red]FAIL[/red]", escape(d))
        console.print(t)
    if not all(ok for _, ok, _ in checks):
        raise typer.Exit(1)


@app.command()
def stats() -> None:
    """Show counts for runs, skills, corrections and the graph."""
    emit(vallet().stats())


# ---- record -------------------------------------------------------------------------------------------


@record_app.command("start")
def record_start(
    goal: str,
    tag: list[str] = typer.Option([], "--tag", "-t"),
    agent: str = typer.Option("cli", "--agent"),
) -> None:
    """Start a run; prints its id."""
    h = vallet().record(goal, tags=tag, agent=agent)
    emit({"run_id": h.id}, h.id)


@record_app.command("step")
def record_step(
    run_id: str,
    kind: StepKind = typer.Argument(..., help="input|output|prompt|response|tool_call|code|note|error"),
    content: str = typer.Argument("", help="Text content (use '-' to read stdin)."),
    data: str | None = typer.Option(None, "--data", "-d", help="JSON data (inline, @file or -)."),
) -> None:
    """Append a step to a running run."""
    h = vallet().recorder.resume(run_id)
    if h.finished:
        fail("run already finished")
    if content == "-":
        content = sys.stdin.read()
    payload = parse_json_arg(data)
    if kind == StepKind.CODE:
        s = h.code(content)
    elif kind == StepKind.INPUT:
        s = h.input(payload, content)
    elif kind == StepKind.OUTPUT:
        s = h.output(payload, content)
    else:
        s = h.step(kind, content, payload)
    emit({"step": s.seq, "kind": s.kind.value}, f"step {s.seq} ({s.kind.value}) recorded")


@record_app.command("file")
def record_file(run_id: str, path: Path, note: str = "") -> None:
    """Snapshot a file into the content-addressed artifact store."""
    h = vallet().recorder.resume(run_id)
    s = h.file(path, note)
    emit({"step": s.seq, "artifact": s.artifact_hash}, f"file stored as {s.artifact_hash}")


@record_app.command("exec")
def record_exec(run_id: str, cmd: list[str] = typer.Argument(..., help="Command to execute and record.")) -> None:
    """Execute a command and record argv, exit code and output."""
    h = vallet().recorder.resume(run_id)
    s = h.command(cmd)
    console.print(s.data["stdout"], end="")
    err.print(s.data["stderr"], end="")
    raise typer.Exit(s.data["exit_code"] if s.data["exit_code"] >= 0 else 1)


@record_app.command("finish")
def record_finish(run_id: str, status: RunStatus = typer.Option(RunStatus.SUCCESS, "--status", "-s")) -> None:
    """Finish a run as success or failed."""
    run = vallet().recorder.resume(run_id).finish(status)
    emit(run.model_dump(), f"run {run.id[:8]} finished: {run.status.value}")


@record_app.command("json")
def record_json(path: str = typer.Argument("-", help="JSON file (or - for stdin) with goal, steps[], status.")) -> None:
    """Record a whole run from a JSON document (for agents that log after the fact)."""
    doc = parse_json_arg(path if path == "-" else f"@{path}")
    av = vallet()
    h = av.record(doc["goal"], tags=doc.get("tags", []), agent=doc.get("agent", "import"))
    for st in doc.get("steps", []):
        h.step(st["kind"], st.get("content", ""), st.get("data"))
    run = h.finish(doc.get("status", "success"))
    emit({"run_id": run.id, "status": run.status.value}, run.id)


# ---- runs ---------------------------------------------------------------------------------------------


@runs_app.command("list")
def runs_list(
    status: str | None = None,
    skill: str | None = None,
    search: str | None = None,
    limit: int = 20,
) -> None:
    runs = vallet().runs.list(status=status, skill_name=skill, search=search, limit=limit)

    def show() -> None:
        t = Table("id", "status", "goal", "skill", "agent", "created")
        for r in runs:
            color = {"success": "green", "failed": "red"}.get(r.status.value, "yellow")
            t.add_row(
                r.id[:8],
                f"[{color}]{r.status.value}[/{color}]",
                r.goal[:60],
                f"{r.skill_name}@{r.skill_version}" if r.skill_name else "",
                r.agent,
                r.created_at,
            )
        console.print(t)

    emit([r.model_dump() for r in runs], show)


@runs_app.command("show")
def runs_show(run_id: str) -> None:
    av = vallet()
    try:
        run = av.runs.require(run_id)
    except KeyError as exc:
        fail(str(exc))
    steps = av.runs.steps(run.id)

    def show() -> None:
        console.print(f"[bold]{run.goal}[/bold]  ({run.status.value}, {run.id})")
        for s in steps:
            body = s.content[:200] if s.content else json.dumps(s.data, default=str)[:200]
            console.print(f"  {s.seq:>3}. [cyan]{s.kind.value:<10}[/cyan] {body}")

    emit({"run": run.model_dump(), "steps": [s.model_dump() for s in steps]}, show)


# ---- corrections / learning ---------------------------------------------------------------------------


@app.command()
def correct(
    text: str,
    skill: str | None = typer.Option(None, "--skill"),
    run: str | None = typer.Option(None, "--run"),
    rule: str | None = typer.Option(None, "--rule", help='Rule JSON, e.g. \'{"type":"range","path":"total","min":0}\''),
    example: str | None = typer.Option(
        None, "--example", help="JSON {input, expected_output} that overrides behaviour."
    ),
    author: str = typer.Option("human", "--author"),
    priority: int = typer.Option(100, "--priority"),
) -> None:
    """Record a human correction (highest-priority learning signal)."""
    r = parse_json_arg(rule)
    if example:
        r = {"example": parse_json_arg(example)}
    try:
        c = vallet().correct(text, skill=skill, run_id=run, rule=r, author=author, priority=priority)
    except (ValueError, KeyError) as exc:
        fail(str(exc))
    emit(c.model_dump(), f"correction {c.id[:8]} recorded; run `av learn --name {c.skill_name or '<skill>'}` to apply")


@app.command()
def learn(
    name: str | None = typer.Option(None, "--name", help="Skill name (existing skill => new version)."),
    goal: str | None = typer.Option(None, "--goal", help="Select successful runs with a similar goal."),
    run: list[str] = typer.Option([], "--run", help="Explicit run ids."),
    description: str | None = None,
    validate: bool = typer.Option(True, "--validate/--no-validate"),
) -> None:
    """Learn a draft skill version from recorded runs and corrections."""
    from .learning import LearningError

    av = vallet()
    try:
        res = av.learn(name=name, goal=goal, run_ids=run or None, description=description)
    except LearningError as exc:
        fail(str(exc))
    out = res.summary()
    if validate:
        rep = av.validate(res.version.name, res.version.version)
        out["validation"] = {
            "passed": rep.passed,
            "score": rep.score,
            "failures": [c.model_dump() for c in rep.failures()],
        }

    def show() -> None:
        console.print(
            f"[green]✓[/green] learned [bold]{res.version.name}@{res.version.version}[/bold] "
            f"via [cyan]{res.strategy}[/cyan] from {len(res.runs_used)} runs, "
            f"{res.examples} examples, {res.rules} rules"
        )
        for n in res.notes:
            console.print(f"  [yellow]•[/yellow] {n}")
        if "validation" in out:
            v = out["validation"]
            color = "green" if v["passed"] else "red"
            console.print(
                f"  validation: [{color}]{'passed' if v['passed'] else 'failed'}[/{color}] score={v['score']}"
            )
            for f in v["failures"]:
                console.print(f"    [red]✗[/red] {f['name']}: {f['message']}")
            if v["passed"]:
                console.print(f"  next: av skill approve {res.version.name} {res.version.version} --by <you>")

    emit(out, show)


# ---- skills -------------------------------------------------------------------------------------------


@skill_app.command("new")
def skill_new(
    name: str, description: str = typer.Option(..., "--description", "-d"), tag: list[str] = typer.Option([], "--tag")
) -> None:
    """Scaffold a new skill package (draft) you can edit by hand."""
    sv = vallet().new_skill(name, description, tag)
    emit(sv.model_dump(), f"created draft {sv.name}@{sv.version} at {sv.path}")


@skill_app.command("add")
def skill_add(path: Path) -> None:
    """Register an existing skill package directory as a new draft version."""
    sv = vallet().registry.add_from_dir(path, actor="cli")
    emit(sv.model_dump(), f"registered {sv.name}@{sv.version} (draft)")


@skill_app.command("list")
def skill_list() -> None:
    skills = vallet().registry.list_skills()

    def show() -> None:
        t = Table("name", "current (trusted)", "latest", "status", "versions", "description")
        for s in skills:
            t.add_row(
                s["name"],
                s["current"] or "-",
                s["latest"],
                s["latest_status"],
                str(s["versions"]),
                s["description"][:50],
            )
        console.print(t)

    emit(skills, show)


@skill_app.command("show")
def skill_show(name: str, version: str | None = None) -> None:
    av = vallet()
    sv = av.registry.get(name, version)
    from .spec import SkillPackage

    pkg = SkillPackage(sv.path)
    emit(
        {
            "version": sv.model_dump(),
            "meta": pkg.meta,
            "rules": pkg.rules(),
            "versions": [v.model_dump() for v in av.registry.versions(name)],
            "validations": av.registry.validations(name, sv.version, limit=5),
        }
    )


@skill_app.command("validate")
def skill_validate(
    name: str, version: str | None = None, tests: bool = typer.Option(True, "--tests/--no-tests")
) -> None:
    """Run the full deterministic validation suite for a version."""
    rep = vallet().validate(name, version, run_tests=tests)

    def show() -> None:
        for c in rep.checks:
            mark = (
                "[green]✓[/green]"
                if c.passed
                else ("[yellow]![/yellow]" if c.severity == "warning" else "[red]✗[/red]")
            )
            console.print(f" {mark} {c.name}: {c.message}")
        color = "green" if rep.passed else "red"
        console.print(
            f"[{color}]{'PASSED' if rep.passed else 'FAILED'}[/{color}] {name}@{rep.version} score={rep.score}"
        )

    emit(rep.summary(), show)
    if not rep.passed:
        raise typer.Exit(1)


@skill_app.command("approve")
def skill_approve(
    name: str, version: str, by: str = typer.Option(..., "--by", help="Approver name."), note: str = ""
) -> None:
    """Approve (trust) a validated version. Makes it immutable."""
    from .skills import ApprovalError

    try:
        sv = vallet().approve(name, version, by, note)
    except ApprovalError as exc:
        fail(str(exc))
    emit(sv.model_dump(), f"[green]✓[/green] {name}@{version} approved by {by}")


@skill_app.command("deprecate")
def skill_deprecate(name: str, version: str, by: str = typer.Option(..., "--by"), reason: str = "") -> None:
    sv = vallet().registry.deprecate(name, version, by, reason)
    emit(sv.model_dump(), f"{name}@{version} deprecated")


@skill_app.command("fork")
def skill_fork(name: str, version: str | None = None, bump: str = "minor") -> None:
    """Create an editable draft from an existing version."""
    sv = vallet().registry.fork(name, version, bump=bump, actor="cli")
    emit(sv.model_dump(), f"draft {sv.name}@{sv.version} at {sv.path}")


@skill_app.command("diff")
def skill_diff(name: str, a: str, b: str) -> None:
    console.print(vallet().registry.diff(name, a, b) or "(no differences)", markup=False, highlight=False)


@skill_app.command("export")
def skill_export(name: str, version: str | None = None, out: Path = typer.Option(Path("."), "--out")) -> None:
    """Export a portable .avskill.zip (verifiable manifest)."""
    p = vallet().registry.export(name, version, out)
    emit({"file": str(p)}, f"exported {p}")


@skill_app.command("import")
def skill_import(path: Path) -> None:
    """Import a package zip/dir. Integrity verified; lands as a draft (trust is local)."""
    sv = vallet().registry.import_package(path)
    emit(sv.model_dump(), f"imported {sv.name}@{sv.version} as draft")


@skill_app.command("path")
def skill_path(name: str, version: str | None = None) -> None:
    console.print(vallet().registry.get(name, version).path)


# ---- routing / execution ------------------------------------------------------------------------------


@app.command()
def route(task: str, limit: int = 5, drafts: bool = typer.Option(False, "--drafts")) -> None:
    """Find the best skills for a task."""
    matches = vallet().route(task, limit=limit, include_drafts=drafts)

    def show() -> None:
        if not matches:
            console.print("no matching skills")
        t = Table("skill", "version", "status", "score", "why")
        for m in matches:
            t.add_row(m.name, m.version, m.status.value, f"{m.score:.3f}", "; ".join(m.reasons))
        console.print(t)

    emit([m.model_dump() for m in matches], show)


def _print_result(res: Any) -> None:
    out = res.summary()
    if _state["json"]:
        emit(out)
    else:
        color = "green" if res.ok else "red"
        console.print(f"[{color}]{res.status}[/{color}] skill={res.skill}@{res.version} run={res.run_id}")
        if res.output is not None:
            console.print_json(json.dumps(res.output, default=str))
        if res.error:
            err.print(f"[red]{res.error}[/red]")
        if res.repair:
            for n in res.repair.get("notes", []):
                console.print(f"  [yellow]repair:[/yellow] {n}")
    if not res.ok:
        raise typer.Exit(1)


@app.command()
def run(
    name: str,
    input: str = typer.Option(..., "--input", "-i", help="Input JSON (inline, @file or -)."),
    version: str | None = None,
    repair: bool = typer.Option(True, "--repair/--no-repair"),
) -> None:
    """Run a specific skill (validated + recorded)."""
    _print_result(vallet().run_skill(name, parse_json_arg(input), version=version, auto_repair=repair))


@app.command()
def execute(
    task: str,
    input: str = typer.Option(..., "--input", "-i", help="Input JSON (inline, @file or -)."),
    drafts: bool = typer.Option(False, "--drafts", help="Allow unapproved skills."),
    repair: bool = typer.Option(True, "--repair/--no-repair"),
) -> None:
    """Route a task to the best trusted skill, run, validate and repair."""
    _print_result(vallet().execute(task, parse_json_arg(input), include_drafts=drafts, auto_repair=repair))


# ---- graph / audit ------------------------------------------------------------------------------------


@graph_app.command("search")
def graph_search(text: str, type: list[str] = typer.Option([], "--type"), limit: int = 20) -> None:
    emit(vallet().graph.search(text, types=type or None, limit=limit))


@graph_app.command("neighbors")
def graph_neighbors(node_id: str, rel: str | None = None) -> None:
    emit(vallet().graph.neighbors(node_id, rel=rel))


@graph_app.command("stats")
def graph_stats() -> None:
    emit(vallet().graph.stats())


@audit_app.command("log")
def audit_log(limit: int = 30, subject: str | None = None) -> None:
    entries = vallet().db.audit_entries(limit=limit, subject=subject)

    def show() -> None:
        t = Table("seq", "time", "actor", "action", "subject")
        for e in entries:
            t.add_row(str(e["seq"]), e["ts"], e["actor"], e["action"], e["subject"])
        console.print(t)

    emit(entries, show)


@audit_app.command("verify")
def audit_verify() -> None:
    ok, n, msg = vallet().db.verify_audit()
    emit({"ok": ok, "entries": n, "message": msg}, f"{'[green]✓' if ok else '[red]✗'}[/] {msg} ({n} entries)")
    if not ok:
        raise typer.Exit(1)


# ---- servers ------------------------------------------------------------------------------------------


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the REST API and web dashboard (desktop companion)."""
    try:
        import uvicorn
    except ImportError:
        fail("install the API extra: pip install 'agentvallet[api]'")
    from .api.server import create_app

    console.print(f"AgentVallet dashboard: [bold]http://{host}:{port}/[/bold]")
    uvicorn.run(create_app(vallet()), host=host, port=port, log_level="info")


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (for Claude, Cursor, and other MCP clients)."""
    try:
        from .mcp.server import build_server
    except ImportError:
        fail("install the MCP extra: pip install 'agentvallet[mcp]'")
    build_server(vallet()).run("stdio")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
