"""Skill router: rank skills for a natural-language task.

Scoring combines
- BM25 over each skill's name (x3), triggers (x2), tags, description and workflow,
- query *coverage* (IDF-weighted fraction of query terms the skill explains) -> confidence in [0, 1],
- an optional graph boost from concepts shared with past runs that used the skill.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from ..models import RouteMatch, SkillStatus
from ..spec import SkillPackage
from ..util import tokenize
from .registry import SkillNotFound, SkillRegistry

# Light synonym normalisation so "calculate the total" finds "compute invoice sum".
SYNONYMS = {
    "calculate": "compute",
    "calc": "compute",
    "work": "compute",
    "figure": "compute",
    "determine": "compute",
    "sum": "total",
    "add": "total",
    "aggregate": "total",
    "tally": "total",
    "fetch": "get",
    "retrieve": "get",
    "obtain": "get",
    "read": "get",
    "load": "get",
    "make": "create",
    "generate": "create",
    "build": "create",
    "produce": "create",
    "summarize": "summary",
    "summarise": "summary",
    "digest": "summary",
    "overview": "summary",
    "check": "validate",
    "verify": "validate",
    "audit": "validate",
    "bill": "invoice",
    "receipt": "invoice",
    "count": "number",
    "amount": "value",
    "cost": "price",
}


def _terms(text: str) -> list[str]:
    return [SYNONYMS.get(t, t) for t in tokenize(text)]


class SkillRouter:
    def __init__(self, registry: SkillRegistry, graph: Any = None, k1: float = 1.5, b: float = 0.75):
        self.registry = registry
        self.graph = graph
        self.k1, self.b = k1, b

    def _documents(self, include_drafts: bool) -> list[tuple[Any, list[str]]]:
        docs = []
        for s in self.registry.list_skills():
            try:
                sv = self.registry.get(s["name"], include_drafts=include_drafts)
            except SkillNotFound:
                continue
            if sv.status == SkillStatus.DEPRECATED or (not include_drafts and sv.status != SkillStatus.APPROVED):
                continue
            try:
                pkg = SkillPackage(sv.path)
            except Exception:
                continue
            toks = (
                _terms(pkg.name.replace("_", " ")) * 3
                + _terms(" ".join(pkg.meta.get("triggers", []))) * 2
                + _terms(" ".join(pkg.meta.get("tags", [])))
                + _terms(pkg.description)
                + _terms(pkg.read_text("workflow.md")[:4000])
            )
            docs.append((sv, toks))
        return docs

    def route(self, task: str, limit: int = 5, include_drafts: bool = False) -> list[RouteMatch]:
        q = _terms(task)
        if not q:
            return []
        docs = self._documents(include_drafts)
        if not docs:
            return []
        n = len(docs)
        avgdl = sum(len(t) for _, t in docs) / n or 1.0
        df: Counter[str] = Counter()
        for _, toks in docs:
            df.update(set(toks))

        def idf(term: str) -> float:
            return math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))

        graph_scores = self.graph.related_skills(task) if self.graph is not None else {}
        q_terms = list(dict.fromkeys(q))
        known = [idf(t) for t in q_terms if df[t]]
        unknown_w = 1.0 + (sum(known) / len(known) if known else 0.0)

        def weight(term: str) -> float:
            return 1.0 + idf(term) if df[term] else unknown_w

        total_w = sum(weight(t) for t in q_terms) or 1.0

        matches: list[RouteMatch] = []
        for sv, toks in docs:
            tf = Counter(toks)
            dl = len(toks)
            bm25, covered, hit_terms = 0.0, 0.0, []
            for t in q_terms:
                if tf[t]:
                    i = idf(t)
                    bm25 += i * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / avgdl))
                    covered += weight(t)
                    hit_terms.append(t)
            if not hit_terms:
                continue
            coverage = covered / total_w
            g = graph_scores.get(sv.name, 0.0)
            confidence = min(1.0, 0.75 * coverage + 0.25 * (bm25 / (bm25 + 3.0)) + min(0.15, 0.05 * g))
            reasons = [f"matched: {', '.join(hit_terms)}", f"bm25={bm25:.2f}", f"coverage={coverage:.2f}"]
            if g:
                reasons.append(f"graph={g:.2f}")
            matches.append(
                RouteMatch(
                    name=sv.name, version=sv.version, score=round(confidence, 4), status=sv.status, reasons=reasons
                )
            )
        matches.sort(key=lambda m: (-m.score, m.name))
        return matches[:limit]
