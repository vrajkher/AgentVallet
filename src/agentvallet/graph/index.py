"""Graph memory interface. The graph is an *index* over runs/skills/corrections, never the source of truth."""

from __future__ import annotations

from typing import Any, Protocol


class GraphIndex(Protocol):
    backend: str

    def upsert_node(self, node_id: str, type: str, label: str, props: dict[str, Any] | None = None) -> None: ...

    def add_edge(
        self, src: str, rel: str, dst: str, weight: float = 1.0, props: dict[str, Any] | None = None
    ) -> None: ...

    def neighbors(self, node_id: str, rel: str | None = None, direction: str = "both") -> list[dict[str, Any]]: ...

    def search(self, text: str, types: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]: ...

    def related_skills(self, text: str, limit: int = 10) -> dict[str, float]: ...

    def stats(self) -> dict[str, Any]: ...
