# text_stats

Count characters, words and lines in text and report the most frequent words.

- **Version:** 1.0.0
- **Tags:** text, nlp, analysis
- **Spec:** AgentVallet Portable Agent Skill Package v1.0

## How to use (any agent, any vendor)

```bash
echo '<input json>' | python run.py        # -> output json
echo '{"input": ..., "output": ...}' | python validate.py
```

Inputs are described by `inputs.schema.json`, outputs by `outputs.schema.json`,
deterministic checks by `rules.json`, and the human-readable procedure by `workflow.md`.
Human corrections live in `corrections.md` and have priority over learned behaviour.
