import pytest

from agentvallet.learning import LearningError
from agentvallet.learning.infer import infer_schema, mine_rules, synthesize_mapping
from agentvallet.models import SkillStatus
from agentvallet.spec import SkillPackage

from .conftest import record_invoice_runs

PAIRS = [
    ({"c": "a", "items": [{"x": 1}, {"x": 2}]}, {"c": "a", "total": 3, "n": 2}),
    ({"c": "b", "items": [{"x": 5}, {"x": 5}, {"x": 1.5}]}, {"c": "b", "total": 11.5, "n": 3}),
]


def test_infer_schema_merges_types_and_required():
    s = infer_schema([{"a": 1, "b": "x"}, {"a": 2.5}])
    assert s["properties"]["a"]["type"] == "number"
    assert s["required"] == ["a"]


def test_mine_rules_finds_sum_and_copy_relations():
    rules = {r["id"]: r for r in mine_rules(PAIRS)}
    assert "sum_equals_total" in rules and rules["sum_equals_total"]["items_path"] == "items[*].x"
    assert rules["equals_path_c"]["other_path"] == "c"
    assert "required_total" in rules and "range_total" in rules


def test_synthesize_mapping():
    spec = synthesize_mapping(PAIRS)
    assert spec == {
        "c": {"op": "copy", "path": "c"},
        "n": {"op": "count", "path": "items"},
        "total": {"op": "sum", "path": "items[*].x", "round": 1},
    }
    assert synthesize_mapping([({"a": 1}, {"z": "random"}), ({"a": 2}, {"z": "other"})]) is None


def test_learn_synthesized_then_validate(av):
    record_invoice_runs(av)
    res = av.learn(goal="compute invoice total", name="invoice_sum")
    assert res.strategy == "synthesized" and res.examples == 3
    report = av.validate("invoice_sum", res.version.version)
    assert report.passed, report.failures()
    # Generalises to unseen input.
    pkg = av.registry.package("invoice_sum", res.version.version)
    out = av.runner.run(pkg, {"customer": "new", "items": [{"sku": "q", "amount": 1.25}, {"sku": "r", "amount": 2}]})
    assert out.ok and out.output == {"customer": "new", "total": 3.25, "line_count": 2}


def test_learn_prefers_recorded_code(av):
    record_invoice_runs(av, with_code=True)
    res = av.learn(goal="compute invoice total")
    assert res.strategy == "recorded"
    assert res.version.name == "compute_invoice_total"


def test_learn_lookup_fallback_is_flagged(av):
    for text, label in [("great product", "positive"), ("awful", "negative")]:
        with av.record("classify sentiment") as run:
            run.input({"text": text})
            run.output({"label": label})
    res = av.learn(goal="classify sentiment", name="sentiment")
    assert res.strategy == "lookup"
    pkg = SkillPackage(res.version.path)
    assert "lookup table" in pkg.read_text("exceptions.md")
    assert not av.runner.run(pkg, {"text": "unseen"}).ok


def test_corrections_become_rules_and_examples_in_new_version(av):
    record_invoice_runs(av, with_code=True)
    v1 = av.learn(goal="compute invoice total", name="inv").version
    assert av.validate("inv", v1.version).passed
    av.approve("inv", v1.version, "alice")
    av.correct("totals are never negative", skill="inv", rule={"type": "range", "path": "total", "min": 0})
    av.correct(
        "empty invoices total zero",
        skill="inv",
        rule={
            "example": {
                "input": {"customer": "z", "items": []},
                "expected_output": {"customer": "z", "total": 0, "line_count": 0},
            }
        },
    )
    res = av.learn(name="inv")
    assert res.version.version == "1.1.0" and res.version.parent_version == "1.0.0"
    assert len(res.corrections_applied) == 2
    pkg = SkillPackage(res.version.path)
    sources = {r["id"]: r.get("source") for r in pkg.rules()}
    assert any(s == "correction" for s in sources.values())
    assert any(e["input"]["customer"] == "z" for e in pkg.examples())
    assert "never negative" in pkg.read_text("corrections.md")
    # v1 is untouched and still trusted
    assert av.registry.get("inv").version == "1.0.0"
    assert av.registry.get("inv", "1.0.0").status == SkillStatus.APPROVED
    assert av.runs.corrections(skill_name="inv", pending_only=True) == []


def test_learn_without_runs_errors(av):
    with pytest.raises(LearningError):
        av.learn(goal="nothing recorded")
