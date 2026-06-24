---
name: qsar-autoresearch-harness
description: "Coordinate a multi-agent QSAR harness for workbook audits, in-vivo subset preparation, leakage-aware feature staging, per-table model training, bounded retry-loop design, result packaging, and QA. Use this skill first for real QSAR pipeline execution; load qsar-autoresearch as the protocol source."
metadata:
  protocol_skill: "qsar-autoresearch"
  protocol_relative_path: "../qsar-autoresearch/SKILL.md"
---

# QSAR Autoresearch Harness

This harness coordinates specialist agents around the `qsar-autoresearch` protocol. The goal is to reduce workbook-specific failure modes by separating contract audit, deterministic preparation, leakage review, model training, retry-loop design, and independent QA.

Use this harness when the user asks for actual QSAR model-building runs, cross-dataset generalization, workbook reruns, leakage-focused audits, result packaging, or a bounded next-experiment loop. For a simple conceptual question about QSAR workflow design, answer directly or use only `qsar-autoresearch`.

## Execution Mode

Use Codex multi-agent fan-out/fan-in with producer-reviewer gates.

- Spawn independent agents only when the current task needs real audit, execution, or packaging work.
- Use read-only analysts for contract and QA review.
- Use worker agents for preparation, staging, training, packaging, and rerun tasks.
- Keep the main agent as orchestrator. The main agent owns final decisions, user communication, and the final report.

## Protocol Source

Before any run, load:

1. The sibling skill `$qsar-autoresearch`, normally installed at `../qsar-autoresearch/SKILL.md` relative to this harness skill.
2. Any project-local artifact contracts already proven in previous runs, such as dataset introspection manifests, prep manifests, feature manifests, training summaries, and next-experiment manifests.

Do not duplicate or override the protocol source casually. If the harness exposes a repeated QSAR failure pattern, update `qsar-autoresearch` itself so the protocol improves.

## Agent Roster

| Role file | Agent type | Purpose | Primary outputs |
|---|---|---|---|
| `references/agents/dataset-contract-auditor.md` | `explorer` or `worker` | Identify workbook shape, sheet policy, in-vivo filter, task-family map, and artifact contracts | `dataset_contract.md`, introspection manifest |
| `references/agents/feature-prep-lead.md` | `worker` | Build deterministic prepared tables and leakage-aware feature manifests | `prep_manifest.json`, `feature_manifest.json` |
| `references/agents/training-eval-lead.md` | `worker` | Train approved tables, compute split counts, label balance, metrics, and HTML summaries | `training_summary.json`, `training_summary.html` |
| `references/agents/next-experiment-controller.md` | `worker` or `explorer` | Generate exactly one bounded next-experiment proposal from current evidence | `next_experiment_manifest.json` |
| `references/agents/qa-reviewer.md` | `worker` | Cross-check manifests, metrics, leakage guardrails, and final package clarity | `qa_verdict.md`, required fixes |

Use fewer agents for smaller jobs, but keep QA separate from the producer when results will be shared or interpreted scientifically.

## Workspace Layout

Create one run directory unless the user gives one:

```text
qsar_run_<dataset>_<timestamp>/
  _workspace/
    00_contract/
    01_prepare/
    02_features/
    03_training/
    04_next_experiment/
    05_qa/
  introspection/
  prep/
  features/
  training/
  package/
  summary.html
  run_index.json
```

Intermediate artifacts stay in `_workspace/` for audit. Final user-facing artifacts are copied or written to the top-level domain folders.

## Workflow

### Phase 0: Context check

Classify the request before touching inputs.

- Initial run: no existing manifests. Create a fresh run directory.
- Extension of a proven harness: reuse validated contract and artifact formats when the dataset family truly matches.
- Packaging-only request: do not retrain unless outputs are missing or stale.
- Simple explanation: no harness run. Use `qsar-autoresearch` directly and answer.

### Phase 1: Blocking preflight

Do locally before delegation because every later step depends on it.

1. Verify Python is available.
2. Verify `pandas`, `scikit-learn`, and `openpyxl` are importable.
3. Verify RDKit is importable when structure normalization, molecular weight conversion, or chemistry features are required.
4. Verify the workbook exists and can be opened.
5. Write `preflight.md`.

If Python, RDKit, or the required tabular ML stack is missing, stop and report missing dependencies. Do not improvise a fake QSAR pipeline.

### Phase 2: Parallel planning

Spawn independent agents in parallel when the task is non-trivial.

- Dataset-contract auditor: detect or verify workbook adapter assumptions, in-vivo filter, grouping lattice, task families, and output contracts.
- Feature-prep lead: inspect columns, structure fields, and known leakage risks, then propose exact preparation and feature-stage outputs.
- Training-eval lead: inspect prior baseline or comparison evidence and propose the exact metric and summary plan.

Each agent writes a short artifact under `_workspace/` and lists hard stops.

### Phase 3: Gate before training

The main agent reads Phase 2 outputs and decides whether the run can proceed.

Hard stops:

- No explicit workbook contract.
- No defensible in-vivo selection rule.
- No mapping from endpoint/response fields into allowed task families.
- Suspected leakage fields are unresolved.
- No clear output path for manifests and summary artifacts.

Allowed proceed states:

- Contract is explicit and current: proceed.
- Contract can be adapted from a sibling dataset with cited differences: proceed and record those differences.
- User explicitly asks for a diagnostic-only run: proceed with a bounded diagnostic package and label it diagnostic.

### Phase 4: Preparation and execution

Run preparation, feature staging, and training through worker agents or locally when the work is sequential.

Required behavior:

- Save exact commands, configs, manifests, and outputs.
- Preserve excluded-subset reasons.
- Fit selectors only within training folds.
- Record total/train/test counts for every trained table.
- For classification, record train/test label balance.
- Build `training_summary.html` before final interpretation.
- Keep incomparable historical baselines as context only.

### Phase 5: Next-experiment control

Generate a bounded retry only after current evidence exists.

Rules:

- Emit exactly one deterministic `next_experiment_manifest`.
- No automatic chain into another run.
- Explain rejected alternatives.
- If the run is good enough, emit a stop decision rather than a fake next experiment.

### Phase 6: QA review

Run QA after artifacts exist and before the final report.

The QA reviewer must cross-check:

- Required manifest files exist and are non-empty.
- Dataset contract and actual workbook outputs still match.
- Exclusion reasons and empty prepared-table folders are documented.
- Training summaries show split counts and, for classification, label balance.
- Leakage-sensitive fields stayed excluded or were tested under a declared policy.
- Final HTML and package docs are understandable to a non-author reader.

If QA finds a blocking issue, fix it and rerun only the affected stage.

### Phase 7: Final verdict

Write the final report with:

- dataset contract summary,
- preparation and exclusion rationale,
- feature and leakage policy summary,
- training result summary,
- next-experiment verdict,
- limitations and what remains manual.

Never present weak or opaque model quality as a successful autoresearch outcome. If the run cannot defend its data contract or leakage controls, the correct result is failure analysis and a bounded corrective proposal.

## Data Handoff

Use files for handoff:

- `_workspace/00_contract/dataset_contract.md`
- `_workspace/01_prepare/prep_notes.md`
- `_workspace/02_features/feature_notes.md`
- `_workspace/03_training/training_notes.md`
- `_workspace/04_next_experiment/next_experiment_notes.md`
- `_workspace/05_qa/qa_verdict.md`

For spawned agents, include:

- The user objective.
- The run directory.
- The relevant role file.
- The protocol source skill or sibling protocol path.
- The exact output path they own.
- A warning not to modify other agents' files unless explicitly asked.

## Error Handling

- Retry a failed command once only after identifying a concrete fix.
- If an agent fails, continue with available artifacts only when the missing role is non-blocking; document the omission.
- Preserve conflicting evidence with sources. Do not delete inconvenient leakage findings.
- If more than half the required roles fail, stop and ask for user input or dependency fixes.
- Treat contract drift, unresolved leakage fields, and unreadable final summaries as blocking defects.

## Trigger Tests

Should trigger:

- "Train QSAR models from this workbook and tell me what to try next if the first run is weak."
- "Generalize this endocrine harness to skin and eye irritation datasets."
- "Package the QSAR results for a thesis and include prepared tables for leakage review."
- "Use the QSAR harness to rerun the approved pipeline and summarize the final HTML."
- "Build a reusable in-vivo QSAR pipeline with a deterministic next-experiment loop."

Should not trigger:

- "What is QSAR?"
- "Summarize this paper without building a model."
- "Convert this workbook to CSV."
- "Make a slide deck from this figure."

## Test Scenarios

Normal flow:

1. User provides a workbook and says to build a reusable in-vivo QSAR pipeline.
2. Preflight finds Python, pandas, scikit-learn, openpyxl, and RDKit.
3. Contract and prep agents produce an explicit dataset contract plus prepared subsets.
4. Training agent emits JSON and HTML summaries with split counts and label balance.
5. Next-experiment controller emits exactly one deterministic proposal or a stop decision.
6. QA verifies leakage guardrails and package clarity.

Failure flow:

1. Workbook mixes in-vivo and in-vitro rows with no explicit filter.
2. Contract auditor cannot defend the row-selection rule.
3. Harness stops before training and reports the missing contract.
