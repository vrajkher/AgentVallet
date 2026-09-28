"""Custom validator for skill `text_stats`.

Contract: stdin is {"input": ..., "output": ...}; stdout is {"passed": bool, "errors": [str, ...]}.
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
    print(json.dumps({"passed": not errs, "errors": errs}))
