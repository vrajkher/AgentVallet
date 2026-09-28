"""CLI, REST API and MCP server."""

import asyncio
import json

import pytest
from typer.testing import CliRunner

from agentvallet import cli

from .conftest import EXAMPLES


@pytest.fixture
def invoke(tmp_path):
    runner = CliRunner()
    home = str(tmp_path / "clihome")

    def _run(*args, input=None):
        cli._state.pop("av", None)
        res = runner.invoke(cli.app, ["--home", home, *args], input=input)
        return res

    return _run


def test_cli_full_flow(invoke):
    assert invoke("init").exit_code == 0
    for n in (1, 2, 4):
        rid = invoke("record", "start", "double a number").stdout.strip()
        assert invoke("record", "step", rid, "input", "-d", json.dumps({"x": n})).exit_code == 0
        assert invoke("record", "step", rid, "output", "-d", json.dumps({"y": n * 2})).exit_code == 0
        assert invoke("record", "finish", rid).exit_code == 0
    res = invoke("--json", "learn", "--goal", "double a number", "--name", "doubler")
    assert res.exit_code == 0, res.stdout
    out = json.loads(res.stdout)
    assert out["strategy"] == "lookup" or out["validation"]["passed"]
    assert invoke("skill", "approve", "doubler", out["version"], "--by", "me").exit_code == 0
    assert invoke("--json", "skill", "list").exit_code == 0
    res = invoke("--json", "run", "doubler", "-i", json.dumps({"x": 2}))
    assert res.exit_code == 0 and json.loads(res.stdout)["output"] == {"y": 4}
    assert invoke("audit", "verify").exit_code == 0
    assert invoke("doctor").exit_code == 0
    res = invoke("--json", "runs", "list")
    assert len(json.loads(res.stdout)) == 4


def test_cli_skill_add_validate_export(invoke, tmp_path):
    assert invoke("skill", "add", str(EXAMPLES / "text_stats")).exit_code == 0
    assert invoke("skill", "validate", "text_stats").exit_code == 0
    res = invoke("--json", "skill", "export", "text_stats", "--out", str(tmp_path))
    assert res.exit_code == 0 and json.loads(res.stdout)["file"].endswith(".avskill.zip")
    res = invoke("--json", "route", "count words", "--drafts")
    assert json.loads(res.stdout)[0]["name"] == "text_stats"
    assert invoke("correct", "no target").exit_code == 1


def test_api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from agentvallet.api.server import create_app
    from agentvallet.config import Settings
    from agentvallet.core import AgentVallet

    monkeypatch.setenv("AV_API_TOKEN", "t0ken")
    av = AgentVallet(Settings(home=tmp_path / "apihome"))
    c = TestClient(create_app(av))
    assert c.get("/api/health").json()["auth"] is True
    assert c.get("/api/stats").status_code == 401
    c.headers["Authorization"] = "Bearer t0ken"
    assert c.get("/").status_code == 200

    for n in (1, 2, 3):
        r = c.post(
            "/api/runs",
            json={
                "goal": "square a number",
                "status": "success",
                "steps": [{"kind": "input", "data": {"n": n}}, {"kind": "output", "data": {"sq": n * n}}],
            },
        )
        assert r.status_code == 201
    runs = c.get("/api/runs").json()
    assert len(runs) == 3
    assert len(c.get(f"/api/runs/{runs[0]['id']}").json()["steps"]) == 2

    learned = c.post("/api/learn", json={"goal": "square a number", "name": "squarer"}).json()
    assert learned["validation"]["passed"]
    v = learned["version"]
    assert c.post(f"/api/skills/squarer/{v}/approve", json={"approver": "dana"}).status_code == 200
    detail = c.get("/api/skills/squarer").json()
    assert detail["version"]["status"] == "approved" and "run.py" in detail["files"]
    assert "def run" in c.get(f"/api/skills/squarer/{v}/files/run.py").text
    assert c.get(f"/api/skills/squarer/{v}/files/../../agentvallet.db").status_code == 404
    ex = c.post("/api/execute", json={"skill": "squarer", "input": {"n": 2}}).json()
    assert ex["ok"] and ex["output"] == {"sq": 4}
    assert c.post("/api/corrections", json={"text": "fix", "skill": "squarer"}).status_code == 201
    assert c.get(f"/api/skills/squarer/{v}/export").headers["content-type"] == "application/zip"
    assert c.get("/api/audit/verify").json()["ok"]
    assert c.post("/api/route", json={"task": "square number"}).json()[0]["name"] == "squarer"


def test_mcp_server(av):
    pytest.importorskip("mcp")
    from agentvallet.mcp.server import build_server

    server = build_server(av)

    async def flow():
        names = {t.name for t in await server.list_tools()}
        assert {"route_task", "run_skill", "record_run", "learn_skill", "add_correction"} <= names
        code = "import json,sys\nd=json.load(sys.stdin)\nprint(json.dumps({'s': d['a'] + d['b']}))\n"
        for a, b in ((1, 2), (5, 7)):
            await server.call_tool(
                "record_run",
                {"goal": "add two numbers", "input": {"a": a, "b": b}, "output": {"s": a + b}, "code": code},
            )
        res = await server.call_tool("learn_skill", {"goal": "add two numbers", "name": "adder"})
        return res

    res = asyncio.run(flow())
    payload = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if payload is None:  # older SDKs return content blocks
        payload = json.loads(res[0].text if isinstance(res, list) else res.content[0].text)
    assert payload["strategy"] == "recorded" and payload["validation"]["passed"]
