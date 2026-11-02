# Evaluation, improvement and terminal APIs

These are numerical producers. The coordinator must use the durable runtime for
native execution bindings, accepted prerequisites and independent review. Calling
a producer directly does not establish scientific acceptance.

## Development evaluation

```sh
python scripts/qsar_assess.py --development development.csv --folds folds.json --predictions selection_procedure_oof.csv --contract contract.json --split-manifest preprocessing_manifest.json --output-dir EMPTY_DIR
```

The contract's `evaluation` object requires `mode: development`, `target_column`
identical to preparation/training, `required_candidates: ["selection_procedure"]`,
`uncertainty: molecular_group_bootstrap`, positive integer
`bootstrap_repetitions`, and an explicit `seed`. The prediction CSV requires
`task_id,output_row_id,outer_fold,candidate_id,prediction`.

The evaluator verifies frozen split hashes, development-only partitions, unique
headers/IDs, exact candidate/task coverage and group-disjoint folds. It derives
targets from accepted development records, recomputes MAE/RMSE/R² and residual
sums, and uses each outer training mean for the baseline. Constant-target R² is
undefined, not silently replaced with zero or one. Numerical overflow blocks.
Source-level metrics and group-bootstrap intervals accompany pooled metrics.
Intervals are conditional on saved predictions and do not include uncertainty
from repeating training/model selection. No outer-score model ranking is performed.

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
task coverage. It regenerates only the selected fixed feature recipe and calls
`predict`; it never fits on terminal data. Full descriptor order and RDKit version
must match. Descriptor failures/nonfinite values become missing values under the
locked training policy, with a separate per-row/feature ledger.

Outputs include `terminal_predictions.csv`, `terminal_nonfinite_ledger.csv` and
`terminal_evaluation.json`. Prior exposure remains disclosed; a newly reserved
partition of historically used data is not independent external validation.
Only locally produced, hash-bound joblib models are supported; untrusted model
deserialization is not an intended input path.

Each producer emits a `manifest.json` handoff where applicable. Preparation and
training supply stage-specific manifests; the coordinator wraps their exact
owned output files, checks and limitations for `Runtime.submit`. A manifest or
successful process exit is never a substitute for a distinct review receipt.
