from pathlib import Path

from agentvallet.recorder import redact, redact_text

from .conftest import record_invoice_runs


def _trusted(av, example_dir, name):
    sv = av.registry.add_from_dir(example_dir(name))
    assert av.validate(name, sv.version).passed
    av.approve(name, sv.version, "tester")


def test_router_picks_the_right_skill(av, example_dir):
    _trusted(av, example_dir, "invoice_total")
    _trusted(av, example_dir, "text_stats")
    top = av.route("calculate the tax and total for this invoice")[0]
    assert top.name == "invoice_total" and top.score >= 0.5
    top = av.route("count the words in this text")[0]
    assert top.name == "text_stats"
    assert av.route("xyzzy plugh") == []


def test_drafts_not_routed_unless_allowed(av, example_dir):
    av.registry.add_from_dir(example_dir("text_stats"))
    assert av.route("count words in text") == []
    assert av.route("count words in text", include_drafts=True)[0].name == "text_stats"


def test_execute_records_run_and_links_graph(av, example_dir):
    _trusted(av, example_dir, "text_stats")
    res = av.execute("give me text statistics", {"text": "a b a", "top": 1})
    assert res.ok and res.output["top_words"] == [{"word": "a", "count": 2}]
    run = av.runs.require(res.run_id)
    assert run.skill_name == "text_stats" and run.status.value == "success"
    kinds = [s.kind.value for s in av.runs.steps(run.id)]
    assert kinds[:2] == ["input", "tool_call"] and "validation" in kinds and kinds[-1] == "output"
    assert any(n["rel"] == "used_skill" for n in av.graph.neighbors(f"run:{run.id}"))


def test_invalid_input_is_not_repaired(av, example_dir):
    _trusted(av, example_dir, "invoice_total")
    res = av.run_skill("invoice_total", {"items": []})
    assert res.status == "invalid_input" and res.repair is None
    assert av.runs.require(res.run_id).status.value == "failed"


def test_execute_no_skill(av):
    res = av.execute("do something unknown", {})
    assert res.status == "no_skill" and not res.ok


def test_repair_falls_back_to_previous_approved_version(av, example_dir):
    _trusted(av, example_dir, "text_stats")
    fork = av.registry.fork("text_stats")
    run_py = Path(fork.path) / "run.py"
    run_py.write_text(run_py.read_text().replace('"words": len(words)', '"words": -1'))
    av.registry.refresh_draft("text_stats", fork.version)
    res = av.run_skill("text_stats", {"text": "hello there"}, version=fork.version)
    assert res.status == "repaired"
    assert res.version == "1.0.0" and res.output["words"] == 2
    assert res.repair["strategy"] == "fallback"
    no_repair = av.run_skill("text_stats", {"text": "hello"}, version=fork.version, auto_repair=False)
    assert no_repair.status == "failed" and "words_non_negative" in no_repair.error


def test_end_to_end_learn_approve_execute(av):
    record_invoice_runs(av)
    res = av.learn(goal="Compute invoice total", name="invoice_sum")
    assert av.validate("invoice_sum", res.version.version).passed
    av.approve("invoice_sum", res.version.version, "vraj")
    out = av.execute(
        "calculate the invoice total",
        {"customer": "x", "items": [{"sku": "z", "amount": 2.5}, {"sku": "y", "amount": 2.5}]},
    )
    assert out.ok, out.error
    assert out.output == {"customer": "x", "total": 5, "line_count": 2}


def test_recorder_redacts_secrets(av):
    with av.record("call api with sk-ant-abcdefghijklmnop123") as run:
        run.prompt("use api_key=supersecretvalue and Bearer abcdefghijklmnopqrstuvwxyz")
        run.input({"password": "hunter2", "nested": {"token": "x"}, "ok": "ghp_" + "a" * 30})
    stored = av.runs.require(run.id)
    assert "sk-ant" not in stored.goal
    steps = av.runs.steps(run.id)
    assert "supersecretvalue" not in steps[0].content and "abcdefghijklmnopqrstuvwxyz" not in steps[0].content
    assert steps[1].data["password"] == "[REDACTED]" and steps[1].data["nested"]["token"] == "[REDACTED]"
    assert "ghp_" not in steps[1].data["ok"]
    assert redact_text("nothing secret here") == "nothing secret here"
    assert redact([1, "AKIAABCDEFGHIJKLMNOP"]) == [1, "[REDACTED:aws_access_key]"]


def test_failed_block_marks_run_failed(av):
    try:
        with av.record("will fail") as run:
            raise RuntimeError("kaboom")
    except RuntimeError:
        pass
    r = av.runs.require(run.id)
    assert r.status.value == "failed"
    assert "kaboom" in av.runs.steps(r.id)[-1].content
