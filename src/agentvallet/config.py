"""Runtime configuration.

All state lives under a single home directory (``AV_HOME`` or ``~/.agentvallet``)
so an entire AgentVallet brain can be copied, backed up or synced as plain files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path(os.environ.get("AV_HOME", "~/.agentvallet")).expanduser())
    llm_provider: str = field(default_factory=lambda: os.environ.get("AV_LLM_PROVIDER", "none"))
    llm_model: str | None = field(default_factory=lambda: os.environ.get("AV_LLM_MODEL"))
    graph_backend: str = field(default_factory=lambda: os.environ.get("AV_GRAPH_BACKEND", "local"))
    run_timeout: float = field(default_factory=lambda: _env_float("AV_RUN_TIMEOUT", 60.0))
    route_threshold: float = field(default_factory=lambda: _env_float("AV_ROUTE_THRESHOLD", 0.5))
    redact_secrets: bool = field(default_factory=lambda: os.environ.get("AV_REDACT", "1") != "0")

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.home / "agentvallet.db"

    @property
    def objects_dir(self) -> Path:
        return self.home / "objects"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def exports_dir(self) -> Path:
        return self.home / "exports"

    def ensure(self) -> Settings:
        for p in (self.home, self.objects_dir, self.skills_dir, self.exports_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self
