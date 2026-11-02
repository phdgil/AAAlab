---
name: qsar-preprocess
description: "Specify and freeze QSAR evaluation groups, partitions, row axes and fold-local preprocessing before fitting. Use after accepted curation; not fitted transformation selection, hyperparameter search, or terminal evaluation."
---

# QSAR Split and Preprocessing Specification

Produce a defensible pre-fit split and transformation contract. This is stage `preprocessing`, normally assigned to an `executor`. Training executes the fitted operations later; this skill must not fit an imputer, scaler, selector, resampler or predictive model.

## Load and inputs

Read the shared [protocol](../qsar-autoresearch/SKILL.md) and [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md). Load only the accepted endpoint's evaluation and preprocessing policies.

Require current accepted curation artifacts/receipt, research objective, intended generalization population, identity lineage, available study/source groups and prior benchmark-exposure history. The dispatch must distinguish development-accessible inputs from the sealed terminal benchmark and identify its evaluator/custodian.

## Owned outputs

- Versioned split membership and group/row-axis definitions with hashes, deterministic seeds or source-based assignment rules.
- `preprocessing_manifest.json`: split purpose, policy, input bindings, development counts, transform specification and overlap evidence.
- Unfitted preprocessing pipeline specification, schema/dtype rules, expected fit-index behavior and inference contract.
- Exposure ledger referencing known prior access without copying protected terminal labels.
- Pre-fit checks and unresolved-group/metadata ledger; write only dispatched paths.

## Procedure

1. Match evaluation design to the declared scientific use: new molecules, scaffolds, studies, sources, time periods or a deliberate adaptation benchmark. Record the estimand and limitations rather than choosing the easiest split.
2. Derive grouping from accepted identities and required study/source relationships. Keep all records connected by the chosen leakage relation together; document exact, parent, stereochemical and scaffold overlap as applicable.
3. Construct reproducible development folds and reserved evaluation membership from the full eligible population. Sparse strata receive an explicit feasibility verdict, not an undisclosed merge or row cap.
4. When stratification requires terminal target information, have the authorized evaluator/custodian execute that operation and return approved membership/identity evidence only. Modeling agents must not receive sealed terminal labels or use their distribution to redesign the task.
5. Verify membership completeness, unique row identifiers, disjoint required groups and stable ordering. Bind any feature-matrix row order to these identifiers; identical shapes alone do not prove alignment.
   Keep a supplied external cohort separate from the reserved terminal partition. Preserve its source bytes and exposure history. Remove canonical-parent overlaps from candidate training data under the declared identity policy; never delete external compounds to manufacture independence. Scaffold or whole-group exclusions require explicit authorization and distinct ledger reasons. Combined grouping ledgers containing protected structures remain custodian-only; provide a development-only ledger to modeling executions.
6. Define missingness, invalid-feature and transformation handling per feature family. Specify which operations are fixed row-wise computations and which must learn exclusively from the current inner/outer training fold.
7. Specify imputation, scaling, feature selection, resampling, calibration and threshold-selection fit boundaries where relevant. A complete-case policy must be scientifically justified and account for all dropped rows; do not assume one missingness rule fits all assays.
8. Export an unfitted pipeline and a test plan for actual fit-index instrumentation and holdout/label-perturbation checks. The training stage must execute these checks before expensive search; a written plan is not execution evidence.

## Decision boundaries

- Do not fit transformations or select variables here, even when only development rows are available. Feature staging supplies fixed representations; `qsar-train` fits and audits fold-local operations.
- Do not reshuffle a used benchmark and call it unexposed. Record previous test-label use and restrict claims accordingly.
- Do not tune split rules, thresholds or eligibility to increase performance; revise scientific scope only through a versioned reviewed contract.
- Do not disclose sealed terminal targets to collection, curation, feature or model agents once custody is established. Identity-only overlap checks remain allowed within the dispatch's access scope.
- Do not silently weaken grouping because a stratum is small. Return feasibility evidence and an explicit alternative for leader/reviewer adjudication.

## Completion and handoff

A distinct [qsar-review](../qsar-review/SKILL.md) agent checks source-bound row accounting, grouping, exposure and the unfitted specification before features/training consume it. Its pre-fit PASS does not certify later fitting behavior; that requires independent execution review during training.

Return split/preprocessing paths and hashes, development-only diagnostics, unresolved issues and the terminal custodian boundary. Changed membership, grouping or transform policy invalidates all dependent features/searches/results that used the changed contract.

## Routing examples

Use: "Freeze grouped development folds and a sealed final holdout for these accepted QSAR tables."

Use: "Define fold-local preprocessing and verify row identities before generating model inputs."

Near miss: "Fit a scaler and tune feature count across CV folds" belongs to `qsar-train`.

Near miss: "Score the locked model on the final holdout" belongs to `qsar-evaluate` in terminal mode.
