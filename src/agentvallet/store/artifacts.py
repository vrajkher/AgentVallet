"""Content-addressed artifact store (objects/<aa>/<rest-of-sha256>)."""

from __future__ import annotations

import builtins
import mimetypes
import os
import tempfile
from pathlib import Path

from ..models import Artifact
from ..util import now, sha256_bytes, sha256_file
from .db import Database


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    def put_bytes(self, data: bytes, name: str = "blob", media_type: str | None = None) -> Artifact:
        digest = sha256_bytes(data)
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write so a crash never leaves a half-written object under its final hash.
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        mt = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        art = Artifact(hash=digest, size=len(data), name=name, media_type=mt, created_at=now())
        with self.db.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO artifacts(hash,size,name,media_type,created_at) VALUES (?,?,?,?,?)",
                (art.hash, art.size, art.name, art.media_type, art.created_at),
            )
        return art

    def put_file(self, path: Path | str) -> Artifact:
        p = Path(path)
        return self.put_bytes(p.read_bytes(), name=p.name)

    def get_bytes(self, digest: str, verify: bool = True) -> bytes:
        path = self._path(digest)
        if not path.exists():
            raise FileNotFoundError(f"artifact {digest} not found")
        data = path.read_bytes()
        if verify and sha256_bytes(data) != digest:
            raise ArtifactIntegrityError(f"artifact {digest} is corrupted")
        return data

    def meta(self, digest: str) -> Artifact | None:
        row = self.db.query_one("SELECT * FROM artifacts WHERE hash = ?", (digest,))
        return Artifact(**dict(row)) if row else None

    def list(self, limit: int = 100) -> builtins.list[Artifact]:
        rows = self.db.query("SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [Artifact(**dict(r)) for r in rows]

    def verify_all(self) -> builtins.list[str]:
        """Return hashes of objects that are missing or corrupted."""
        bad: builtins.list[str] = []
        for r in self.db.query("SELECT hash FROM artifacts"):
            p = self._path(r["hash"])
            if not p.exists() or sha256_file(p) != r["hash"]:
                bad.append(r["hash"])
        return bad
