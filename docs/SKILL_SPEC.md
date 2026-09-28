# Portable Agent Skill Package — Specification v1.0

A skill package is a plain directory any agent can read and run: ChatGPT, Claude,
Codex, Cursor, local LLMs, ERP agents, or a shell script. No AgentVallet install
is needed to *use* one; AgentVallet is only needed to learn, validate and version them.

```text
<skill_name>/
├── README.md            human overview + usage
├── skill.yaml           metadata (required fields below)
├── workflow.md          the procedure, human-readable
├── rules.json           deterministic output checks
├── inputs.schema.json   JSON Schema (2020-12) for input
├── outputs.schema.json  JSON Schema (2020-12) for output
├── run.py               entrypoint: stdin JSON -> stdout JSON
├── validate.py          custom validator: stdin {input, output} -> {passed, errors}
├── corrections.md       human corrections (highest priority)
├── exceptions.md        known edge cases and failures
├── examples/*.json      {"input": ..., "expected_output": ...}
├── tests/test_*.py      portable pytest regression tests
└── manifest.json        sha256 of every file + content hash
```

## skill.yaml

| field | required | notes |
|---|---|---|
| `name` | yes | `^[a-z][a-z0-9_]{1,63}$` |
| `version` | yes | semver `MAJOR.MINOR.PATCH` |
| `description` | yes | one sentence |
| `spec_version` | | `"1.0"` |
| `tags`, `triggers` | | used by routers; triggers are example task phrasings |
| `entrypoint`, `validator` | | default `run.py`, `validate.py` |
| `timeout_seconds` | | default 60 |
| `permissions` | | `{network: false, filesystem: "workdir"}` |
| `implementation` | | `manual`, `recorded`, `synthesized`, `llm` or `lookup` |
| `created_from` | | provenance: run ids, base version, learned_at |

## Execution contract

```bash
echo '{"text": "hello world"}' | python run.py
# {"chars": 11, "words": 2, ...}
```

- One JSON object on stdin, one JSON object on stdout (last line wins if the program logs).
- Non-zero exit = failure; the last stderr line is the error message.
- Standard library only unless the package documents otherwise.
- AgentVallet runs entrypoints in a temporary copy of the package with an allowlisted
  environment (no API keys), a timeout, and CPU/memory limits on POSIX.

## rules.json

```json
{"rules": [
  {"id": "subtotal_is_sum", "type": "sum_equals", "path": "subtotal",
   "items_path": "lines[*].amount", "tolerance": 0.01,
   "target": "output", "severity": "error", "source": "manual",
   "description": "subtotal equals the sum of line amounts"}
]}
```

Paths use a small JSONPath subset: `a.b`, `items[0]`, `items[*].amount`, optional `$.` prefix.
`target` is `output` (default) or `input`.

| type | extra fields | passes when |
|---|---|---|
| `required` | | the path exists and is not null |
| `not_empty` | | not `""`, `[]`, `{}` or null |
| `type` | `expected` | JSON type matches (`string`, `number`, `integer`, `boolean`, `array`, `object`, `null`) |
| `equals` | `value` | every value equals `value` |
| `enum` | `values` | every value is in `values` |
| `regex` | `pattern` | every value is a string matching `pattern` |
| `range` | `min`, `max` | every value is numeric and within bounds |
| `length` | `min`, `max` | `len(value)` within bounds |
| `sum_equals` | `items_path`, `items_target`, `tolerance` | value = Σ values at `items_path` |
| `equals_path` | `other_path`, `other_target` | value equals the value at another path (e.g. copied from input) |
| `forbid_path` | | the path does not exist |
| `forbid_pattern` | `pattern` | the serialized document does not contain `pattern` (e.g. secrets) |

`severity: "warning"` rules are reported but do not fail validation.
`source` is `manual`, `learned` or `correction`; correction rules take precedence over learned ones.

## manifest.json

```json
{"name": "text_stats", "version": "1.0.0", "spec_version": "1.0",
 "files": {"run.py": "<sha256>", "...": "..."},
 "content_hash": "<sha256 of sorted 'path:hash' lines>"}
```

The manifest makes a package tamper-evident. Importers must verify it; AgentVallet
refuses to import a package whose files don't match, and refuses to load an approved
version whose content changed after approval.

## Trust model

1. **draft**: newly learned, imported or edited.
2. **validated**: the full suite passed (structure, manifest, every example, rules,
   `validate.py`, `tests/`).
3. **approved**: a named human approved the *exact* content hash that passed validation.
   The version becomes read-only and immutable.
4. **deprecated**: retired; no longer routed.

Trust never travels with a package: an imported skill always lands as a draft.
