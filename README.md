# AgentVallet

**Portable AI Work, Memory & Skill Brain**

AgentVallet records AI-assisted work that succeeded and turns it into skills that are portable,
readable by people and machines, tested, and reusable. The same skill package can be read
by ChatGPT, Claude, Codex, Cursor, local LLMs, ERPNext/Frappe agents or future AI systems.

```text
Goal -> Record observable work -> Validate -> Learn -> Freeze as Skill -> Reuse anywhere
```

## Quick start

```bash
pip install -e ".[api,mcp]"          # Python 3.11+
av init

# 1. Record work (from any agent, script, the REST API or MCP)
RUN=$(av record start "Convert celsius to fahrenheit" --tag units)
av record step $RUN input  -d '{"celsius": 100}'
av record step $RUN code - <<'EOF'
import json, sys
d = json.load(sys.stdin)
print(json.dumps({"fahrenheit": d["celsius"] * 9 / 5 + 32}))
EOF
av record step $RUN output -d '{"fahrenheit": 212.0}'
av record finish $RUN
# ...record a couple more runs...

# 2. Learn a draft skill; it is validated automatically
av learn --goal "convert celsius to fahrenheit" --name c_to_f

# 3. Approve it (a named human; the version becomes immutable)
av skill approve c_to_f 1.0.0 --by you

# 4. Reuse it: route a task to the best trusted skill, run, validate, repair
av execute "change 37 celsius into fahrenheit" -i '{"celsius": 37}'

# 5. Correct it. Corrections become the highest-priority learning signal
av correct "must never go below absolute zero" --skill c_to_f \
  --rule '{"type": "range", "path": "fahrenheit", "min": -459.67}'
av learn --name c_to_f          # -> c_to_f@1.1.0 (1.0.0 stays trusted and untouched)
av skill diff c_to_f 1.0.0 1.1.0

# 6. Move it anywhere
av skill export c_to_f --out .  # c_to_f-1.0.0.avskill.zip with a verifiable manifest
```

Dashboard and REST API: `av serve` → <http://127.0.0.1:8765/>
MCP server for Claude, Cursor and other MCP clients: `av mcp`

## Design principles

- The core runs locally and stays lightweight: only pydantic, pyyaml, jsonschema, typer and rich.
- The exact audit history lives in SQLite plus content-addressed files. The audit log is **hash-chained and append-only**.
- Skills are portable, written as Markdown + YAML/JSON + scripts ([spec](docs/SKILL_SPEC.md)).
- Deterministic validation comes before trust: schemas, rules, examples, a custom validator and tests.
- When a person corrects a skill, that correction becomes high-priority learning.
- Graph memory is an index, not the source of truth.
- Every skill is versioned and trusted knowledge is never silently overwritten: approved versions are read-only and integrity-checked on load.
- AgentVallet records observable work only, never hidden chain-of-thought. Secrets are redacted on capture.

## Architecture

```text
AI Desktop / CLI / REST / MCP / ERP / Local Agent
              |
              v
        Work Recorder  (redaction, command capture, file snapshots)
              |
      +-------+--------+
      v                v
 Exact Run Store   Artifact Store
   (SQLite +        (sha256
  audit chain)     content-addressed)
      +-------+--------+
              v
        Learning Engine  (schema inference, invariant mining, program synthesis,
              |           recorded-code adoption, optional LLM, corrections first)
      +-------+--------+
      v                v
 Portable Skill     Graph Index
 (versioned dirs)   (local SQLite graph | Graphiti)
      |
      v
 Skill Router -> Runner -> Validator -> Repair -> Approve -> New Version
```

| Module | What it does |
|---|---|
| `store/` | SQLite with versioned migrations, WAL, and a hash-chained audit log that database triggers keep append-only. Content-addressed artifact store with atomic writes and integrity checks. |
| `recorder/` | `with av.record(goal) as run:` captures input/output/prompt/response/code/tool calls/commands/files. Secrets are redacted before anything is stored. |
| `spec/` | The Portable Agent Skill Package: build, load, manifest hashing, tamper detection, structural validation, and a rule engine with 12 rule types. |
| `runner/` | Sandboxed subprocess execution: temporary working copy, env allowlist, timeout, rlimits, process-group kill. The validator runs structure + manifest + every example + rules + `validate.py` + `tests/`. |
| `learning/` | Builds a draft skill from runs. It infers JSON Schemas and mines invariants (sums, copies, ranges, formats), then picks an implementation in order: **recorded** code → **synthesized** program → **LLM** → **lookup** (flagged). |
| `skills/` | Registry (draft → validated → approved → deprecated; fork, diff, export, import), BM25 + coverage + graph router, repair loop (retry → fall back to the previous approved version → LLM patch as a new draft → escalate to a human), and the end-to-end pipeline. |
| `graph/` | Built-in SQLite graph of runs, skills, versions, corrections, agents and concepts, with an optional Graphiti backend. |
| `api/` | FastAPI REST API with optional bearer-token auth, plus a self-contained dashboard (no CDN, works offline). |
| `mcp/` | MCP server tools: `route_task`, `run_skill`, `execute_task`, `record_run`, `learn_skill`, `add_correction`, `get_skill`, `list_skills`, `search_memory`. |

### How learning works

For each learning request, the engine:

1. Selects successful runs (by id, similar goal, or earlier executions of the skill) and pulls out their `(input, output)` pairs.
2. Applies pending **human corrections**. Rule corrections become error-severity rules. Example corrections override recorded outputs for the same input.
3. Infers `inputs.schema.json` and `outputs.schema.json`, and mines rules that hold across *all* examples, such as `total == sum(items[*].amount)`, `customer` copied from the input, non-negative values, and ISO dates.
4. Chooses the first implementation that reproduces **every** example:
   - **recorded**: code captured in a run that follows the stdin→stdout contract.
   - **synthesized**: a generated program in which each output field is a copy, sum, count, mean, min, max or constant over the input.
   - **llm**: `AV_LLM_PROVIDER=anthropic` with `pip install -e ".[llm]"`. It is re-checked against every example and retried with feedback.
   - **lookup**: memorizes the known pairs and rejects unseen input. `exceptions.md` flags this.
5. Writes a new **draft** version with provenance and validates it. It is trusted only after a human approves it.

## Python API

```python
from agentvallet import AgentVallet

av = AgentVallet()                      # AV_HOME or ~/.agentvallet
with av.record("Sum invoice lines", tags=["finance"], agent="my-agent") as run:
    run.input({"items": [{"amount": 2.5}, {"amount": 4}]})
    run.prompt("add the amounts")
    run.output({"total": 6.5})

res = av.learn(goal="sum invoice lines", name="invoice_sum")
report = av.validate("invoice_sum", res.version.version)
av.approve("invoice_sum", res.version.version, approver="vraj")
print(av.execute("add up this invoice", {"items": [{"amount": 1}, {"amount": 2}]}).output)
```

## REST API (selected)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/runs` | record a complete run `{goal, steps[], status}` |
| `POST` | `/api/runs/{id}/steps`, `/finish` | incremental recording |
| `POST` | `/api/corrections` | add a correction |
| `POST` | `/api/learn` | learn and validate a draft |
| `POST` | `/api/skills/{name}/{version}/validate`, `/approve` | trust workflow |
| `GET` | `/api/skills/{name}/diff?a=&b=` | version diff |
| `GET` | `/api/skills/{name}/{version}/export` | portable zip |
| `POST` | `/api/route`, `/api/execute` | reuse |
| `GET` | `/api/audit/verify` | verify the hash chain |

Interactive OpenAPI docs are at `/docs`. Set `AV_API_TOKEN` to require `Authorization: Bearer <token>`.

## MCP

```json
{"mcpServers": {"agentvallet": {"command": "av", "args": ["mcp"]}}}
```

The server tells clients to call `route_task` before solving from scratch, to call `record_run` (with the code they used) after succeeding, and to call `add_correction` when a result is wrong.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `AV_HOME` | `~/.agentvallet` | all state: `agentvallet.db`, `objects/`, `skills/`, `exports/` |
| `AV_LLM_PROVIDER` | `none` | `anthropic` enables LLM synthesis and repair |
| `AV_LLM_MODEL` | `claude-opus-5` | model for the Anthropic provider |
| `AV_GRAPH_BACKEND` | `local` | `graphiti` (+ `AV_GRAPHITI_URI/USER/PASSWORD`) |
| `AV_RUN_TIMEOUT` | `60` | default skill timeout in seconds |
| `AV_ROUTE_THRESHOLD` | `0.5` | minimum router confidence for `execute` |
| `AV_REDACT` | `1` | set `0` to disable secret redaction |
| `AV_API_TOKEN` | unset | bearer token for the REST API |

## Development

```bash
pip install -e ".[api,mcp,dev]"
ruff check src tests examples && ruff format --check src tests
mypy src
pytest -q
av doctor        # health: audit chain, artifact integrity, approved-skill integrity
```

The example skills in [`examples/`](examples) are complete packages. `invoice_total` computes exact decimal amounts and uses custom validation. `text_stats` computes text statistics.

## Safety boundary

AgentVallet stores prompts, responses, scripts, files, corrections, validations and other observable execution artifacts that the user explicitly provides or authorizes. It does not capture private hidden model reasoning. Secret-looking values (API keys, tokens, passwords, private keys) are redacted before storage. Skills run in a sandbox that receives an allowlisted environment only, so no credentials are passed to skills. Trust is local: imported skills always arrive as drafts.
