"""Synthetic end-to-end producer integration, not scientific acceptance."""
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
import qsar_prepare as prep
import qsar_train as train
import qsar_assess as assess
import qsar_finish as finish
import test_qsar_runtime as gate_tests


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, kind="regression", with_external=False, with_domain=False, feature_families=None, calibration=None):
        self.curated, self.contract = self.root / "curated.csv", self.root / "contract.json"
        records = [{"output_row_id": str(i), "task_id": "fixture", "group_id": str(i),
                    "parent_smiles": "C" * (i + 1), "target_pLD50": str(i / 10), "source_id": "synthetic"} for i in range(24)]
        if kind == "classification":
            for i, record in enumerate(records):
                record.update(group_id=str(i // 2), target_pLD50=str(i % 2))
        prep.write_csv(self.curated, records, list(records[0]))
        config = {"preparation": {"target_column": "target_pLD50", "group_column": "group_id", "seed": 3,
                  "terminal_fraction": .25, "outer_folds": 3, "inner_folds": 2, "intended_generalization": "fixture molecules",
                  "terminal_custodian": "fixture", "prior_exposure": {"status": "exposed", "evidence": [], "description": "synthetic test"},
                  "unfitted_transforms": {"imputation": "fold-local median", "selection": "fold-local f_regression"}},
                  "features": {"families": {"RDKit2D": {}}},
                  "training": {"target_column": "target_pLD50", "metric": "rmse", "seed": 3, "tasks": {"fixture": {"cells": [
                      {"candidate_id": "ridge-full", "family": "RDKit2D", "algorithm": "Ridge", "n_trials": 2,
                       "search_space": {"model__alpha": [.1, 1.0], "select__k": [1, "all"]},
                       "anchor": {"model__alpha": 1.0, "select__k": "all"},
                       "preprocessing": {"scale": True, "variance_threshold": 0.0}}]}}},
                  "evaluation": {"mode": "development", "target_column": "target_pLD50", "uncertainty": "molecular_group_bootstrap",
                                 "bootstrap_repetitions": 20, "seed": 3, "required_candidates": ["selection_procedure"]}}
        if kind == "classification":
            config["training"].update(task_type="classification", positive_class=1, threshold=.7, metric="balanced_accuracy")
            config["training"]["tasks"]["fixture"]["cells"] = [{
                "candidate_id": "logistic", "family": "RDKit2D", "algorithm": "LogisticRegression", "n_trials": 1,
                "search_space": {"model__C": [1.0], "model__max_iter": [2000], "select__k": [1, "all"]},
                "anchor": {"model__C": 1.0, "model__max_iter": 2000, "select__k": "all"},
                "preprocessing": {"scale": True, "variance_threshold": 0.0}}]
            config["preparation"]["stratify"] = True
            config["preparation"]["unfitted_transforms"]["selection"] = "fold-local f_classif"
        if calibration is not None:
            config["training"]["tasks"]["fixture"]["cells"][0]["calibration"] = calibration
        if feature_families is not None:
            config["features"]["families"].update(feature_families)
        if with_domain:
            config["training"]["applicability_domain"] = {
                "radius": 2, "n_bits": 128, "use_chirality": False, "similarity": "max", "min_references": 1,
                "cutoff": {"method": "fixed", "value": 1.0}}
        external_path = None
        if with_external:
            external_path = self.root / "external.csv"
            external_rows = [{"output_row_id": "external-" + str(i), "task_id": "fixture", "group_id": "0",
                              "parent_smiles": smiles, "target_pLD50": str(i % 2 if kind == "classification" else i / 10),
                              "source_id": "external-synthetic"}
                             for i, smiles in enumerate(["F", "Cl", "Br", "I"])]
            prep.write_csv(external_path, external_rows, list(external_rows[0]))
            config["preparation"]["external_cohort"] = {
                "prior_exposure": {"status": "exposed", "evidence": [], "description": "External synthetic rows previously inspected"},
                "exclude_scaffold_overlap": False}
        prep.dump(self.contract, config)
        self.split = prep.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external_path)
        self.dev = self.root / "split/development.csv"
        self.folds = self.root / "split/folds.json"
        self.split_path = self.root / "split/preprocessing_manifest.json"
        prep.generate_features(self.dev, self.split_path, self.contract, self.root / "features")
        self.features = self.root / "features/feature_manifest.json"
        train.run_search(self.dev, self.folds, self.features, self.contract, self.root / "search")
        assess.evaluate_development(self.dev, self.folds, self.root / "search/selection_procedure_oof.csv", self.contract,
                                   self.split_path, self.root / "evaluation", self.root / "search/selection_procedure_oof_manifest.json")
        proposal = {"decision": "STOP", "reason": "Synthetic integration search complete", "failure_class": "none_observed",
                    "hypothesis": "Test replay preserves feature and row identities", "expected_comparison": "No scientific comparison",
                    "stopping_rule": "All declared synthetic trials complete", "rejected_alternatives": [],
                    "evidence": [str(self.root / "evaluation/evaluation.json")]}
        prep.dump(self.root / "proposal.json", proposal)
        finish.improvement(self.root / "evaluation/evaluation.json", self.root / "search/search_coverage.json",
                           self.root / "search/final_selection.json", self.root / "proposal.json", self.root / "improvement")
        train.final_fit(self.dev, self.folds, self.features, self.contract, self.root / "search/final_selection.json",
                        self.root / "improvement/model_lock.json", self.root / "final-fit")

    def evaluate_terminal(self):
        return finish.terminal(self.dev, self.root / "split/sealed_terminal.csv", self.split_path, self.features,
                               self.root / "final-fit/final_fit.json", self.contract, self.root / "terminal")

    def evaluate_external(self):
        return finish.external(self.dev, self.root / "split/sealed_external.csv", self.split_path, self.features,
                               self.root / "final-fit/final_fit.json", self.contract, self.root / "external-evaluation")

    def test_complete_producer_chain_and_locked_terminal_replay(self):
        self.fixture()
        result = self.evaluate_terminal()
        self.assertEqual(result["coverage"]["rows"], self.split["terminal_rows"])
        saved = finish.load(self.root / "terminal/terminal_evaluation.json")
        self.assertEqual(saved["exposure_history"]["status"], "exposed")
        self.assertIsNotNone(saved["results"][0]["metrics"]["r2"])

    def test_classification_chain_replays_locked_threshold_and_never_fits(self):
        self.fixture(kind="classification")
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Terminal must never fit")) as fit:
            self.evaluate_terminal()
        fit.assert_not_called()
        report = finish.load(self.root / "terminal/terminal_evaluation.json")
        result = report["results"][0]
        self.assertEqual(result["task_type"], "classification")
        self.assertEqual(result["positive_class"], 1)
        self.assertEqual(result["threshold"], .7)
        self.assertEqual(result["threshold_policy"], "locked_training_selection")
        rows = finish.read_csv(self.root / "terminal/terminal_predictions.csv")
        self.assertEqual(len(rows), self.split["terminal_rows"])
        self.assertEqual(result["metrics"]["n"], len(rows))
        for row in rows:
            self.assertEqual(float(row["threshold"]), .7)
            self.assertEqual(int(row["predicted_label"]), int(float(row["prediction"]) >= .7))
            self.assertTrue(0 <= float(row["prediction"]) <= 1)
        self.assertEqual(result["metrics"]["tp"] + result["metrics"]["tn"] + result["metrics"]["fp"] + result["metrics"]["fn"], len(rows))

    def test_calibrated_locked_evaluation_never_refits_base_or_calibration(self):
        self.fixture(kind="classification", with_external=True, calibration={"method": "sigmoid", "n_splits": 2})
        final = finish.load(self.root / "final-fit/final_fit.json")
        fitted = joblib.load(final["models"]["fixture"]["artifact"]["path"])
        self.assertIsInstance(fitted, train.qsar_calibration.CrossFittedCalibratedClassifier)
        self.assertEqual(fitted.named_steps["audit"].fit_row_ids_, fitted.fit_training_row_ids_)
        self.assertEqual(fitted.training_identity_sha256_, train.digest(final["identity"]))
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as base_fit, \
                patch.object(train.qsar_calibration.CrossFittedCalibratedClassifier, "fit",
                             side_effect=AssertionError("Evaluation must never calibrate")) as calibration_fit:
            self.evaluate_terminal()
            self.evaluate_external()
        base_fit.assert_not_called()
        calibration_fit.assert_not_called()
        for mode, directory in (("terminal", "terminal"), ("external", "external-evaluation")):
            report = finish.load(self.root / directory / (mode + "_evaluation.json"))
            self.assertEqual(report["results"][0]["threshold"], .7)
            self.assertEqual(report["coverage"]["dropped_rows"], 0)

    def test_stale_label_model_rejected_after_rebinding_final_artifact_for_terminal_and_external(self):
        self.fixture(with_external=True)
        original = finish.load(self.root / "final-fit/final_fit.json")
        stale = joblib.load(original["models"]["fixture"]["artifact"]["path"])
        rows = finish.read_csv(self.dev)
        for row in rows:
            row["target_pLD50"] = str(float(row["target_pLD50"]) + 10)
        prep.write_csv(self.dev, rows, list(rows[0]))
        split = finish.load(self.split_path)
        split["development"] = train.binding(self.dev)
        finish.dump(self.split_path, split)
        feature = finish.load(self.features)
        feature["input"] = train.binding(self.dev)
        feature["split_manifest"] = train.binding(self.split_path)
        finish.dump(self.features, feature)
        _, _, _, _, features, identity = train.read_inputs(self.dev, self.folds, self.features, self.contract)
        selection = finish.load(original["selection"]["path"])
        selection["identity"] = identity
        selected_path, lock_path = self.root / "revised-selection.json", self.root / "revised-lock.json"
        finish.dump(selected_path, selection)
        finish.dump(lock_path, {"decision": "STOP", "selection": train.binding(selected_path),
                               "tasks": {task: chosen["candidate_id"] for task, chosen in selection["tasks"].items()}})
        final = train.final_fit(self.dev, self.folds, self.features, self.contract, selected_path, lock_path, self.root / "revised-final")
        saved = final["models"]["fixture"]
        current = joblib.load(saved["artifact"]["path"])
        self.assertEqual(stale.frozen_recipe_sha256_, current.frozen_recipe_sha256_)
        self.assertEqual(stale.fit_training_row_ids_, current.fit_training_row_ids_)
        self.assertNotEqual(stale.training_identity_sha256_, current.training_identity_sha256_)
        X, _ = features[saved["family"]]
        self.assertFalse(np.allclose(current.predict(X), stale.predict(X), rtol=1e-7, atol=1e-7))
        report_path = self.root / "revised-final/final_fit.json"
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as fit:
            finish.terminal(self.dev, self.root / "split/sealed_terminal.csv", self.split_path, self.features,
                            report_path, self.contract, self.root / "current-terminal")
        fit.assert_not_called()
        joblib.dump(stale, saved["artifact"]["path"])
        saved["artifact"] = train.binding(saved["artifact"]["path"])
        finish.dump(report_path, final)
        with patch.object(Pipeline, "fit", side_effect=AssertionError("Evaluation must never fit")) as fit:
            for mode in ("terminal", "external"):
                with self.subTest(mode=mode), self.assertRaisesRegex(finish.DataError, "training identity"):
                    finish.evaluate_locked(self.dev, self.root / ("split/sealed_" + mode + ".csv"), self.split_path,
                        self.features, report_path, self.contract, self.root / ("stale-" + mode), mode)
        fit.assert_not_called()

    def test_positive_probability_uses_class_identity_not_column_position(self):
        fitted = SimpleNamespace(classes_=np.array([1, 0]), predict_proba=lambda X: np.array([[.8, .2], [.1, .9]]))
        np.testing.assert_array_equal(finish.positive_probability(fitted, np.zeros((2, 1))), [.8, .1])

    def test_locked_classifier_rejects_invalid_probability_range_and_class_axis(self):
        for matrix in (np.array([[1.01, -.01]]), np.array([[np.nan, .5]]), np.array([[.6, .6]]), np.array([[.5]])):
            with self.subTest(matrix=matrix.tolist()), self.assertRaises(finish.DataError):
                finish.positive_probability(SimpleNamespace(classes_=np.array([0, 1]), predict_proba=lambda X: matrix), np.zeros((1, 1)))
        for classes in ([0], [1], [0, 2], [0, 1, 2]):
            with self.subTest(classes=classes), self.assertRaisesRegex(finish.DataError, "classes 0/1"):
                finish.positive_probability(SimpleNamespace(classes_=np.array(classes)), np.zeros((1, 1)))

    def test_terminal_threshold_metadata_cannot_replace_locked_pipeline(self):
        self.fixture(kind="classification")
        path = self.root / "final-fit/final_fit.json"
        final = finish.load(path)
        final["models"]["fixture"]["threshold"] = .4
        finish.dump(path, final)
        with self.assertRaisesRegex(finish.DataError, "threshold/positive-class replay mismatch"):
            self.evaluate_terminal()

    def test_terminal_pipeline_threshold_cannot_be_changed_even_with_new_artifact_hash(self):
        self.fixture(kind="classification")
        path = self.root / "final-fit/final_fit.json"
        final = finish.load(path)
        model = final["models"]["fixture"]
        fitted = joblib.load(model["artifact"]["path"])
        fitted.threshold_ = .4
        joblib.dump(fitted, model["artifact"]["path"])
        model["artifact"]["sha256"] = finish.digest(model["artifact"]["path"])
        finish.dump(path, final)
        with self.assertRaisesRegex(finish.DataError, "threshold/positive-class replay mismatch"):
            self.evaluate_terminal()

    def test_external_is_separate_preserves_exposure_and_all_members(self):
        self.fixture(kind="classification", with_external=True, with_domain=True)
        terminal_bytes = (self.root / "split/sealed_terminal.csv").read_bytes()
        with patch.object(Pipeline, "fit", side_effect=AssertionError("External evaluation must never fit")) as fit:
            handoff = self.evaluate_external()
        fit.assert_not_called()
        report = finish.load(self.root / "external-evaluation/external_evaluation.json")
        self.assertEqual(report["mode"], "external")
        self.assertEqual(report["prior_exposure"], self.split["external_cohort"]["prior_exposure"])
        self.assertEqual(report["exposure_history"]["status"], "exposed")
        self.assertFalse(report["independent_holdout"])
        self.assertEqual(report["sealed_input"], self.split["external_cohort"]["artifact"])
        self.assertEqual(report["coverage"]["rows"], 4)
        self.assertEqual(report["coverage"]["predicted_rows"], 4)
        self.assertEqual(report["coverage"]["dropped_rows"], 0)
        self.assertEqual(handoff["coverage"]["rows"], 4)
        rows = finish.read_csv(self.root / "external-evaluation/external_predictions.csv")
        self.assertEqual({r["output_row_id"] for r in rows}, {"external-" + str(i) for i in range(4)})
        domain = report["results"][0]["applicability_domain"]
        self.assertEqual(domain["coverage"]["rows"], 4)
        self.assertEqual(domain["coverage"]["out_of_domain"], 4)
        self.assertEqual(domain["metrics"]["out_of_domain"]["n"], 4)
        self.assertEqual(domain["metrics"]["in_domain"]["n"], 0)
        self.assertEqual((self.root / "split/sealed_terminal.csv").read_bytes(), terminal_bytes)
        self.assertFalse((self.root / "external-evaluation/terminal_evaluation.json").exists())
        self.evaluate_terminal()
        terminal = finish.load(self.root / "terminal/terminal_evaluation.json")
        self.assertEqual(terminal["mode"], "terminal")
        self.assertEqual(terminal["coverage"]["rows"], self.split["terminal_rows"])
        self.assertNotIn("external_evaluation", terminal)

    def test_external_artifact_tampering_is_blocked_without_terminal_evaluation(self):
        self.fixture(with_external=True)
        with (self.root / "split/sealed_external.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(finish.DataError, "binding"):
            self.evaluate_external()
        self.assertFalse((self.root / "terminal").exists())

    def test_external_exposure_ledger_tampering_is_blocked(self):
        self.fixture(with_external=True)
        exposure = Path(self.split["external_cohort"]["exposure_ledger"]["path"])
        ledger = finish.load(exposure)
        ledger["status"] = "none_known"
        finish.dump(exposure, ledger)
        with self.assertRaisesRegex(finish.DataError, "binding"):
            self.evaluate_external()

    def test_terminal_input_cannot_substitute_for_external_cohort(self):
        self.fixture(with_external=True)
        with self.assertRaisesRegex(finish.DataError, "cohort differs from frozen split"):
            finish.external(self.dev, self.root / "split/sealed_terminal.csv", self.split_path, self.features,
                            self.root / "final-fit/final_fit.json", self.contract, self.root / "external-evaluation")

    def test_parent_identity_ignores_atom_maps_and_stereochemistry_not_scaffolds(self):
        self.assertEqual(finish.parent_identity({"parent_smiles": "[CH3:41][OH:42]"}), finish.parent_identity({"parent_smiles": "CO"}))
        self.assertEqual(finish.parent_identity({"parent_smiles": "N[C@H](C)O"}), finish.parent_identity({"parent_smiles": "N[C@@H](C)O"}))
        self.assertNotEqual(finish.parent_identity({"parent_smiles": "c1ccccc1O"}), finish.parent_identity({"parent_smiles": "c1ccccc1N"}))

    def test_smiles_feature_route_preserves_complete_query_axis(self):
        rows = [{"output_row_id": "a", "parent_smiles": "CC"}, {"output_row_id": "b", "parent_smiles": "O"}]
        model = {"feature_route": "smiles", "feature_names": ["parent_smiles"], "family": "SMILES"}
        matrix = finish.features_for(rows, model, {"families": {}})
        self.assertEqual(matrix.shape, (2,))
        np.testing.assert_array_equal(matrix, ["CC", "O"])

    def test_fingerprint_replay_preserves_counts_invariants_and_feature_dtype(self):
        for family in ("Morgan2Count", "Morgan3Count", "FCFP2", "FCFP3", "FCFP2Count", "FCFP3Count"):
            with self.subTest(family=family):
                parameters = {"n_bits": 64, "use_chirality": False}
                generator, width, recipe = prep.fingerprint_generator(family, parameters)
                record = {"parameters": parameters, "recipe": recipe,
                          "feature_names": prep.fingerprint_feature_names(family, width, recipe)}
                matrix = finish.replay(["CCCCCCCC", "CCO"], family, record, {"rdkit": finish.rdBase.rdkitVersion})
                expected = np.empty_like(matrix)
                for i, smiles in enumerate(["CCCCCCCC", "CCO"]):
                    finish.DataStructs.ConvertToNumpyArray(generator(finish.Chem.MolFromSmiles(smiles)), expected[i])
                np.testing.assert_array_equal(matrix, expected)
                self.assertEqual(str(matrix.dtype), prep.fingerprint_dtype(recipe))
                if family.endswith("Count"):
                    self.assertGreater(matrix.max(), 1)
                else:
                    self.assertLessEqual(matrix.max(), 1)

    def test_fingerprint_replay_rejects_changed_generator_recipe(self):
        parameters = {"n_bits": 64, "use_chirality": False}
        _, width, recipe = prep.fingerprint_generator("FCFP2Count", parameters)
        record = {"parameters": parameters, "recipe": {**recipe, "method": "GetFingerprint"},
                  "feature_names": prep.fingerprint_feature_names("FCFP2Count", width, recipe)}
        with self.assertRaisesRegex(finish.DataError, "generator differs from locked recipe"):
            finish.replay(["CCO"], "FCFP2Count", record, {"rdkit": finish.rdBase.rdkitVersion})

    def test_terminal_mutation_blocked(self):
        self.fixture()
        with (self.root / "split/sealed_terminal.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(finish.DataError, "binding"):
            self.evaluate_terminal()

    def test_alternate_terminal_split_rejected(self):
        self.fixture()
        changed = dict(self.split)
        path = self.root / "alternate.csv"
        records = finish.read_csv(self.root / "split/sealed_terminal.csv")[:1]
        prep.write_csv(path, records, list(records[0]))
        changed["sealed_terminal"] = prep.binding(path)
        alternate = self.root / "alternate-split.json"
        prep.dump(alternate, changed)
        with self.assertRaisesRegex(finish.DataError, "locked feature/training lineage"):
            finish.terminal(self.dev, path, alternate, self.features, self.root / "final-fit/final_fit.json",
                            self.contract, self.root / "out")

    def test_dependency_drift_rejected(self):
        self.fixture()
        path = self.root / "final-fit/final_fit.json"
        report = finish.load(path)
        report["identity"]["environment"]["scikit-learn"] = "impossible-version"
        finish.dump(path, report)
        with self.assertRaisesRegex(finish.DataError, "version drift"):
            self.evaluate_terminal()

    def test_nonfinite_replay_is_audited_and_matches_training_nan_policy(self):
        ledger = []
        with patch.object(finish.Descriptors, "_descList", [("bad", lambda mol: float("inf"))]):
            matrix = finish.replay(["CCO"], "RDKit2D", {"feature_names": ["bad"]},
                                   {"rdkit": finish.rdBase.rdkitVersion}, ["row1"], ledger)
        self.assertTrue(np.isnan(matrix[0, 0]))
        self.assertEqual(ledger, [{"output_row_id": "row1", "family": "RDKit2D", "feature": "bad", "reason": "nonfinite"}])

    def test_partial_search_cannot_stop_successfully(self):
        finish.dump(self.root / "eval.json", {"mode": "development"})
        finish.dump(self.root / "coverage.json", {"status": "incomplete"})
        finish.dump(self.root / "selection.json", {"status": "complete"})
        finish.dump(self.root / "proposal.json", {"decision": "STOP", "reason": "budget", "failure_class": "coverage",
            "hypothesis": "incomplete", "expected_comparison": "none", "stopping_rule": "budget", "rejected_alternatives": [],
            "evidence": [str(self.root / "eval.json")]})
        with self.assertRaisesRegex(finish.DataError, "Incomplete search"):
            finish.improvement(self.root / "eval.json", self.root / "coverage.json", self.root / "selection.json",
                               self.root / "proposal.json", self.root / "out")

    def test_continue_requires_budget_and_earliest_stage(self):
        finish.dump(self.root / "eval.json", {"mode": "development"})
        finish.dump(self.root / "coverage.json", {"status": "incomplete"})
        finish.dump(self.root / "selection.json", {"status": "incomplete"})
        proposal = {"decision": "CONTINUE", "reason": "repair source", "failure_class": "curation", "hypothesis": "unit mismatch",
            "expected_comparison": "versioned cohort", "stopping_rule": "units reconciled", "rejected_alternatives": [],
            "evidence": [str(self.root / "eval.json")], "restart_from": "curation", "remaining_budget": 1,
            "proposed_change": "Resolve native units from source documentation"}
        finish.dump(self.root / "proposal.json", proposal)
        result = finish.improvement(self.root / "eval.json", self.root / "coverage.json", self.root / "selection.json",
                                    self.root / "proposal.json", self.root / "out")
        self.assertEqual(result["restart_from"], "curation")
        self.assertNotIn("model_lock", result)

    def test_improvement_manifest_is_accepted_by_real_runtime_protocol(self):
        # Native IDs/receipts here are explicitly synthetic gate fixtures.
        gate = gate_tests.RuntimeTests()
        gate.setUp()
        try:
            gate.through("training-preflight")
            dispatch = gate.bind(gate.prepare("training"))
            selection = Path(dispatch["owned_output_dir"]) / "final_selection.json"
            coverage = selection.with_name("search_coverage.json")
            finish.dump(selection, {"status": "complete", "tasks": {"fixture": {"candidate_id": "ridge"}}})
            finish.dump(coverage, {"status": "complete", "completed_trials": 1, "expected_trials": 1, "failed_trials": 0})
            gate.rt.submit(dispatch["dispatch_id"], {"outputs": [str(selection), str(coverage)], "summary": "Synthetic training fixture", "checks": ["fixture only"]})
            gate.accept("training")
            dispatch = gate.bind(gate.prepare("development-evaluation"))
            evaluation = Path(dispatch["owned_output_dir"]) / "evaluation.json"
            finish.dump(evaluation, {"mode": "development"})
            gate.rt.submit(dispatch["dispatch_id"], {"outputs": [str(evaluation)], "summary": "Synthetic evaluation fixture", "checks": ["fixture only"]})
            gate.accept("development-evaluation")
            proposal = gate.base / "proposal.json"
            finish.dump(proposal, {"decision": "STOP", "reason": "fixture complete", "failure_class": "none", "hypothesis": "protocol consistency",
                "expected_comparison": "fixture", "stopping_rule": "complete fixture", "rejected_alternatives": [], "evidence": [str(evaluation)]})
            dispatch = gate.bind(gate.prepare("improvement", inputs=[str(selection), str(coverage), str(evaluation), str(proposal)]))
            manifest = finish.improvement(evaluation, coverage, selection, proposal, dispatch["owned_output_dir"])
            gate.rt.submit(dispatch["dispatch_id"], manifest)
            gate.accept("improvement")
            self.assertEqual(gate.rt.advance_improvement()["next_stage"], "final-fit")
        finally:
            gate.tearDown()


if __name__ == "__main__":
    unittest.main()
