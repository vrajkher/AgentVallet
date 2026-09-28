"""REST API + web dashboard (the desktop companion).

Optional bearer-token auth: set ``AV_API_TOKEN`` and send ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .. import __version__
from ..core import AgentVallet
from ..learning import LearningError
from ..models import RunStatus, StepKind
from ..skills import ApprovalError, IntegrityError, SkillNotFound
from ..spec import PackageError, SkillPackage

WEB_DIR = Path(__file__).parent / "web"


class StepIn(BaseModel):
    kind: StepKind
    content: str = ""
    data: Any = None


class RunIn(BaseModel):
    goal: str
    tags: list[str] = Field(default_factory=list)
    agent: str = "api"
    steps: list[StepIn] = Field(default_factory=list)
    status: RunStatus | None = None


class FinishIn(BaseModel):
    status: RunStatus = RunStatus.SUCCESS


class CorrectionIn(BaseModel):
    text: str
    skill: str | None = None
    run_id: str | None = None
    rule: dict[str, Any] | None = None
    author: str = "human"
    priority: int = 100


class LearnIn(BaseModel):
    name: str | None = None
    goal: str | None = None
    run_ids: list[str] | None = None
    description: str | None = None
    validate_after: bool = True


class ApproveIn(BaseModel):
    approver: str
    note: str = ""


class DeprecateIn(BaseModel):
    actor: str
    reason: str = ""


class RouteIn(BaseModel):
    task: str
    limit: int = 5
    include_drafts: bool = False


class ExecuteIn(BaseModel):
    task: str = ""
    input: Any
    skill: str | None = None
    version: str | None = None
    include_drafts: bool = False
    auto_repair: bool = True


def create_app(av: AgentVallet | None = None) -> FastAPI:
    av = av or AgentVallet()
    token = os.environ.get("AV_API_TOKEN")
    app = FastAPI(title="AgentVallet", version=__version__, description="Portable AI Work, Memory & Skill Brain")

    def auth(request: Request) -> None:
        if not token:
            return
        header = request.headers.get("authorization", "")
        if not secrets.compare_digest(header, f"Bearer {token}"):
            raise HTTPException(401, "invalid or missing bearer token")

    guarded = [Depends(auth)]

    def not_found(exc: Exception) -> HTTPException:
        return HTTPException(404, str(exc).strip("'\""))

    # ---- meta --------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "auth": bool(token)}

    @app.get("/api/stats", dependencies=guarded)
    def stats() -> dict[str, Any]:
        return av.stats()

    @app.get("/api/doctor", dependencies=guarded)
    def doctor() -> list[dict[str, Any]]:
        return [{"check": c, "ok": ok, "detail": d} for c, ok, d in av.doctor()]

    # ---- runs --------------------------------------------------------------------------------------

    @app.get("/api/runs", dependencies=guarded)
    def list_runs(
        status: str | None = None, skill: str | None = None, search: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return [r.model_dump() for r in av.runs.list(status, skill, search, min(limit, 500), offset)]

    @app.get("/api/runs/{run_id}", dependencies=guarded)
    def get_run(run_id: str) -> dict[str, Any]:
        run = av.runs.get(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        corr = av.runs.corrections(run_ids=[run.id])
        return {
            "run": run.model_dump(),
            "steps": [s.model_dump() for s in av.runs.steps(run.id)],
            "corrections": [c.model_dump() for c in corr],
        }

    @app.post("/api/runs", dependencies=guarded, status_code=201)
    def create_run(body: RunIn) -> dict[str, Any]:
        h = av.record(body.goal, tags=body.tags, agent=body.agent)
        for st in body.steps:
            h.step(st.kind, st.content, st.data)
        if body.status:
            h.finish(body.status)
        return h.run.model_dump()

    @app.post("/api/runs/{run_id}/steps", dependencies=guarded, status_code=201)
    def add_step(run_id: str, body: StepIn) -> dict[str, Any]:
        try:
            h = av.recorder.resume(run_id)
        except KeyError as exc:
            raise not_found(exc) from exc
        if h.finished:
            raise HTTPException(409, "run already finished")
        return h.step(body.kind, body.content, body.data).model_dump()

    @app.post("/api/runs/{run_id}/finish", dependencies=guarded)
    def finish_run(run_id: str, body: FinishIn) -> dict[str, Any]:
        try:
            return av.recorder.resume(run_id).finish(body.status).model_dump()
        except KeyError as exc:
            raise not_found(exc) from exc

    @app.get("/api/artifacts/{digest}", dependencies=guarded)
    def get_artifact(digest: str) -> Response:
        meta = av.artifacts.meta(digest)
        if not meta:
            raise HTTPException(404, "artifact not found")
        return Response(av.artifacts.get_bytes(digest), media_type=meta.media_type)

    # ---- corrections -------------------------------------------------------------------------------

    @app.get("/api/corrections", dependencies=guarded)
    def list_corrections(skill: str | None = None, pending: bool = False) -> list[dict[str, Any]]:
        return [c.model_dump() for c in av.runs.corrections(skill_name=skill, pending_only=pending)]

    @app.post("/api/corrections", dependencies=guarded, status_code=201)
    def add_correction(body: CorrectionIn) -> dict[str, Any]:
        try:
            return av.correct(body.text, body.skill, body.run_id, body.rule, body.author, body.priority).model_dump()
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    # ---- learning / skills -------------------------------------------------------------------------

    @app.post("/api/learn", dependencies=guarded)
    def learn(body: LearnIn) -> dict[str, Any]:
        try:
            res = av.learn(name=body.name, goal=body.goal, run_ids=body.run_ids, description=body.description)
        except (LearningError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        out = res.summary()
        if body.validate_after:
            out["validation"] = av.validate(res.version.name, res.version.version).summary()
        return out

    @app.get("/api/skills", dependencies=guarded)
    def list_skills() -> list[dict[str, Any]]:
        return av.registry.list_skills()

    @app.get("/api/skills/{name}", dependencies=guarded)
    def get_skill(name: str, version: str | None = None) -> dict[str, Any]:
        try:
            sv = av.registry.get(name, version)
        except SkillNotFound as exc:
            raise not_found(exc) from exc
        pkg = SkillPackage(sv.path)
        return {
            "version": sv.model_dump(),
            "meta": pkg.meta,
            "rules": pkg.rules(),
            "files": sorted(pkg.file_hashes()),
            "versions": [v.model_dump() for v in av.registry.versions(name)],
            "validations": av.registry.validations(name, sv.version, limit=10),
            "corrections": [c.model_dump() for c in av.runs.corrections(skill_name=name)],
        }

    @app.get("/api/skills/{name}/{version}/files/{path:path}", dependencies=guarded)
    def get_skill_file(name: str, version: str, path: str) -> Response:
        try:
            root = Path(av.registry.get(name, version).path).resolve()
        except SkillNotFound as exc:
            raise not_found(exc) from exc
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404, "file not found")
        return Response(target.read_text(errors="replace"), media_type="text/plain; charset=utf-8")

    @app.post("/api/skills/{name}/{version}/validate", dependencies=guarded)
    def validate(name: str, version: str, tests: bool = True) -> dict[str, Any]:
        try:
            return av.validate(name, version, run_tests=tests).summary()
        except SkillNotFound as exc:
            raise not_found(exc) from exc

    @app.post("/api/skills/{name}/{version}/approve", dependencies=guarded)
    def approve(name: str, version: str, body: ApproveIn) -> dict[str, Any]:
        try:
            return av.approve(name, version, body.approver, body.note).model_dump()
        except SkillNotFound as exc:
            raise not_found(exc) from exc
        except ApprovalError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/skills/{name}/{version}/deprecate", dependencies=guarded)
    def deprecate(name: str, version: str, body: DeprecateIn) -> dict[str, Any]:
        try:
            return av.registry.deprecate(name, version, body.actor, body.reason).model_dump()
        except SkillNotFound as exc:
            raise not_found(exc) from exc

    @app.get("/api/skills/{name}/diff", dependencies=guarded)
    def diff(name: str, a: str, b: str) -> dict[str, str]:
        try:
            return {"diff": av.registry.diff(name, a, b)}
        except SkillNotFound as exc:
            raise not_found(exc) from exc

    @app.get("/api/skills/{name}/{version}/export", dependencies=guarded)
    def export(name: str, version: str) -> FileResponse:
        try:
            p = av.registry.export(name, version, av.settings.exports_dir)
        except (SkillNotFound, IntegrityError, PackageError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return FileResponse(p, filename=p.name, media_type="application/zip")

    # ---- routing / execution -----------------------------------------------------------------------

    @app.post("/api/route", dependencies=guarded)
    def route(body: RouteIn) -> list[dict[str, Any]]:
        return [m.model_dump() for m in av.route(body.task, body.limit, body.include_drafts)]

    @app.post("/api/execute", dependencies=guarded)
    def execute(body: ExecuteIn) -> dict[str, Any]:
        if not body.task and not body.skill:
            raise HTTPException(400, "provide a task or a skill")
        try:
            res = av.execute(
                body.task or f"run {body.skill}",
                body.input,
                skill=body.skill,
                version=body.version,
                include_drafts=body.include_drafts,
                auto_repair=body.auto_repair,
            )
        except (SkillNotFound, IntegrityError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return res.summary()

    # ---- graph / audit -----------------------------------------------------------------------------

    @app.get("/api/graph/search", dependencies=guarded)
    def graph_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
        return av.graph.search(q, limit=limit)

    @app.get("/api/graph/neighbors", dependencies=guarded)
    def graph_neighbors(id: str) -> list[dict[str, Any]]:
        return av.graph.neighbors(id)

    @app.get("/api/graph/stats", dependencies=guarded)
    def graph_stats() -> dict[str, Any]:
        return av.graph.stats()

    @app.get("/api/audit", dependencies=guarded)
    def audit(limit: int = 100, subject: str | None = None) -> list[dict[str, Any]]:
        return av.db.audit_entries(limit=min(limit, 1000), subject=subject)

    @app.get("/api/audit/verify", dependencies=guarded)
    def audit_verify() -> dict[str, Any]:
        ok, n, msg = av.db.verify_audit()
        return {"ok": ok, "entries": n, "message": msg}

    # ---- dashboard ---------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse((WEB_DIR / "index.html").read_text())

    app.state.av = av
    return app
