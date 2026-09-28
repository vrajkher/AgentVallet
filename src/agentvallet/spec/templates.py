"""File templates used when scaffolding or generating skill packages."""

from __future__ import annotations

RUN_PY_STUB = '''"""Entrypoint for skill `{name}`.

Contract: read one JSON object (the input) from stdin, write one JSON object (the output) to stdout.
Exit non-zero and write to stderr on failure.
"""

import json
import sys


def run(data: dict) -> dict:
    # TODO: implement the skill.
    return {{"echo": data}}


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
'''

VALIDATE_PY = '''"""Custom validator for skill `{name}`.

Contract: stdin is {{"input": ..., "output": ...}}; stdout is {{"passed": bool, "errors": [str, ...]}}.
Declarative checks belong in rules.json; put logic here that rules cannot express.
"""

import json
import sys


def validate(inp: dict, out: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(out, dict):
        errors.append("output must be a JSON object")
    return errors


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    errs = validate(payload.get("input"), payload.get("output"))
    print(json.dumps({{"passed": not errs, "errors": errs}}))
'''

TEST_PY = '''"""Portable regression tests: every example must reproduce its expected output."""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example(path):
    case = json.loads(path.read_text())
    proc = subprocess.run(
        [sys.executable, str(ROOT / "run.py")],
        input=json.dumps(case["input"]), capture_output=True, text=True, timeout=60, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    if "expected_output" in case:
        assert out == case["expected_output"]
'''

README_MD = """# {name}

{description}

- **Version:** {version}
- **Tags:** {tags}
- **Spec:** AgentVallet Portable Agent Skill Package v{spec_version}

## How to use (any agent, any vendor)

```bash
echo '<input json>' | python run.py        # -> output json
echo '{{"input": ..., "output": ...}}' | python validate.py
```

Inputs are described by `inputs.schema.json`, outputs by `outputs.schema.json`,
deterministic checks by `rules.json`, and the human-readable procedure by `workflow.md`.
Human corrections live in `corrections.md` and have priority over learned behaviour.
"""
