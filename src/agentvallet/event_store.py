from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import utc_now


@dataclass(slots=True)
class StoredEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


class EventStore:
    """Append-only event log for replayable AgentVallet work history."""

    def __init__(self, db_path: str | Path = "data/agentvallet.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, sequence)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, sequence)"
        )
        self.conn.commit()

    def append(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> StoredEvent:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sequence = int(row[0])
        item = StoredEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            payload=dict(payload or {}),
            created_at=utc_now(),
        )
        self.conn.execute(
            "INSERT INTO run_events(event_id, run_id, sequence, event_type, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (
                item.event_id,
                item.run_id,
                item.sequence,
                item.event_type,
                json.dumps(item.payload, ensure_ascii=False),
                item.created_at,
            ),
        )
        self.conn.commit()
        return item

    def list(self, run_id: str) -> list[StoredEvent]:
        rows = self.conn.execute(
            "SELECT event_id, run_id, sequence, event_type, payload_json, created_at FROM run_events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        return [
            StoredEvent(
                event_id=row[0],
                run_id=row[1],
                sequence=row[2],
                event_type=row[3],
                payload=json.loads(row[4]),
                created_at=row[5],
            )
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()
