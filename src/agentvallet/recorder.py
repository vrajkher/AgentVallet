from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .models import Event, RunRecord, ValidationResult, utc_now


class WorkRecorder:
    """Local-first recorder for observable AI-assisted work."""

    def __init__(self, db_path: str | Path = "data/agentvallet.db", artifact_root: str | Path = "data/artifacts"):
        self.db_path = Path(db_path)
        self.artifact_root = Path(artifact_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()
        self.current: RunRecord | None = None

    def start(self, goal: str, *, source: str = "manual", tags: list[str] | None = None) -> RunRecord:
        if self.current and self.current.status == "running":
            raise RuntimeError("A run is already active")
        self.current = RunRecord(
            run_id=str(uuid.uuid4()), goal=goal, source=source, tags=list(tags or [])
        )
        self._save()
        return self.current

    def event(self, event_type: str, content: str = "", **metadata: Any) -> Event:
        run = self._require_run()
        item = Event(event_type=event_type, content=content, metadata=metadata)
        run.events.append(item)
        self._save()
        return item

    def correction(self, text: str, **metadata: Any) -> Event:
        return self.event("correction", text, priority="high", **metadata)

    def validate(self, name: str, passed: bool, details: str = "") -> ValidationResult:
        run = self._require_run()
        result = ValidationResult(name=name, passed=passed, details=details)
        run.validations.append(result)
        self._save()
        return result

    def snapshot(self, path: str | Path) -> dict[str, Any]:
        run = self._require_run()
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target_dir = self.artifact_root / run.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest[:12]}_{source.name}"
        shutil.copy2(source, target)
        artifact = {
            "source_path": str(source),
            "snapshot_path": str(target),
            "sha256": digest,
            "size": source.stat().st_size,
            "captured_at": utc_now(),
        }
        run.artifacts.append(artifact)
        self._save()
        return artifact

    def finish(self, final_result: dict[str, Any] | None = None, *, approved: bool = False) -> RunRecord:
        run = self._require_run()
        run.finished_at = utc_now()
        run.approved = approved
        run.final_result = dict(final_result or {})
        run.status = "success" if all(v.passed for v in run.validations) else "needs_review"
        self._save()
        completed = run
        self.current = None
        return completed

    def load(self, run_id: str) -> RunRecord:
        row = self.conn.execute("SELECT payload_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        data = json.loads(row[0])
        data["events"] = [Event(**item) for item in data.get("events", [])]
        data["validations"] = [ValidationResult(**item) for item in data.get("validations", [])]
        return RunRecord(**data)

    def close(self) -> None:
        self.conn.close()

    def _require_run(self) -> RunRecord:
        if not self.current:
            raise RuntimeError("No active run")
        return self.current

    def _save(self) -> None:
        run = self._require_run()
        self.conn.execute(
            """
            INSERT INTO runs(run_id, payload_json, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (run.run_id, json.dumps(run.to_dict(), ensure_ascii=False), utc_now()),
        )
        self.conn.commit()
