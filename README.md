# AgentVallet

**Portable AI Work, Memory & Skill Brain**

AgentVallet turns successful AI-assisted work into portable, human-readable, machine-readable, testable and reusable agent skills.

## Core idea

```text
Goal -> Record observable work -> Validate -> Learn -> Freeze as Skill -> Reuse anywhere
```

AgentVallet is deliberately vendor-neutral. The same skill package can be read by ChatGPT, Claude, Codex, Cursor, local LLMs, ERPNext/Frappe agents or future AI systems.

## Design principles

- Local-first and lightweight core
- Exact audit history in SQLite/files
- Portable skills in Markdown + YAML/JSON + scripts
- Deterministic validation before trust
- Human corrections become high-priority learning
- Graph memory is an index, not the source of truth
- Version every skill; never silently overwrite trusted knowledge
- Record observable work, not hidden chain-of-thought

## Portable Agent Skill Package

```text
skills/<skill_name>/
├── README.md
├── skill.yaml
├── workflow.md
├── rules.json
├── inputs.schema.json
├── outputs.schema.json
├── run.py
├── validate.py
├── corrections.md
├── exceptions.md
├── examples/
├── tests/
└── manifest.json
```

## Architecture

```text
AI Desktop / CLI / ERP / Local Agent
              |
              v
        Work Recorder
              |
      +-------+--------+
      |                |
      v                v
 Exact Run Store   Artifact Store
   (SQLite)        (files + hashes)
      |                |
      +-------+--------+
              v
         Learning Engine
              |
      +-------+--------+
      |                |
      v                v
 Portable Skill     Graph Index
  (Git/files)       (Graphiti)
      |
      v
 Skill Router -> Runner -> Validator -> Repair -> Approve -> New Version
```

## Status

Early foundation. The project is being built around a portable `Agent Skill Package` specification, work recorder, validator/runner, skill router, Graphiti adapter and desktop companion.

## Safety boundary

AgentVallet stores prompts, responses, scripts, files, corrections, validations and other observable execution artifacts that the user explicitly provides or authorizes. It does not capture private hidden model reasoning.
