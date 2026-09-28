"""Generate portable, dependency-free ``run.py`` programs."""

from __future__ import annotations

import json
import pprint
from typing import Any

_RESOLVER = """
def _resolve(doc, path):
    cur = [doc]
    for seg in re.findall(r"[^.\\[\\]]+|\\[\\*\\]|\\[-?\\d+\\]", path):
        nxt = []
        for node in cur:
            if seg == "[*]":
                nxt.extend(node if isinstance(node, list) else [])
            elif seg.startswith("["):
                i = int(seg[1:-1])
                if isinstance(node, list) and -len(node) <= i < len(node):
                    nxt.append(node[i])
            elif isinstance(node, dict) and seg in node:
                nxt.append(node[seg])
        cur = nxt
    return cur
"""

MAPPING_TEMPLATE = '''"""Skill `{name}` — synthesised by AgentVallet from {n} validated examples.

Each output field is derived by a deterministic operation over the input (see SPEC).
Contract: JSON object on stdin -> JSON object on stdout.
"""

import json
import re
import sys

SPEC = {spec}
{resolver}

def _num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def run(data):
    out = {{}}
    for key, rule in SPEC.items():
        op = rule["op"]
        if op == "const":
            out[key] = rule["value"]
            continue
        vals = _resolve(data, rule["path"])
        if op == "copy":
            if not vals:
                raise ValueError(f"input is missing {{rule['path']}}")
            out[key] = vals[0]
        elif op == "count":
            out[key] = len(vals[0]) if vals and isinstance(vals[0], list) else 0
        else:
            nums = [float(v) for v in vals]
            if op == "sum":
                r = sum(nums)
            elif not nums:
                raise ValueError(f"{{rule['path']}} is empty")
            elif op == "mean":
                r = sum(nums) / len(nums)
            elif op == "min":
                r = min(nums)
            else:
                r = max(nums)
            out[key] = _num(round(r, rule.get("round", 6)))
    return out


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
'''

LOOKUP_TEMPLATE = '''"""Skill `{name}` — memorised behaviour (no general implementation learned yet).

AgentVallet could not derive a general program from the recorded work, so this entrypoint
replays known input->output pairs exactly and fails loudly on anything new. Record more
runs with code, add corrections, or enable an LLM provider, then re-learn.
"""

import json
import sys

KNOWN = {known}


def run(data):
    key = json.dumps(data, sort_keys=True)
    if key not in KNOWN:
        raise SystemExit("unsupported input: no learned implementation covers this case")
    return KNOWN[key]


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
'''


def mapping_program(name: str, spec: dict[str, Any], n_examples: int) -> str:
    return MAPPING_TEMPLATE.format(
        name=name, n=n_examples, spec=pprint.pformat(spec, indent=1, sort_dicts=True), resolver=_RESOLVER
    )


def lookup_program(name: str, pairs: list[tuple[Any, Any]]) -> str:
    known = {json.dumps(i, sort_keys=True): o for i, o in pairs}
    return LOOKUP_TEMPLATE.format(name=name, known=pprint.pformat(known, indent=1, sort_dicts=True))


def looks_like_entrypoint(source: str) -> bool:
    """Heuristic: recorded code that follows the stdin-JSON -> stdout-JSON contract."""
    return "sys.stdin" in source and "json" in source and "print(" in source
