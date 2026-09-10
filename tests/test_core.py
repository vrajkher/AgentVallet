from pathlib import Path

from agentvallet import SkillRegistry, WorkRecorder


def test_record_validate_and_freeze_candidate(tmp_path: Path):
    recorder = WorkRecorder(tmp_path / "runs.db", tmp_path / "artifacts")
    recorder.start("Reconcile FMR", source="test", tags=["finance"])
    recorder.event("prompt", "Reconcile bank and FMR")
    recorder.correction("Treat bank charge as reconciliation adjustment")
    recorder.validate("difference_zero", True, "difference = 0")
    finished = recorder.finish({"difference": 0}, approved=True)

    assert finished.status == "success"
    assert finished.approved is True
    assert finished.events[-1].event_type == "correction"

    registry = SkillRegistry(tmp_path / "skills")
    skill = registry.create_from_run(finished.to_dict(), "fmr_reconciliation")

    assert skill.manifest["spec"] == "agentvallet.skill/v1"
    assert (skill.path / "run.py").exists()
    assert (skill.path / "validate.py").exists()
    assert skill.trusted is False

    recorder.close()


def test_artifact_snapshot_is_hashed(tmp_path: Path):
    source = tmp_path / "work.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    recorder = WorkRecorder(tmp_path / "runs.db", tmp_path / "artifacts")
    recorder.start("Capture script")
    artifact = recorder.snapshot(source)

    assert len(artifact["sha256"]) == 64
    assert Path(artifact["snapshot_path"]).exists()
    recorder.close()
