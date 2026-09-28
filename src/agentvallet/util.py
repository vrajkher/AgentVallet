"""Small shared helpers: ids, time, hashing, JSON, tokenising."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "please",
        "can",
        "could",
        "would",
        "should",
        "do",
        "does",
        "did",
        "then",
        "than",
        "into",
        "over",
        "under",
        "up",
        "down",
        "out",
        "get",
        "got",
        "make",
        "made",
        "using",
        "use",
        "via",
        "per",
        "all",
        "any",
        "some",
        "each",
    ]
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(obj, indent=indent, sort_keys=indent is not None, default=str, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    return json.loads(text)


def _stem(tok: str) -> str:
    # Tiny, dependency-free plural normaliser: "invoices" ~ "invoice", "entries" ~ "entry".
    if len(tok) <= 3:
        return tok
    if tok.endswith("ies"):
        return tok[:-3] + "y"
    if tok.endswith(("sses", "xes", "ches", "shes")):
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    toks = _TOKEN_RE.findall(text.lower().replace("_", " "))
    return [_stem(t) for t in toks if keep_stopwords or t not in STOPWORDS]


def slugify(text: str, max_len: int = 48) -> str:
    toks = tokenize(text) or ["skill"]
    slug = "_".join(toks)[:max_len].strip("_")
    return slug or "skill"


def bump_version(version: str, part: str = "minor") -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))
