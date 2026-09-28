"""Secret redaction applied to everything the recorder stores."""

from __future__ import annotations

import re
from typing import Any

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}")),
    (
        "assignment",
        re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)(\s*[=:]\s*)(['\"]?)[^\s'\"]{4,}\3"),
    ),
]
SENSITIVE_KEYS = re.compile(r"(?i)^(password|passwd|secret|api[_-]?key|token|access[_-]?token|authorization)$")


def redact_text(text: str) -> str:
    for name, pat in PATTERNS:
        if name == "bearer":
            text = pat.sub(lambda m: m.group(1) + "[REDACTED]", text)
        elif name == "assignment":
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pat.sub(f"[REDACTED:{name}]", text)
    return text


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if isinstance(k, str) and SENSITIVE_KEYS.match(k) else redact(v)) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
