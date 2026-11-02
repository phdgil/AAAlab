---
name: qsar-evaluate
description: Derive QSAR development metrics, evaluate a locked model on a sealed terminal holdout, or package reviewed results without refitting. Requires an explicit evaluation or release mode; does not tune models or repair candidate data.
---

# QSAR Evaluation

Own one explicit mode: `development-evaluation`, `terminal-evaluation` or `release`.
For evaluation use a distinct native `verifier` execution from the model producer; for release packaging use `executor`. Both outputs receive a further independent `qsar-review` pass before acceptance.
This skill supplies evaluation methodology, not native-agent registration or access authority.

## Load only this stage's context

Read the [shared protocol](../qsar-autoresearch/SKILL.md), [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md), current evaluation contract and the locked inputs for the assigned mode.
Do not load optimization history beyond the selection/exposure evidence needed to establish the claim.

## Required inputs

- Current accepted data/split/feature/training receipts and immutable identity, target and prediction axes.
- Declared metrics, target units/transforms, class mapping, frozen thresholds, baseline policy, grouping and uncertainty procedure.
- For development: OOF predictions, inner/outer fold identities, candidate IDs and selection provenance.
- For terminal: reviewed improvement stop, locked selected pipeline/configuration, permitted development-fit receipt, sealed holdout binding and exposure history.
- For release: current accepted stage receipts, evaluated predictions/metrics, inference artifacts, declared package coverage and existing limitations; do not request raw sealed outcomes merely to format a report.
- Assigned evaluation output paths and actual runtime permission to read the relevant evidence.

Missing provenance, a stale pipeline or unexplained test exposure blocks the corresponding claim. A historical internal benchmark may still be evaluated under its honest label.

## Owned outputs

Write assigned evaluation metrics, per-row scored predictions, reconciliation checks, uncertainty results and concise interpretation.
Preserve candidates and source data. Include exact commands, evaluator execution ID, input/model/environment hashes and the evaluation mode.
Keep development, terminal and release artifacts separate; do not overwrite one with another.

## Procedure common to evaluation modes

1. Verify hashes and compare prediction IDs against expected memberships. Reject duplicated, missing or misordered rows unless the repeated-row contract explicitly accounts for them.
2. Reconstruct target values and class order from accepted evidence, not from a producer's printed score. Verify transformed versus original scales and probability-to-class thresholds.
3. Recompute each reported metric independently from saved predictions. Reconcile confusion-matrix counts or regression residual totals with the actual denominator; report undefined metrics explicitly rather than silently replacing them.
4. Compute declared baselines without fitting on evaluation labels. For regression, distinguish the training-mean predictor from the test-mean denominator used by R-squared.
5. Compare candidates only on matching tasks, rows, targets and folds. If cohorts differ, report separate estimates and identify the difference instead of ranking headline scores.
6. Estimate uncertainty under the declared dependence structure, such as molecular-group resampling. Do not treat repeated CV folds or repeated measurements as independent external datasets.
7. Report source/stratum/domain coverage and the metric required by the scientific use case. Missing classification thresholds mean threshold-dependent metrics are unavailable, not permission to optimize them here.

## Development mode

- Evaluate OOF predictions without opening terminal outcomes. Confirm each prediction was produced without fitting or choosing its pipeline on that row's evaluation fold.
- For nested CV, verify that the full model/feature selection procedure was inside the outer training partition; aggregate scores alone cannot prove this.
- Record whether outer-CV findings have already guided adaptation. Repeatedly inspected development results are exploratory evidence.
- Provide residual/error diagnostics to `qsar-improve`, clearly separating source-label conflicts, pipeline failures and model prediction errors.

## Terminal mode

- Check that development improvement stopped and the model, threshold and analysis plan were locked before terminal outcomes were accessed.
- Evaluate the locked inference pipeline on the sealed cohort once under the recorded contract. No refitting, feature selection, calibration or threshold search on that cohort.
- If an execution defect requires correction, preserve the failed attempt and disclose all exposure. The orchestrator must review whether any corrected evaluation still supports a confirmatory claim.
- Record access and release of terminal results in the exposure ledger. Any subsequent development use makes this cohort an exposed benchmark; renaming or reseeding does not restore independence.

## Release mode

- Reconcile current accepted artifacts and their dependency hashes before packaging. A missing, failed or stale stage review remains visible and prevents an unqualified research-complete claim.
- Package source/exclusion accounting, task and feature coverage, executed search settings, CV and terminal metrics, uncertainty, applicability limits, inference schema/model, environment and reproducibility commands. Include `training_summary.html` when requested.
- Preserve scientifically inapplicable versus operationally failed cells, budget limitations, exposed benchmarks and superseded attempts. Copy or reference immutable evidence without changing its scientific meaning.
- Reproduce bounded inference/schema loading where permitted, without refitting, choosing another model or editing accepted predictions. Report a portability failure to the responsible stage.
- Submit the package and checksums to a separate `qsar-review` release execution. Formatting changes invalidate affected package reviews, not unchanged source acquisitions or models.

## Completion and handoff

Return metrics with prediction-level evidence, limits and exact artifact hashes to a different `qsar-review` execution.
Do not modify the candidate to make checks pass or approve your own evaluation. A read-only role returns its computed findings for the leader to persist under the same execution provenance.
A reproducible score does not establish an adequate endpoint, useful applicability domain or deployment readiness on its own.

## Routing examples

- Use: "Recompute the grouped OOF metrics and compare matched candidates."
- Use: "The development run is stopped and locked; evaluate its reserved final cohort."
- Use: "Package the accepted QSAR results and inference artifacts without retraining."
- Do not use: "Tune the threshold to maximize sensitivity on the test set"; return the exposure/selection conflict.
- Do not use: "Search for a better model"; use `qsar-train` or `qsar-improve`.
