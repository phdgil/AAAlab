# Native QSAR agent definitions

These TOMLs follow the installed Codex native role format (`name`, `description`, `model_reasoning_effort`, `developer_instructions`). They inherit the runtime/caller model and do not alter sandbox or approval settings. Skills remain separate files; installation renders their exact absolute paths into each definition.

`stage-map.json` is the native-role mapping. It is checked against the existing skill assignment map before installation.

| Stage | Producer | Independent reviewer |
|---|---|---|
| Contract | qsar-contract-analyst | qsar-method-reviewer |
| Collection | qsar-source-collector | qsar-data-reviewer |
| Curation | qsar-data-curator | qsar-data-reviewer |
| Preprocessing | qsar-preprocess-engineer | qsar-model-reviewer |
| Features | qsar-feature-engineer | qsar-model-reviewer |
| Training | qsar-trainer | qsar-model-reviewer |
| Development evaluation | qsar-evaluator | qsar-model-reviewer |
| Improvement | qsar-improvement-lead | qsar-method-reviewer |
| Terminal evaluation | qsar-evaluator | qsar-model-reviewer |
| Release | qsar-release-editor | qsar-model-reviewer |

Every review is a separate native execution from the producer, even when the same review role is reused later. The caller must use a fresh context (`fork_turns: "none"` where supported) and provide the exact stage envelope. The role definition cannot set this launch option or prove independent execution by itself.

## Validation and explicit installation

Run from the repository root (Python3.11+):

```powershell
python harnesses/qsar-autoresearch-harness/scripts/install_native_agents.py --check
python scripts/test_qsar_native_agents.py
```

Install project-local definitions pointing at this checkout's skills:

```powershell
python harnesses/qsar-autoresearch-harness/scripts/install_native_agents.py --agent-home .codex --skills-root harnesses/qsar-autoresearch-harness/skills
```

For an isolated installed skill pack, give its actual agent home and omit `--skills-root`; it defaults to `<agent-home>/skills`. There is no implicit global installation. This helper installs role definitions only, not skills, and does not modify `config.toml` or existing OMX/user roles.

All source definitions and skill links are checked before writing. Byte-identical definitions are left alone. A conflicting QSAR role fails without changes unless `--replace` is explicit; replacements retain the old bytes and a hash manifest under `agents/.aaalab-qsar-backups/`. Linked/reparse destinations are refused. Unrelated roles, including course-producer, are untouched.

Reload the runtime and inspect its actual exposed role catalog before invoking these names. Installed files alone do not prove that a particular Codex App/CLI session has discovered them. The present session's tool schema may remain fixed until reload. If a custom role is unavailable, report that fact and use an available generic role with the exact definition and skills only when the coordinator explicitly chooses that supported adaptation; do not invent native role availability.

## Integration boundary

The helper returns JSON with installed/unchanged role names, backup directory, skills root and stage assignments. `runtime_status` is deliberately `files_installed_not_runtime_verified`. Root orchestration owns integrating this mapping into dispatch and package commands; the helper does not launch agents, authenticate review receipts or implement scientific stage gates.
