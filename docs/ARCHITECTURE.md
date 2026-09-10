# AgentVallet Architecture

## Mission

Own your AI working knowledge. Capture successful observable work once, validate it, freeze it into a portable skill, and let any future AI or local agent reuse it.

## Source-of-truth rule

AgentVallet separates exact knowledge from semantic memory:

- **SQLite**: exact run/audit history
- **Files + Git**: executable portable skills, rules, scripts and documentation
- **Graphiti**: relationship, temporal and experience index
- **Validators/tests**: trust gate

Graph memory must never be the only copy of executable business logic.

## Agentic loop

```text
Goal
 -> Skill Router
 -> existing trusted skill?
    -> yes: execute -> validate -> finish / repair
    -> no: AI solves -> record -> validate -> human approve
 -> extract learning
 -> create candidate skill
 -> test
 -> version
 -> trust
 -> index in Graphiti
```

## Trust states

1. `candidate` — generated from work but not frozen
2. `tested` — automated validation exists and passes
3. `approved` — human approved the behavior
4. `trusted` — versioned, approved, tested, reusable
5. `deprecated` — retained for history but no longer selected

## Portable skill contract

Every skill is self-describing and should remain useful without AgentVallet itself. A future AI can inspect `manifest.json`, `README.md`, `workflow.md`, rules, scripts, validators and examples using ordinary filesystem/Git tools.

## Learning hierarchy

Human correction > validated rule > successful run pattern > unvalidated model suggestion.

Corrections must never be silently converted into universal rules. They first become candidate knowledge, then require validation and scope.

## Privacy boundary

Capture only observable material supplied or explicitly authorized by the user: prompts, visible responses, scripts, files, tool events, corrections, validation results and final outputs. Do not attempt to capture hidden chain-of-thought, passwords, secrets, system-wide keystrokes or silent clipboard history.

## Long-term modules

- Desktop Companion
- CLI/IDE capture adapters
- OpenAI Agents tracing adapter
- Frappe/ERPNext adapter
- Skill Router
- Skill Compiler
- Deterministic Runner
- Validator + Repair loop
- Graphiti experience index
- Policy/approval engine
- Portable skill exchange format
