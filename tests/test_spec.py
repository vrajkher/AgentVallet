import pytest

from agentvallet.spec import PackageContent, PackageError, SkillPackage, build_package, jsonpath, scaffold
from agentvallet.spec.rules import evaluate, evaluate_rule, validate_rule_shape

DOC = {"a": {"b": [{"c": 1}, {"c": 2}]}, "total": 3, "name": "x", "tags": ["p", "q"]}


def test_jsonpath_resolve():
    assert jsonpath.resolve(DOC, "a.b[*].c") == [1, 2]
    assert jsonpath.resolve(DOC, "$.a.b[1].c") == [2]
    assert jsonpath.resolve(DOC, "a.b[-1].c") == [2]
    assert jsonpath.resolve(DOC, "missing") == []
    assert jsonpath.first(DOC, "total") == 3
    flat = jsonpath.flatten(DOC)
    assert flat["a.b[*].c"] == 1 and flat["tags[*]"] == "p"


@pytest.mark.parametrize(
    "rule,ok",
    [
        ({"type": "required", "path": "total"}, True),
        ({"type": "required", "path": "nope"}, False),
        ({"type": "type", "path": "total", "expected": "integer"}, True),
        ({"type": "type", "path": "name", "expected": "number"}, False),
        ({"type": "equals", "path": "name", "value": "x"}, True),
        ({"type": "enum", "path": "name", "values": ["y"]}, False),
        ({"type": "regex", "path": "name", "pattern": "^x$"}, True),
        ({"type": "range", "path": "a.b[*].c", "min": 1, "max": 2}, True),
        ({"type": "range", "path": "a.b[*].c", "min": 2}, False),
        ({"type": "length", "path": "tags", "min": 1, "max": 2}, True),
        ({"type": "sum_equals", "path": "total", "items_path": "a.b[*].c"}, True),
        ({"type": "sum_equals", "path": "total", "items_path": "a.b[0].c"}, False),
        ({"type": "not_empty", "path": "tags"}, True),
        ({"type": "forbid_path", "path": "secret"}, True),
        ({"type": "forbid_pattern", "pattern": "sk-[a-z]+"}, True),
    ],
)
def test_rule_types(rule, ok):
    assert evaluate_rule(rule, {}, DOC).passed is ok


def test_equals_path_across_input_output():
    rule = {"type": "equals_path", "path": "name", "other_path": "who", "other_target": "input"}
    assert evaluate_rule(rule, {"who": "x"}, DOC).passed
    assert not evaluate_rule(rule, {"who": "z"}, DOC).passed


def test_rule_shape_errors_and_disabled():
    assert "unknown rule type" in validate_rule_shape({"type": "nope", "path": "x"})
    assert "missing" in validate_rule_shape({"type": "regex", "path": "x"})
    assert "invalid regex" in validate_rule_shape({"type": "regex", "path": "x", "pattern": "("})
    assert evaluate([{"type": "required", "path": "zzz", "enabled": False}], {}, DOC) == []


def test_build_package_manifest_and_tamper(tmp_path):
    pkg = scaffold(tmp_path / "demo", "demo_skill", "A demo")
    assert pkg.verify_manifest() == []
    checks = {c.name: c for c in pkg.validate_structure()}
    assert all(c.passed for c in checks.values()), checks
    (pkg.path / "run.py").write_text("print('hacked')")
    assert "modified file run.py" in pkg.verify_manifest()
    (pkg.path / "extra.txt").write_text("x")
    assert any("unexpected file" in p for p in pkg.verify_manifest())


def test_build_rejects_bad_name_and_overwrite(tmp_path):
    with pytest.raises(PackageError):
        build_package(tmp_path / "x", PackageContent(name="Bad-Name", version="1.0.0", description="d"))
    scaffold(tmp_path / "ok", "ok_skill", "d")
    with pytest.raises(PackageError):
        scaffold(tmp_path / "ok", "ok_skill", "d")


def test_structure_detects_problems(tmp_path):
    pkg = scaffold(tmp_path / "p", "p_skill", "d")
    (pkg.path / "rules.json").write_text('{"rules": [{"type": "bogus", "path": "x"}]}')
    (pkg.path / "workflow.md").unlink()
    pkg = SkillPackage(pkg.path)
    failed = {c.name for c in pkg.validate_structure() if not c.passed}
    assert {"structure:files", "structure:rules.json"} <= failed


def test_example_packages_are_structurally_valid(example_dir):
    for name in ("invoice_total", "text_stats"):
        pkg = SkillPackage(example_dir(name))
        assert pkg.verify_manifest() == []
        assert all(c.passed for c in pkg.validate_structure())
