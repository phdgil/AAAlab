# Training runtime

`scripts/qsar_train.py` runs regression or binary classification search on
development artifacts only.
The coordinator must use `qsar_runtime.py` to require accepted upstream reviews
and an independent review of the actual preflight before scientific training.
The standalone numerical APIs do not authenticate reviews or provide a sandbox.
No terminal labels, terminal metrics or terminal files are inputs to these APIs.

## Contract

The research contract contains this explicit `training` object. These values are
an illustrative fixture, not a recommended scientific model list or budget.

```json
{
  "training": {
    "target_column": "target_pLD50",
    "metric": "rmse",
    "seed": 71,
    "nonconvergence_policy": "fail",
    "tasks": {
      "oral": {
        "cells": [{
          "candidate_id": "ridge-descriptors",
          "family": "RDKit2D",
          "algorithm": "Ridge",
          "preprocessing": {"scale": true, "variance_threshold": 0.0},
          "search_space": {"model__alpha": [0.1, 1.0], "select__k": [32, "all"]},
          "n_trials": 4,
          "anchor": {"model__alpha": 1.0, "select__k": "all"}
        }]
      }
    }
  }
}
```

`training.task_type` is `regression` by default, or `classification`.
Regression metrics are `rmse` or `mae`, minimized over pooled inner-validation row losses.
Each task requires a nonempty candidate matrix with unique candidate IDs.
Regression algorithm identifiers are `RandomForest`, `ExtraTrees`, `Ridge`,
`ElasticNet`, `SVR`, `KNN`, `HistGradientBoosting`, `XGBoost`, and `CatBoost`.
The last two require their real optional dependencies; there are no substitutes
and no installation side effects. Unspecified estimator parameters use the
installed library defaults, bound by the environment version record. Declare
scientifically important parameters explicitly in the contract.

### Binary classification

Classification requires explicit numeric labels `0` and `1`; there is no automatic
recoding, binarization, or inference from target names. The positive class is
always `1`. Declare `training.task_type: "classification"` and a maximized primary
metric: `mcc`, `balanced_accuracy`, `roc_auc`, or `average_precision`. Every frozen
outer and inner training **and validation** fold must contain both classes.
Single-class boundaries fail feasibility checks before fitting rather than
producing undefined or misleading selection scores. Revise the scientifically
approved split specification in a new versioned preparation when infeasible.

Canonical classifier identifiers are `LogisticRegression`, `SVC`, `RandomForest`,
`ExtraTrees`, `HistGradientBoosting`, `XGBoost`, `CatBoost`, `LightGBM`, `KNN`,
`MLP`, and `GaussianNB`. The three optional boosting libraries must actually be
installed and are recorded in the environment identity; no estimator substitutes
are used. Without explicit calibration, `SVC` uses its native training-only
`probability=True`; disabling it then fails explicitly. Explicit cross-fitted
calibration instead requires `probability=False`. All classifiers must expose
honest, finite, normalized `predict_proba` outputs with fitted classes exactly
`0/1`; decision-function scores are never passed off as probabilities or
converted using an arbitrary sigmoid. Classification feature selection uses
fold-local `f_classif` instead of `f_regression`.

`training.threshold` is a finite number in `[0,1]`, defaulting to `0.5`.
Labels are predicted by `P(class=1) >= threshold`. Optional `positive_class` must
be the integer `1`. For `mcc` or `balanced_accuracy`, an optional nonempty,
distinct numeric `training.threshold_grid` selects a threshold for **each trial**
using only its pooled current CV validation probabilities. Grid ties use the
first declared value. In an outer scope, these are exclusively inner-validation
rows; no outer holdout labels or metrics influence a threshold or configuration.
Trial/configuration ties then use candidate ID and trial ID. Ranking metrics use
probabilities directly, retain the declared fixed threshold for later label
reporting, and reject a threshold grid because their primary score does not
identify a threshold. Classification scores are pooled across validation rows,
not averaged across per-fold metrics. Fresh final-selection CV independently
selects its configuration and threshold on the declared full-development CV.

For example, the training object can declare:

```json
{
  "task_type": "classification",
  "target_column": "target_class",
  "metric": "mcc",
  "threshold": 0.5,
  "threshold_grid": [0.3, 0.5, 0.7],
  "seed": 71,
  "tasks": {
    "oral": {"cells": [{
      "candidate_id": "logistic-descriptors",
      "family": "RDKit2D",
      "algorithm": "LogisticRegression",
      "preprocessing": {"scale": true, "variance_threshold": 0.0},
      "search_space": {"model__C": [0.1, 1.0], "select__k": [32, "all"]},
      "n_trials": 4,
      "anchor": {"model__C": 1.0, "select__k": "all"}
    }]}
  }
}
```

Preparation and evaluation must name the same target column. Threshold options
are rejected for regression instead of being silently ignored.
Class weighting may be declared as ordinary `model__class_weight` parameters
where the chosen estimator supports it.

Optional `training.selection_constraints` declares any of `min_sensitivity`,
`min_specificity`, and `max_sensitivity_specificity_gap`, each in `[0, 1]`.
These bounds apply to pooled inner-validation predictions at the candidate
threshold, not outer or terminal performance. Thresholds and candidates failing
the bounds retain their real scores and completed-fit evidence but cannot win.
If no candidate meets the bounds, no replacement winner is promoted and the
search stops without a final selection. Bounds are project decisions, not
universal success criteria.

### Explicit calibration and numeric SMOTE

Each classification cell may declare `calibration: {"method": "sigmoid",
"n_splits": 3}` (or `"isotonic"`). On every current training boundary, seeded
`StratifiedGroupKFold` creates inner calibration folds using only that boundary's
labels and molecular groups. Each calibration fold clones the **entire** unfitted
candidate pipeline: audit, optional frozen encoder, imputation, variance filter,
scaling, selection, optional sampler, and estimator. Group overlap, incomplete
coverage, missing classes, and insufficient class/group counts fail explicitly.
The calibrator learns from these held-out scores and original labels. A separate
full-current-training clone supplies the final base estimator. An outer CV
validation row never enters any calibration fit belonging to that CV model.
Threshold search still uses the outer scope's pooled **inner OOF probabilities**,
not calibration-fold training predictions or outer holdout labels.

`sigmoid` fits a logistic calibrator to decision scores (preferred) or class-1
probabilities; `isotonic` fits bounded monotone isotonic regression. Invalid scores
fail instead of switching methods. Explicit SVC calibration never enables SVC's
native probability fitting. The persisted `CrossFittedCalibratedClassifier`
exposes `named_steps` for its final base pipeline and preserves task, threshold,
fit-row and domain-bank attributes. `calibration_audit_` includes exact fold and
calibrator row/group memberships, independent clone IDs, preprocessing fit
statistics, and sampler audits. Fold models are discarded immediately after
score/audit extraction; only the final base and calibrator are persisted.

Numeric classification cells may also declare `sampling: {"method": "smote",
"k_neighbors": 5, "sampling_strategy": "auto"}`. This inserts the real
imbalanced-learn SMOTE immediately before the classifier, **after** fold-local
preprocessing and the original-row audit. It applies in search, calibration
clones, outer refits, and final fitting, never in prediction. Raw SMILES, chemical
rules and frozen-encoder candidates cannot request SMOTE. Imputation never fits
on a validation row, and synthetic samples never enter calibration targets.
Sampler provenance records original fit-row identities and synthetic counts;
individual synthetic IDs/parent IDs are explicitly `null` because SMOTE does not
expose them. No fake ancestry is inferred. Missing/broken imbalanced-learn fails
operationally with no substitute. Regression rejects calibration and sampling.

### Search backend and resources

The default per-cell `backend: "parameter_sampler"` preserves the original
regression search. Its dimensions are explicit nonempty lists, using `model__<parameter>` and
`select__k`. The feature-count dimension includes `all` and at least one positive
integer. `ParameterSampler` samples distinct Cartesian combinations using the
declared seed. `n_trials` cannot exceed the combination count. The separate
all-features anchor adds one trial per cell and scope; it never consumes or
masquerades as the declared search budget. All denotes all features surviving
the fold-local variance filter. An integer exceeding that fold's available
features fails explicitly instead of silently selecting fewer features.

Optional `backend: "optuna"` uses the real optional dependency and TPE ask/tell.
Each suggestion is asked only after the preceding trial's actual CV outcome was
told. Lists and `{"type":"categorical","choices":[...]}` are categorical;
`{"type":"float","low":0.001,"high":10.0,"log":true}` and
`{"type":"int","low":2,"high":10,"step":2}` are genuine distributions,
not discretized surrogate grids. `select__k` remains categorical with `all` and
positive counts. Rule candidates may use valid integer bin and numeric
regularization distributions. The separate anchor is never inserted into the
study or charged against `n_trials`. Repeated suggestions consume attempts and
do not increase distinct-configuration coverage. Failed/pruned attempts remain
in study snapshots and block scientific completion; the trainer does not
silently drop them or implement automatic pruning.

`studies/*.sqlite3` and matching `.snapshot.json` files are exact, hash-bound
Optuna state, tied to task, scope, candidate, training input/code/environment
identity, search declaration and direction. Every terminal trial stores a digest
of its actual CV ledger and fit evidence in Optuna's own user attributes. Resume
checks SQLite bytes, actual storage rows, the exact snapshot, trial parameters,
objective, status and CV digest before reuse. A mutation interrupted between its
durable steps fails closed; history is never reconstructed approximately.
`final_selection.json.search_checkpoint` binds the checkpoint needed to verify
Optuna recipes before locked final fitting; keep the whole search directory.

`training.resources` accepts `threads` (default 1), `concurrent_fits` (default 1),
and an optional ISO-8601 `deadline` with explicit timezone. The trainer is
sequential, so actual concurrent pipeline fits are always 1, never above the
declared ceiling. One thread limit controls estimator `n_jobs`/equivalent,
threadpoolctl BLAS/OpenMP pools and an active frozen encoder's torch intra-op
pool. Estimator thread parameters cannot be searched independently. Optional
per-cell `resource_policy` uses `maximum_trials`, `concurrency` and `deadline`
from the search API; `n_trials` must fit its maximum, and the earlier applicable
deadline wins. Anchors do not consume search trials but obey fit/deadline limits.

Deadlines are checked before every trial and every learned fit, including each
calibration clone, the calibrator, base refits, and locked final fitting. An
expired deadline starts no further search. Exhaustion persists `status:
"incomplete"` and returns that state/report; it is not successful coverage.
These are **start-boundary** controls, not in-fit hard cancellation or a peak-RAM
limiter. A running fit may overrun the deadline; the job layer owns hard stops.
An absolute expired contract deadline cannot be extended on resume without
changing identity; that requires a new versioned contract/run.

Preprocessing uses median imputation with empty columns retained, variance
filtering, optional standard scaling, and supervised univariate F-regression
or F-classification selection according to task type. Every learned transform and estimator is fitted inside the same
pipeline on only the current training rows. No rows or input features are capped.
Python warnings are captured per trial/fold with category, message, fit context,
and a `nonconverged` flag. `nonconvergence_policy` defaults to `fail`, currently
the only accepted value: a scikit-learn `ConvergenceWarning` (including subclasses)
fails that trial and therefore blocks complete search coverage. Ordinary warnings
remain in the ledger even when fitting succeeds. The same rule applies to outer
refits and locked final fits; their failure reports retain the warnings.
`nonconverged: false` means no convergence warning was observed, not a guarantee
of mathematical convergence. Native-library stdout/stderr messages that are not
Python warnings still require review of execution logs.
This implementation does not include pruning, time-budget termination,
encoder fine-tuning, or arbitrary distribution-valued search spaces.
The `MLP` classifier consumes the same frozen feature matrices; it is not a learned
molecular representation. Such requirements need an explicit implementation extension.

Each search dimension must contain distinct values. Repeated choices, including
numerically equivalent values such as `1` and `1.0`, are rejected rather than
silently deduplicated: changing the space would change the declared budget.
The separate anchor may intentionally match one searched configuration; it is
reported as an anchor and does not increase distinct search coverage.

### SMILES-native fitted rules and frozen encoders

Numeric feature cells retain their existing behavior. A raw-structure cell must
explicitly use `family: "SMILES"` and `feature_route: "smiles"`. It consumes only
the row-aligned `parent_smiles` from the frozen development CSV, not target or
other provenance columns. There is no SMILES NPZ, numeric descriptor substitute,
download, remote code, or fallback estimator.

For example, a regression rule cell is:

```json
{
  "candidate_id": "fitted-chemical-rules",
  "family": "SMILES",
  "feature_route": "smiles",
  "algorithm": "ChemicalRuleRegressor",
  "preprocessing": {},
  "rule": {"descriptor_names": ["MolWt", "MolLogP", "TPSA"]},
  "search_space": {"model__n_bins": [2, 4], "model__alpha": [0.1, 1.0]},
  "anchor": {"model__n_bins": 4, "model__alpha": 1.0},
  "n_trials": 4
}
```

Classification uses `ChemicalRuleClassifier` and `model__C` instead of `alpha`.
Both require at least two bin choices and two regularization choices plus their
explicit anchor values. `select__k` is forbidden: the interpretable chemical
basis, not an unrelated numeric selector, is optimized. Descriptor ranges, bins,
scaffold vocabulary/frequencies and additive Ridge/logistic weights are fitted
anew inside every current training fold. Validation scaffolds never enter that
vocabulary. These are fitted rule-derived additive scorers, not unfitted expert
rules, decision trees, or deep networks. Optional `rule` settings are
`descriptor_names`, `smarts_rules`, `fit_intercept`, and classifier `max_iter`.
The fitted model's `explain` reports actual basis contributions.

An encoder cell uses `algorithm: "FrozenSmilesEncoder"`, the same explicit SMILES
route, ordinary numeric preprocessing, a declared `head: {"algorithm": "Ridge"}`
(or a supported sklearn classifier for classification), and this `encoder` object:

```json
{
  "checkpoint_dir": "local-approved-checkpoint",
  "checkpoint_inventory": {"config.json": "SHA256", "model.safetensors": "SHA256", "tokenizer.json": "SHA256"},
  "pooling": "mean",
  "batch_size": 32,
  "device": "cpu",
  "max_length": 128
}
```

The illustrative hashes above must be replaced by real hashes, and the inventory
must include **every** checkpoint file, including all tokenizer/config files and
weight shards. Relative checkpoint directories resolve beside the research
contract. Missing, changed, extra or symlinked files reject before fitting, at
encoder loading/inference, and before completion. Exact inventories and every
file binding enter the training identity, as do `qsar_models.py`,
`qsar_domain.py` and dependency versions (including RDKit and, for encoders,
torch/transformers/tokenizers/Hugging Face Hub/safetensors availability).

The real local Hugging Face encoder is loaded offline with remote code disabled,
frozen, and placed in evaluation mode. The feature stage never receives labels;
only the declared downstream sklearn head is fitted. Head parameters use
`model__...`, and `select__k` searches the actual embedding coordinates. Encoder
parameters/checkpoint identity are fixed candidate settings, not head parameters.
Pooling must explicitly be `mean` (all nonpadding tokens, including special
tokens) or `cls` (a verified leading CLS token). Overlength sequences fail:
truncation is forbidden, including during terminal replay. Provenance records
the actual token limit, pooling, batch size, device, frozen status and checkpoint
fingerprint. This is frozen pretrained representation reuse, not encoder
fine-tuning or a GNN. Missing optional dependencies fail without fake embeddings.

### Training-only applicability domains

Optional `training.applicability_domain` applies to either feature route:

```json
{
  "radius": 2,
  "n_bits": 2048,
  "use_chirality": true,
  "similarity": "max",
  "min_references": 1,
  "cutoff": {"method": "training_quantile", "quantile": 0.05}
}
```

`similarity: "knn_mean"` additionally requires an explicit positive `k`.
A fixed cutoff instead uses `{"method":"fixed","value":0.3}`. The recipe is
validated by `qsar_domain.py`; infeasible reference banks fail, never widen the
cohort. Each outer pipeline's `applicability_domain_` is fitted only on its
ordered outer-training parent structures/row IDs. The locked final pipeline's
bank uses only that task's development rows. Quantile cutoffs use distinct
training-only leave-parent-out similarities; no outer/terminal labels or
terminal structures tune the cutoff. Domain recipe/reference/calibration hashes
are recorded in fit evidence and final metadata and persisted within the
hash-bound model artifact. Pipelines also expose `fit_training_row_ids_` and
`applicability_domain_sha256_`. Inference only calls the existing bank's `score`;
it must never refit a terminal bank or discard OOD/invalid rows.

## Inputs and fold semantics

`development.csv` supplies immutable `output_row_id`, `task_id`, `group_id`,
`partition=development`, and the configured target. SMILES/domain cells also
require `parent_smiles`. All declared task rows must occur exactly once.
`folds.json` follows the preparation runtime contract: each task contains ordered
`row_ids` and `outer_folds`; each outer fold contains `fold`, `train_row_ids`,
`validation_row_ids`, and `inner_folds` with the same membership fields.
Partitions must cover all permitted rows exactly once on validation, and
molecular groups cannot cross any train/validation boundary.

`feature_manifest.json` contains `families[NAME]` with `artifact` (`path`,
`sha256`), `shape`, `dtype`, `feature_names`, and `nonfinite_count`. NPZ files
contain `X`, `row_ids`, `task_ids`, `feature_names`. The runner checks hashes,
row/task/feature axes, shape, dtype and nonfinite counts before fitting. It joins
by identity even when NPZ rows are reordered. Nonfinite feature values become
missing values for fold-local imputation. Targets must be finite.
The complete feature manifest must bind `input`, `contract`, and `split_manifest`.
The complete split manifest must bind the exact development, folds, and contract
files and declare `development_rows`. Missing or stale frozen lineage blocks
training. All consumed files are hashed before reading and reverified before
completion; this never reads the sealed-terminal artifact listed in a split manifest.

Feature validation loads one NPZ family at a time in its stored dtype, verifies
every requested family's hash, shape, dtype, row/task/feature axes and nonfinite
count before fitting, then discards the matrix and retains metadata/row indices.
The returned mapping-compatible `FeatureStore` starts with no resident matrix.
On demand it evicts the preceding family, rechecks the next file's hash, loads
the full matrix and converts to float64 without reducing precision, rows or
features. Nonfinite checks/normalization use bounded row blocks; this changes
temporary mask size only. Repeated access to the same family reuses its matrix.
Cross-family access reloads from the hash-bound artifact. Development CSV reading
retains only row ID, task, group, partition, parent SMILES and target; full provenance remains
in the immutable source CSV.

The cache bounds retained feature storage to one float64 family, not total process
memory. Decompression/casting can temporarily retain the native matrix too;
fold indexing, imputation, scaling, selection, estimator state and model artifacts
also require memory. A family plus those costs can still exceed available RAM.
The resource gate must account for these costs before real training; the cache
is not proof that a particular workload fits. `peak_resident_bytes` measures
cached matrix bytes only, not process peak RSS.

For each outer fold, the runner searches every cell and all its trials on the
frozen inner folds, selects the entire family/model/parameter pipeline using
the inner primary metric, refits only on outer training rows, and predicts outer validation
rows. The OOF candidate identity is `selection_procedure`, because the selected
model may differ between outer folds. Actual selected candidates remain in
`checkpoint.json.completed` and fit evidence. Outer scores never select a model.

Final selection is a separate search with fresh fits using the frozen outer
train/validation membership as full-development CV. It does not reuse outer
evaluation scores or outer-fitted models. Its CV scores are selection evidence,
not another unbiased nested estimate. `final_selection.json` records the
winning configuration; it does not fit any model on all development data.

## Commands and Python APIs

```text
python qsar_train.py preflight --output PREFLIGHT --contract research.json
python qsar_train.py search --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --output SEARCH
python qsar_train.py search --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --output SEARCH --resume
python qsar_train.py final-fit --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --selection SEARCH/final_selection.json --lock lock.json --output FINAL
python qsar_train.py final-fit --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --selection SEARCH/final_selection.json --lock lock.json --output FINAL --resume
python scripts/test_qsar_train.py -v
```

`run_search(development, folds_path, manifest_path, contract_path, output_dir,
resume=False)` returns the checkpoint state. `final_fit(development, folds_path,
manifest_path, contract_path, selection_path, lock_path, output_dir, resume=False)` returns its
fit manifest. Check the returned status: deadline exhaustion returns incomplete.
Exceptions are failures; callers must not treat an existing output
directory as successful completion.

Preflight executes deterministic synthetic checks through those same public
APIs, retains a synthetic fixture and its actual fit evidence, and writes
`preflight.json`. It is self-contained in the installed runtime and does not
import repository test files. With `--contract`, it additionally fits
every declared cell's anchor and first sampled configuration on synthetic
matrices or real synthetic SMILES as appropriate, checking holdout-label
perturbation invariance. Encoder checks load the declared pinned local checkpoint,
not a replacement model. This checks
actual imports and parameter compatibility. It is deliberately not full HPO or
a claim that arbitrary real feature schemas are valid. Review the bound contract,
code and preflight artifacts independently before real training.

## Artifacts and completion

JSON checkpoint commits use a unique same-directory temporary file and a flushed,
atomic replacement. Transient Windows access/sharing/lock denials at replacement
receive at most eight attempts (bounded backoff); persistent errors still fail
without replacing the previous checkpoint. Only the already serialized bytes are
retried, never training fits or scientific warnings.

- `trial_ledger.json`: task, scope, candidate, family, algorithm, anchor/search
  identity, exact parameters, fold losses, pooled score, timing, status and
  failure reason for trials in the current scope attempts, plus per-fold warnings
  and explicit nonconvergence flags. Classification uses `fold_scores[].score`
  instead of regression `loss`, and retains ordered `fold_predictions` entries
  with `fold,prediction`. Trial-level fields include `task_type`, `positive_class`,
  the selected `threshold`, and `threshold_scores:[{threshold,score}]`. Probabilities align exactly to each
  frozen fold's ordered validation row IDs. These records permit deterministic
  metric/threshold recomputation during resume. Superseded attempts remain below.
- `attempt_history.json`: ordered superseded scope attempts, preserving their
  task/scope, ledger, fit evidence, failure reason and warnings. The checkpoint
  retains the same history. A successful retry does not erase an earlier failure.
- `fit_index_registry.json`: immutable `schema_version: "1.0"` with
  `axes: {AXIS_SHA256: {sha256, count, row_ids}}`. Each distinct ordered row axis
  from the frozen inner/outer folds is stored once; SHA-256 uses the runtime's
  canonical `digest(row_ids)` JSON representation. The registry is written once,
  not embedded into or rewritten with every checkpoint.
- `fit_evidence.json`: `schema_version: "2.0"`, the registry's exact file binding,
  and `records` containing `fit_axis_id`, `transform_axis_id`, learned steps,
  selected feature count, and fit context. Resolve each axis ID through the
  registry to recover every actual ordered fitting/prediction row ID. There is
  no row cap or loss of row-level evidence. Pipeline artifacts also retain their
  actual fit IDs in the audit step. Candidate-specific evidence requires the
  exact learned-step sequence: chemical basis/model for rules, or encoder plus
  the numeric transforms/head for frozen representations. Rule basis hashes and
  frozen encoder provenance are mandatory for those candidates, also on resume.
- `selection_procedure_oof.csv`: `task_id,output_row_id,outer_fold,candidate_id,
  prediction`; one prediction per development row when complete. Classification
  adds `task_type,positive_class,threshold`; `prediction` is always `P(class=1)`,
  not a hard label. Each row's threshold belongs to **its outer model**; never
  replace it with the final-selection threshold when evaluating nested OOF.
- `selection_procedure_oof_manifest.json`: status, exact input and prediction
  file bindings, training identity, fit-index registry binding, and
  `tasks[TASK].{task_type,positive_class,outer_folds}`. Each outer-fold entry has
  `fold,threshold,model:{path,sha256},threshold_provenance,selection,training_identity_sha256,frozen_recipe_sha256`; provenance identifies
  inner CV versus a fixed contract threshold and the outer training axis. This
  prevents replacing an outer threshold/model with the final-selection model.
- `outer_models/*.joblib`: fitted outer-training pipelines. These are trusted
  local Python serialization artifacts; load only artifacts from this run.
  Pipelines expose `task_type_`, `positive_class_`, and `threshold_` for replay
  (`None` for the last two attributes in regression), plus `feature_route_`
  (`numeric` or `smiles`). SMILES replay takes 1D parent strings, not an NPZ.
  For classification, select
  the `predict_proba` column whose fitted `classes_` equals `positive_class_`.
- `search_coverage.json`: aggregate and per-task/scope/cell expected anchor/search
  counts, completed, failed and pending counts. Failed trials block selection;
  a successful neighboring cell cannot silently substitute. `attempted_trials`,
  `failed_attempts` and `attempt_seconds` include recorded superseded attempts;
  active `completed_trials`, `failed_trials`, and `incomplete_trials` describe
  current coverage only. Per-cell `pending` includes interrupted trials.
  Trial timing excludes outer refits and unrecorded work lost to hard termination;
  it is not total process wall time or a guarantee of resource-budget enforcement.
- `final_selection.json`: `schema_version,status,method,tasks,identity`.
  Each task has `candidate_id,family,algorithm,trial_id,parameters,preprocessing,
  task_type`; classification also has `positive_class,threshold`.
- `checkpoint.json`: `schema_version: "2.0"`, compact fit evidence, a
  `fit_index_registry` file binding, completed scopes, fitted artifact hashes and
  the complete input/code/environment identity. Full row axes are not duplicated
  in this file. The separate final-fit manifest still stores one complete fit-row
  list per selected final model.

The atomic checkpoint commits after each successful CV fold and each attempted
trial, as well as complete outer/final-selection scopes. Other report files are
refreshed at scope boundaries and on caught failures/interruptions; following hard
termination the checkpoint is authoritative. A live trial has `status: "incomplete"`
and contains its durable fold progress, never a completion claim. Its partial
classification fold scores use the declared fixed threshold; completing the CV
trial replaces them with scores at the inner-selected threshold. Resume
requires identical data, splits, feature files, contract, training source and
package versions, and verifies saved model hashes and the fit-index registry's
file hash, internal axis hashes/counts, frozen-fold equality and evidence
references. Before skipping a completed scope, it reconciles exact declared trial
identities, parameters, fold evidence, selected candidate, OOF row/fold membership
and required model bindings. Missing or substituted records cannot be replaced by
a completion flag or matching aggregate count. Classification additionally
recomputes pooled scores, threshold-grid scores, the deterministic selected
threshold, and per-fold metrics from saved probabilities and frozen labels, and
reconciles each OOF threshold with its own outer selection. Partial fold progress
must retain its declared trial identity, ordered fold prefix, and actual fitting
evidence. Inconsistent checkpoints reject
before rewriting persisted outputs; reconciliation also runs before completion.
Legacy checkpoint formats are rejected explicitly; they require a
new versioned search and are never silently reinterpreted. ParameterSampler
interrupted scopes restart; completed scopes are reused. Optuna scopes can reuse
only terminal trials whose exact SQLite-bound CV digest reconciles. Partial
trials/folds and anchors restart, with their evidence retained in attempt
history before active records are replaced. Failed scopes can be retried only unchanged; repairs
requiring changed code/configuration need a new versioned output directory.
Caught interruptions are `incomplete`; hard termination leaves the last durable
incomplete checkpoint. A trial failure is `failed`, never complete scientific
coverage. Persisted final selection and coverage carry the overall status.

## Locked final fitting

Only the reviewed improvement STOP permits a separate final-fit runtime stage.
The lock JSON must contain `decision: "STOP"`, `selection: {path, sha256}` bound
to the exact complete final-selection file, and `tasks: {TASK: candidate_id}`.
There is no self-asserted `reviewed` boolean. Runtime stage reviews supply that
authorization; the numerical API verifies the lock's consistency and input
identity. It rechecks that each selected trial belongs to the declared search,
then fits only those selected pipelines on all permitted development rows.

`final_fit.json` records `schema_version,status,identity,selection,lock,models,
fit_evidence`. Each task's model has `artifact:{path,sha256}`, `candidate_id`,
`family`, `feature_names`, `feature_route`, `pipeline_provenance`, and `task_type`;
SMILES feature names are `["parent_smiles"]`. Classification also records
`positive_class,threshold`. The serialized pipeline retains the exact selected
threshold/positive class attributes, not an outer-fold threshold. A persisted
`frozen_recipe_sha256_` binds candidate, parameters, seed, task, threshold and
thread limit. Each outer and final model also carries `training_identity_sha256_`,
binding the complete input/code/environment identity rather than just row names.
Evaluation checks both digests and replays bound OOF predictions without fitting.
`--resume` checks identity/lock/selection, model bytes, recipe,
provenance and ordered fit rows before reusing completed final tasks. Unfinished
tasks restart with failed/incomplete attempt evidence preserved. Any partial final fit remains failed/incomplete.
Terminal inference and terminal evaluation belong to another execution.

Fold-level fits are not reused: there is no independently sealed prediction/model
artifact for each partial fold. Restart is deliberate, not invented reuse.
Only completed scopes, SQLite-reconciled terminal Optuna trials, and verified
completed final tasks are reusable. All synthetic checks establish execution,
leakage boundaries and determinism only, never scientific predictive performance.
