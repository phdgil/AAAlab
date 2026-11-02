import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"))
import qsar_assess as assess
import qsar_domain as domain
import qsar_train as train


def table(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.numeric_fixture()

    def numeric_fixture(self, kind="regression", calibration=None, fold_thresholds=(.7, .5), chemical=False, with_domain=False):
        count = 8 if kind == "classification" else 4
        self.rows = [{"output_row_id": str(i), "task_id": "rat", "group_id": str(i),
                      "target_pLD50": i % 2 if kind == "classification" else i,
                      "parent_smiles": "C" * (i + 1), "source_id": "source", "partition": "development"} for i in range(count)]
        ids = [r["output_row_id"] for r in self.rows]
        def partition(members):
            halves = [members[:len(members) // 2], members[len(members) // 2:]]
            return [{"fold": i, "train_row_ids": halves[1 - i], "validation_row_ids": halves[i]} for i in range(2)]
        outer = partition(ids)
        for fold in outer:
            fold["inner_folds"] = partition(fold["train_row_ids"])
        self.folds = {"tasks": {"rat": {"row_ids": ids, "outer_folds": outer}}}
        cell = {"candidate_id": "ridge", "family": "numeric", "algorithm": "Ridge", "n_trials": 1,
                "search_space": {"model__alpha": [.1, 1.0], "select__k": [1, "all"]},
                "anchor": {"model__alpha": 1.0, "select__k": "all"},
                "preprocessing": {"scale": True, "variance_threshold": 0.0}}
        training = {"target_column": "target_pLD50", "metric": "rmse", "seed": 7, "tasks": {"rat": {"cells": [cell]}}}
        if kind == "classification":
            cell.update(candidate_id="logistic", algorithm="LogisticRegression",
                        search_space={"model__C": [.1, 1.0], "select__k": [1, "all"]},
                        anchor={"model__C": 1.0, "select__k": "all"})
            training.update(task_type=kind, positive_class=1, threshold=.5,
                            threshold_grid=list(dict.fromkeys([.5, *fold_thresholds])), metric="mcc")
            for row in self.rows:
                row["group_id"] = str(int(row["output_row_id"]) // 2)
                row["source_id"] = "positive" if row["target_pLD50"] else "negative"
        if calibration is not None:
            cell["calibration"] = calibration
        if chemical:
            cell.update(candidate_id="chemical-rule", family="SMILES", feature_route="smiles", algorithm="ChemicalRuleRegressor",
                preprocessing={}, rule={"descriptor_names": ["MolWt"], "smarts_rules": {}},
                search_space={"model__n_bins": [2, 3], "model__alpha": [.1, 1.0]},
                anchor={"model__n_bins": 2, "model__alpha": 1.0})
        if with_domain:
            training["applicability_domain"] = {"radius": 2, "n_bits": 64, "use_chirality": False,
                "similarity": "max", "min_references": 1, "cutoff": {"method": "fixed", "value": 1.0}}
        self.contract = {"preparation": {"target_column": "target_pLD50"}, "training": training, "evaluation": {
            "target_column": "target_pLD50", "mode": "development", "uncertainty": "molecular_group_bootstrap",
            "bootstrap_repetitions": 20, "seed": 7, "required_candidates": ["selection_procedure"]}}
        reverse = list(reversed(range(count)))
        X = np.asarray([[i / count, (i / count) ** 2] for i in range(count)])
        np.savez(self.root / "features.npz", X=X[reverse], row_ids=np.asarray(ids)[reverse],
                 task_ids=np.asarray(["rat"] * count), feature_names=np.asarray(["a", "b"]))
        self.write_inputs()
        spec, folds, _, target, features, identity = train.read_inputs(
            self.root / "development.csv", self.root / "folds.json", self.root / "features.json", self.root / "contract.json")
        axes = train.frozen_fit_axes(folds)
        self.pred, entries = [], []
        for fold in folds["tasks"]["rat"]["outer_folds"]:
            threshold = fold_thresholds[fold["fold"]]
            chosen = train.selection(cell, "anchor", cell["anchor"], spec, {"threshold": threshold})
            model, predictions = train.fit_predict(cell, chosen["parameters"], fold["train_row_ids"], fold["validation_row_ids"],
                target, features, spec["seed"], [], {"phase": "outer-refit"}, axes, task_spec={**spec, **chosen})
            model.training_identity_sha256_ = train.digest(identity)
            path = self.root / ("outer-" + str(fold["fold"]) + ".joblib")
            joblib.dump(model, path)
            for rid, prediction in zip(fold["validation_row_ids"], predictions):
                row = {"output_row_id": rid, "task_id": "rat", "outer_fold": fold["fold"],
                       "candidate_id": "selection_procedure", "prediction": float(prediction)}
                if kind == "classification":
                    row.update(task_type=kind, positive_class=1, threshold=threshold)
                self.pred.append(row)
            entries.append({"fold": fold["fold"], "threshold": chosen.get("threshold"), "model": train.binding(path),
                "selection": chosen, "training_identity_sha256": train.digest(identity),
                "frozen_recipe_sha256": model.frozen_recipe_sha256_,
                "threshold_provenance": {"source": "inner_cv" if kind == "classification" else "contract_fixed",
                    "scope": "outer-" + str(fold["fold"]), "trial_id": "anchor",
                    "fit_axis_id": train.digest(fold["train_row_ids"])}})
        registry = self.root / "fit_index_registry.json"
        assess.dump(registry, {"schema_version": "1.0", "axes": axes})
        self.oof_manifest = {"schema_version": "1.0", "status": "complete", "inputs": identity["inputs"], "identity": identity,
            "fit_index_registry": train.binding(registry), "tasks": {"rat": {"task_type": kind,
            "positive_class": 1 if kind == "classification" else None, "outer_folds": entries}}}

    def tearDown(self):
        self.tmp.cleanup()

    def write_inputs(self):
        table(self.root / "development.csv", self.rows)
        assess.dump(self.root / "folds.json", self.folds)
        assess.dump(self.root / "contract.json", self.contract)
        split = {key: train.binding(self.root / filename)
                 for key, filename in (("development", "development.csv"), ("folds", "folds.json"), ("contract", "contract.json"))}
        assess.dump(self.root / "split.json", {"status": "complete", "development_rows": len(self.rows), **split})
        assess.dump(self.root / "features.json", {"status": "complete", "input": split["development"], "contract": split["contract"],
            "split_manifest": train.binding(self.root / "split.json"), "families": {"numeric": {
                "artifact": train.binding(self.root / "features.npz"), "shape": [len(self.rows), 2], "dtype": "float64",
                "feature_names": ["a", "b"], "nonfinite_count": 0}}})

    def run_eval(self, use_manifest=True, manifest_transform=None):
        self.write_inputs()
        table(self.root / "predictions.csv", self.pred)
        manifest_path = self.write_oof_manifest(manifest_transform) if use_manifest else None
        return assess.evaluate_development(self.root / "development.csv", self.root / "folds.json",
                                           self.root / "predictions.csv", self.root / "contract.json", self.root / "split.json", self.root / "out", manifest_path)

    def classification_fixture(self):
        self.numeric_fixture("classification")

    def write_oof_manifest(self, manifest_transform=None):
        manifest = json.loads(json.dumps(self.oof_manifest))
        manifest["predictions"] = train.binding(self.root / "predictions.csv")
        if manifest_transform is not None:
            manifest_transform(manifest)
        path = self.root / "oof_manifest.json"
        assess.dump(path, manifest)
        return path

    def test_independent_metrics_and_training_baseline(self):
        self.run_eval()
        r = assess.load(self.root / "out/evaluation.json")["results"][0]
        residual = [self.rows[int(row["output_row_id"])]["target_pLD50"] - row["prediction"] for row in self.pred]
        self.assertAlmostEqual(r["metrics"]["mae"], sum(abs(value) for value in residual) / len(residual))
        self.assertAlmostEqual(r["metrics"]["rmse"], (sum(value ** 2 for value in residual) / len(residual)) ** .5)
        self.assertAlmostEqual(r["metrics"]["r2"], 1 - sum(value ** 2 for value in residual) / 5)
        self.assertEqual(r["training_mean_baseline"]["mae"], 2)
        self.assertEqual(r["metrics"]["sst"], 5)

    def test_constant_target_r2_is_undefined(self):
        self.assertIsNone(assess.metrics([1, 1], [1, 1])["r2"])

    def test_terminal_rows_rejected(self):
        self.rows[0]["partition"] = "terminal"
        with self.assertRaisesRegex(assess.DataError, "rejects terminal"):
            self.run_eval()

    def test_duplicate_header_rejected(self):
        path = self.root / "bad.csv"
        path.write_text("prediction,prediction\n1,999\n", encoding="utf-8")
        with self.assertRaisesRegex(assess.DataError, "duplicate CSV"):
            assess.read_csv(path)

    def test_ragged_row_rejected(self):
        path = self.root / "bad.csv"
        path.write_text("a,b\n1,2,3\n", encoding="utf-8")
        with self.assertRaisesRegex(assess.DataError, "row width"):
            assess.read_csv(path)

    def test_extreme_finite_values_fail_explicitly(self):
        with self.assertRaisesRegex(assess.DataError, "overflow"):
            assess.metrics([1e308, -1e308], [0, 0])
        with self.assertRaisesRegex(assess.DataError, "overflow"):
            assess.metrics([0, 1e-150], [1e100, 1e100])

    def test_frozen_manifest_mutation_rejected(self):
        self.run_eval()
        (self.root / "development.csv").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(assess.DataError, "frozen preprocessing"):
            assess.evaluate_development(self.root / "development.csv", self.root / "folds.json", self.root / "predictions.csv",
                                        self.root / "contract.json", self.root / "split.json", self.root / "second", self.root / "oof_manifest.json")

    def test_lower_mae_does_not_require_higher_r2(self):
        a = assess.metrics([0, 1, 2], [0, 1, 5])
        b = assess.metrics([0, 1, 2], [1.1, 2.1, 3.1])
        self.assertLess(a["mae"], b["mae"])
        self.assertLess(a["r2"], b["r2"])

    def test_missing_predictions_fail(self):
        self.pred.pop()
        with self.assertRaisesRegex(assess.DataError, "Missing candidate"):
            self.run_eval()

    def test_duplicate_predictions_fail(self):
        self.pred.append(dict(self.pred[0]))
        with self.assertRaisesRegex(assess.DataError, "Duplicate prediction"):
            self.run_eval()

    def test_wrong_fold_fails(self):
        self.pred[0]["outer_fold"] = 1
        with self.assertRaisesRegex(assess.DataError, "row/task/fold"):
            self.run_eval()

    def test_group_leakage_fails(self):
        self.rows[0]["group_id"] = "2"
        with self.assertRaisesRegex(assess.DataError, "group leakage"):
            self.run_eval()

    def test_nonfinite_fails(self):
        self.pred[0]["prediction"] = "nan"
        with self.assertRaisesRegex(assess.DataError, "Nonfinite"):
            self.run_eval()

    def test_extra_candidate_cannot_hide_in_coverage(self):
        self.pred.append(dict(self.pred[0], candidate_id="unexpected"))
        with self.assertRaisesRegex(assess.DataError, "coverage differs"):
            self.run_eval()

    def test_seeded_group_bootstrap_reproducible(self):
        args = ([1, 2, 3], [1, 2.5, 2.5], ["a", "a", "b"], 20, 10)
        self.assertEqual(assess.group_intervals(*args), assess.group_intervals(*args))

    def test_classification_confusion_and_ranking_metrics(self):
        result = assess.metrics([0, 1, 0, 1], [.1, .8, .7, .4], "classification", .5)
        self.assertEqual({key: result[key] for key in ("tp", "tn", "fp", "fn")}, {"tp": 1, "tn": 1, "fp": 1, "fn": 1})
        for name in ("sensitivity", "specificity", "balanced_accuracy", "accuracy"):
            self.assertEqual(result[name], .5)
        self.assertEqual(result["mcc"], 0)
        self.assertEqual(result["roc_auc"], .75)
        self.assertAlmostEqual(result["average_precision"], 5 / 6)
        self.assertTrue(all(result[key + "_undefined_reason"] is None for key in assess.CLASSIFICATION_METRICS))

    def test_threshold_equality_is_positive_and_metrics_are_not_optimized(self):
        result = assess.metrics([0, 1, 0, 1], [.1, .8, .7, .4], "classification", .4)
        self.assertEqual((result["tp"], result["tn"], result["fp"], result["fn"]), (2, 1, 1, 0))
        self.assertAlmostEqual(result["mcc"], 2 / np.sqrt(12))
        self.assertEqual(result["accuracy"], .75)

    def test_single_class_and_empty_slices_have_explicit_undefined_reasons(self):
        result = assess.metrics([0, 0], [.2, .8], "classification", .5)
        self.assertIsNone(result["sensitivity"])
        self.assertEqual(result["sensitivity_undefined_reason"], "no_positive_targets")
        self.assertEqual(result["specificity"], .5)
        self.assertEqual(result["accuracy"], .5)
        for name in ("roc_auc", "average_precision", "balanced_accuracy", "mcc"):
            self.assertIsNone(result[name])
            self.assertEqual(result[name + "_undefined_reason"], "single_class_target")
        empty = assess.metrics([], [], "classification", .5)
        self.assertEqual(empty["n"], 0)
        self.assertEqual(empty["tp"] + empty["tn"] + empty["fp"] + empty["fn"], 0)
        for name in assess.CLASSIFICATION_METRICS:
            self.assertIsNone(empty[name])
            self.assertEqual(empty[name + "_undefined_reason"], "empty_slice")

    def test_constant_predictions_do_not_hide_mcc_zero_denominator(self):
        result = assess.metrics([0, 1], [.2, .3], "classification", .5)
        self.assertIsNone(result["mcc"])
        self.assertEqual(result["mcc_undefined_reason"], "single_class_prediction")
        self.assertEqual(result["balanced_accuracy"], .5)

    def test_invalid_classification_probabilities_labels_and_thresholds_rejected(self):
        for value in (-.001, 1.001, float("nan"), float("inf")):
            with self.subTest(probability=value), self.assertRaises(assess.DataError):
                assess.metrics([0, 1], [value, .7], "classification", .5)
        for threshold in (None, True, -.1, 1.1, float("nan"), [.5]):
            with self.subTest(threshold=threshold), self.assertRaises(assess.DataError):
                assess.metrics([0, 1], [.2, .8], "classification", threshold)
        with self.assertRaisesRegex(assess.DataError, "labels must be 0/1"):
            assess.metrics([0, 2], [.2, .8], "classification", .5)

    def test_classification_bootstrap_counts_single_class_replicates_honestly(self):
        args = ([0, 0, 1, 1], [.1, .2, .8, .9], ["negative", "negative", "positive", "positive"], 100, 8, "classification", .5)
        report = assess.group_intervals(*args)
        self.assertEqual(report, assess.group_intervals(*args))
        self.assertEqual(report["attempted_replicates"], 100)
        self.assertEqual(report["replicate_accounting"]["accuracy"]["valid_replicates"], 100)
        for name in ("roc_auc", "average_precision", "mcc", "balanced_accuracy"):
            count = report["replicate_accounting"][name]
            self.assertGreater(count["invalid_replicates"], 0)
            self.assertGreater(count["valid_replicates"], 0)
            self.assertEqual(count["valid_replicates"] + count["invalid_replicates"], 100)
            self.assertEqual(count["undefined_reasons"], {"single_class_target": count["invalid_replicates"]})

    def test_development_uses_per_fold_thresholds_and_single_class_source_slices(self):
        self.classification_fixture()
        self.run_eval()
        result = assess.load(self.root / "out/evaluation.json")["results"][0]
        self.assertEqual(result["fold_thresholds"], {"0": .7, "1": .5})
        correct = sum(int(row["prediction"] >= row["threshold"]) == self.rows[int(row["output_row_id"])]["target_pLD50"]
                      for row in self.pred)
        self.assertEqual(result["metrics"]["accuracy"], correct / len(self.pred))
        self.assertEqual(result["threshold_policy"], "locked_per_outer_model_inner_only")
        self.assertNotIn("threshold", result)
        self.assertEqual(result["source_metrics"]["positive"]["roc_auc_undefined_reason"], "single_class_target")
        self.assertEqual(result["source_metrics"]["negative"]["sensitivity_undefined_reason"], "no_positive_targets")
        self.assertIn("training_prevalence_baseline", result)

    def test_classification_requires_hash_bound_outer_provenance(self):
        self.classification_fixture()
        with self.assertRaisesRegex(assess.DataError, "bound OOF manifest"):
            self.run_eval(use_manifest=False)

    def test_oof_row_cannot_substitute_final_selection_threshold(self):
        self.classification_fixture()
        self.pred[0]["threshold"] = .5
        with self.assertRaisesRegex(assess.DataError, "bound outer model"):
            self.run_eval()

    def test_changed_manifest_threshold_cannot_override_saved_outer_pipeline(self):
        self.classification_fixture()
        def mutate(manifest):
            manifest["tasks"]["rat"]["outer_folds"][0]["threshold"] = .5
        with self.assertRaisesRegex(assess.DataError, "model provenance"):
            self.run_eval(manifest_transform=mutate)

    def test_outer_threshold_provenance_rejects_wrong_fit_axis_and_final_scope(self):
        for field, value in (("scope", "final-selection"), ("fit_axis_id", "wrong-axis")):
            with self.subTest(field=field):
                self.classification_fixture()
                def mutate(manifest):
                    manifest["tasks"]["rat"]["outer_folds"][0]["threshold_provenance"][field] = value
                with self.assertRaisesRegex(assess.DataError, "outer training boundary"):
                    self.run_eval(manifest_transform=mutate)

    def test_oof_prediction_hash_cannot_be_rebound_to_other_csv(self):
        self.classification_fixture()
        def mutate(manifest):
            manifest["predictions"]["sha256"] = "not-the-predictions"
        with self.assertRaisesRegex(assess.DataError, "prediction binding mismatch"):
            self.run_eval(manifest_transform=mutate)

    def test_regression_requires_models_not_only_raw_prediction_csv(self):
        with self.assertRaisesRegex(assess.DataError, "bound OOF manifest"):
            self.run_eval(use_manifest=False)

    def test_numeric_replay_joins_bound_feature_rows_and_never_fits(self):
        self.pred.reverse()
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as fit:
            self.run_eval()
        fit.assert_not_called()
        result = assess.load(self.root / "out/evaluation.json")["results"][0]
        self.assertEqual(result["row_ids"], self.folds["tasks"]["rat"]["row_ids"])
        self.assertEqual(result["metrics"]["n"], len(self.rows))

    def test_chemical_rule_domain_replay_uses_real_fitted_basis_and_bank(self):
        self.numeric_fixture(chemical=True, with_domain=True)
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as fit:
            self.run_eval()
        fit.assert_not_called()
        result = assess.load(self.root / "out/evaluation.json")["results"][0]
        self.assertEqual(result["metrics"]["n"], len(self.rows))
        self.assertEqual(result["applicability_domain"]["coverage"]["rows"], len(self.rows))
        for entry in self.oof_manifest["tasks"]["rat"]["outer_folds"]:
            fitted = joblib.load(entry["model"]["path"])
            self.assertGreater(len(fitted.named_steps["model"].basis_.get_feature_names_out()), 0)
            self.assertEqual(fitted.applicability_domain_.training_row_ids, fitted.fit_training_row_ids_)

    def test_rebound_modified_numeric_predictions_rejected(self):
        self.pred[0]["prediction"] += .1
        with self.assertRaisesRegex(assess.DataError, "differ from fitted model replay"):
            self.run_eval()

    def test_same_training_identity_cannot_replace_the_selected_fitted_recipe(self):
        spec, folds, _, target, features, identity = train.read_inputs(
            self.root / "development.csv", self.root / "folds.json", self.root / "features.json", self.root / "contract.json")
        cell = spec["tasks"]["rat"]["cells"][0]
        fold = folds["tasks"]["rat"]["outer_folds"][0]
        changed = {**cell["anchor"], "model__alpha": .1}
        fitted, _ = train.fit_predict(cell, changed, fold["train_row_ids"], fold["validation_row_ids"], target, features,
            spec["seed"], [], {"phase": "outer-refit"}, train.frozen_fit_axes(folds), task_spec=spec)
        fitted.training_identity_sha256_ = train.digest(identity)
        entry = self.oof_manifest["tasks"]["rat"]["outer_folds"][0]
        self.assertNotEqual(fitted.frozen_recipe_sha256_, entry["frozen_recipe_sha256"])
        joblib.dump(fitted, entry["model"]["path"])
        entry["model"] = train.binding(entry["model"]["path"])
        with self.assertRaisesRegex(assess.DataError, "recipe differs"):
            self.run_eval()

    def test_rebound_modified_oof_probabilities_rejected(self):
        self.classification_fixture()
        self.pred[0]["prediction"] = .99 if self.pred[0]["prediction"] < .5 else .01
        with self.assertRaisesRegex(assess.DataError, "differ from fitted model replay"):
            self.run_eval()

    def test_replay_numeric_tolerance_does_not_allow_changed_threshold_classification(self):
        self.classification_fixture()
        threshold = self.pred[0]["prediction"]
        self.numeric_fixture("classification", fold_thresholds=(threshold, .5))
        self.assertEqual(self.pred[0]["prediction"], threshold)
        self.pred[0]["prediction"] = float(np.nextafter(threshold, -np.inf))
        with self.assertRaisesRegex(assess.DataError, "threshold classifications"):
            self.run_eval()

    def test_small_numeric_roundoff_is_within_explicit_replay_tolerance(self):
        self.pred[0]["prediction"] += assess.OOF_REPLAY_ATOL / 4
        self.run_eval()
        report = assess.load(self.root / "out/evaluation.json")
        self.assertEqual(report["oof_replay_tolerance"], {"rtol": 1e-7, "atol": 1e-9, "threshold_classifications": "exact"})

    def test_manifest_inputs_must_equal_identity_inputs(self):
        def mutate(manifest):
            manifest["inputs"].pop()
        with self.assertRaisesRegex(assess.DataError, "training identity inputs"):
            self.run_eval(manifest_transform=mutate)

    def test_identity_is_reconstructed_from_current_trainer_inputs(self):
        def mutate(manifest):
            manifest["identity"]["environment"]["scikit-learn"] = "not-the-current-version"
        with self.assertRaisesRegex(assess.DataError, "identity differs from current"):
            self.run_eval(manifest_transform=mutate)

    def test_uncalibrated_run_cannot_replace_calibrated_models_with_rebound_hashes(self):
        self.classification_fixture()
        old_entries = self.oof_manifest["tasks"]["rat"]["outer_folds"]
        old_models = [joblib.load(entry["model"]["path"]) for entry in old_entries]
        old_predictions = self.pred
        self.numeric_fixture("classification", calibration={"method": "sigmoid", "n_splits": 2})
        entries = self.oof_manifest["tasks"]["rat"]["outer_folds"]
        self.assertEqual([model.fit_training_row_ids_ for model in old_models],
                         [joblib.load(entry["model"]["path"]).fit_training_row_ids_ for entry in entries])
        self.pred = old_predictions
        for entry, old in zip(entries, old_models):
            joblib.dump(old, entry["model"]["path"])
            entry["model"] = train.binding(entry["model"]["path"])
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as fit:
            with self.assertRaisesRegex(assess.DataError, "training identity"):
                self.run_eval()
        fit.assert_not_called()
        def substitute_recipe(manifest):
            for entry, old in zip(manifest["tasks"]["rat"]["outer_folds"], old_entries):
                entry["selection"] = old["selection"]
                entry["frozen_recipe_sha256"] = old["frozen_recipe_sha256"]
        with self.assertRaisesRegex(assess.DataError, "declared candidate contract"):
            self.run_eval(manifest_transform=substitute_recipe)

    def test_calibrated_probability_replay_never_refits_calibration_or_base_pipeline(self):
        self.numeric_fixture("classification", calibration={"method": "sigmoid", "n_splits": 2})
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as base_fit, \
                patch.object(train.qsar_calibration.CrossFittedCalibratedClassifier, "fit",
                             side_effect=AssertionError("Evaluation must never calibrate")) as calibration_fit:
            self.run_eval()
        base_fit.assert_not_called()
        calibration_fit.assert_not_called()

    def test_domain_slices_retain_ood_and_abstention_rows(self):
        scores = [{"in_domain": True}, {"in_domain": False}, {"in_domain": None}, {"in_domain": True}]
        result = assess.domain_summary(self.rows, scores, [0, 1, 0, 1], [.1, .9, .3, .8], "classification", .5)
        self.assertEqual(result["coverage"], {"rows": 4, "in_domain": 2, "out_of_domain": 1, "abstained": 1})
        self.assertEqual(result["metrics"]["out_of_domain"]["n"], 1)
        self.assertEqual(result["metrics"]["abstained"]["n"], 1)
        self.assertEqual(result["metrics"]["abstained"]["roc_auc_undefined_reason"], "single_class_target")

    def test_domain_bank_must_match_exact_permitted_training_rows_and_parents(self):
        recipe = {"radius": 2, "n_bits": 64, "use_chirality": False, "similarity": "max", "min_references": 1,
                  "cutoff": {"method": "fixed", "value": .5}}
        rows = [{"output_row_id": "train-a", "parent_smiles": "CC"}, {"output_row_id": "train-b", "parent_smiles": "CO"}]
        bank = domain.fit([r["parent_smiles"] for r in rows], [r["output_row_id"] for r in rows], recipe)
        fitted = SimpleNamespace(applicability_domain_=bank, applicability_domain_sha256_=bank.to_dict()["artifact_sha256"])
        self.assertIs(assess.validate_domain(fitted, rows, recipe), bank)
        with self.assertRaisesRegex(assess.DataError, "permitted training rows"):
            assess.validate_domain(fitted, rows[:1], recipe)
        changed = [{**rows[0], "parent_smiles": "CN"}, rows[1]]
        with self.assertRaisesRegex(assess.DataError, "parent identity mismatch"):
            assess.validate_domain(fitted, changed, recipe)
        mapped = [{**rows[0], "parent_smiles": "[CH3:11][CH3:12]"}, rows[1]]
        self.assertIs(assess.validate_domain(fitted, mapped, recipe), bank)
        with self.assertRaisesRegex(assess.DataError, "lacks the required"):
            assess.validate_domain(SimpleNamespace(applicability_domain_=None), rows, recipe)


if __name__ == "__main__":
    unittest.main()
