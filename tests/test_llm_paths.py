"""LLM-backed synthesis and repair, exercised with a scripted stub provider."""

from pathlib import Path

from agentvallet.learning.llm import extract_code, get_provider
from agentvallet.models import SkillStatus

GOOD = "```python\nimport json, sys\nd = json.load(sys.stdin)\nprint(json.dumps({'upper': d['s'].upper()}))\n```"
BAD = "```python\nimport json, sys\nprint(json.dumps({'upper': 'nope'}))\n```"


class StubLLM:
    name = "stub"
    available = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, prompt, max_tokens=16000):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _record(av):
    for s in ("abc", "Hello"):
        with av.record("uppercase a string") as run:
            run.input({"s": s})
            run.output({"upper": s.upper()})


def test_llm_strategy_with_feedback_retry(av):
    _record(av)
    stub = StubLLM([BAD, GOOD])
    av.learner.llm = stub
    res = av.learn(goal="uppercase a string", name="upper")
    assert res.strategy == "llm"
    assert any("attempt 1 rejected" in n for n in res.notes)
    assert "previous program failed" in stub.prompts[1]
    assert av.validate("upper", res.version.version).passed


def test_llm_repair_creates_unapproved_draft(av):
    _record(av)
    res = av.learn(goal="uppercase a string", name="upper")  # lookup table (no LLM)
    assert res.strategy == "lookup"
    assert av.validate("upper", res.version.version).passed
    av.approve("upper", res.version.version, "reviewer")
    av.repairer.llm = StubLLM([GOOD])
    out = av.run_skill("upper", {"s": "new input"})
    assert out.status == "needs_human"  # the patch is a draft and is never auto-trusted
    assert out.repair["strategy"] == "llm_patch" and out.repair["new_version"] == "1.0.1"
    draft = av.registry.get("upper", "1.0.1")
    assert draft.status in (SkillStatus.DRAFT, SkillStatus.VALIDATED)
    assert "upper()" in (Path(draft.path) / "run.py").read_text()
    assert av.registry.get("upper").version == "1.0.0"


def test_provider_factory_and_extract_code():
    assert get_provider("none").available is False
    assert extract_code("```python\nprint(1)\n```") == "print(1)\n"
    assert extract_code("print(2)") == "print(2)\n"
