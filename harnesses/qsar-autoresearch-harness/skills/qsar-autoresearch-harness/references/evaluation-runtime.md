# Evaluation, improvement and terminal APIs

These are numerical producers. The coordinator must use the durable runtime for
native execution bindings, accepted prerequisites and independent review. Calling
a producer directly does not establish scientific acceptance.

## Development evaluation

```sh
python scripts/qsar_assess.py --development development.csv --folds folds.json --predictions selection_procedure_oof.csv --oof-manifest selection_procedure_oof_manifest.json --contract contract.json --split-manifest preprocessing_manifest.json --output-dir EMPTY_DIR
```

The contract's `evaluation` object requires `mode: development`, `target_column`
identical to preparation/training, `required_candidates: ["selection_procedure"]`,
`uncertainty: molecular_group_bootstrap`, positive integer
`bootstrap_repetitions`, and an explicit `seed`. The prediction CSV requires
`task_id,output_row_id,outer_fold,candidate_id,prediction`.

`training.task_type` defaults to `regression`. Classification requires labels
`0/1`, with `positive_class: 1`; `prediction` is the probability for the fitted
`classes_ == 1` column, not an assumed column position. Classification OOF rows
also require `task_type,positive_class,threshold`. The OOF manifest is mandatory
for both regression and classification. It binds the prediction CSV, training inputs, fit-index registry and
each outer model. Its `tasks[task].outer_folds` records bind `fold,threshold,model`
and `threshold_provenance` (`source`, `scope`, `trial_id`, `fit_axis_id`), the
selected recipe/parameters, and the frozen training-identity digest. The
evaluator reconstructs the bound input identity and replays each outer model
on its validation rows without fitting. Rebound hashes alone cannot authorize
a model trained against another contract or corrected target data. Each row
must match its own outer pipeline's saved threshold and exact training boundary.
Different folds may have different thresholds; the final-selection threshold
must never replace them. Thresholds are either fixed by the training contract or
selected from its grid on inner-validation evidence. Evaluation never searches
thresholds or ranks outer outcomes.

The evaluator verifies frozen split hashes, development-only partitions, unique
headers/IDs, exact candidate/task coverage and group-disjoint folds. It derives
targets from accepted development records, recomputes MAE/RMSE/R² and residual
sums, and uses each outer training mean for the baseline. Constant-target R² is
undefined, not silently replaced with zero or one. Numerical overflow blocks.
Source-level metrics and group-bootstrap intervals accompany pooled metrics.
Intervals are conditional on saved predictions and do not include uncertainty
from repeating training/model selection. No outer-score model ranking is performed.

Classification recomputes MCC, sensitivity, specificity, balanced accuracy,
accuracy, ROC AUC, average precision and `tp,tn,fp,fn` confusion counts. A label
is positive when its probability is **greater than or equal to** its locked
threshold. Probabilities outside `[0,1]`, nonfinite values and nonbinary labels
block evaluation. Undefined metrics are `null` with a corresponding
`<metric>_undefined_reason`: single-class targets do not acquire invented AUC/AP
or balanced accuracy; MCC is undefined for a zero denominator. Sensitivity and
specificity require their respective target class. Empty slices retain zero
rows/counts and explicit undefined reasons. Classification baselines use each
outer-training positive prevalence under the same locked thresholds.

Molecular-group bootstrap samples whole groups with replacement, retaining
row-specific frozen thresholds. `attempted_replicates` and per-metric
`replicate_accounting` disclose valid/invalid replicates and undefined reasons;
single-class resamples are not silently counted as valid ranking estimates.
Fewer than two groups yields an explicit undefined interval with zero attempts.

When training declares `applicability_domain`, each bound outer model's bank must
match **only that fold's training IDs and parent identities**. Validation scores
use its saved bank/cutoff. Row-level `domain_rows` and inside/outside/abstained
coverage and metrics include source slices. OOD and domain-abstention rows remain
in pooled metrics. No full-development bank substitutes for an outer-training bank.

## Improvement decision

```sh
python scripts/qsar_finish.py improvement --evaluation evaluation.json --coverage search_coverage.json --selection final_selection.json --proposal proposal.json --output-dir EMPTY_DIR
```

The producer supplies a reasoned proposal with `decision: STOP|CONTINUE`, `reason`,
`failure_class`, `hypothesis`, `expected_comparison`, `stopping_rule`, `evidence`
(existing file paths) and `rejected_alternatives`. CONTINUE also requires
`restart_from`, `proposed_change` and positive integer `remaining_budget`.
The script binds evidence and emits `next_experiment_manifest.json`; it does not
decide which scientific intervention is justified. An independent reviewer must
challenge that judgment before the runtime advances the loop.

A successful STOP requires complete trial accounting without unresolved failed
trials and a complete final selection. It produces `model_lock.json`, which binds
the selection file and task/candidate IDs. The runtime submission's `model_lock`
references the already accepted training selection; the generated lock is itself
a reviewed improvement output. Incomplete/budget-exhausted work cannot use this
successful STOP path to claim research completion.

After STOP passes, use `qsar_train.py final-fit` under the internal `final-fit`
stage. Fit only the locked recipe on permitted development data, then obtain a
separate review. The terminal stage cannot begin before that review passes.

## Terminal evaluation

```sh
python scripts/qsar_finish.py terminal --development development.csv --terminal sealed_terminal.csv --split preprocessing_manifest.json --features feature_manifest.json --final-fit final_fit.json --contract contract.json --output-dir EMPTY_DIR
```

The runtime reserves terminal exposure before dispatch; a failed invocation does
not restore unseen status. This producer checks the exact feature/training/split
lineage, locked model artifacts, dependency versions, row/group disjointness and
task coverage. Each fitted artifact must carry the exact frozen training-identity
digest as well as its recipe digest; identical row IDs do not authorize reuse
after training labels or other bound inputs change. It regenerates only the selected fixed feature recipe and calls
`predict` for regression or `predict_proba` for classification; it never fits on
terminal data. Classification checks selection, final-fit metadata and fitted
pipeline agree on `task_type`, `positive_class` and the locked scalar `threshold`.
It locates class `1` explicitly, validates both probability columns and records
threshold and predicted label beside every probability. Descriptor order and RDKit
version must match. Descriptor failures/nonfinite values become missing under the
locked training policy, with a separate per-row/feature ledger.

Fingerprint replay checks the complete saved generator recipe, count/bit method,
atom invariants, feature order and dtype. Counts remain counts. Models with
`feature_route: smiles` receive the complete 1-D `parent_smiles` axis through
their locked pipeline, not a replacement numeric representation. A final domain
bank must match the exact development training IDs and parents; replay uses its
frozen cutoff and writes `terminal_domain.csv` without filtering OOD/abstained rows.

Outputs include `terminal_predictions.csv`, `terminal_nonfinite_ledger.csv` and
`terminal_evaluation.json`. Prior exposure remains disclosed; a newly reserved
partition of historically used data is not independent external validation.
Only locally produced, hash-bound joblib models are supported; untrusted model
deserialization is not an intended input path.

## External evaluation

```sh
python scripts/qsar_finish.py external --development development.csv --external sealed_external.csv --split preprocessing_manifest.json --features feature_manifest.json --final-fit final_fit.json --contract contract.json --output-dir EMPTY_DIR
```

Use the runtime's separate `terminal-evaluation` dispatch with
`evaluation_mode: external`, after reviewed STOP and final-fit. It reserves
external exposure once without reserving, resetting or restoring terminal
exposure. It never substitutes for the required reserved terminal stage.

The external artifact must match `split.external_cohort.artifact` and its original
input bytes; exposure and exclusion ledgers must also verify. Declared
`prior_exposure`/`exposure_history` and the sealed artifact binding remain visible.
`independent_holdout: false` prevents sealing or external origin from being
represented as untouched independent validation. Development/external canonical
parent overlap blocks replay; atom maps and stereochemical annotations cannot
bypass this connectivity check. Shared group IDs or scaffolds alone are not
identity leakage. Scaffold overlap blocks only when explicitly requested by the
frozen external exclusion policy.

Outputs are separate `external_predictions.csv`, `external_nonfinite_ledger.csv`,
`external_evaluation.json` (`mode: external`) and, when enabled,
`external_domain.csv`. The same locked model, positive class, threshold and
training-reference bank are replayed with no additional fits. External task
subsets are disclosed; every supplied row must receive a prediction or evaluation
blocks. Coverage reports original/predicted rows, zero dropped rows and
OOD/domain-abstention source slices. External results never replace terminal results.

Each producer emits a `manifest.json` handoff where applicable. Preparation and
training supply stage-specific manifests; the coordinator wraps their exact
owned output files, checks and limitations for `Runtime.submit`. A manifest or
successful process exit is never a substitute for a distinct review receipt.
