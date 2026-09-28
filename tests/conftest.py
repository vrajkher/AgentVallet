from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentvallet.config import Settings
from agentvallet.core import AgentVallet

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


@pytest.fixture
def av(tmp_path: Path) -> AgentVallet:
    return AgentVallet(Settings(home=tmp_path / "home", llm_provider="none", run_timeout=20))


@pytest.fixture
def example_dir(tmp_path: Path):
    def _copy(name: str) -> Path:
        dest = tmp_path / "src" / name
        shutil.copytree(EXAMPLES / name, dest)
        return dest

    return _copy


def record_invoice_runs(av: AgentVallet, n: int = 3, with_code: bool = False) -> list[str]:
    data = [
        {"customer": "acme", "items": [{"sku": "a", "amount": 10.5}, {"sku": "b", "amount": 4.25}]},
        {
            "customer": "globex",
            "items": [{"sku": "c", "amount": 3}, {"sku": "d", "amount": 7}, {"sku": "e", "amount": 1.1}],
        },
        {"customer": "initech", "items": [{"sku": "f", "amount": 100}, {"sku": "g", "amount": 0.99}]},
        {"customer": "umbrella", "items": [{"sku": "h", "amount": 1}, {"sku": "i", "amount": 2}]},
    ][:n]
    code = (
        "import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "print(json.dumps({'customer': d['customer'], 'total': round(sum(i['amount'] for i in d['items']), 2),"
        " 'line_count': len(d['items'])}))\n"
    )
    ids = []
    for inp in data:
        with av.record("Compute invoice total", tags=["finance"], agent="test") as run:
            run.input(inp)
            run.prompt("Add up the line amounts")
            if with_code:
                run.code(code)
            run.output(
                {
                    "customer": inp["customer"],
                    "total": round(sum(i["amount"] for i in inp["items"]), 2),
                    "line_count": len(inp["items"]),
                }
            )
        ids.append(run.id)
    return ids
