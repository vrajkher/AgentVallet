# AgentVallet MCP Server

AgentVallet uses MCP as its primary external AI interface. The core stays local-first: SQLite for exact run history, files/Git for portable skills, optional Graphiti for semantic/temporal memory.

Three operating layers make the core more efficient and powerful:

- **Event Sourcing** — every important run lifecycle event is appended in sequence and can be replayed/audited later.
- **Skill Router** — every new goal is checked against trusted skills before an AI starts solving from zero.
- **Transparent MCP Recording (optional)** — AgentVallet can sit between an AI client and another stdio MCP server and automatically capture observable tool calls, results, and errors.

## Install

```bash
pip install -e .
```

## Run AgentVallet MCP

```bash
agentvallet-mcp
```

The default transport is `stdio`, which is lightweight and appropriate for local AI desktop/CLI hosts that launch MCP servers as subprocesses.

## Core MCP tools

- `system_status`
- `list_skills`
- `route_skill`
- `get_skill`
- `start_work`
- `record_work`
- `record_correction`
- `record_validation`
- `snapshot_artifact`
- `finish_work`
- `get_run_events`
- `create_skill_candidate`

## MCP resource

- `agentvallet://skills`

## MCP prompt

- `reuse_prior_work`

## Suggested AgentVallet client configuration

Configure your MCP-capable AI host to launch:

```text
command: agentvallet-mcp
```

If the executable is not on PATH, launch the module instead:

```text
command: python
args: -m agentvallet.mcp_server
```

Run it from the AgentVallet project directory so `data/` and `skills/` resolve there, or set:

- `AGENTVALLET_DATA_DIR`
- `AGENTVALLET_SKILLS_DIR`

## Transparent MCP recorder

Transparent recording is optional. Use it when an AI client launches another MCP server over stdio and you want AgentVallet to capture the observable MCP tool traffic without requiring the AI to manually call `record_work` for every tool action.

Instead of configuring the downstream server directly:

```text
AI -> downstream MCP server
```

configure the AI to launch the AgentVallet proxy, which then launches the downstream server:

```text
AI -> AgentVallet MCP proxy -> downstream MCP server
```

Example:

```bash
agentvallet-mcp-proxy -- python -m your_mcp_server
```

or:

```bash
python -m agentvallet.mcp_proxy -- python -m your_mcp_server
```

The proxy passes stdin/stdout through unchanged and records only observable JSON-RPC `tools/call` requests and their matching results/errors.

### What gets captured

- downstream MCP tool name
- tool arguments
- matching tool result or error
- request ID
- event sequence and timestamp
- proxy session ID

If an AgentVallet work run is active, events are attached to that run. If no work run is active, they are stored under a dedicated `mcp-proxy-...` session ID so evidence is not lost.

### Safety defaults

Sensitive dictionary keys such as passwords, API keys, access/refresh tokens, authorization headers, cookies and secrets are redacted before storage. Payloads are also size-bounded to avoid uncontrolled database growth.

Default maximum serialized payload size is 12,000 characters. Override with:

```text
AGENTVALLET_MCP_MAX_PAYLOAD_CHARS
```

or pass:

```bash
agentvallet-mcp-proxy --max-payload-chars 8000 -- <downstream command>
```

The proxy records MCP traffic only. It does not capture private hidden chain-of-thought, arbitrary desktop screen activity, or actions that bypass the proxied MCP connection.

## Agentic operating pattern

```text
AI receives goal
  -> route_skill(goal)
  -> trusted relevant skill exists?
       yes -> get_skill -> execute -> validate -> record result
       no  -> start_work -> solve -> record useful observable work
             -> corrections -> validations -> finish_work
             -> human approval -> create skill candidate
```

When transparent recording is enabled for downstream MCP tools:

```text
AI work
  -> MCP tool call
  -> AgentVallet proxy auto-records request
  -> downstream MCP executes
  -> AgentVallet proxy auto-records result/error
  -> normal validation/correction/finalization continues
```

## Event-sourced run pattern

```text
run_started
  -> work_event
  -> mcp_tool_call / mcp_tool_result / mcp_tool_error (optional proxy evidence)
  -> work_event/correction
  -> validation_recorded
  -> artifact_snapshotted (optional)
  -> run_finished
```

Each event has a run ID, monotonically increasing sequence number, event ID, timestamp and structured payload. The normal `runs` table remains a fast current-state snapshot; `run_events` is the append-only audit/replay history.

A run requires at least one validation and all recorded validations must pass before its status can become `success`. A skill candidate can be created through MCP only from a successful human-approved run.

Candidate skills are intentionally not trusted automatically. AgentVallet separates learning evidence from executable trust.

## Privacy boundary

AgentVallet records observable material supplied through tools: task goals, visible outputs/actions, corrections, validation results, explicitly selected artifacts, and optionally proxied MCP tool traffic. It does not capture or require private hidden chain-of-thought.
