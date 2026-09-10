from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .event_store import EventStore


SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _bounded(value: Any, max_chars: int) -> Any:
    safe = _redact(value)
    text = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return safe
    return {"truncated": True, "preview": text[:max_chars], "original_chars": len(text)}


def _latest_active_run(db_path: Path) -> str | None:
    import sqlite3

    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT run_id, payload_json FROM runs ORDER BY updated_at DESC LIMIT 25").fetchall()
        for run_id, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "running":
                return str(run_id)
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None


class TransparentMCPRecorder:
    """Records observable MCP JSON-RPC traffic without changing downstream behavior."""

    def __init__(self, db_path: str | Path, *, max_payload_chars: int = 12000):
        self.db_path = Path(db_path)
        self.store = EventStore(self.db_path)
        self.session_id = f"mcp-proxy-{uuid.uuid4()}"
        self.max_payload_chars = max_payload_chars
        self.pending: dict[str, dict[str, Any]] = {}

    def _run_id(self) -> str:
        return _latest_active_run(self.db_path) or self.session_id

    def client_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method != "tools/call":
            return
        params = message.get("params") or {}
        tool_name = params.get("name")
        payload = {
            "direction": "client_to_server",
            "request_id": request_id,
            "method": method,
            "tool_name": tool_name,
            "arguments": _bounded(params.get("arguments") or {}, self.max_payload_chars),
            "proxy_session_id": self.session_id,
        }
        event = self.store.append(self._run_id(), "mcp_tool_call", payload)
        if request_id is not None:
            self.pending[str(request_id)] = {
                "run_id": event.run_id,
                "tool_name": tool_name,
            }

    def server_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is None:
            return
        pending = self.pending.pop(str(request_id), None)
        if not pending:
            return
        if "error" in message:
            event_type = "mcp_tool_error"
            body = _bounded(message.get("error"), self.max_payload_chars)
        else:
            event_type = "mcp_tool_result"
            body = _bounded(message.get("result"), self.max_payload_chars)
        self.store.append(
            pending["run_id"],
            event_type,
            {
                "direction": "server_to_client",
                "request_id": request_id,
                "tool_name": pending.get("tool_name"),
                "payload": body,
                "proxy_session_id": self.session_id,
            },
        )

    def close(self) -> None:
        self.store.close()


async def _pipe_client_to_server(proc: asyncio.subprocess.Process, recorder: TransparentMCPRecorder) -> None:
    assert proc.stdin is not None
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
        if not line:
            proc.stdin.close()
            await proc.stdin.wait_closed()
            return
        try:
            message = json.loads(line)
            if isinstance(message, dict):
                recorder.client_message(message)
        except Exception:
            pass
        proc.stdin.write(line)
        await proc.stdin.drain()


async def _pipe_server_to_client(proc: asyncio.subprocess.Process, recorder: TransparentMCPRecorder) -> None:
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            return
        try:
            message = json.loads(line)
            if isinstance(message, dict):
                recorder.server_message(message)
        except Exception:
            pass
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


async def _pipe_stderr(proc: asyncio.subprocess.Process) -> None:
    assert proc.stderr is not None
    while True:
        chunk = await proc.stderr.read(4096)
        if not chunk:
            return
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


async def run_proxy(command: list[str], db_path: Path, max_payload_chars: int) -> int:
    if not command:
        raise ValueError("A downstream MCP server command is required")
    recorder = TransparentMCPRecorder(db_path, max_payload_chars=max_payload_chars)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.gather(
            _pipe_client_to_server(proc, recorder),
            _pipe_server_to_client(proc, recorder),
            _pipe_stderr(proc),
        )
        return await proc.wait()
    finally:
        recorder.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transparent stdio MCP proxy that records tool calls/results into AgentVallet."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("AGENTVALLET_DB", "data/agentvallet.db"),
        help="AgentVallet SQLite database path",
    )
    parser.add_argument(
        "--max-payload-chars",
        type=int,
        default=int(os.environ.get("AGENTVALLET_MCP_MAX_PAYLOAD_CHARS", "12000")),
        help="Maximum serialized payload size stored per tool call/result",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Use -- before downstream command")
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("Provide a downstream MCP command after --")
    raise SystemExit(asyncio.run(run_proxy(command, Path(args.db), args.max_payload_chars)))


if __name__ == "__main__":
    main()
