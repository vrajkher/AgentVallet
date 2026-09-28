"""Versioned skill registry.

Lifecycle: draft -> validated -> approved (trusted, immutable) -> deprecated.

- Each version lives in its own directory ``<skills_dir>/<name>/<version>/``.
- Drafts may be edited in place; their manifest is refreshed before validation.
- Approval requires a passing validation of the *exact* content hash being approved.
- Approved versions are made read-only and are integrity-checked on every load, so trusted
  knowledge is never silently overwritten. Changes always produce a new version.
"""

from __future__ import annotations

import difflib
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import yaml

from ..models import SkillStatus, SkillVersion, ValidationReport
from ..runner.validator import Validator
from ..spec import PackageContent, PackageError, SkillPackage, build_package
from ..spec.package import NAME_RE
from ..store.db import Database
from ..util import bump_version, dumps, loads, now, version_key


class SkillNotFound(KeyError):
    pass


class IntegrityError(RuntimeError):
    pass


class ApprovalError(RuntimeError):
    pass


def _row_to_version(r: Any) -> SkillVersion:
    return SkillVersion(**dict(r))


def _make_readonly(path: Path) -> None:
    for p in path.rglob("*"):
        if p.is_file():
            p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


class SkillRegistry:
    def __init__(self, db: Database, root: Path, validator: Validator | None = None):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.validator = validator or Validator()

    # ---- queries -----------------------------------------------------------------------------------

    def versions(self, name: str) -> list[SkillVersion]:
        rows = self.db.query("SELECT * FROM skill_versions WHERE name = ?", (name,))
        return sorted((_row_to_version(r) for r in rows), key=lambda v: version_key(v.version))

    def exists(self, name: str) -> bool:
        return bool(self.db.query_one("SELECT 1 FROM skill_versions WHERE name = ?", (name,)))

    def get(self, name: str, version: str | None = None, include_drafts: bool = True) -> SkillVersion:
        vs = self.versions(name)
        if not vs:
            raise SkillNotFound(f"skill {name!r} not found")
        if version:
            for v in vs:
                if v.version == version:
                    return v
            raise SkillNotFound(f"skill {name}@{version} not found")
        approved = [v for v in vs if v.status == SkillStatus.APPROVED]
        if approved:
            return approved[-1]
        live = [v for v in vs if v.status != SkillStatus.DEPRECATED]
        if include_drafts and live:
            return live[-1]
        raise SkillNotFound(f"skill {name!r} has no approved version")

    def package(self, name: str, version: str | None = None, verify: bool = True) -> SkillPackage:
        sv = self.get(name, version)
        pkg = SkillPackage(sv.path)
        if verify and sv.status == SkillStatus.APPROVED:
            problems = pkg.verify_manifest()
            if problems or pkg.content_hash() != sv.content_hash:
                raise IntegrityError(f"{name}@{sv.version} was modified after approval: {problems}")
        return pkg

    def list_skills(self) -> list[dict[str, Any]]:
        names = [r["name"] for r in self.db.query("SELECT DISTINCT name FROM skill_versions ORDER BY name")]
        out = []
        for n in names:
            vs = self.versions(n)
            approved = [v for v in vs if v.status == SkillStatus.APPROVED]
            cur = approved[-1] if approved else vs[-1]
            out.append(
                {
                    "name": n,
                    "description": cur.description,
                    "current": approved[-1].version if approved else None,
                    "latest": vs[-1].version,
                    "latest_status": vs[-1].status.value,
                    "versions": len(vs),
                    "trusted": bool(approved),
                }
            )
        return out

    def validations(self, name: str, version: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if version:
            rows = self.db.query(
                "SELECT * FROM validations WHERE skill_name = ? AND version = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (name, version, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM validations WHERE skill_name = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (name, limit),
            )
        return [{**dict(r), "passed": bool(r["passed"]), "report": loads(r["report"], {})} for r in rows]

    # ---- registration ------------------------------------------------------------------------------

    def _next_version(self, name: str, requested: str | None, bump: str) -> tuple[str, str | None]:
        vs = self.versions(name)
        if not vs:
            return requested or "1.0.0", None
        latest = vs[-1].version
        if requested and version_key(requested) > version_key(latest):
            return requested, latest
        return bump_version(latest, bump), latest

    def _insert(self, pkg: SkillPackage, parent: str | None, actor: str) -> SkillVersion:
        sv = SkillVersion(
            name=pkg.name,
            version=pkg.version,
            status=SkillStatus.DRAFT,
            content_hash=pkg.content_hash(),
            path=str(pkg.path),
            description=pkg.description,
            parent_version=parent,
        )
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO skill_versions"
                "(name,version,status,content_hash,path,description,parent_version,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sv.name, sv.version, sv.status.value, sv.content_hash, sv.path, sv.description, parent, sv.created_at),
            )
            self.db.audit(
                "skill.register",
                f"{sv.name}@{sv.version}",
                {"content_hash": sv.content_hash, "parent": parent},
                actor,
                conn=c,
            )
        return sv

    def add_from_content(self, content: PackageContent, bump: str = "minor", actor: str = "system") -> SkillVersion:
        version, parent = self._next_version(
            content.name, None if content.version == "0.0.0" else content.version, bump
        )
        content.version = version
        dest = self.root / content.name / version
        pkg = build_package(dest, content)
        return self._insert(pkg, parent, actor)

    def add_from_dir(self, src: Path | str, bump: str = "minor", actor: str = "system") -> SkillVersion:
        src_pkg = SkillPackage(src)
        name = src_pkg.name
        if not NAME_RE.match(name):
            raise PackageError(f"invalid skill name {name!r}")
        version, parent = self._next_version(name, src_pkg.version, bump)
        dest = self.root / name / version
        if dest.exists():
            raise PackageError(f"{name}@{version} already exists")
        shutil.copytree(src_pkg.path, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        for p in dest.rglob("*"):
            if p.is_file():
                p.chmod(p.stat().st_mode | stat.S_IWUSR)
        meta = yaml.safe_load((dest / "skill.yaml").read_text())
        if meta.get("version") != version:
            meta["version"] = version
            (dest / "skill.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
        pkg = SkillPackage(dest)
        pkg.write_manifest()
        return self._insert(pkg, parent, actor)

    def fork(self, name: str, version: str | None = None, bump: str = "minor", actor: str = "system") -> SkillVersion:
        """Create a new editable draft from an existing version."""
        return self.add_from_dir(self.get(name, version).path, bump=bump, actor=actor)

    def refresh_draft(self, name: str, version: str) -> SkillVersion:
        sv = self.get(name, version)
        if sv.status == SkillStatus.APPROVED:
            raise IntegrityError("approved versions are immutable; fork a new version instead")
        pkg = SkillPackage(sv.path)
        pkg.write_manifest()
        h = pkg.content_hash()
        if h != sv.content_hash:
            with self.db.tx() as c:
                c.execute(
                    "UPDATE skill_versions SET content_hash = ?, status = ?, description = ?"
                    " WHERE name = ? AND version = ?",
                    (h, SkillStatus.DRAFT.value, pkg.description, name, version),
                )
        return self.get(name, version)

    # ---- validation / approval ---------------------------------------------------------------------

    def record_validation(self, report: ValidationReport) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO validations(id,skill_name,version,content_hash,passed,score,report,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    report.id,
                    report.skill_name,
                    report.version,
                    report.content_hash,
                    int(report.passed),
                    report.score,
                    dumps(report.summary()),
                    report.created_at,
                ),
            )
            row = c.execute(
                "SELECT status FROM skill_versions WHERE name = ? AND version = ?", (report.skill_name, report.version)
            ).fetchone()
            if row and row[0] in (SkillStatus.DRAFT.value, SkillStatus.VALIDATED.value):
                new = SkillStatus.VALIDATED if report.passed else SkillStatus.DRAFT
                c.execute(
                    "UPDATE skill_versions SET status = ? WHERE name = ? AND version = ?",
                    (new.value, report.skill_name, report.version),
                )
            self.db.audit(
                "skill.validate",
                f"{report.skill_name}@{report.version}",
                {"passed": report.passed, "score": report.score},
                conn=c,
            )

    def validate(self, name: str, version: str | None = None, run_tests: bool = True) -> ValidationReport:
        sv = self.get(name, version)
        if sv.status in (SkillStatus.DRAFT, SkillStatus.VALIDATED):
            sv = self.refresh_draft(name, sv.version)
        pkg = self.package(name, sv.version)
        report = self.validator.validate(pkg, run_tests=run_tests)
        self.record_validation(report)
        return report

    def approve(self, name: str, version: str, approver: str, note: str = "") -> SkillVersion:
        if not approver or approver.strip() in ("", "system"):
            raise ApprovalError("approval requires a named human approver")
        sv = self.get(name, version)
        if sv.status == SkillStatus.APPROVED:
            return sv
        if sv.status == SkillStatus.DEPRECATED:
            raise ApprovalError("cannot approve a deprecated version")
        pkg = SkillPackage(sv.path)
        current_hash = pkg.content_hash()
        last = self.db.query_one(
            "SELECT passed, content_hash FROM validations WHERE skill_name = ? AND version = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (name, version),
        )
        if not last or not last["passed"]:
            raise ApprovalError(f"{name}@{version} has no passing validation; run validate first")
        if last["content_hash"] != current_hash or current_hash != sv.content_hash:
            raise ApprovalError(f"{name}@{version} changed since it was validated; re-validate first")
        if pkg.verify_manifest():
            raise ApprovalError(f"{name}@{version} manifest does not match files")
        ts = now()
        with self.db.tx() as c:
            c.execute(
                "UPDATE skill_versions SET status = ?, approved_by = ?, approved_at = ? WHERE name = ? AND version = ?",
                (SkillStatus.APPROVED.value, approver, ts, name, version),
            )
            self.db.audit(
                "skill.approve", f"{name}@{version}", {"content_hash": current_hash, "note": note}, approver, conn=c
            )
        _make_readonly(Path(sv.path))
        (self.root / name / "CURRENT").write_text(version + "\n")
        return self.get(name, version)

    def deprecate(self, name: str, version: str, actor: str, reason: str = "") -> SkillVersion:
        self.get(name, version)
        with self.db.tx() as c:
            c.execute(
                "UPDATE skill_versions SET status = ? WHERE name = ? AND version = ?",
                (SkillStatus.DEPRECATED.value, name, version),
            )
            self.db.audit("skill.deprecate", f"{name}@{version}", {"reason": reason}, actor, conn=c)
        cur = [v for v in self.versions(name) if v.status == SkillStatus.APPROVED]
        cur_file = self.root / name / "CURRENT"
        if cur:
            cur_file.write_text(cur[-1].version + "\n")
        elif cur_file.exists():
            cur_file.unlink()
        return self.get(name, version)

    def discard_draft(self, name: str, version: str, actor: str = "system") -> None:
        sv = self.get(name, version)
        if sv.status == SkillStatus.APPROVED:
            raise ApprovalError("approved versions cannot be discarded; deprecate instead")
        with self.db.tx() as c:
            c.execute("DELETE FROM skill_versions WHERE name = ? AND version = ?", (name, version))
            self.db.audit("skill.discard", f"{name}@{version}", {}, actor, conn=c)
        shutil.rmtree(sv.path, ignore_errors=True)

    # ---- diff / export / import --------------------------------------------------------------------

    def diff(self, name: str, a: str, b: str) -> str:
        pa, pb = SkillPackage(self.get(name, a).path), SkillPackage(self.get(name, b).path)
        fa, fb = pa.file_hashes(), pb.file_hashes()
        chunks: list[str] = []
        for rel in sorted(set(fa) | set(fb)):
            if fa.get(rel) == fb.get(rel):
                continue
            ta = (pa.path / rel).read_text(errors="replace").splitlines(keepends=True) if rel in fa else []
            tb = (pb.path / rel).read_text(errors="replace").splitlines(keepends=True) if rel in fb else []
            chunks.extend(difflib.unified_diff(ta, tb, f"{a}/{rel}", f"{b}/{rel}"))
        return "".join(chunks)

    def export(self, name: str, version: str | None, dest_dir: Path | str) -> Path:
        pkg = self.package(name, version)
        if pkg.verify_manifest():
            pkg = SkillPackage(self.refresh_draft(name, pkg.version).path)
        dest = Path(dest_dir) / f"{pkg.name}-{pkg.version}.avskill.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in [*pkg.files(), pkg.path / "manifest.json"]:
                zf.write(p, f"{pkg.name}/{p.relative_to(pkg.path).as_posix()}")
        self.db.audit("skill.export", f"{pkg.name}@{pkg.version}", {"file": str(dest)})
        return dest

    def import_package(self, src: Path | str, actor: str = "import") -> SkillVersion:
        """Import a zip or directory. Integrity is verified; trust is NOT transferred (lands as draft)."""
        src = Path(src)
        with tempfile.TemporaryDirectory(prefix="av-import-") as tmp:
            if src.is_file():
                with zipfile.ZipFile(src) as zf:
                    for member in zf.namelist():
                        target = (Path(tmp) / member).resolve()
                        if not str(target).startswith(str(Path(tmp).resolve()) + os.sep):
                            raise PackageError(f"unsafe path in archive: {member}")
                    zf.extractall(tmp)
                roots = [p.parent for p in Path(tmp).rglob("skill.yaml")]
                if len(roots) != 1:
                    raise PackageError("archive must contain exactly one skill package")
                pkg_dir = roots[0]
            else:
                pkg_dir = src
            problems = SkillPackage(pkg_dir).verify_manifest()
            if problems:
                raise IntegrityError(f"package integrity check failed: {problems}")
            return self.add_from_dir(pkg_dir, actor=actor)
