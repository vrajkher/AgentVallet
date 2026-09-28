import sqlite3

import pytest

from agentvallet.models import Correction, Run, RunStatus, Step, StepKind
from agentvallet.store import ArtifactIntegrityError, ArtifactStore, Database, RunStore


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def test_migrations_idempotent(db):
    assert db.migrate() == 2
    assert db.migrate() == 2


def test_run_lifecycle_and_steps(db):
    rs = RunStore(db)
    run = rs.create(Run(goal="do thing", tags=["x"]))
    rs.add_step(Step(run_id=run.id, kind=StepKind.INPUT, data={"a": 1}))
    rs.add_step(Step(run_id=run.id, kind=StepKind.OUTPUT, data={"b": 2}))
    steps = rs.steps(run.id)
    assert [s.seq for s in steps] == [1, 2]
    assert steps[1].data == {"b": 2}
    done = rs.finish(run.id, RunStatus.SUCCESS, {"k": "v"})
    assert done.status == RunStatus.SUCCESS and done.finished_at and done.metadata == {"k": "v"}
    assert rs.get(run.id[:8]).id == run.id  # prefix lookup
    assert rs.count("success") == 1
    assert rs.list(search="thing")[0].id == run.id


def test_corrections_priority_and_applied(db):
    rs = RunStore(db)
    rs.add_correction(Correction(text="low", skill_name="s", priority=1))
    hi = rs.add_correction(Correction(text="high", skill_name="s", priority=500))
    got = rs.corrections(skill_name="s")
    assert [c.text for c in got] == ["high", "low"]
    rs.mark_corrections_applied([hi.id], "s@1.0.0")
    assert [c.text for c in rs.corrections(skill_name="s", pending_only=True)] == ["low"]


def test_audit_chain_is_append_only_and_tamper_evident(db):
    for i in range(3):
        db.audit("act", f"s{i}", {"i": i})
    ok, n, _ = db.verify_audit()
    assert ok and n == 3
    conn = sqlite3.connect(db.path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET subject = 'x' WHERE seq = 2")
    # Simulate a privileged attacker removing the guard and editing history.
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("UPDATE audit_log SET details = '{\"i\": 99}' WHERE seq = 2")
    conn.commit()
    conn.close()
    ok, n, msg = db.verify_audit()
    assert not ok and "seq 2" in msg


def test_artifacts_dedup_and_integrity(db, tmp_path):
    store = ArtifactStore(db, tmp_path / "objects")
    a = store.put_bytes(b"hello", name="h.txt")
    b = store.put_bytes(b"hello", name="h.txt")
    assert a.hash == b.hash and a.media_type == "text/plain"
    assert store.get_bytes(a.hash) == b"hello"
    assert len(store.list()) == 1
    path = store._path(a.hash)
    path.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        store.get_bytes(a.hash)
    assert store.verify_all() == [a.hash]
