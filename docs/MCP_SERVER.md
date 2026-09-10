# AgentVallet MCP Server

AgentVallet uses MCP as its primary external AI interface. The core stays local-first: SQLite for exact run history, files/Git for portable skills, optional Graphiti for semantic/temporal memory.

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
- `get_skill`
- `start_work`
- `record_work`
- `record_correction`
- `record_validation`
- `snapshot_artifact`
- `finish_work`
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
  -> list_skills
  -> trusted relevant skill exists?
       yes -> read skill -> execute via host/runtime -> validate -> record result
       no  -> start_work -> solve -> record important observable work
             -> record corrections -> record validations -> finish_work
             -> create skill candidate only after validated success
```

Candidate skills are intentionally not trusted automatically. AgentVallet separates learning evidence from executable trust.

## Privacy boundary

AgentVallet records observable material supplied through tools: task goals, visible outputs/actions, corrections, validation results, and explicitly selected artifacts. It does not capture or require private hidden chain-of-thought.
