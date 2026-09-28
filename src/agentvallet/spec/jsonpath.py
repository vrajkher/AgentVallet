"""Minimal JSON path resolver used by rules: ``total``, ``items[*].amount``, ``a.b[0].c``.

A leading ``$.`` is accepted and ignored. ``[*]`` fans out over lists, so a path may
resolve to several values.
"""

from __future__ import annotations

import re
from typing import Any

_SEG_RE = re.compile(r"([^.\[\]]+)|\[(\*|-?\d+)\]")
MISSING = object()


def parse(path: str) -> list[str | int]:
    path = path.strip()
    if path in ("", "$"):
        return []
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
    segs: list[str | int] = []
    for name, idx in _SEG_RE.findall(path):
        if name:
            segs.append(name)
        elif idx == "*":
            segs.append("*")
        else:
            segs.append(int(idx))
    return segs


def resolve(doc: Any, path: str) -> list[Any]:
    """Return all values at ``path``; an empty list means the path does not exist."""
    current = [doc]
    for seg in parse(path):
        nxt: list[Any] = []
        for node in current:
            if seg == "*":
                if isinstance(node, list):
                    nxt.extend(node)
                elif isinstance(node, dict):
                    nxt.extend(node.values())
            elif isinstance(seg, int):
                if isinstance(node, list) and -len(node) <= seg < len(node):
                    nxt.append(node[seg])
            elif isinstance(node, dict) and seg in node:
                nxt.append(node[seg])
        current = nxt
    return current


def first(doc: Any, path: str, default: Any = MISSING) -> Any:
    vals = resolve(doc, path)
    return vals[0] if vals else default


def set_value(doc: dict[str, Any], path: str, value: Any) -> None:
    """Set a value at a simple dotted path (no wildcards), creating objects as needed."""
    segs = parse(path)
    node: Any = doc
    for seg in segs[:-1]:
        node = node[seg] if isinstance(seg, int) else node.setdefault(seg, {})
    node[segs[-1]] = value


def flatten(doc: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts to dotted leaf paths; lists become ``[*]`` paths of their items."""
    out: dict[str, Any] = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict | list):
                out.update(flatten(v, p))
            out.setdefault(p, v)
    elif isinstance(doc, list):
        for item in doc:
            p = f"{prefix}[*]"
            if isinstance(item, dict | list):
                for k, v in flatten(item, p).items():
                    out.setdefault(k, v)
            else:
                out.setdefault(p, item)
    return out
