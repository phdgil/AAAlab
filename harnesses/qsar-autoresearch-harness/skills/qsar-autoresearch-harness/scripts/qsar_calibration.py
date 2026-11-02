"""Binary, training-partition-only cross-fitted calibration and optional SMOTE.

The caller supplies only its current training partition. Every calibration fold
clones and fits the entire unfitted pipeline, including its first ``audit``
step. Calibration learns from held-out fold scores, never in-sample scores.
Explicit splits are positional ``(train_indices, validation_indices)`` pairs;
otherwise seeded StratifiedGroupKFold constructs them from the supplied groups.
"""
from __future__ import annotations

from numbers import Integral, Real
import warnings

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone, is_classifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.exceptions import ConvergenceWarning, NotFittedError
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.svm import NuSVC, SVC
from sklearn.utils import _safe_indexing, get_tags
from sklearn.utils.validation import check_is_fitted


class CalibrationError(ValueError):
    """Invalid binary calibration, partition, score or provenance contract."""


class CalibrationDependencyError(ImportError):
    """An explicitly requested sampler has no working implementation installed."""


def _integer(value, name, minimum, maximum=None):
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral)
            or value < minimum or (maximum is not None and value > maximum)):
        raise CalibrationError(name + f" must be an integer >= {minimum}"
                               + (f" and <= {maximum}" if maximum is not None else ""))
    return int(value)


def _size(X):
    try:
        size = X.shape[0] if hasattr(X, "shape") else len(X)
    except (TypeError, IndexError) as error:
        raise CalibrationError("X must be a nonempty row-indexable input") from error
    if not isinstance(size, Integral) or size < 1:
        raise CalibrationError("X must be a nonempty row-indexable input")
    return int(size)


def _identities(values, size, name, unique=False):
    array = np.asarray(values, dtype=object)
    if array.ndim != 1 or len(array) != size:
        raise CalibrationError(name + " must be one-dimensional and match the input rows")
    result = []
    for value in array:
        if isinstance(value, np.generic):
            value = value.item()
        if (isinstance(value, (bool, np.bool_))
                or not isinstance(value, (str, Real))
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, Real) and not np.isfinite(value))):
            raise CalibrationError(name + " requires nonempty strings or finite numeric identities")
        result.append(value)
    if unique and len(set(result)) != size:
        raise CalibrationError(name + " must contain distinct identities")
    return result


def _labels(y, size, context):
    values = np.asarray(y)
    if (values.ndim != 1 or len(values) != size or values.dtype.kind not in "iuf"
            or not np.isfinite(values).all() or not np.array_equal(np.unique(values), [0, 1])):
        raise CalibrationError(context + " requires both finite numeric classes 0 and 1")
    return values.astype(np.int64)


def _components(pipeline):
    return [("pipeline", pipeline)] + [(name, value) for name, value in pipeline.get_params(deep=True).items()
                                       if isinstance(value, BaseEstimator)]


def _validate_pipeline(pipeline, calibration=True):
    if not isinstance(pipeline, Pipeline) or len(pipeline.steps) < 2:
        raise CalibrationError("base_estimator must be a full unfitted sklearn-compatible Pipeline")
    if pipeline.steps[0][0] != "audit" or pipeline.steps[0][1] in (None, "passthrough"):
        raise CalibrationError("The first pipeline step must be audit, before preprocessing or resampling")
    if pipeline.memory is not None or getattr(pipeline, "transform_input", None) is not None:
        raise CalibrationError("Independent calibration fits require memory=None and no transformed metadata")
    if not is_classifier(pipeline):
        raise CalibrationError("The pipeline final estimator must be a binary classifier")
    for name, component in _components(pipeline):
        if get_tags(component).requires_fit:
            try:
                check_is_fitted(component)
            except NotFittedError:
                pass
            else:
                raise CalibrationError("base_estimator and all its steps must be unfitted: " + name)
        if calibration and isinstance(component, (SVC, NuSVC)) and component.probability:
            raise CalibrationError("Cross-fitted SVC calibration requires probability=False; native calibration is forbidden")
        if calibration and isinstance(component, CalibratedClassifierCV):
            raise CalibrationError("An already calibrated estimator cannot be nested inside cross-fitted calibration")
    samplers = [(index, step) for index, (_, step) in enumerate(pipeline.steps[:-1])
                if callable(getattr(step, "fit_resample", None))]
    if samplers:
        try:
            from imblearn.over_sampling import SMOTE
            from imblearn.pipeline import Pipeline as SamplingPipeline
        except ImportError as error:
            raise CalibrationDependencyError("SMOTE requires a working imbalanced-learn installation; no fallback is used") from error
        if (not isinstance(pipeline, SamplingPipeline) or len(samplers) != 1
                or samplers[0][0] != len(pipeline.steps) - 2 or not isinstance(samplers[0][1], SMOTE)):
            raise CalibrationError("Only one real SMOTE step immediately before the classifier is supported")
        _numeric_pipeline(pipeline)


def _numeric_pipeline(pipeline):
    if any("smiles_column" in component.get_params(deep=False) for _, component in _components(pipeline)):
        raise CalibrationError("SMOTE requires an explicitly numeric representation, not a raw SMILES/rule candidate")


def _indices(values, size):
    array = np.asarray(values)
    if (array.ndim != 1 or not len(array) or array.dtype.kind not in "iu"
            or np.any(array < 0) or np.any(array >= size) or len(np.unique(array)) != len(array)):
        raise CalibrationError("Each split requires distinct, in-range integer row indices")
    return array.astype(np.intp)


def _partition(splits, y, groups, n_splits, random_state):
    size = len(y)
    group_codes = {group: index for index, group in enumerate(dict.fromkeys(groups))}
    codes = np.asarray([group_codes[group] for group in groups])
    if len(group_codes) < n_splits:
        raise CalibrationError("Calibration fold count exceeds the number of current training groups")
    if any(len(set(codes[y == label])) < n_splits for label in (0, 1)):
        raise CalibrationError("Each class must occur in at least n_splits distinct training groups")
    if splits is None:
        splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
        splits = list(splitter.split(np.zeros((size, 1)), y, codes))
    if not isinstance(splits, (list, tuple)) or len(splits) != n_splits:
        raise CalibrationError("Explicit splits must contain exactly n_splits train/validation pairs")
    result, coverage = [], np.zeros(size, dtype=np.int64)
    for fold in splits:
        if not isinstance(fold, (list, tuple)) or len(fold) != 2:
            raise CalibrationError("Each calibration split must be a train/validation pair")
        train, validation = (_indices(part, size) for part in fold)
        if (np.intersect1d(train, validation).size
                or not np.array_equal(np.sort(np.concatenate((train, validation))), np.arange(size))):
            raise CalibrationError("Each split must partition all current training rows without overlap")
        if np.intersect1d(codes[train], codes[validation]).size:
            raise CalibrationError("A group overlaps calibration training and validation rows")
        _labels(y[train], len(train), "Calibration training fold")
        _labels(y[validation], len(validation), "Calibration validation fold")
        coverage[validation] += 1
        result.append((train.copy(), validation.copy()))
    if not np.all(coverage == 1):
        raise CalibrationError("Calibration validation folds must cover each current training row exactly once")
    return tuple(result)


def _classes(estimator):
    classes = np.asarray(getattr(estimator, "classes_", None))
    if (classes.shape != (2,) or classes.dtype.kind not in "iuf"
            or not np.array_equal(np.sort(classes), [0, 1])):
        raise CalibrationError("Every fitted classifier must expose exactly classes 0 and 1")
    return classes


def _positive_probability(estimator, X):
    classes = _classes(estimator)
    values = np.asarray(estimator.predict_proba(X), dtype=float)
    if (values.shape != (_size(X), 2) or not np.isfinite(values).all()
            or np.any((values < 0) | (values > 1))
            or not np.allclose(values.sum(axis=1), 1, rtol=1e-7, atol=1e-7)):
        raise CalibrationError("predict_proba must return finite, normalized binary probabilities")
    return values[:, np.flatnonzero(classes == 1)[0]]


def _raw_scores(estimator, X, method):
    classes = _classes(estimator)
    if not callable(getattr(estimator, method, None)):
        raise CalibrationError("The fitted pipeline no longer exposes the selected score method: " + method)
    if method == "predict_proba":
        return _positive_probability(estimator, X)
    scores = np.asarray(estimator.decision_function(X), dtype=float)
    if scores.shape != (_size(X),) or not np.isfinite(scores).all():
        raise CalibrationError("Binary decision_function must return one finite score per row")
    # sklearn's binary decision score is oriented toward classes_[1], not
    # necessarily toward label 1. Do not silently change method between fits.
    return scores if classes[1] == 1 else -scores


def _sampler_audit(pipeline, row_ids):
    result = []
    for name, step in pipeline.steps[:-1]:
        if callable(getattr(step, "fit_resample", None)):
            if not hasattr(step, "sampling_strategy_"):
                raise CalibrationError("SMOTE did not expose fitted sampling evidence")
            result.append({"step": name, "class": type(step).__module__ + "." + type(step).__qualname__,
                           "original_fit_row_ids": list(row_ids),
                           "row_identity_scope": "original_rows_before_resampling",
                           "synthetic_row_ids": None, "synthetic_parent_row_ids": None,
                           "provenance_limit": "SMOTE does not expose individual synthetic-row parent identities",
                           "synthetic_counts": [{"class": int(label), "count": int(count)}
                                                for label, count in step.sampling_strategy_.items()]})
    return result


def _preprocessing_audit(pipeline):
    """Keep inspectable fit statistics, not duplicate fitted fold estimators."""
    result = {}
    for name, step in pipeline.steps[:-1]:
        attributes = {}
        for attribute in ("statistics_", "variances_", "mean_", "scale_", "scores_"):
            value = getattr(step, attribute, None)
            if value is not None:
                array = np.asarray(value)
                attributes[attribute] = np.where(np.isfinite(array), array, None).tolist()
        if callable(getattr(step, "get_support", None)):
            attributes["selected_indices"] = step.get_support(indices=True).tolist()
        if attributes:
            result[name] = attributes
    return result


def _fit_pipeline(base, X, y, row_ids, before_fit=None, context=None):
    if before_fit is not None:
        before_fit({} if context is None else context)
    fitted = clone(base)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        fitted.fit(X, y, audit__row_ids=list(row_ids))
    audit_ids = getattr(fitted.named_steps["audit"], "fit_row_ids_", None)
    if audit_ids is None or list(audit_ids) != list(row_ids):
        raise CalibrationError("The fitted pipeline audit did not preserve the exact ordered training row IDs")
    return fitted, {"audit_fit_row_ids": list(audit_ids), "samplers": _sampler_audit(fitted, row_ids),
                    "independent_pipeline_clone": True, "preprocessing": _preprocessing_audit(fitted)}


def _calibrated_positive(calibrator, scores, method):
    if method == "sigmoid":
        return _positive_probability(calibrator, scores.reshape(-1, 1))
    probabilities = np.asarray(calibrator.predict(scores), dtype=float)
    if (probabilities.shape != scores.shape or not np.isfinite(probabilities).all()
            or np.any((probabilities < 0) | (probabilities > 1))):
        raise CalibrationError("Isotonic calibration returned invalid probabilities")
    return probabilities


class CrossFittedCalibratedClassifier(ClassifierMixin, BaseEstimator):
    """Calibrate a full unfitted audited pipeline using grouped OOF scores.

    ``method`` is ``sigmoid`` (LogisticRegression) or ``isotonic``. ``n_splits``
    is at least two; ``random_state`` is an explicit nonnegative integer seed.
    A binary scalar decision_function is preferred; otherwise class-1
    predict_proba is used. Invalid scores never trigger a different method.
    Native SVC probability calibration and nested calibrators are rejected.

    Fit accepts only current-partition X/y, distinct row_ids and aligned groups.
    Fold pipelines are discarded immediately after scores and fit statistics
    are captured. ``calibration_audit_`` binds their clone identities, row/group
    memberships and the original rows used by the calibrator and final base.
    No synthetic identities are invented when a real SMOTE step is present.
    """

    def __init__(self, base_estimator, *, method="sigmoid", n_splits=5, random_state=0, splits=None):
        self.base_estimator = base_estimator
        self.method = method
        self.n_splits = n_splits
        self.random_state = random_state
        self.splits = splits

    @property
    def named_steps(self):
        check_is_fitted(self, "base_estimator_")
        return self.base_estimator_.named_steps

    def fit(self, X, y, *, row_ids, groups, before_fit=None):
        # A failed refit must not leave a previous fitted model available.
        for name in tuple(vars(self)):
            if name.endswith("_") and not name.startswith("__"):
                delattr(self, name)
        if self.method not in ("sigmoid", "isotonic"):
            raise CalibrationError("Calibration method must be sigmoid or isotonic")
        count = _integer(self.n_splits, "n_splits", 2)
        seed = _integer(self.random_state, "random_state", 0, 2**32 - 1)
        size = _size(X)
        labels = _labels(y, size, "Calibration fitting")
        ids = _identities(row_ids, size, "row_ids", unique=True)
        group_ids = _identities(groups, size, "groups")
        _validate_pipeline(self.base_estimator)
        score_method = next((name for name in ("decision_function", "predict_proba")
                             if callable(getattr(self.base_estimator, name, None))), None)
        if score_method is None:
            raise CalibrationError("The base pipeline requires decision_function or predict_proba")
        partitions = _partition(self.splits, labels, group_ids, count, seed)
        scores, fold_indices = np.full(size, np.nan), np.full(size, -1, dtype=np.int64)
        evidence = []
        self.calibration_audit_ = {"method": self.method, "score_method": score_method,
                                   "n_splits": count, "random_state": seed, "folds": evidence,
                                   "fold_models_retained": False}
        for index, (train, validation) in enumerate(partitions):
            train_ids = [ids[i] for i in train]
            fitted, audit = _fit_pipeline(self.base_estimator, _safe_indexing(X, train), labels[train], train_ids,
                                          before_fit, {"phase": "calibration-fold", "calibration_fold": index})
            scores[validation] = _raw_scores(fitted, _safe_indexing(X, validation), score_method)
            fold_indices[validation] = index
            evidence.append({"fold": index, "train_row_ids": train_ids,
                             "clone_id": "calibration-fold-" + str(index),
                             "validation_row_ids": [ids[i] for i in validation],
                             "train_groups": list(dict.fromkeys(group_ids[i] for i in train)),
                             "validation_groups": list(dict.fromkeys(group_ids[i] for i in validation)), **audit})
            del fitted
        if not np.isfinite(scores).all() or np.any(fold_indices < 0):
            raise CalibrationError("Every calibration row must have one finite out-of-fold score")
        calibrator = (LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000, random_state=seed)
                      if self.method == "sigmoid" else
                      IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip"))
        if before_fit is not None:
            before_fit({"phase": "calibrator-fit"})
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            calibrator.fit(scores.reshape(-1, 1) if self.method == "sigmoid" else scores, labels)
        calibrator.fit_row_ids_ = list(ids)
        calibrator.fit_groups_ = list(group_ids)
        _calibrated_positive(calibrator, scores, self.method)
        self.calibration_audit_.update(calibrator_row_ids=list(ids), calibrator_groups=list(group_ids))
        final, final_audit = _fit_pipeline(self.base_estimator, X, labels, ids, before_fit,
                                          {"phase": "calibration-base-refit"})
        _raw_scores(final, X, score_method)
        self.base_estimator_ = final
        self.calibrator_ = calibrator
        self.splits_ = partitions
        self.oof_scores_ = scores
        self.oof_fold_indices_ = fold_indices
        self.fit_row_ids_ = list(ids)
        self.fit_groups_ = list(group_ids)
        self.calibrator_row_ids_ = list(ids)
        self.score_method_ = score_method
        self.calibration_method_ = self.method
        self.classes_ = np.asarray([0, 1], dtype=np.int64)
        self.task_type_ = "classification"
        self.positive_class_ = 1
        self.threshold_ = 0.5
        self.calibration_audit_ = {"method": self.method, "score_method": score_method,
                                   "n_splits": count, "random_state": seed, "folds": evidence,
                                   "fold_models_retained": False, "base_clone_id": "calibration-base-refit",
                                   "calibrator_row_ids": list(ids), "calibrator_groups": list(group_ids),
                                   "base_fit_row_ids": list(ids), "base_fit": final_audit}
        return self

    def predict_proba(self, X):
        check_is_fitted(self, ["base_estimator_", "calibrator_", "score_method_", "calibration_method_"])
        scores = _raw_scores(self.base_estimator_, X, self.score_method_)
        positive = _calibrated_positive(self.calibrator_, scores, self.calibration_method_)
        return np.column_stack((1.0 - positive, positive))

    def predict(self, X):
        check_is_fitted(self, ["base_estimator_", "calibrator_", "threshold_"])
        threshold = self.threshold_
        if (isinstance(threshold, (bool, np.bool_)) or not isinstance(threshold, Real)
                or not np.isfinite(threshold) or not 0 <= threshold <= 1):
            raise CalibrationError("threshold_ must be a finite number in [0, 1]")
        return (self.predict_proba(X)[:, 1] >= threshold).astype(np.int64)


def with_smote(pipeline, *, representation, random_state=0, k_neighbors=5, sampling_strategy="auto"):
    """Clone a numeric audited pipeline and insert real fit-only SMOTE.

    The caller must explicitly declare ``representation='numeric'``; raw
    SMILES/rule adapters are not supported. Audit stays first and SMOTE is
    inserted after all preprocessing, immediately before the classifier.
    Predict bypasses resampling through imblearn.pipeline.Pipeline semantics.
    A missing or broken imbalanced-learn installation is an operational error.
    """
    if representation != "numeric":
        raise CalibrationError("SMOTE requires representation='numeric'; raw SMILES/rule candidates are unsupported")
    _validate_pipeline(pipeline, calibration=False)
    _numeric_pipeline(pipeline)
    if any(callable(getattr(step, "fit_resample", None)) or name == "smote" for name, step in pipeline.steps):
        raise CalibrationError("The pipeline already contains a sampler or reserved smote step")
    seed = _integer(random_state, "random_state", 0, 2**32 - 1)
    neighbors = _integer(k_neighbors, "k_neighbors", 1)
    try:
        from imblearn.over_sampling import SMOTE
        from imblearn.pipeline import Pipeline as SamplingPipeline
    except ImportError as error:
        raise CalibrationDependencyError("SMOTE requires a working imbalanced-learn installation; no fallback is used") from error
    cloned = clone(pipeline)
    steps = list(cloned.steps)
    steps.insert(len(steps) - 1, ("smote", SMOTE(random_state=seed, k_neighbors=neighbors,
                                              sampling_strategy=sampling_strategy)))
    return SamplingPipeline(steps, memory=None, verbose=pipeline.verbose)
