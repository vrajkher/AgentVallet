from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SkillPackage:
    name: str
    version: str
    path: Path
    manifest: dict[str, Any]

    @property
    def trusted(self) -> bool:
        return self.manifest.get("status") == "trusted"


class SkillRegistry:
    """Portable file/Git based skill registry. Graph memory is an optional index."""

    def __init__(self, root: str | Path = "skills"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[SkillPackage]:
        packages: list[SkillPackage] = []
        for manifest_path in self.root.glob("*/manifest.json"):
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            packages.append(
                SkillPackage(
                    name=data["name"],
                    version=str(data.get("version", "0.1.0")),
                    path=manifest_path.parent,
                    manifest=data,
                )
            )
        return sorted(packages, key=lambda item: item.name)

    def find(self, name: str) -> SkillPackage | None:
        path = self.root / name / "manifest.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return SkillPackage(name=data["name"], version=str(data.get("version", "0.1.0")), path=path.parent, manifest=data)

    def create_from_run(self, record: dict[str, Any], name: str, *, version: str = "0.1.0") -> SkillPackage:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "spec": "agentvallet.skill/v1",
            "name": name,
            "version": version,
            "status": "candidate",
            "goal": record.get("goal", ""),
            "source_run_id": record.get("run_id"),
            "entrypoint": "run.py",
            "validator": "validate.py",
            "portable": True,
        }
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (path / "README.md").write_text(f"# {name}\n\nGoal: {record.get('goal', '')}\n", encoding="utf-8")
        (path / "workflow.md").write_text("# Workflow\n\nExtract and freeze approved steps here.\n", encoding="utf-8")
        (path / "rules.json").write_text("[]\n", encoding="utf-8")
        (path / "corrections.md").write_text("# Corrections\n\n", encoding="utf-8")
        (path / "exceptions.md").write_text("# Exceptions\n\n", encoding="utf-8")
        (path / "run.py").write_text('def run(inputs):\n    raise NotImplementedError("Freeze executable workflow before trust")\n', encoding="utf-8")
        (path / "validate.py").write_text('def validate(result):\n    return {"passed": False, "reason": "validator not frozen"}\n', encoding="utf-8")
        return SkillPackage(name=name, version=version, path=path, manifest=manifest)
