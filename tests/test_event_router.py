import json

from agentvallet.recorder import WorkRecorder
from agentvallet.router import SkillRouter
from agentvallet.skills import SkillRegistry


def test_append_only_event_history(tmp_path):
    db = tmp_path / "agentvallet.db"
    recorder = WorkRecorder(db, tmp_path / "artifacts")
    run = recorder.start("Send EPF reminder", source="test")
    recorder.event("action", "drafted email")
    recorder.correction("Zero demand must still be confirmed")
    recorder.validate("recipient_checked", True, "verified")
    finished = recorder.finish({"sent": True}, approved=True)

    history = recorder.history(run.run_id)
    assert finished.status == "success"
    assert [event["sequence"] for event in history] == list(range(1, len(history) + 1))
    assert history[0]["event_type"] == "run_started"
    assert history[-1]["event_type"] == "run_finished"
    assert any(event["event_type"] == "validation_recorded" for event in history)
    recorder.close()


def test_no_validation_never_becomes_success(tmp_path):
    recorder = WorkRecorder(tmp_path / "agentvallet.db", tmp_path / "artifacts")
    recorder.start("Unvalidated task")
    finished = recorder.finish({"done": True}, approved=True)
    assert finished.status == "needs_review"
    recorder.close()


def test_router_prefers_matching_trusted_skill(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    run = {"run_id": "r1", "goal": "Send monthly EPF pending reminder email"}
    package = registry.create_from_run(run, "epf_pending_email")
    manifest_path = package.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "trusted"
    manifest["tags"] = ["epf", "email", "monthly"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    router = SkillRouter(registry)
    decision = router.route("Send EPF pending email")
    assert decision.action == "reuse_skill"
    assert decision.skill is not None
    assert decision.skill.name == "epf_pending_email"


def test_router_falls_back_for_unrelated_goal(tmp_path):
    registry = SkillRegistry(tmp_path / "skills")
    run = {"run_id": "r1", "goal": "Send monthly EPF reminder email"}
    package = registry.create_from_run(run, "epf_email")
    manifest_path = package.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "trusted"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    decision = SkillRouter(registry).route("Reconcile audited FMR bank statement")
    assert decision.action == "solve_new"
    assert decision.skill is None
