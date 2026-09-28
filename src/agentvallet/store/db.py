"""SQLite exact-history store with versioned migrations and a hash-chained audit log."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..util import dumps, loads, now, sha256_bytes

MIGRATIONS: list[str] = [
    # 1: core schema
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        agent TEXT NOT NULL DEFAULT 'unknown',
        tags TEXT NOT NULL DEFAULT '[]',
        metadata TEXT NOT NULL DEFAULT '{}',
        skill_name TEXT,
        skill_version TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX idx_runs_status ON runs(status);
    CREATE INDEX idx_runs_skill ON runs(skill_name);

    CREATE TABLE steps (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        data TEXT,
        artifact_hash TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, seq)
    );

    CREATE TABLE artifacts (
        hash TEXT PRIMARY KEY,
        size INTEGER NOT NULL,
        name TEXT NOT NULL,
        media_type TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE corrections (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        run_id TEXT,
        skill_name TEXT,
        rule TEXT,
        priority INTEGER NOT NULL DEFAULT 100,
        applied_in TEXT,
        author TEXT NOT NULL DEFAULT 'human',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_corrections_skill ON corrections(skill_name);

    CREATE TABLE skill_versions (
        name TEXT NOT NULL,
        version TEXT NOT NULL,
        status TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        path TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        parent_version TEXT,
        created_at TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT,
        PRIMARY KEY (name, version)
    );

    CREATE TABLE validations (
        id TEXT PRIMARY KEY,
        skill_name TEXT NOT NULL,
        version TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        passed INTEGER NOT NULL,
        score REAL NOT NULL,
        report TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_validations_skill ON validations(skill_name, version);

    CREATE TABLE audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        subject TEXT NOT NULL,
        details TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """,
    # 2: local graph index
    """
    CREATE TABLE graph_nodes (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        label TEXT NOT NULL,
        props TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_graph_nodes_type ON graph_nodes(type);
    CREATE TABLE graph_edges (
        src TEXT NOT NULL,
        rel TEXT NOT NULL,
        dst TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        props TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        PRIMARY KEY (src, rel, dst)
    );
    CREATE INDEX idx_graph_edges_dst ON graph_edges(dst);
    """,
]

GENESIS = "0" * 64


class Database:
    """Thin wrapper that hands out short-lived connections (safe across threads)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def query(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    def query_one(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def migrate(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                current = conn.execute("PRAGMA user_version").fetchone()[0]
                for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
                    conn.executescript("BEGIN;" + script + f"PRAGMA user_version = {i}; COMMIT;")
                return int(conn.execute("PRAGMA user_version").fetchone()[0])
            finally:
                conn.close()

    # ---- audit log -------------------------------------------------------------------------------

    @staticmethod
    def _audit_hash(prev: str, ts: str, actor: str, action: str, subject: str, details: str) -> str:
        return sha256_bytes("\x1f".join([prev, ts, actor, action, subject, details]).encode())

    def audit(
        self,
        action: str,
        subject: str,
        details: dict[str, Any] | None = None,
        actor: str = "system",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        def _write(c: sqlite3.Connection) -> None:
            row = c.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts, det = now(), dumps(details or {})
            h = self._audit_hash(prev, ts, actor, action, subject, det)
            c.execute(
                "INSERT INTO audit_log(ts, actor, action, subject, details, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (ts, actor, action, subject, det, prev, h),
            )

        if conn is not None:
            _write(conn)
        else:
            with self.tx() as c:
                _write(c)

    def audit_entries(self, limit: int = 100, subject: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_log"
        params: list[Any] = []
        if subject:
            sql += " WHERE subject = ? OR subject LIKE ?"
            params += [subject, subject + "@%"]
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        return [{**dict(r), "details": loads(r["details"], {})} for r in self.query(sql, params)]

    def verify_audit(self) -> tuple[bool, int, str]:
        """Recompute the hash chain. Returns (ok, entries_checked, message)."""
        prev = GENESIS
        count = 0
        for r in self.query("SELECT * FROM audit_log ORDER BY seq ASC"):
            expected = self._audit_hash(prev, r["ts"], r["actor"], r["action"], r["subject"], r["details"])
            if r["prev_hash"] != prev or r["hash"] != expected:
                return False, count, f"audit chain broken at seq {r['seq']}"
            prev = r["hash"]
            count += 1
        return True, count, "audit chain intact"
