# QSAR Autoresearch

This is the base protocol skill for reusable QSAR workbook pipelines.

Install it as a sibling of `qsar-autoresearch-harness`:

```text
<CODEX_HOME>/skills/qsar-autoresearch/
<CODEX_HOME>/skills/qsar-autoresearch-harness/
```

Use this skill directly for single-agent protocol questions, contract design, or a small local rerun where orchestration would be overkill. Use `$qsar-autoresearch-harness` when you need the full multi-agent audit, pipeline build, training, retry-loop, and QA flow.
