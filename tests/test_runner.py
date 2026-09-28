import pytest

from agentvallet.runner import SkillRunner, Validator
from agentvallet.spec import PackageContent, SkillPackage, build_package


def make(tmp_path, run_py, **kw):
    return build_package(
        tmp_path / "pkg", PackageContent(name="t_skill", version="1.0.0", description="t", run_py=run_py, **kw)
    )


def test_runs_example_package(example_dir):
    pkg = SkillPackage(example_dir("invoice_total"))
    res = SkillRunner().run(pkg, pkg.examples()[0]["input"])
    assert res.ok, res.error
    assert res.output == pkg.examples()[0]["expected_output"]


def test_full_validation_of_example(example_dir):
    pkg = SkillPackage(example_dir("invoice_total"))
    report = Validator(SkillRunner()).validate(pkg)
    assert report.passed, [c for c in report.checks if not c.passed]
    assert report.score == 1.0
    assert any(c.name == "tests:pytest" and c.passed for c in report.checks)


def test_input_schema_enforced(example_dir):
    pkg = SkillPackage(example_dir("invoice_total"))
    res = SkillRunner().run(pkg, {"currency": "usd", "items": []})
    assert not res.ok and "inputs.schema.json" in res.error


def test_timeout_kills_process(tmp_path):
    pkg = make(tmp_path, "import time\ntime.sleep(30)\n")
    res = SkillRunner().run(pkg, {}, timeout=1, check_schemas=False)
    assert res.timed_out and not res.ok and res.duration_ms < 10000


def test_environment_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "sk-live-123")
    pkg = make(tmp_path, "import json, os\nprint(json.dumps({'env': sorted(os.environ)}))\n")
    res = SkillRunner().run(pkg, {}, check_schemas=False)
    assert res.ok
    assert "SUPER_SECRET_API_KEY" not in res.output["env"]
    assert "AV_SANDBOX" in res.output["env"]


@pytest.mark.parametrize(
    "code,msg",
    [
        ("print('not json')", "not valid JSON"),
        ("import sys\nsys.exit('boom')", "exit code 1"),
        ("raise ValueError('bad')", "ValueError"),
    ],
)
def test_failure_modes(tmp_path, code, msg):
    res = SkillRunner().run(make(tmp_path, code), {}, check_schemas=False)
    assert not res.ok and msg in res.error


def test_validator_reports_rule_and_expected_mismatch(tmp_path):
    pkg = make(
        tmp_path,
        "import json,sys\nd=json.load(sys.stdin)\nprint(json.dumps({'total': -1}))\n",
        rules=[{"id": "nonneg", "type": "range", "path": "total", "min": 0}],
        examples=[{"name": "e1", "input": {}, "expected_output": {"total": 1}}],
    )
    report = Validator().validate(pkg, run_tests=False)
    failed = {c.name for c in report.failures()}
    assert "example[e1]:expected" in failed and "example[e1]:rule:nonneg" in failed
    assert not report.passed
