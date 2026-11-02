---
name: qsar-autoresearch-harness
description: Coordinate end-to-end QSAR collection, curation, preprocessing, representation/model search and reviewed improvement using focused agent skills. Use for multi-stage research execution, not a simple QSAR explanation or file conversion.
---

# QSAR agent and skill orchestration

Read the shared [scientific protocol](../qsar-autoresearch/SKILL.md) and [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md). The authoritative assignment map is [agent-skills.json](references/agent-skills.json). Load a stage skill only for its assigned work. Keep one leader-owned run plan and decision log.

## Agent assignments

| Stage | Producer native role | Assigned skill | Separate reviewer |
|---|---|---|---|
| Contract | analyst | qsar-autoresearch | critic + qsar-review |
| Collection | executor | qsar-collect | verifier + qsar-review |
| Curation | executor | qsar-curate | verifier + qsar-review |
| Splits/preprocessing | executor | qsar-preprocess | verifier + qsar-review |
| Feature generation | executor | qsar-features | verifier + qsar-review |
| Training/search | executor | qsar-train | verifier + qsar-review |
| Development evaluation | verifier | qsar-evaluate | separate verifier + qsar-review |
| Improvement | executor | qsar-improve | critic + qsar-review |
| Terminal evaluation | verifier | qsar-evaluate | separate verifier + qsar-review |
| Release | executor | qsar-evaluate | verifier + qsar-review |

The table shows supported generic fallback roles. The assignment map also names the12 domain-native roles packaged under native-agents, with skill and stage ownership checked by validators. Install them with the explicit native-role installer and verify actual runtime role availability before using their names. If unavailable, use a supported equivalent with declared capabilities; never fake independent review. Ordinary execution does not use the Team-only worker role. Read-only agents return findings for the leader to persist.

## Phase 0: Context check

Identify full research, explicitly bounded diagnostic, resume, or packaging-only scope. Read current user decisions, existing artifact hashes and applicable project rules. Preserve superseded runs. Do not retrain completed work merely because an old instruction remains. A diagnostic result is not full research completion.

## Preflight and dispatch

Verify inputs and the environment needed for the next stage; collection does not require an existing workbook. Use Python/pandas/scikit-learn, openpyxl for Excel and RDKit for chemistry where needed. Check optional representation/model dependencies before promising coverage.

For each dispatch supply:

1. Objective, stage/mode/iteration and accepted contract version.
2. Exact shared protocol and assigned SKILL.md paths; relevant artifact paths/hashes only.
3. Accepted upstream review receipts, owned output paths and permitted operations.
4. Producer identity for review, independent acceptance criteria and stop conditions.
5. A reminder that other agents share the workspace and their files must not be overwritten.

Require the child to acknowledge the skill and inputs actually read. Do not send every skill, private historical transcript or full coordinator context. For supported Codex native dispatch, set `fork_turns: "none"` and provide the explicit bounded context envelope; omitting text from the task does not prevent the default inheritance of conversation history. Use fresh contexts for stage producers and reviewers, especially after any holdout exposure. If the runtime cannot isolate context, disclose that limitation and do not claim sealed evaluation or independent blind review. Set agent_type to the installed role; supplying a role name in prose is not role routing. Keep model selection configurable; do not override user/OMX model settings merely to follow this map.

`node scripts/validate_skill_map.js` validates package wiring. `node scripts/validate_skill_map.js --dispatch training` (or `--review training`) prints the assigned role and minimal skill reads for a dispatch. The leader adds real run artifacts/ownership and calls the native tool. This utility does not launch agents or enforce live scientific gates.

## Stage execution and independent acceptance

Use `qsar_runtime.py` to initialize the versioned run, prepare and bind actual native dispatches, submit owned artifacts and record independent review evidence. Execute contract -> collection -> curation -> preprocessing -> features -> training-preflight -> training -> development-evaluation -> improvement. The training-preflight substage uses qsar-train/qsar-review and locks the same pipeline_identity used by training. Each stage has a separate source-based review before dependent execution. A producer cannot review its own stage, even after changing its role label. Check actual dispatch identity, returned evidence and matching artifact hashes.

Preprocessing initially defines splits and transforms without fitting. Before broad HPO, independently review the actual pipeline's fit-index/leakage preflight. Features may generate deterministic descriptors, but learned transforms and selection fit only inside training folds.

The improvement agent chooses one justified intervention or STOP. Review it, then execute within the authorized scope/budget by returning to the earliest changed stage. Invalidate all downstream results/reviews. Explicit proposal-only requests stop at the proposal; full-research mode does not stop after merely writing one. A reviewed STOP can mean success, no justified change, budget exhaustion or unresolved evidence, with distinct result labels.

After a reviewed STOP locks the selection recipe, dispatch the internal final-fit stage using qsar-train. It fits only permitted development data and receives a separate qsar-review PASS. Only then may a separate terminal evaluator access sealed labels and run the locked model. No terminal outcomes are routed to ordinary development. If they are used for redesign, record exposure and begin a new research version; do not claim untouched validation.

## QA review and release

Use qsar-review with the exact stage. Reviewers reconstruct source facts and metrics, not just mutual consistency of producer manifests. The leader records PASS/REVISE/BLOCK with reviewed hashes and actual reviewer evidence. REVISE returns bounded findings to the producer; BLOCK prevents dependent execution. Independent safe work may continue while a gate is unresolved.

Release through qsar-evaluate release mode, reconciling all current reviews and coverage. Preserve train/test counts, label balance, exclusions, feature/search coverage, frozen predictions, uncertainty and next_experiment_manifest. Produce training_summary.html when requested. State scientific acceptance separately from execution completion.

This revision supplies stage skills, dispatch wiring and review procedures. The local [runtime](scripts/RUNTIME.md) now enforces dispatch bindings, independent review gates, pipeline identity, invalidation and terminal reservation. Project-specific scientific adapters and their acceptance remain separately validated; packaging/runtime checks cannot establish scientific correctness. See the shared protocol's maintenance record when changing methods.

## Test Scenarios

- Full research: collect declared LD50 sources, audit endpoint/identity/units, use all eligible strata and planned feature/model searches, review each output, execute justified development improvement, then terminal evaluation.
- Curation repair: changed duplicate policy invalidates splits and dependent searches/reviews; unchanged raw acquisition remains reusable.
- Missing source/unknown unit: preserve evidence and block the dependent pooling/training stage rather than guessing.
- Bounded diagnostic: honor an explicit small-run request, label it diagnostic and do not claim completed full research.
- Near misses: explaining R-squared or converting CSV alone does not start this harness.
