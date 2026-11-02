import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"))
import qsar_assess as assess


def table(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rows = [{"output_row_id": str(i), "task_id": "rat", "group_id": str(i),
                      "target_pLD50": i, "source_id": "source", "partition": "development"} for i in range(4)]
        self.pred = [{"output_row_id": str(i), "task_id": "rat", "outer_fold": i // 2,
                      "candidate_id": "selection_procedure", "prediction": i + .5} for i in range(4)]
        self.folds = {"tasks": {"rat": {"row_ids": [str(i) for i in range(4)], "outer_folds": [
            {"fold": 0, "train_row_ids": ["2", "3"], "validation_row_ids": ["0", "1"]},
            {"fold": 1, "train_row_ids": ["0", "1"], "validation_row_ids": ["2", "3"]}]}}}
        self.contract = {"preparation": {"target_column": "target_pLD50"}, "evaluation": {
            "target_column": "target_pLD50", "mode": "development", "uncertainty": "molecular_group_bootstrap",
            "bootstrap_repetitions": 20, "seed": 7, "required_candidates": ["selection_procedure"]}}

    def tearDown(self):
        self.tmp.cleanup()

    def run_eval(self):
        table(self.root / "development.csv", self.rows)
        table(self.root / "predictions.csv", self.pred)
        assess.dump(self.root / "folds.json", self.folds)
        assess.dump(self.root / "contract.json", self.contract)
        assess.dump(self.root / "split.json", {key: {"path": str((self.root / filename).resolve()), "sha256": assess.digest(self.root / filename)}
                    for key, filename in (("development", "development.csv"), ("folds", "folds.json"), ("contract", "contract.json"))})
        return assess.evaluate_development(self.root / "development.csv", self.root / "folds.json",
                                           self.root / "predictions.csv", self.root / "contract.json", self.root / "split.json", self.root / "out")

    def test_independent_metrics_and_training_baseline(self):
        self.run_eval()
        r = assess.load(self.root / "out/evaluation.json")["results"][0]
        self.assertEqual(r["metrics"]["mae"], .5)
        self.assertEqual(r["metrics"]["rmse"], .5)
        self.assertEqual(r["metrics"]["r2"], .8)
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
                                        self.root / "contract.json", self.root / "split.json", self.root / "second")

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


if __name__ == "__main__":
    unittest.main()
