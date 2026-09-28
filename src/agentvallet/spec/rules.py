"""Deterministic rule engine for ``rules.json``.

Every rule is a JSON object::

    {"id": "total_is_sum", "type": "sum_equals", "path": "total",
     "items_path": "items[*].amount", "tolerance": 0.01,
     "target": "output", "severity": "error", "source": "learned", "description": "..."}

Supported types: required, not_empty, type, equals, enum, regex, range, length,
sum_equals, equals_path, forbid_pattern, forbid_path.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..models import Check
from . import jsonpath

RULE_TYPES = {
    "required",
    "not_empty",
    "type",
    "equals",
    "enum",
    "regex",
    "range",
    "length",
    "sum_equals",
    "equals_path",
    "forbid_pattern",
    "forbid_path",
}

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def _is_type(value: Any, expected: str) -> bool:
    if expected in ("number", "integer") and isinstance(value, bool):
        return False
    if expected == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _JSON_TYPES.get(expected, (object,)))


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_rule_shape(rule: dict[str, Any]) -> str | None:
    """Return an error message if the rule is malformed, else None."""
    t = rule.get("type")
    if t not in RULE_TYPES:
        return f"unknown rule type {t!r}"
    if t not in ("forbid_pattern",) and not rule.get("path"):
        return f"rule {rule.get('id', '?')} requires 'path'"
    needs = {
        "type": ["expected"],
        "equals": ["value"],
        "enum": ["values"],
        "regex": ["pattern"],
        "sum_equals": ["items_path"],
        "equals_path": ["other_path"],
        "forbid_pattern": ["pattern"],
    }.get(t, [])
    missing = [k for k in needs if k not in rule]
    if missing:
        return f"rule {rule.get('id', '?')} ({t}) missing {missing}"
    if t in ("regex", "forbid_pattern"):
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            return f"rule {rule.get('id', '?')} has invalid regex: {exc}"
    return None


def evaluate_rule(rule: dict[str, Any], inp: Any, out: Any) -> Check:
    rid = rule.get("id") or rule.get("type", "rule")
    severity = rule.get("severity", "error")
    name = f"rule:{rid}"
    shape_err = validate_rule_shape(rule)
    if shape_err:
        return Check(name=name, passed=False, message=shape_err, severity=severity)

    docs = {"input": inp, "output": out}
    doc = docs.get(rule.get("target", "output"), out)
    t = rule["type"]
    path = rule.get("path", "")
    values = jsonpath.resolve(doc, path) if path else []

    def ok(msg: str = "") -> Check:
        return Check(name=name, passed=True, message=msg or rule.get("description", ""), severity=severity)

    def fail(msg: str) -> Check:
        desc = rule.get("description")
        return Check(name=name, passed=False, message=f"{msg}" + (f" ({desc})" if desc else ""), severity=severity)

    if t == "forbid_path":
        return ok() if not values else fail(f"{path} must not be present")
    if t == "forbid_pattern":
        text = json.dumps(doc, default=str)
        m = re.search(rule["pattern"], text)
        return ok() if not m else fail(f"forbidden pattern found: {m.group(0)[:40]!r}")

    if not values:
        return fail(f"{path} is missing")

    if t == "required":
        bad = [v for v in values if v is None]
        return ok() if not bad else fail(f"{path} is null")
    if t == "not_empty":
        bad = [v for v in values if v in (None, "", [], {})]
        return ok() if not bad else fail(f"{path} is empty")
    if t == "type":
        exp = rule["expected"]
        bad = [v for v in values if not _is_type(v, exp)]
        return ok() if not bad else fail(f"{path} expected {exp}, got {type(bad[0]).__name__}")
    if t == "equals":
        bad = [v for v in values if v != rule["value"]]
        return ok() if not bad else fail(f"{path} = {bad[0]!r}, expected {rule['value']!r}")
    if t == "enum":
        bad = [v for v in values if v not in rule["values"]]
        return ok() if not bad else fail(f"{path} = {bad[0]!r} not in {rule['values']!r}")
    if t == "regex":
        pat = re.compile(rule["pattern"])
        bad = [v for v in values if not isinstance(v, str) or not pat.search(v)]
        return ok() if not bad else fail(f"{path} = {bad[0]!r} does not match /{rule['pattern']}/")
    if t == "range":
        lo, hi = rule.get("min"), rule.get("max")
        for v in values:
            n = _num(v)
            if n is None:
                return fail(f"{path} = {v!r} is not numeric")
            if lo is not None and n < lo:
                return fail(f"{path} = {v} < min {lo}")
            if hi is not None and n > hi:
                return fail(f"{path} = {v} > max {hi}")
        return ok()
    if t == "length":
        lo, hi = rule.get("min"), rule.get("max")
        for v in values:
            if not hasattr(v, "__len__"):
                return fail(f"{path} has no length")
            if lo is not None and len(v) < lo:
                return fail(f"len({path}) = {len(v)} < {lo}")
            if hi is not None and len(v) > hi:
                return fail(f"len({path}) = {len(v)} > {hi}")
        return ok()
    if t == "sum_equals":
        items_doc = docs.get(rule.get("items_target", rule.get("target", "output")), out)
        items = [_num(v) for v in jsonpath.resolve(items_doc, rule["items_path"])]
        if any(i is None for i in items):
            return fail(f"{rule['items_path']} contains non-numeric values")
        total = sum(i for i in items if i is not None)
        tol = float(rule.get("tolerance", 1e-6))
        actual = _num(values[0])
        if actual is None:
            return fail(f"{path} is not numeric")
        if abs(actual - total) <= tol:
            return ok()
        return fail(f"{path} = {actual} but sum({rule['items_path']}) = {round(total, 6)}")
    if t == "equals_path":
        other_doc = docs.get(rule.get("other_target", "input"), inp)
        others = jsonpath.resolve(other_doc, rule["other_path"])
        if not others:
            return fail(f"{rule['other_path']} missing in {rule.get('other_target', 'input')}")
        return ok() if values[0] == others[0] else fail(f"{path} = {values[0]!r} != {others[0]!r}")
    return fail(f"unhandled rule type {t}")  # pragma: no cover


def evaluate(rules: list[dict[str, Any]], inp: Any, out: Any) -> list[Check]:
    return [evaluate_rule(r, inp, out) for r in rules if r.get("enabled", True)]
