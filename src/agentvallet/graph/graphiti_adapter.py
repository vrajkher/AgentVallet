"""Optional Graphiti backend (``pip install agentvallet[graph]`` + a Neo4j/FalkorDB instance).

Structural operations are always kept in the local graph (so routing and neighbours work
offline and deterministically); nodes are additionally mirrored to Graphiti as episodes so
its temporal knowledge graph and hybrid search can be used for semantic recall.

Environment: ``AV_GRAPHITI_URI``, ``AV_GRAPHITI_USER``, ``AV_GRAPHITI_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from .local import LocalGraph

log = logging.getLogger(__name__)


class GraphitiGraph(LocalGraph):
    backend = "graphiti"

    def __init__(self, db: Any):
        super().__init__(db)
        from graphiti_core import Graphiti  # raises ImportError if extra not installed

        self._g = Graphiti(
            os.environ.get("AV_GRAPHITI_URI", "bolt://localhost:7687"),
            os.environ.get("AV_GRAPHITI_USER", "neo4j"),
            os.environ.get("AV_GRAPHITI_PASSWORD", "password"),
        )
        self._run(self._g.build_indices_and_constraints())

    @staticmethod
    def _run(coro: Any) -> Any:
        try:
            return asyncio.run(coro)
        except Exception as exc:  # never let the index break the source of truth
            log.warning("graphiti operation failed: %s", exc)
            return None

    def upsert_node(self, node_id: str, type: str, label: str, props: dict[str, Any] | None = None) -> None:
        super().upsert_node(node_id, type, label, props)
        if type == "concept":
            return
        from graphiti_core.nodes import EpisodeType

        body = f"{type} {node_id}: {label}" + (f"\n{props}" if props else "")
        self._run(
            self._g.add_episode(
                name=node_id,
                episode_body=body,
                source=EpisodeType.text,
                source_description=f"agentvallet {type}",
                reference_time=datetime.now(UTC),
            )
        )

    def search(self, text: str, types: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        results = super().search(text, types, limit)
        edges = self._run(self._g.search(text)) or []
        for e in edges[:limit]:
            results.append(
                {
                    "id": f"graphiti:{getattr(e, 'uuid', '')}",
                    "type": "fact",
                    "label": getattr(e, "fact", str(e)),
                    "props": {},
                    "score": 0.0,
                }
            )
        return results


def make_graph(db: Any, backend: str = "local") -> LocalGraph:
    if backend == "graphiti":
        try:
            return GraphitiGraph(db)
        except Exception as exc:
            log.warning("Graphiti unavailable (%s); falling back to local graph", exc)
    return LocalGraph(db)
