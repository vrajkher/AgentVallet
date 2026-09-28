"""Built-in SQLite graph index (default backend, zero dependencies).

Node ids are namespaced: ``run:<id>``, ``skill:<name>``, ``skillv:<name>@<ver>``,
``concept:<token>``, ``correction:<id>``, ``agent:<name>``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..store.db import Database
from ..util import dumps, loads, now, tokenize


class LocalGraph:
    backend = "local"

    def __init__(self, db: Database):
        self.db = db

    def upsert_node(self, node_id: str, type: str, label: str, props: dict[str, Any] | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO graph_nodes(id,type,label,props,created_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET label = excluded.label, props = excluded.props",
                (node_id, type, label, dumps(props or {}), now()),
            )

    def add_edge(self, src: str, rel: str, dst: str, weight: float = 1.0, props: dict[str, Any] | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO graph_edges(src,rel,dst,weight,props,created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(src,rel,dst) DO UPDATE SET weight = graph_edges.weight + excluded.weight",
                (src, rel, dst, weight, dumps(props or {}), now()),
            )

    def link_concepts(self, node_id: str, text: str, rel: str = "about", weight: float = 1.0) -> list[str]:
        concepts = sorted(set(t for t in tokenize(text) if len(t) > 2 and not t.isdigit()))
        for t in concepts:
            self.upsert_node(f"concept:{t}", "concept", t)
            self.add_edge(node_id, rel, f"concept:{t}", weight)
        return concepts

    def node(self, node_id: str) -> dict[str, Any] | None:
        r = self.db.query_one("SELECT * FROM graph_nodes WHERE id = ?", (node_id,))
        return {**dict(r), "props": loads(r["props"], {})} if r else None

    def neighbors(self, node_id: str, rel: str | None = None, direction: str = "both") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        q = "SELECT e.*, n.type, n.label FROM graph_edges e JOIN graph_nodes n ON n.id = e.{other} WHERE e.{me} = ?" + (
            " AND e.rel = ?" if rel else ""
        )
        params: tuple[Any, ...] = (node_id, rel) if rel else (node_id,)
        if direction in ("out", "both"):
            for r in self.db.query(q.format(other="dst", me="src"), params):
                out.append(
                    {
                        "id": r["dst"],
                        "rel": r["rel"],
                        "dir": "out",
                        "type": r["type"],
                        "label": r["label"],
                        "weight": r["weight"],
                    }
                )
        if direction in ("in", "both"):
            for r in self.db.query(q.format(other="src", me="dst"), params):
                out.append(
                    {
                        "id": r["src"],
                        "rel": r["rel"],
                        "dir": "in",
                        "type": r["type"],
                        "label": r["label"],
                        "weight": r["weight"],
                    }
                )
        return out

    def search(self, text: str, types: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        toks = tokenize(text)
        scores: dict[str, float] = defaultdict(float)
        for t in toks:
            for r in self.db.query("SELECT id FROM graph_nodes WHERE label LIKE ? LIMIT 200", (f"%{t}%",)):
                scores[r["id"]] += 1.0
            for n in self.neighbors(f"concept:{t}", direction="in"):
                scores[n["id"]] += 0.5 * n["weight"]
        results = []
        for nid, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            node = self.node(nid)
            if node and (not types or node["type"] in types):
                results.append({**node, "score": round(s, 3)})
            if len(results) >= limit:
                break
        return results

    def related_skills(self, text: str, limit: int = 10) -> dict[str, float]:
        """Skills reachable from the query's concepts (directly or via runs that used them)."""
        scores: dict[str, float] = defaultdict(float)
        for t in set(tokenize(text)):
            for n in self.neighbors(f"concept:{t}", direction="in"):
                if n["type"] == "skill":
                    scores[n["id"].split(":", 1)[1]] += 1.0 * n["weight"]
                elif n["type"] == "run":
                    for s in self.neighbors(n["id"], rel="used_skill", direction="out"):
                        scores[s["id"].split(":", 1)[1]] += 0.25
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return dict(top)

    def stats(self) -> dict[str, Any]:
        types = {r["type"]: r["n"] for r in self.db.query("SELECT type, COUNT(*) n FROM graph_nodes GROUP BY type")}
        edges = self.db.query_one("SELECT COUNT(*) FROM graph_edges")
        return {"backend": self.backend, "nodes": types, "edges": int(edges[0]) if edges else 0}
