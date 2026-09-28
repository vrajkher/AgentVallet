"""Exact run history: runs, ordered steps and human corrections."""

from __future__ import annotations

import builtins
from typing import Any

from ..models import Correction, Run, RunStatus, Step
from ..util import dumps, loads, now
from .db import Database


def _run_from_row(r: Any) -> Run:
    d = dict(r)
    d["tags"] = loads(d["tags"], [])
    d["metadata"] = loads(d["metadata"], {})
    return Run(**d)


def _step_from_row(r: Any) -> Step:
    d = dict(r)
    d["data"] = loads(d["data"])
    return Step(**d)


def _corr_from_row(r: Any) -> Correction:
    d = dict(r)
    d["rule"] = loads(d["rule"])
    return Correction(**d)


class RunStore:
    def __init__(self, db: Database):
        self.db = db

    # ---- runs --------------------------------------------------------------------------------------

    def create(self, run: Run) -> Run:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO runs(id,goal,status,agent,tags,metadata,skill_name,skill_version,created_at,finished_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.goal,
                    run.status.value,
                    run.agent,
                    dumps(run.tags),
                    dumps(run.metadata),
                    run.skill_name,
                    run.skill_version,
                    run.created_at,
                    run.finished_at,
                ),
            )
            self.db.audit("run.start", run.id, {"goal": run.goal, "agent": run.agent}, run.agent, conn=c)
        return run

    def get(self, run_id: str) -> Run | None:
        row = self.db.query_one("SELECT * FROM runs WHERE id = ? OR id LIKE ?", (run_id, run_id + "%"))
        return _run_from_row(row) if row else None

    def require(self, run_id: str) -> Run:
        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run {run_id!r} not found")
        return run

    def finish(self, run_id: str, status: RunStatus, metadata: dict[str, Any] | None = None) -> Run:
        run = self.require(run_id)
        meta = {**run.metadata, **(metadata or {})}
        with self.db.tx() as c:
            c.execute(
                "UPDATE runs SET status = ?, finished_at = ?, metadata = ? WHERE id = ?",
                (status.value, now(), dumps(meta), run.id),
            )
            self.db.audit("run.finish", run.id, {"status": status.value}, run.agent, conn=c)
        return self.require(run.id)

    def link_skill(self, run_id: str, name: str, version: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE runs SET skill_name = ?, skill_version = ? WHERE id = ?", (name, version, run_id))

    def list(
        self,
        status: RunStatus | str | None = None,
        skill_name: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        sql = "SELECT * FROM runs WHERE 1=1"
        params: builtins.list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(str(status.value if isinstance(status, RunStatus) else status))
        if skill_name:
            sql += " AND skill_name = ?"
            params.append(skill_name)
        if search:
            sql += " AND (goal LIKE ? OR tags LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [_run_from_row(r) for r in self.db.query(sql, params)]

    def count(self, status: str | None = None) -> int:
        if status:
            row = self.db.query_one("SELECT COUNT(*) FROM runs WHERE status = ?", (status,))
        else:
            row = self.db.query_one("SELECT COUNT(*) FROM runs")
        return int(row[0]) if row else 0

    # ---- steps -------------------------------------------------------------------------------------

    def add_step(self, step: Step) -> Step:
        with self.db.tx() as c:
            row = c.execute("SELECT COALESCE(MAX(seq), 0) FROM steps WHERE run_id = ?", (step.run_id,)).fetchone()
            step.seq = int(row[0]) + 1
            c.execute(
                "INSERT INTO steps(id,run_id,seq,kind,content,data,artifact_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    step.id,
                    step.run_id,
                    step.seq,
                    step.kind.value,
                    step.content,
                    None if step.data is None else dumps(step.data),
                    step.artifact_hash,
                    step.created_at,
                ),
            )
        return step

    def steps(self, run_id: str, kind: str | None = None) -> builtins.list[Step]:
        if kind:
            rows = self.db.query("SELECT * FROM steps WHERE run_id = ? AND kind = ? ORDER BY seq", (run_id, kind))
        else:
            rows = self.db.query("SELECT * FROM steps WHERE run_id = ? ORDER BY seq", (run_id,))
        return [_step_from_row(r) for r in rows]

    # ---- corrections -------------------------------------------------------------------------------

    def add_correction(self, corr: Correction) -> Correction:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO corrections(id,text,run_id,skill_name,rule,priority,applied_in,author,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    corr.id,
                    corr.text,
                    corr.run_id,
                    corr.skill_name,
                    None if corr.rule is None else dumps(corr.rule),
                    corr.priority,
                    corr.applied_in,
                    corr.author,
                    corr.created_at,
                ),
            )
            self.db.audit(
                "correction.add",
                corr.skill_name or corr.run_id or corr.id,
                {"text": corr.text, "rule": corr.rule},
                corr.author,
                conn=c,
            )
        return corr

    def corrections(
        self, skill_name: str | None = None, run_ids: builtins.list[str] | None = None, pending_only: bool = False
    ) -> builtins.list[Correction]:
        sql = "SELECT * FROM corrections WHERE 1=1"
        params: builtins.list[Any] = []
        conds = []
        if skill_name:
            conds.append("skill_name = ?")
            params.append(skill_name)
        if run_ids:
            conds.append(f"run_id IN ({','.join('?' * len(run_ids))})")
            params += run_ids
        if conds:
            sql += " AND (" + " OR ".join(conds) + ")"
        if pending_only:
            sql += " AND applied_in IS NULL"
        sql += " ORDER BY priority DESC, created_at ASC"
        return [_corr_from_row(r) for r in self.db.query(sql, params)]

    def mark_corrections_applied(self, ids: builtins.list[str], version_ref: str) -> None:
        if not ids:
            return
        with self.db.tx() as c:
            c.executemany("UPDATE corrections SET applied_in = ? WHERE id = ?", [(version_ref, i) for i in ids])
