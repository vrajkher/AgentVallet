# AgentVallet MCP Server

AgentVallet uses MCP as its primary external AI interface. The core stays local-first: SQLite for exact run history, files/Git for portable skills, optional Graphiti for semantic/temporal memory.

Two operating layers make the core more efficient and powerful:

- **Event Sourcing** — every important run lifecycle event is appended in sequence and can be replayed/audited later.
- **Skill Router** — every new goal is checked against trusted skills before an AI starts solving from zero.

## Install

```bash
pip install -e .
```

## Run

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

## Suggested client configuration

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

## Event-sourced run pattern

```text
run_started
  -> work_event
  -> work_event/correction
  -> validation_recorded
  -> artifact_snapshotted (optional)
  -> run_finished
```

Each event has a run ID, monotonically increasing sequence number, event ID, timestamp and structured payload. The normal `runs` table remains a fast current-state snapshot; `run_events` is the append-only audit/replay history.

A run now requires at least one validation and all recorded validations must pass before its status can become `success`. A skill candidate can be created through MCP only from a successful human-approved run.

Candidate skills are intentionally not trusted automatically. AgentVallet separates learning evidence from executable trust.

## Privacy boundary

AgentVallet records observable material supplied through tools: task goals, visible outputs/actions, corrections, validation results, and explicitly selected artifacts. It does not capture or require private hidden chain-of-thought.
