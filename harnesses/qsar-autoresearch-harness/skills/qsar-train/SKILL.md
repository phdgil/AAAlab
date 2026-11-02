---
name: qsar-train
description: Execute a declared QSAR representation-by-algorithm search with fold-local preprocessing, feature-count optimization and auditable trial coverage. Use after data, split and feature reviews; not for terminal holdout evaluation.
---

# QSAR Training

Own the `training` stage as an implementation agent, normally native `executor`.
This skill defines a procedure; it does not register a native agent or grant permissions.

## Load only this stage's context

Read the [shared protocol](../qsar-autoresearch/SKILL.md), [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md), current research contract, and assigned training inputs.
Do not load historical transcripts or unrelated stage skills by default.

## Required inputs

- Accepted curation, preprocessing and feature artifacts with current independent review receipts.
- Development rows, targets, immutable row IDs, molecular groups and frozen inner/outer fold assignments.
- Feature inventory, eligible representations, transform specifications and inference feature schema.
- Per-task representation-by-algorithm candidate matrix, search spaces, primary metric, seeds, resource/trial budgets and pruning rules.
- Benchmark exposure ledger and explicit output ownership. Final-test labels and outcome summaries are not training inputs.

If a prerequisite is absent, stale or failed, return the exact missing contract rather than beginning a smaller substitute experiment.

## Owned outputs

Write only assigned training paths: search manifest, complete trial ledger, fitted development pipelines, per-row out-of-fold predictions, fit-index evidence, execution logs and coverage summary.
Record hashes of data, splits, features, code, configuration and environment using the shared contract.
The ledger includes task, representation, algorithm, trial ID, searched/fixed parameters, selected feature count, fold scores, status, timing and failure reason.

## Procedure

1. Reconcile all planned tasks and model/representation cells with actual input artifacts. Use all eligible development records for each declared task; distinguish task-specific missingness from an arbitrary sample cap.
2. Confirm estimator/task compatibility and real dependency availability. A failed import or unsupported representation is an operational blocker, not permission to substitute a different implementation.
3. Build an executable pipeline in which imputation, scaling, feature selection, balancing and any learned representation fitting occur only on the current training fold. Deterministic structure-only calculations may be reused when their input/configuration hashes match.
4. Before full search, run the actual pipeline on an isolated validation fixture. Record fit/transform row IDs; check feature/target alignment and that changes to fixture holdout labels cannot change preprocessing or selected settings. Submit this evidence for independent `training` pre-search review; do not open the real sealed holdout.
5. After that review passes, execute the declared search for every required feasible representation-algorithm pair. Include a full supported feature-family baseline where specified, then optimize selection/feature count inside CV rather than fixing it silently.
6. Apply the search budget and pruning policy consistently. Record fixed anchor trials separately from searched trials so they cannot masquerade as broad optimization. Reject duplicate values in declared search dimensions rather than counting repeated configurations as distinct search coverage. Failed and pruned trials retain their actual status and budget cost, including attempts superseded by a retry.
7. Tune on inner development folds. If nested evaluation is required, repeat the entire selection procedure within each outer training fold; outer labels cannot choose its features, hyperparameters or thresholds.
8. Save row-aligned OOF predictions with task/fold/model identity and the declared target scale. Distinguish repeated observations and repeated folds rather than presenting them as independent samples.
9. Reconcile planned, completed, pruned, scientifically inapplicable and operationally blocked cells. On resume, reconstruct completed coverage from the required trial identities, parameters, fold-fit evidence, row-aligned predictions and model bindings before skipping work. A saved completion flag or matching record count alone cannot prove execution. Report current unique coverage separately from historical attempts and their recorded cost; an incomplete search is not a complete negative result.
10. Hand off development models and predictions for `qsar-evaluate`, plus an independent `qsar-review` execution for this stage. Only fit a terminal candidate on all permitted development data after a reviewed improvement stop and lock instruction.

## Boundaries

- Do not revise endpoint definitions, grouping policy, source inclusion or selection metrics to improve a score. Return a versioned change request to the earliest affected stage.
- Do not use terminal-test labels, residuals, rankings or aggregate performance to choose a candidate.
- Do not label a handcrafted descriptor subset as the full toolkit family, approximate embeddings as the requested pretrained method, or an unexecuted algorithm as tested.
- Do not hard-code a universal model list, trial count or success score; execute the current reviewed research contract.
- Do not replace a full run with a smoke test because time or memory is short. Checkpoint and report incomplete coverage when the budget is exhausted.

## Completion and handoff

Return the actual output paths, execution identity, exact commands, input/output digests, coverage and unresolved findings.
Producer checks establish readiness for review, not acceptance. The independent reviewer must be a distinct execution and bind its verdict to these exact outputs.
Stop at the assigned training boundary; terminal evaluation belongs to a separate evaluator.

## Routing examples

- Use: "Run the reviewed fingerprint-by-model HPO matrix and include feature-count search."
- Use: "Resume the remaining declared QSAR trials from verified checkpoints."
- Do not use: "Open the final holdout and report the locked model's performance"; use `qsar-evaluate`.
- Do not use: "Explain why R-squared can be negative"; answer directly.
