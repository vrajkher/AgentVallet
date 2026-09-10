import json
import sqlite3
from pathlib import Path

from agentvallet.mcp_proxy import TransparentMCPRecorder, _redact
from agentvallet.recorder import WorkRecorder


def test_redacts_sensitive_keys_recursively():
    payload = {
        "password": "secret",
        "nested": {"api_key": "abc", "safe": "ok"},
        "items": [{"authorization": "Bearer xyz"}],
    }
    safe = _redact(payload)
    assert safe["password"] == "[REDACTED]"
    assert safe["nested"]["api_key"] == "[REDACTED]"
    assert safe["nested"]["safe"] == "ok"
    assert safe["items"][0]["authorization"] == "[REDACTED]"


def test_records_tool_call_and_result_into_active_run(tmp_path: Path):
    db = tmp_path / "agentvallet.db"
    recorder = WorkRecorder(db, tmp_path / "artifacts")
    run = recorder.start("Reconcile finance data", source="test")

    proxy = TransparentMCPRecorder(db)
    proxy.client_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "read_sheet",
                "arguments": {"file": "fmr.xlsx", "api_key": "should-not-store"},
            },
        }
    )
    proxy.server_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"content": [{"type": "text", "text": "done"}]},
        }
    )

    history = recorder.history(run.run_id)
    event_types = [event["event_type"] for event in history]
    assert "mcp_tool_call" in event_types
    assert "mcp_tool_result" in event_types

    call = next(event for event in history if event["event_type"] == "mcp_tool_call")
    assert call["payload"]["tool_name"] == "read_sheet"
    assert call["payload"]["arguments"]["api_key"] == "[REDACTED]"

    proxy.close()
    recorder.close()


def test_uses_proxy_session_when_no_active_run(tmp_path: Path):
    db = tmp_path / "agentvallet.db"
    proxy = TransparentMCPRecorder(db)
    proxy.client_message(
        {
            "jsonrpc": "2.0",
            "id": "abc",
            "method": "tools/call",
            "params": {"name": "ping", "arguments": {}},
        }
    )
    proxy.server_message({"jsonrpc": "2.0", "id": "abc", "result": {"ok": True}})

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT run_id, event_type, payload_json FROM run_events ORDER BY sequence"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0][0].startswith("mcp-proxy-")
    assert rows[0][1] == "mcp_tool_call"
    assert json.loads(rows[1][2])["tool_name"] == "ping"
    proxy.close()
