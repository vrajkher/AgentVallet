"""Portable Agent Skill Package: load, build, scaffold, hash and structurally validate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .. import SPEC_VERSION
from ..models import Check
from ..util import dumps, now, sha256_bytes, sha256_file
from . import rules as rules_mod
from . import templates

REQUIRED_FILES = [
    "README.md",
    "skill.yaml",
    "workflow.md",
    "rules.json",
    "inputs.schema.json",
    "outputs.schema.json",
    "run.py",
    "validate.py",
    "corrections.md",
    "exceptions.md",
    "manifest.json",
]
REQUIRED_DIRS = ["examples", "tests"]
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

SKILL_YAML_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "version", "description"],
    "properties": {
        "name": {"type": "string", "pattern": NAME_RE.pattern},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "description": {"type": "string", "minLength": 1},
        "spec_version": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "triggers": {"type": "array", "items": {"type": "string"}},
        "entrypoint": {"type": "string"},
        "validator": {"type": "string"},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
        "runtime": {"type": "object"},
        "permissions": {
            "type": "object",
            "properties": {"network": {"type": "boolean"}, "filesystem": {"type": "string"}},
        },
        "author": {"type": "string"},
        "license": {"type": "string"},
        "created_from": {"type": "object"},
    },
}


class PackageError(ValueError):
    pass


@dataclass
class PackageContent:
    """Everything needed to materialise a skill package on disk."""

    name: str
    version: str
    description: str
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    run_py: str | None = None
    validate_py: str | None = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    workflow_md: str = ""
    corrections_md: str = ""
    exceptions_md: str = ""
    readme_md: str | None = None
    examples: list[dict[str, Any]] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)
    extra_files: dict[str, str] = field(default_factory=dict)


class SkillPackage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        meta_path = self.path / "skill.yaml"
        if not meta_path.exists():
            raise PackageError(f"{self.path} is not a skill package (missing skill.yaml)")
        self.meta: dict[str, Any] = yaml.safe_load(meta_path.read_text()) or {}

    # ---- accessors ----------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return str(self.meta.get("name", ""))

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "0.0.0"))

    @property
    def description(self) -> str:
        return str(self.meta.get("description", ""))

    @property
    def entrypoint(self) -> Path:
        return self.path / self.meta.get("entrypoint", "run.py")

    @property
    def validator(self) -> Path:
        return self.path / self.meta.get("validator", "validate.py")

    @property
    def timeout(self) -> float | None:
        t = self.meta.get("timeout_seconds")
        return float(t) if t else None

    def _json(self, rel: str, default: Any) -> Any:
        p = self.path / rel
        return json.loads(p.read_text()) if p.exists() else default

    def rules(self) -> list[dict[str, Any]]:
        data = self._json("rules.json", {"rules": []})
        return list(data.get("rules", [])) if isinstance(data, dict) else list(data)

    def input_schema(self) -> dict[str, Any]:
        return dict(self._json("inputs.schema.json", {}))

    def output_schema(self) -> dict[str, Any]:
        return dict(self._json("outputs.schema.json", {}))

    def examples(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.path / "examples").glob("*.json")):
            case = json.loads(p.read_text())
            case.setdefault("name", p.stem)
            out.append(case)
        return out

    def read_text(self, rel: str) -> str:
        p = self.path / rel
        return p.read_text() if p.exists() else ""

    def search_text(self) -> str:
        """Text used by the router to index this skill."""
        parts = [
            self.name.replace("_", " "),
            self.description,
            " ".join(self.meta.get("tags", [])),
            " ".join(self.meta.get("triggers", [])),
            self.read_text("workflow.md")[:4000],
        ]
        return "\n".join(parts)

    # ---- hashing / manifest -------------------------------------------------------------------------

    def files(self) -> list[Path]:
        out = []
        for p in sorted(self.path.rglob("*")):
            rel = p.relative_to(self.path)
            if p.is_file() and rel.as_posix() != "manifest.json" and not IGNORED_PARTS & set(rel.parts):
                out.append(p)
        return out

    def file_hashes(self) -> dict[str, str]:
        return {p.relative_to(self.path).as_posix(): sha256_file(p) for p in self.files()}

    @staticmethod
    def hash_of(file_hashes: dict[str, str]) -> str:
        return sha256_bytes("\n".join(f"{k}:{v}" for k, v in sorted(file_hashes.items())).encode())

    def content_hash(self) -> str:
        return self.hash_of(self.file_hashes())

    def write_manifest(self) -> dict[str, Any]:
        hashes = self.file_hashes()
        manifest = {
            "name": self.name,
            "version": self.version,
            "spec_version": self.meta.get("spec_version", SPEC_VERSION),
            "files": hashes,
            "content_hash": self.hash_of(hashes),
            "generated_at": now(),
        }
        (self.path / "manifest.json").write_text(dumps(manifest, indent=2) + "\n")
        return manifest

    def manifest(self) -> dict[str, Any]:
        return dict(self._json("manifest.json", {}))

    def verify_manifest(self) -> list[str]:
        """Return a list of integrity problems (empty = intact)."""
        m = self.manifest()
        if not m:
            return ["manifest.json missing"]
        problems = []
        actual = self.file_hashes()
        expected = m.get("files", {})
        for rel, h in expected.items():
            if rel not in actual:
                problems.append(f"missing file {rel}")
            elif actual[rel] != h:
                problems.append(f"modified file {rel}")
        problems += [f"unexpected file {rel}" for rel in actual if rel not in expected]
        if m.get("content_hash") != self.hash_of(expected):
            problems.append("manifest content_hash mismatch")
        return problems

    # ---- structural validation ----------------------------------------------------------------------

    def validate_structure(self) -> list[Check]:
        checks: list[Check] = []
        missing = [f for f in REQUIRED_FILES if not (self.path / f).is_file()]
        missing += [d + "/" for d in REQUIRED_DIRS if not (self.path / d).is_dir()]
        checks.append(
            Check(
                name="structure:files",
                passed=not missing,
                message="all required files present" if not missing else f"missing: {', '.join(missing)}",
            )
        )
        try:
            jsonschema.validate(self.meta, SKILL_YAML_SCHEMA)
            checks.append(Check(name="structure:skill.yaml", passed=True, message="skill.yaml valid"))
        except jsonschema.ValidationError as exc:
            checks.append(Check(name="structure:skill.yaml", passed=False, message=exc.message))

        for rel in ("inputs.schema.json", "outputs.schema.json"):
            try:
                schema = self._json(rel, None)
                if schema is None:
                    raise PackageError("missing")
                jsonschema.Draft202012Validator.check_schema(schema)
                checks.append(Check(name=f"structure:{rel}", passed=True, message="valid JSON Schema"))
            except (json.JSONDecodeError, jsonschema.SchemaError, PackageError) as exc:
                checks.append(Check(name=f"structure:{rel}", passed=False, message=str(exc)[:300]))

        try:
            bad = [e for r in self.rules() if (e := rules_mod.validate_rule_shape(r))]
            checks.append(
                Check(
                    name="structure:rules.json",
                    passed=not bad,
                    message=f"{len(self.rules())} rules well-formed" if not bad else "; ".join(bad),
                )
            )
        except json.JSONDecodeError as exc:
            checks.append(Check(name="structure:rules.json", passed=False, message=str(exc)))

        try:
            examples = self.examples()
            in_schema = self.input_schema()
            errs = []
            for ex in examples:
                if "input" not in ex:
                    errs.append(f"{ex['name']}: no 'input'")
                    continue
                try:
                    jsonschema.validate(ex["input"], in_schema)
                except jsonschema.ValidationError as exc:
                    errs.append(f"{ex['name']}: {exc.message}")
            checks.append(
                Check(
                    name="structure:examples",
                    passed=bool(examples) and not errs,
                    severity="error" if errs else "warning",
                    message=(
                        f"{len(examples)} examples conform to input schema"
                        if examples and not errs
                        else "; ".join(errs) or "no examples provided"
                    ),
                )
            )
        except (json.JSONDecodeError, jsonschema.SchemaError) as exc:
            checks.append(Check(name="structure:examples", passed=False, message=str(exc)[:300]))
        return checks


def build_package(dest: Path | str, content: PackageContent) -> SkillPackage:
    """Write a complete package to ``dest`` (which must not already contain one)."""
    dest = Path(dest)
    if not NAME_RE.match(content.name):
        raise PackageError(f"invalid skill name {content.name!r} (use lowercase letters, digits, _)")
    if (dest / "skill.yaml").exists():
        raise PackageError(f"{dest} already contains a skill package")
    (dest / "examples").mkdir(parents=True, exist_ok=True)
    (dest / "tests").mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "name": content.name,
        "version": content.version,
        "description": content.description,
        "spec_version": SPEC_VERSION,
        "tags": content.tags,
        "triggers": content.triggers,
        "entrypoint": "run.py",
        "validator": "validate.py",
        "runtime": {"python": ">=3.11"},
        "permissions": {"network": False, "filesystem": "workdir"},
        "timeout_seconds": 60,
        **content.extra_meta,
    }
    fmt = {
        "name": content.name,
        "version": content.version,
        "description": content.description,
        "tags": ", ".join(content.tags) or "-",
        "spec_version": SPEC_VERSION,
    }
    files: dict[str, str] = {
        "skill.yaml": yaml.safe_dump(meta, sort_keys=False, allow_unicode=True),
        "README.md": content.readme_md or templates.README_MD.format(**fmt),
        "workflow.md": content.workflow_md or f"# Workflow: {content.name}\n\n1. Describe the procedure here.\n",
        "rules.json": dumps({"rules": content.rules}, indent=2) + "\n",
        "inputs.schema.json": dumps(content.input_schema, indent=2) + "\n",
        "outputs.schema.json": dumps(content.output_schema, indent=2) + "\n",
        "run.py": content.run_py or templates.RUN_PY_STUB.format(**fmt),
        "validate.py": content.validate_py or templates.VALIDATE_PY.format(**fmt),
        "corrections.md": content.corrections_md or "# Corrections\n\nHuman corrections (highest priority).\n",
        "exceptions.md": content.exceptions_md or "# Exceptions\n\nKnown edge cases and failures.\n",
        "tests/test_skill.py": templates.TEST_PY,
        **content.extra_files,
    }
    for rel, text in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for i, ex in enumerate(content.examples, start=1):
        ex_name = ex.get("name") or f"example_{i:03d}"
        body = {k: v for k, v in ex.items() if k != "name"}
        (dest / "examples" / f"{ex_name}.json").write_text(dumps(body, indent=2) + "\n")
    pkg = SkillPackage(dest)
    pkg.write_manifest()
    return pkg


def scaffold(dest: Path | str, name: str, description: str, tags: list[str] | None = None) -> SkillPackage:
    return build_package(
        dest,
        PackageContent(
            name=name,
            version="0.1.0",
            description=description,
            tags=tags or [],
            examples=[
                {"name": "example_001", "input": {"hello": "world"}, "expected_output": {"echo": {"hello": "world"}}}
            ],
        ),
    )
