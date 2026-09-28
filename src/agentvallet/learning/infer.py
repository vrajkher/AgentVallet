"""Learning primitives: JSON Schema inference and invariant/relation mining over examples."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..spec import jsonpath

# ---- JSON Schema inference ------------------------------------------------------------------------


def _type_of(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _schema_of(v: Any) -> dict[str, Any]:
    t = _type_of(v)
    if t == "object":
        return {"type": "object", "properties": {k: _schema_of(x) for k, x in v.items()}, "required": sorted(v)}
    if t == "array":
        items = None
        for x in v:
            items = _schema_of(x) if items is None else merge_schemas(items, _schema_of(x))
        return {"type": "array", "items": items or {}}
    return {"type": t}


def _types(s: dict[str, Any]) -> set[str]:
    t = s.get("type")
    if t is None:
        return set()
    return set(t) if isinstance(t, list) else {t}


def merge_schemas(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if not a:
        return b
    if not b:
        return a
    ta, tb = _types(a), _types(b)
    types = ta | tb
    if {"integer", "number"} <= types:
        types.discard("integer")
    out: dict[str, Any] = {}
    if "object" in ta and "object" in tb:
        pa, pb = a.get("properties", {}), b.get("properties", {})
        props = {k: merge_schemas(pa.get(k, {}), pb.get(k, {})) for k in sorted(set(pa) | set(pb))}
        out.update(properties=props, required=sorted(set(a.get("required", [])) & set(b.get("required", []))))
    elif "object" in ta:
        out.update({k: a[k] for k in ("properties", "required") if k in a})
    elif "object" in tb:
        out.update({k: b[k] for k in ("properties", "required") if k in b})
    if "array" in types:
        out["items"] = merge_schemas(a.get("items", {}), b.get("items", {}))
    out["type"] = sorted(types)[0] if len(types) == 1 else sorted(types)
    return out


def infer_schema(samples: Iterable[Any], title: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for s in samples:
        schema = merge_schemas(schema, _schema_of(s))
    if not schema:
        schema = {"type": "object"}
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
    if title:
        schema["title"] = title
    return schema


# ---- invariant mining -----------------------------------------------------------------------------

PATTERNS = {
    "iso_date": r"^\d{4}-\d{2}-\d{2}$",
    "iso_datetime": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
    "email": r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$",
    "currency_code": r"^[A-Z]{3}$",
    "uuid": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
}


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _decimals(v: float) -> int:
    s = repr(float(v))
    return 0 if "e" in s else len(s.split(".")[1].rstrip("0"))


def _leaf_paths(doc: Any) -> dict[str, Any]:
    return {p: v for p, v in jsonpath.flatten(doc).items() if not isinstance(v, dict)}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 1e-9 * max(abs(a), abs(b)))


def mine_rules(pairs: list[tuple[Any, Any]], min_support: int = 2) -> list[dict[str, Any]]:
    """Mine deterministic invariants that hold across *all* example (input, output) pairs."""
    if not pairs:
        return []
    n = len(pairs)
    outs = [_leaf_paths(o) for _, o in pairs]
    ins = [_leaf_paths(i) for i, _ in pairs]
    common_out = set.intersection(*(set(o) for o in outs)) if outs else set()
    common_in = set.intersection(*(set(i) for i in ins)) if ins else set()
    rules: list[dict[str, Any]] = []

    def add(rule: dict[str, Any]) -> None:
        rule.setdefault("target", "output")
        rule.setdefault("severity", "error")
        rule.setdefault("source", "learned")
        rule["id"] = re.sub(r"[^a-z0-9_]+", "_", f"{rule['type']}_{rule.get('path', '')}".lower()).strip("_")
        rules.append(rule)

    for p in sorted(common_out):
        wildcard = "[*]" in p
        vals_per = [jsonpath.resolve(pairs[k][1], p) for k in range(n)]
        flat = [v for vs in vals_per for v in vs]
        if not wildcard:
            add({"type": "required", "path": p, "description": f"{p} is always produced"})
        types = {_type_of(v) for v in flat}
        if types <= {"integer", "number"} and types:
            add({"type": "type", "path": p, "expected": "number"})
            if n >= min_support and all(v >= 0 for v in flat):
                add({"type": "range", "path": p, "min": 0, "description": f"{p} is never negative"})
        elif len(types) == 1:
            t = types.pop()
            add({"type": "type", "path": p, "expected": t})
            if t == "string":
                distinct = {v for v in flat}
                if n >= 3 and len(distinct) == 1 and not wildcard:
                    add({"type": "equals", "path": p, "value": flat[0], "severity": "warning"})
                elif n >= 5 and len(distinct) <= 3:
                    add({"type": "enum", "path": p, "values": sorted(distinct), "severity": "warning"})
                for pname, pat in PATTERNS.items():
                    if n >= min_support and all(re.search(pat, v) for v in flat):
                        add({"type": "regex", "path": p, "pattern": pat, "description": f"{p} is {pname}"})
                        break
            if t == "array" and n >= min_support and all(len(v) > 0 for v in flat):
                add({"type": "length", "path": p, "min": 1})

    if n < min_support:
        return rules

    # Relations between output scalars and input/output collections.
    for p in sorted(common_out):
        if "[*]" in p or not all(_is_num(o[p]) for o in outs):
            continue
        for target in ("input", "output"):
            docs = [i if target == "input" else o for i, o in pairs]
            cands = sorted(common_in if target == "input" else common_out)
            for w in cands:
                if "[*]" not in w or w == p:
                    continue
                ok, nontrivial = True, False
                for k, d in enumerate(docs):
                    vals = jsonpath.resolve(d, w)
                    if not vals or not all(_is_num(v) for v in vals):
                        ok = False
                        break
                    s = sum(vals)
                    dec = _decimals(outs[k][p])
                    if not (_close(s, outs[k][p]) or round(s, dec) == outs[k][p]):
                        ok = False
                        break
                    nontrivial |= len(vals) >= 2
                if ok and nontrivial:
                    add(
                        {
                            "type": "sum_equals",
                            "path": p,
                            "items_path": w,
                            "items_target": target,
                            "tolerance": 0.01,
                            "description": f"{p} equals the sum of {target}.{w}",
                        }
                    )
        for q in sorted(common_in):
            if "[*]" in q:
                continue
            if all(ins[k][q] == outs[k][p] for k in range(n)) and len({outs[k][p] for k in range(n)}) > 1:
                add(
                    {
                        "type": "equals_path",
                        "path": p,
                        "other_path": q,
                        "other_target": "input",
                        "description": f"{p} is copied from input.{q}",
                    }
                )
                break

    for p in sorted(common_out):
        if "[*]" in p or _is_num(outs[0][p]):
            continue
        for q in sorted(common_in):
            if "[*]" in q:
                continue
            if all(ins[k][q] == outs[k][p] for k in range(n)) and len({str(outs[k][p]) for k in range(n)}) > 1:
                add(
                    {
                        "type": "equals_path",
                        "path": p,
                        "other_path": q,
                        "other_target": "input",
                        "description": f"{p} is copied from input.{q}",
                    }
                )
                break
    return rules


# ---- program synthesis ---------------------------------------------------------------------------


def synthesize_mapping(pairs: list[tuple[Any, Any]]) -> dict[str, dict[str, Any]] | None:
    """Explain every top-level output key as copy / sum / count / mean / min / max / const.

    Returns a mapping spec or None if some key cannot be explained consistently.
    """
    if not pairs or not all(isinstance(o, dict) and isinstance(i, dict) for i, o in pairs):
        return None
    keys = set(pairs[0][1])
    if any(set(o) != keys for _, o in pairs):
        return None
    in_leaves = [_leaf_paths(i) for i, _ in pairs]
    common_in = sorted(set.intersection(*(set(x) for x in in_leaves)))
    arrays = sorted(
        {p.rsplit("[*]", 1)[0] for p in common_in if "[*]" in p}
        | {p for p in common_in if all(isinstance(x[p], list) for x in in_leaves)}
    )
    spec: dict[str, dict[str, Any]] = {}

    def agg(op: str, vals: list[Any]) -> float | None:
        nums = [v for v in vals if _is_num(v)]
        if len(nums) != len(vals):
            return None
        if op == "sum":
            return float(sum(nums))
        if not nums:
            return None
        return {"mean": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[op]

    for key in sorted(keys):
        outs = [o[key] for _, o in pairs]
        found: dict[str, Any] | None = None
        # copy
        for q in common_in:
            if "[*]" not in q and all(in_leaves[k].get(q) == outs[k] for k in range(len(pairs))):
                found = {"op": "copy", "path": q}
                break
        # numeric aggregates
        if found is None and all(_is_num(v) for v in outs):
            dec = max(_decimals(v) for v in outs)
            for q in common_in:
                if "[*]" not in q:
                    continue
                for op in ("sum", "mean", "min", "max"):
                    good = True
                    for k, (i, _) in enumerate(pairs):
                        r = agg(op, jsonpath.resolve(i, q))
                        if r is None or not (_close(r, outs[k]) or round(r, dec) == outs[k]):
                            good = False
                            break
                    if good:
                        found = {"op": op, "path": q, "round": dec}
                        break
                if found:
                    break
            if found is None:
                for q in arrays:
                    if all(len(jsonpath.first(i, q, [])) == outs[k] for k, (i, _) in enumerate(pairs)):
                        found = {"op": "count", "path": q}
                        break
        if found is None and len({repr(v) for v in outs}) == 1 and len(pairs) >= 2:
            found = {"op": "const", "value": outs[0]}
        if found is None:
            return None
        spec[key] = found
    return spec
