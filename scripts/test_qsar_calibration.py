"""Synthetic calibration leakage/replay checks; no scientific data or downloads."""
import importlib.util
import io
import json
from pathlib import Path
import pickle
import sys
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone, is_classifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.exceptions import NotFittedError
from sklearn.feature_selection import SelectKBest, VarianceThreshold, f_classif
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import NuSVC, SVC

SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_calibration as calibration
from qsar_train import FitAudit


class RecordingAudit(FitAudit):
    fits = []

    def fit(self, X, y=None, row_ids=None):
        super().fit(X, y, row_ids=row_ids)
        type(self).fits.append(list(self.fit_row_ids_))
        return self


class DecisionProbe(ClassifierMixin, BaseEstimator):
    """Exercise score orientation and invalid outputs, not calibration quality."""

    def __init__(self, reverse=False, invalid=None):
        self.reverse = reverse
        self.invalid = invalid

    def fit(self, X, y):
        self.classes_ = np.array([1, 0] if self.reverse else [0, 1])
        self.fit_size_ = len(X)
        if self.invalid == "classes":
            self.classes_ = np.array([0])
        return self

    def decision_function(self, X):
        scores = np.asarray(X, dtype=float)[:, 0].copy()
        if self.reverse:
            scores *= -1
        if self.invalid == "nan" or (self.invalid == "final_nan" and self.fit_size_ == 24):
            scores[0] = np.nan
        elif self.invalid == "inf":
            scores[0] = np.inf
        elif self.invalid == "shape":
            return np.column_stack((-scores, scores))
        return scores

    def predict_proba(self, X):
        raise AssertionError("An invalid decision_function must never fall back to probabilities")


class ProbabilityProbe(ClassifierMixin, BaseEstimator):
    def __init__(self, reverse=False, invalid=None):
        self.reverse = reverse
        self.invalid = invalid

    def fit(self, X, y):
        self.classes_ = np.array([1, 0] if self.reverse else [0, 1])
        return self

    def predict_proba(self, X):
        positive = 1 / (1 + np.exp(-np.asarray(X, dtype=float)[:, 0]))
        values = np.column_stack((1 - positive, positive))
        if self.reverse:
            values = values[:, ::-1]
        if self.invalid == "nan":
            values[0, 0] = np.nan
        elif self.invalid == "range":
            values[0] = [-0.1, 1.1]
        elif self.invalid == "sum":
            values[0] = [0.2, 0.3]
        elif self.invalid == "shape":
            return values[:, 0]
        return values


class PredictOnlyProbe(ClassifierMixin, BaseEstimator):
    def fit(self, X, y):
        self.classes_ = np.array([0, 1])
        return self

    def predict(self, X):
        return np.zeros(len(X), dtype=int)


def grouped_splits(groups, count=3):
    groups = np.asarray(groups)
    result = []
    for held_out in np.array_split(np.unique(groups), count):
        mask = np.isin(groups, held_out)
        result.append((np.flatnonzero(~mask), np.flatnonzero(mask)))
    return result


def pipeline(model=None):
    return Pipeline([("audit", RecordingAudit()),
                     ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("variance", VarianceThreshold()), ("scale", StandardScaler()),
                     ("select", SelectKBest(f_classif, k=2)),
                     ("model", LogisticRegression(C=0.7, max_iter=2000, random_state=11) if model is None else model)])


def probe_pipeline(model):
    return Pipeline([("audit", RecordingAudit()), ("impute", SimpleImputer(strategy="median")), ("model", model)])


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(19)
        self.X = rng.normal(size=(24, 4))
        self.y = np.tile([0, 1], 12)
        self.X[:, 0] += self.y * 1.4
        self.X[:, 1] += np.repeat(np.arange(12), 2) * 3
        self.X[::5, 0] = np.nan
        self.X[1::7, 2] = np.nan
        self.ids = [f"training-{i:02d}" for i in range(len(self.y))]
        self.groups = [f"group-{i // 2:02d}" for i in range(len(self.y))]
        self.splits = grouped_splits(self.groups)
        self.base = pipeline()
        RecordingAudit.fits.clear()

    def wrapper(self, base=None, **parameters):
        return calibration.CrossFittedCalibratedClassifier(
            self.base if base is None else base,
            **{"method": "sigmoid", "n_splits": 3, "random_state": 17, "splits": self.splits, **parameters})

    def fit(self, model):
        return model.fit(self.X, self.y, row_ids=self.ids, groups=self.groups)

    def test_clone_and_classifier_contract(self):
        original = self.wrapper()
        copied = clone(original)
        self.assertTrue(is_classifier(copied))
        self.assertIsNot(copied.base_estimator, self.base)
        self.assertEqual(copied.method, original.method)
        self.assertEqual(copied.n_splits, original.n_splits)
        self.assertEqual(copied.random_state, original.random_state)
        copied.set_params(base_estimator__model__C=0.2, method="isotonic")
        self.assertEqual(copied.base_estimator.named_steps["model"].C, 0.2)
        self.assertEqual(self.base.named_steps["model"].C, 0.7)
        self.assertFalse(hasattr(copied, "calibration_method_"))
        for name in ("predict_proba", "predict"):
            with self.subTest(name=name), self.assertRaises(NotFittedError):
                getattr(copied, name)(self.X)
        with self.assertRaises(TypeError):
            original.fit(self.X, self.y)
        with self.assertRaises(TypeError):
            original.fit(self.X, self.y, row_ids=self.ids, groups=self.groups, validation_X=self.X)

    def test_each_fold_fits_the_full_pipeline_only_on_its_training_rows(self):
        model = self.fit(self.wrapper())
        expected_fits = [[self.ids[i] for i in train] for train, _ in self.splits] + [self.ids]
        self.assertEqual(RecordingAudit.fits, expected_fits)
        self.assertFalse(hasattr(self.base.named_steps["audit"], "fit_row_ids_"))
        self.assertFalse(hasattr(self.base.named_steps["impute"], "statistics_"))
        self.assertFalse(hasattr(self.base.named_steps["select"], "scores_"))
        self.assertFalse(hasattr(model, "calibration_estimators_"))
        self.assertFalse(model.calibration_audit_["fold_models_retained"])
        self.assertEqual(model.named_steps, model.base_estimator_.named_steps)
        coverage = np.zeros(len(self.ids), dtype=int)
        for index, ((train, validation), audit) in enumerate(zip(self.splits, model.calibration_audit_["folds"])):
            with self.subTest(fold=index):
                train_ids = [self.ids[i] for i in train]
                validation_ids = [self.ids[i] for i in validation]
                fitted = clone(self.base).fit(self.X[train], self.y[train], audit__row_ids=train_ids)
                self.assertEqual(fitted.named_steps["audit"].fit_row_ids_, train_ids)
                self.assertTrue(audit["independent_pipeline_clone"])
                self.assertEqual(audit["clone_id"], "calibration-fold-" + str(index))
                self.assertEqual(audit["audit_fit_row_ids"], train_ids)
                self.assertEqual(audit["train_row_ids"], train_ids)
                self.assertEqual(audit["validation_row_ids"], validation_ids)
                self.assertFalse(set(audit["train_groups"]) & set(audit["validation_groups"]))
                self.assertFalse(set(train_ids) & set(validation_ids))
                np.testing.assert_allclose(fitted.named_steps["impute"].statistics_, np.nanmedian(self.X[train], axis=0))
                np.testing.assert_allclose(audit["preprocessing"]["impute"]["statistics_"], np.nanmedian(self.X[train], axis=0))
                imputed = fitted.named_steps["impute"].transform(self.X[train])
                varied = fitted.named_steps["variance"].transform(imputed)
                np.testing.assert_allclose(fitted.named_steps["scale"].mean_, varied.mean(axis=0))
                np.testing.assert_allclose(audit["preprocessing"]["scale"]["mean_"], varied.mean(axis=0))
                scaled = fitted.named_steps["scale"].transform(varied)
                expected_scores = f_classif(scaled, self.y[train])[0]
                np.testing.assert_allclose(fitted.named_steps["select"].scores_, expected_scores)
                np.testing.assert_allclose(audit["preprocessing"]["select"]["scores_"], expected_scores)
                np.testing.assert_array_equal(fitted.named_steps["select"].get_support(indices=True),
                                              np.sort(np.argsort(expected_scores, kind="mergesort")[-2:]))
                np.testing.assert_array_equal(model.oof_scores_[validation], fitted.decision_function(self.X[validation]))
                np.testing.assert_array_equal(model.oof_fold_indices_[validation], np.full(len(validation), index))
                coverage[validation] += 1
        np.testing.assert_array_equal(coverage, np.ones(len(self.ids)))
        self.assertEqual(model.base_estimator_.named_steps["audit"].fit_row_ids_, self.ids)
        self.assertEqual(model.calibrator_.fit_row_ids_, self.ids)
        self.assertEqual(model.calibrator_.fit_groups_, self.groups)
        self.assertEqual(model.calibration_audit_["calibrator_row_ids"], self.ids)
        self.assertEqual(model.calibration_audit_["base_fit_row_ids"], self.ids)
        self.assertEqual(model.calibration_audit_["base_fit"]["audit_fit_row_ids"], self.ids)
        self.assertEqual(model.score_method_, "decision_function")
        json.dumps(model.calibration_audit_, allow_nan=False)
        self.assertFalse(np.allclose(model.oof_scores_, model.base_estimator_.decision_function(self.X)))

    def test_calibrators_learn_exactly_from_oof_scores_and_replay_final_base(self):
        for method in ("sigmoid", "isotonic"):
            with self.subTest(method=method):
                model = self.fit(self.wrapper(method=method))
                reference = clone(model.calibrator_)
                scores = model.oof_scores_.reshape(-1, 1) if method == "sigmoid" else model.oof_scores_
                reference.fit(scores, self.y)
                raw_final = model.base_estimator_.decision_function(self.X)
                if method == "sigmoid":
                    self.assertIsInstance(model.calibrator_, LogisticRegression)
                    np.testing.assert_array_equal(model.calibrator_.coef_, reference.coef_)
                    np.testing.assert_array_equal(model.calibrator_.intercept_, reference.intercept_)
                    expected = reference.predict_proba(raw_final.reshape(-1, 1))[:, 1]
                else:
                    self.assertIsInstance(model.calibrator_, IsotonicRegression)
                    np.testing.assert_array_equal(model.calibrator_.X_thresholds_, reference.X_thresholds_)
                    np.testing.assert_array_equal(model.calibrator_.y_thresholds_, reference.y_thresholds_)
                    expected = reference.predict(raw_final)
                    self.assertTrue(np.isfinite(model.predict_proba(np.full((2, 4), 1e6))).all())
                probabilities = model.predict_proba(self.X)
                np.testing.assert_array_equal(model.classes_, [0, 1])
                np.testing.assert_array_equal(probabilities[:, 1], expected)
                np.testing.assert_allclose(probabilities.sum(axis=1), 1)
                self.assertEqual((model.task_type_, model.positive_class_), ("classification", 1))
                model.threshold_ = 0.7
                np.testing.assert_array_equal(model.predict(self.X), (expected >= 0.7).astype(int))

    def test_auto_splits_are_seeded_grouped_complete_and_repeatable(self):
        first = self.fit(self.wrapper(splits=None))
        second = self.fit(clone(first))
        for left, right in zip(first.splits_, second.splits_):
            np.testing.assert_array_equal(left[0], right[0])
            np.testing.assert_array_equal(left[1], right[1])
            self.assertFalse({self.groups[i] for i in left[0]} & {self.groups[i] for i in left[1]})
        np.testing.assert_array_equal(first.oof_scores_, second.oof_scores_)
        np.testing.assert_array_equal(first.predict_proba(self.X), second.predict_proba(self.X))
        np.testing.assert_array_equal(np.sort(np.concatenate([v for _, v in first.splits_])), np.arange(24))

    def test_both_score_paths_respect_reversed_class_order(self):
        for estimator, method in ((DecisionProbe, "decision_function"), (ProbabilityProbe, "predict_proba")):
            with self.subTest(method=method):
                forward = self.fit(self.wrapper(base=probe_pipeline(estimator())))
                reverse = self.fit(self.wrapper(base=probe_pipeline(estimator(reverse=True))))
                self.assertEqual(forward.score_method_, method)
                self.assertEqual(reverse.score_method_, method)
                np.testing.assert_array_equal(reverse.base_estimator_.classes_, [1, 0])
                np.testing.assert_array_equal(forward.oof_scores_, reverse.oof_scores_)
                np.testing.assert_array_equal(forward.predict_proba(self.X), reverse.predict_proba(self.X))
                np.testing.assert_array_equal(reverse.classes_, [0, 1])

    def test_bad_scores_and_missing_fitted_classes_never_fall_back(self):
        cases = [(DecisionProbe, value) for value in ("nan", "inf", "shape", "classes", "final_nan")]
        cases += [(ProbabilityProbe, value) for value in ("nan", "range", "sum", "shape")]
        for estimator, invalid in cases:
            with self.subTest(estimator=estimator.__name__, invalid=invalid):
                model = self.wrapper(base=probe_pipeline(estimator(invalid=invalid)))
                with self.assertRaises(calibration.CalibrationError):
                    self.fit(model)
                with self.assertRaises(NotFittedError):
                    model.predict_proba(self.X)
        model = self.wrapper(base=probe_pipeline(PredictOnlyProbe()))
        with self.assertRaisesRegex(calibration.CalibrationError, "requires decision_function or predict_proba"):
            self.fit(model)

    def test_explicit_splits_reject_overlap_nonpartition_and_group_leakage_before_fit(self):
        def copied():
            return [(t.copy(), v.copy()) for t, v in self.splits]
        cases = []
        bad = copied()
        bad[0] = (np.append(bad[0][0], bad[0][1][0]), bad[0][1])
        cases.append(bad)
        bad = copied()
        bad[0] = (bad[0][0][1:], bad[0][1])
        cases.append(bad)
        bad = copied()
        bad[0] = (np.append(bad[0][0], bad[0][0][0]), bad[0][1])
        cases.append(bad)
        for index in (-1, len(self.ids)):
            bad = copied()
            bad[0][0][0] = index
            cases.append(bad)
        bad = copied()
        bad[0] = (bad[0][0].astype(float), bad[0][1])
        cases.append(bad)
        bad = copied()
        bad[0] = (np.zeros(24, dtype=bool), bad[0][1])
        cases.append(bad)
        bad = copied()
        bad[1] = bad[0]
        cases.append(bad)
        bad = copied()
        train, validation = bad[0]
        train[0], validation[0] = validation[0], train[0]
        cases.append(bad)
        cases.extend([self.splits[:2], [None, *self.splits[1:]], [(t, v, v) for t, v in self.splits]])
        for index, splits in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(calibration.CalibrationError):
                self.fit(self.wrapper(splits=splits))
            self.assertEqual(RecordingAudit.fits, [])

    def test_bad_identities_targets_class_feasibility_and_parameters_are_rejected_before_fit(self):
        for name, value in (("row_ids", self.ids[:-1]), ("row_ids", [self.ids[0]] * 24),
                            ("row_ids", [None, *self.ids[1:]]), ("row_ids", [" ", *self.ids[1:]]),
                            ("row_ids", [float("inf"), *self.ids[1:]]),
                            ("groups", self.groups[:-1]), ("groups", [None, *self.groups[1:]]),
                            ("groups", [float("nan"), *self.groups[1:]]),
                            ("groups", [["nested"], *self.groups[1:]]), ("groups", ["one"] * 24)):
            arguments = {"row_ids": self.ids, "groups": self.groups, name: value}
            with self.subTest(name=name, value=value), self.assertRaises(calibration.CalibrationError):
                self.wrapper().fit(self.X, self.y, **arguments)
        for labels in (np.zeros(24), np.ones(24), self.y.astype(bool), self.y.astype(str),
                       np.array([np.nan, *self.y[1:]]), np.tile([0, 2], 12), self.y[:-1]):
            with self.subTest(labels=str(labels)), self.assertRaises(calibration.CalibrationError):
                self.wrapper().fit(self.X, labels, row_ids=self.ids, groups=self.groups)
        for name, values in (("method", (None, "none", "platt")), ("n_splits", (1, True, 2.0, 13)),
                             ("random_state", (None, True, -1, 2**32, 0.0))):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(calibration.CalibrationError):
                    self.fit(self.wrapper(**{name: value}))
        for train_missing in (False, True):
            labels = self.y.copy()
            if train_missing:
                labels[:] = 0
                labels[self.splits[0][1]] = 1
            else:
                labels[self.splits[0][1]] = 0
            with self.subTest(train_missing=train_missing), self.assertRaises(calibration.CalibrationError):
                self.wrapper().fit(self.X, labels, row_ids=self.ids, groups=self.groups)
        self.assertEqual(RecordingAudit.fits, [])

    def test_unfitted_full_pipeline_and_no_native_double_calibration_are_required(self):
        cases = [LogisticRegression(), Pipeline([("model", LogisticRegression())]),
                 Pipeline([("impute", SimpleImputer()), ("audit", RecordingAudit()), ("model", LogisticRegression())]),
                 pipeline(Ridge()), pipeline(SVC(probability=True)), pipeline(NuSVC(probability=True)),
                 pipeline(CalibratedClassifierCV(LogisticRegression()))]
        cached = pipeline()
        cached.memory = "calibration-cache-must-not-be-created"
        cases.append(cached)
        partial = pipeline()
        partial.named_steps["impute"].fit(self.X)
        cases.append(partial)
        fitted = pipeline().fit(self.X, self.y, audit__row_ids=self.ids)
        cases.append(fitted)
        RecordingAudit.fits.clear()
        for index, base in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(calibration.CalibrationError):
                self.fit(self.wrapper(base=base))
        self.assertEqual(RecordingAudit.fits, [])
        model = self.fit(self.wrapper(base=pipeline(SVC(probability=False, random_state=4))))
        self.assertEqual(model.score_method_, "decision_function")
        self.assertFalse(model.base_estimator_.named_steps["model"].probability)
        self.assertTrue(np.isfinite(model.predict_proba(self.X)).all())

    def test_prediction_reload_and_terminal_extremes_never_fit_or_mutate_evidence(self):
        for method in ("sigmoid", "isotonic"):
            model = self.fit(self.wrapper(method=method))
            terminal = np.array([[1e5, -1e5, np.nan, 2.0], [-1e5, 1e5, 2.0, np.nan]])
            expected = model.predict_proba(terminal)
            payload = io.BytesIO()
            joblib.dump(model, payload)
            payload.seek(0)
            for restored in (pickle.loads(pickle.dumps(model)), joblib.load(payload)):
                before = pickle.dumps(restored)
                before_fits = list(RecordingAudit.fits)
                with patch.object(RecordingAudit, "fit", side_effect=AssertionError("Replay attempted audit fitting")), \
                        patch.object(LogisticRegression, "fit", side_effect=AssertionError("Replay attempted logistic fitting")), \
                        patch.object(IsotonicRegression, "fit", side_effect=AssertionError("Replay attempted isotonic fitting")):
                    np.testing.assert_array_equal(restored.predict_proba(terminal), expected)
                    np.testing.assert_array_equal(restored.predict(terminal), (expected[:, 1] >= 0.5).astype(int))
                self.assertEqual(pickle.dumps(restored), before)
                self.assertEqual(RecordingAudit.fits, before_fits)
                self.assertEqual(restored.fit_row_ids_, self.ids)
                self.assertEqual(restored.calibrator_.fit_row_ids_, self.ids)
                self.assertEqual(restored.calibration_audit_, model.calibration_audit_)

    def test_failed_refit_cannot_reuse_previous_fit_and_threshold_is_strict(self):
        model = self.fit(self.wrapper())
        for threshold in (True, -0.01, 1.01, np.nan, "0.5"):
            model.threshold_ = threshold
            with self.subTest(threshold=threshold), self.assertRaises(calibration.CalibrationError):
                model.predict(self.X)
        model.set_params(method="invalid")
        with self.assertRaises(calibration.CalibrationError):
            self.fit(model)
        with self.assertRaises(NotFittedError):
            model.predict_proba(self.X)

    def test_smote_missing_dependency_fails_and_never_changes_the_original_pipeline(self):
        modules = {"imblearn": None, "imblearn.pipeline": None, "imblearn.over_sampling": None}
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(calibration.CalibrationDependencyError, "no fallback"):
            calibration.with_smote(self.base, representation="numeric", k_neighbors=2)
        self.assertNotIn("smote", self.base.named_steps)
        self.assertEqual(RecordingAudit.fits, [])
        self.assertFalse(hasattr(self.base.named_steps["impute"], "statistics_"))

    def test_smote_requires_explicit_numeric_representation_and_rejects_raw_smiles(self):
        for representation in (None, "smiles", "rules", "fingerprint"):
            with self.subTest(representation=representation), self.assertRaises(calibration.CalibrationError):
                calibration.with_smote(self.base, representation=representation)
        from qsar_models import ChemicalRuleClassifier
        rules = Pipeline([("audit", RecordingAudit()), ("model", ChemicalRuleClassifier())])
        with self.assertRaisesRegex(calibration.CalibrationError, "SMILES/rule"):
            calibration.with_smote(rules, representation="numeric")
        for name, value in (("k_neighbors", 0), ("k_neighbors", True), ("random_state", None)):
            with self.subTest(name=name, value=value), self.assertRaises(calibration.CalibrationError):
                calibration.with_smote(self.base, representation="numeric", **{name: value})
        self.assertEqual(RecordingAudit.fits, [])

    @unittest.skipUnless(importlib.util.find_spec("imblearn"), "Real optional imbalanced-learn is not installed")
    def test_real_smote_is_fold_local_audits_original_ids_and_never_resamples_replay(self):
        from imblearn.over_sampling import SMOTE
        from imblearn.pipeline import Pipeline as SamplingPipeline
        rng = np.random.default_rng(31)
        X = rng.normal(size=(48, 4))
        y = np.tile([0, 0, 0, 1], 12)
        X[:, 0] += y
        X[::9, 1] = np.nan
        ids = [f"imbalance-{i}" for i in range(48)]
        groups = [f"group-{i // 4:02d}" for i in range(48)]
        splits = grouped_splits(groups)
        sampled = calibration.with_smote(self.base, representation="numeric", k_neighbors=2, random_state=9)
        self.assertIsInstance(sampled, SamplingPipeline)
        self.assertIsInstance(sampled.named_steps["smote"], SMOTE)
        self.assertEqual([name for name, _ in sampled.steps],
                         ["audit", "impute", "variance", "scale", "select", "smote", "model"])
        self.assertNotIn("smote", self.base.named_steps)
        model = self.wrapper(base=sampled, splits=splits).fit(X, y, row_ids=ids, groups=groups)
        self.assertEqual(RecordingAudit.fits, [[ids[i] for i in t] for t, _ in splits] + [ids])
        audits = model.calibration_audit_["folds"] + [model.calibration_audit_["base_fit"]]
        self.assertFalse(hasattr(model, "calibration_estimators_"))
        for audit in audits:
            provenance = audit["samplers"][0]
            self.assertEqual(provenance["original_fit_row_ids"], audit["audit_fit_row_ids"])
            self.assertEqual(provenance["row_identity_scope"], "original_rows_before_resampling")
            self.assertIsNone(provenance["synthetic_row_ids"])
            self.assertIsNone(provenance["synthetic_parent_row_ids"])
            self.assertIn("SMOTE", provenance["class"])
            self.assertGreater(sum(row["count"] for row in provenance["synthetic_counts"]), 0)
            training_y = [y[ids.index(row)] for row in audit["audit_fit_row_ids"]]
            self.assertEqual(sum(row["count"] for row in provenance["synthetic_counts"]),
                             abs(training_y.count(0) - training_y.count(1)))
        self.assertTrue(hasattr(model.base_estimator_.named_steps["smote"], "sampling_strategy_"))
        payload = io.BytesIO()
        joblib.dump(model, payload)
        payload.seek(0)
        restored = joblib.load(payload)
        expected = model.predict_proba(X[:4])
        with patch.object(SMOTE, "fit_resample", side_effect=AssertionError("Prediction attempted resampling")):
            np.testing.assert_array_equal(restored.predict_proba(X[:4]), expected)
        with self.assertRaisesRegex(calibration.CalibrationError, "already contains"):
            calibration.with_smote(sampled, representation="numeric")
        json.dumps(model.calibration_audit_, allow_nan=False)

    def test_fit_start_callback_covers_each_clone_calibrator_and_final_refit(self):
        starts = []
        model = self.wrapper().fit(self.X, self.y, row_ids=self.ids, groups=self.groups,
                                   before_fit=lambda context: starts.append(context))
        self.assertEqual(starts, [{"phase": "calibration-fold", "calibration_fold": i} for i in range(3)]
                         + [{"phase": "calibrator-fit"}, {"phase": "calibration-base-refit"}])
        self.assertFalse(hasattr(model, "calibration_estimators_"))
        RecordingAudit.fits.clear()
        def stop(context):
            if context == {"phase": "calibration-fold", "calibration_fold": 1}:
                raise RuntimeError("fit-start budget exhausted")
        partial = self.wrapper()
        with self.assertRaisesRegex(RuntimeError, "fit-start budget"):
            partial.fit(self.X, self.y, row_ids=self.ids, groups=self.groups, before_fit=stop)
        self.assertEqual(len(RecordingAudit.fits), 1)
        self.assertEqual(len(partial.calibration_audit_["folds"]), 1)
        self.assertFalse(hasattr(partial, "base_estimator_"))
        with self.assertRaises(NotFittedError):
            partial.predict_proba(self.X)


if __name__ == "__main__":
    unittest.main(verbosity=2)
