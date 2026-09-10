from __future__ import annotations

import json
import os
from typing import Any


class GraphitiMemory:
    """Optional semantic/temporal index. Portable files remain the source of truth."""

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD")
        self.graphiti = None

    async def initialize(self) -> None:
        if not self.password:
            raise RuntimeError("NEO4J_PASSWORD is required for Graphiti memory")
        from graphiti_core import Graphiti

        self.graphiti = Graphiti(self.uri, self.user, self.password)
        await self.graphiti.build_indices_and_constraints()

    async def add_run(self, record: dict[str, Any]) -> None:
        if self.graphiti is None:
            raise RuntimeError("Call initialize() first")
        from datetime import datetime, timezone
        from graphiti_core.nodes import EpisodeType

        payload = {
            "run_id": record.get("run_id"),
            "goal": record.get("goal"),
            "status": record.get("status"),
            "approved": record.get("approved"),
            "tags": record.get("tags", []),
            "events": record.get("events", []),
            "validations": record.get("validations", []),
            "artifacts": [
                {"sha256": x.get("sha256"), "snapshot_path": x.get("snapshot_path")}
                for x in record.get("artifacts", [])
            ],
            "final_result": record.get("final_result", {}),
        }
        await self.graphiti.add_episode(
            name=f"agentvallet-run-{record.get('run_id')}",
            episode_body=json.dumps(payload, ensure_ascii=False),
            source=EpisodeType.json,
            source_description="AgentVallet observable AI work run",
            reference_time=datetime.now(timezone.utc),
        )

    async def search(self, query: str, limit: int = 10):
        if self.graphiti is None:
            raise RuntimeError("Call initialize() first")
        return await self.graphiti.search(query, num_results=limit)

    async def close(self) -> None:
        if self.graphiti is not None:
            await self.graphiti.close()
