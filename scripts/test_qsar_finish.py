"""Synthetic end-to-end producer integration, not scientific acceptance."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

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

    def fixture(self):
        self.curated, self.contract = self.root / "curated.csv", self.root / "contract.json"
        records = [{"output_row_id": str(i), "task_id": "fixture", "group_id": str(i),
                    "parent_smiles": "C" * (i + 1), "target_pLD50": str(i / 10), "source_id": "synthetic"} for i in range(24)]
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
        prep.dump(self.contract, config)
        self.split = prep.prepare_splits(self.curated, self.contract, self.root / "split")
        self.dev = self.root / "split/development.csv"
        self.folds = self.root / "split/folds.json"
        self.split_path = self.root / "split/preprocessing_manifest.json"
        prep.generate_features(self.dev, self.split_path, self.contract, self.root / "features")
        self.features = self.root / "features/feature_manifest.json"
        train.run_search(self.dev, self.folds, self.features, self.contract, self.root / "search")
        assess.evaluate_development(self.dev, self.folds, self.root / "search/selection_procedure_oof.csv", self.contract,
                                   self.split_path, self.root / "evaluation")
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

    def test_complete_producer_chain_and_locked_terminal_replay(self):
        self.fixture()
        result = self.evaluate_terminal()
        self.assertEqual(result["coverage"]["rows"], self.split["terminal_rows"])
        saved = finish.load(self.root / "terminal/terminal_evaluation.json")
        self.assertEqual(saved["exposure_history"]["status"], "exposed")
        self.assertIsNotNone(saved["results"][0]["metrics"]["r2"])

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
