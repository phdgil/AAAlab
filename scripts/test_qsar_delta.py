"""Synthetic read-only CSV delta diagnostics; no scientific approval fixtures."""
import builtins
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"))
import qsar_delta as delta


class DatasetDeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qsar-delta-")
        self.root = Path(self.tmp.name)
        self.old = self.root / "old.csv"
        self.new = self.root / "new.csv"
        self.rows = [{"output_row_id": "r1", "parent_smiles": "CCO", "task_id": "rat", "group_id": "g1",
                      "activity": "1.0", "eligible": "true", "temperature": "20"},
                     {"output_row_id": "r2", "parent_smiles": "CCC", "task_id": "mouse", "group_id": "g2",
                      "activity": "2", "eligible": "false", "temperature": "25"},
                     {"output_row_id": "r3", "parent_smiles": "CO", "task_id": "rat", "group_id": "g3",
                      "activity": "", "eligible": "false", "temperature": "30"}]
        self.fields = list(self.rows[0])
        self.table(self.old, self.rows)
        self.table(self.new, self.rows)

    def tearDown(self):
        self.tmp.cleanup()

    def table(self, path, rows, fields=None, encoding="utf-8", **options):
        with path.open("w", encoding=encoding, newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields or self.fields, **options)
            writer.writeheader()
            writer.writerows(rows)

    def compare(self, **options):
        return delta.compare_datasets(self.old, self.new, target_column="activity", eligibility_column="eligible", **options)

    def test_reordering_quoting_bom_and_newlines_change_bytes_not_content(self):
        self.table(self.new, list(reversed(self.rows)), list(reversed(self.fields)), encoding="utf-8-sig",
                   quoting=csv.QUOTE_ALL, lineterminator="\n")
        old_bytes, new_bytes = self.old.read_bytes(), self.new.read_bytes()
        report = self.compare()
        self.assertFalse(report["bytes_identical"])
        self.assertTrue(report["effective_content_identical"])
        self.assertTrue(report["complete_csv_semantics_identical"])
        self.assertEqual(report["effective_content_sha256"]["old"], report["effective_content_sha256"]["new"])
        self.assertTrue(report["no_op_diagnostic"]["is_no_op"])
        self.assertTrue(report["no_op_diagnostic"]["advisory_only"])
        self.assertFalse(report["approval_granted"])
        self.assertFalse(report["hash_bypass_authorized"])
        self.assertIn("exact input-hash invalidation", report["retraining_decision"])
        self.assertIn("not complete pipeline-input equality", report["comparison"]["identity_scope"])
        self.assertEqual(report["input_bindings"]["old"]["sha256"], hashlib.sha256(old_bytes).hexdigest())
        self.assertEqual(old_bytes, self.old.read_bytes())
        self.assertEqual(new_bytes, self.new.read_bytes())

    def test_added_removed_and_each_declared_change_by_identity(self):
        changed = dict(self.rows[0], activity="1.1", parent_smiles="CCN", task_id="human", group_id="merged", eligible="false")
        added = dict(self.rows[1], output_row_id="r4")
        self.table(self.new, [self.rows[2], added, changed])
        report = self.compare()
        self.assertEqual(report["added_ids"], ["r4"])
        self.assertEqual(report["removed_ids"], ["r2"])
        for field, column in (("changed_targets", "activity"), ("changed_structures", "parent_smiles"),
                              ("changed_tasks", "task_id"), ("changed_groups", "group_id"), ("changed_eligibility", "eligible")):
            self.assertEqual(report[field], [{"output_row_id": "r1", "old": self.rows[0][column], "new": changed[column]}])
        self.assertEqual(report["row_counts"], {"old": 3, "new": 3, "common": 2})
        self.assertFalse(report["effective_content_identical"])
        self.assertFalse(report["no_op_diagnostic"]["is_no_op"])

    def test_numeric_and_smiles_equivalence_are_not_inferred(self):
        changed = dict(self.rows[0], activity="1", parent_smiles="OCC")
        self.table(self.new, [changed] + self.rows[1:])
        report = self.compare()
        self.assertFalse(report["effective_content_identical"])
        self.assertEqual(report["changed_targets"][0], {"output_row_id": "r1", "old": "1.0", "new": "1"})
        self.assertEqual(report["changed_structures"][0]["new"], "OCC")
        self.assertEqual(report["comparison"]["numeric_or_chemical_equivalence"], "not inferred")

    def test_undeclared_condition_change_cannot_be_hidden_as_no_op(self):
        self.table(self.new, [dict(self.rows[0], temperature="21")] + self.rows[1:])
        report = self.compare()
        self.assertTrue(report["effective_content_identical"])
        self.assertFalse(report["complete_csv_semantics_identical"])
        self.assertFalse(report["no_op_diagnostic"]["is_no_op"])
        self.assertEqual(report["other_column_changes"]["changed_cells"],
                         [{"output_row_id": "r1", "column": "temperature", "old": "20", "new": "21"}])
        self.table(self.new, [dict(row, exposure="acute") for row in self.rows], self.fields + ["exposure"])
        report = self.compare()
        self.assertEqual(report["other_column_changes"]["added_columns"], ["exposure"])
        self.assertFalse(report["no_op_diagnostic"]["is_no_op"])
        stripped = [{key: value for key, value in row.items() if key != "temperature"} for row in self.rows]
        self.table(self.new, stripped, [field for field in self.fields if field != "temperature"])
        self.assertEqual(self.compare()["other_column_changes"]["removed_columns"], ["temperature"])

    def test_all_eligibility_rows_preserved_and_omitted_policy_not_inferred(self):
        report = self.compare()
        self.assertEqual(report["row_counts"]["old"], 3)
        self.assertIn("all rows retained", report["comparison"]["eligibility_handling"])
        self.table(self.new, self.rows[:2] + [dict(self.rows[2], eligible="true")])
        unconfigured = delta.compare_datasets(self.old, self.new, target_column="activity")
        self.assertTrue(unconfigured["effective_content_identical"])
        self.assertEqual(unconfigured["changed_eligibility"], [])
        self.assertFalse(unconfigured["no_op_diagnostic"]["is_no_op"])
        self.assertEqual(unconfigured["other_column_changes"]["changed_cells"][0]["column"], "eligible")
        self.assertEqual(self.compare()["changed_eligibility"][0]["output_row_id"], "r3")

    def test_policy_binding_only_change_is_separate_and_never_approval(self):
        old_policy, new_policy = self.root / "old-policy.json", self.root / "new-policy.json"
        old_policy.write_text('{"cutoff": 0.5}\n', encoding="utf-8")
        new_policy.write_text('{"cutoff": 0.6}\n', encoding="utf-8")
        originals = {path: path.read_bytes() for path in (self.old, self.new, old_policy, new_policy)}
        report = self.compare(old_policy=old_policy, new_policy=new_policy)
        self.assertTrue(report["bytes_identical"])
        self.assertTrue(report["effective_content_identical"])
        self.assertTrue(report["policy_bindings"]["changed"])
        self.assertFalse(report["no_op_diagnostic"]["is_no_op"])
        self.assertFalse(report["approval_granted"])
        self.assertFalse(report["hash_bypass_authorized"])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        new_policy.write_bytes(old_policy.read_bytes())
        self.assertFalse(self.compare(old_policy=old_policy, new_policy=new_policy)["policy_bindings"]["changed"])
        new_policy.write_text('{"cutoff":0.5}', encoding="utf-8")
        self.assertTrue(self.compare(old_policy=old_policy, new_policy=new_policy)["policy_bindings"]["changed"])
        self.assertTrue(self.compare(old_policy=old_policy)["policy_bindings"]["changed"])
        self.assertIsNone(self.compare()["policy_bindings"]["old"])
        self.assertIn("unassessed", self.compare()["policy_bindings"]["comparison"])

    def test_valid_empty_dataset_supports_added_and_removed_axis(self):
        self.table(self.old, [])
        report = self.compare()
        self.assertEqual(report["added_ids"], ["r1", "r2", "r3"])
        self.assertEqual(report["removed_ids"], [])
        self.table(self.new, [])
        report = self.compare()
        self.assertTrue(report["effective_content_identical"])
        self.assertEqual(report["row_counts"], {"old": 0, "new": 0, "common": 0})
        self.table(self.old, self.rows)
        self.assertEqual(self.compare()["removed_ids"], ["r1", "r2", "r3"])

    def test_exact_strings_and_multiline_cells_are_unambiguous(self):
        rows = [dict(self.rows[0], activity="0.123456789012345678901234567890", temperature="line1\nline2")]
        self.table(self.old, rows)
        self.table(self.new, rows, quoting=csv.QUOTE_ALL)
        self.assertTrue(self.compare()["effective_content_identical"])
        self.table(self.new, [dict(rows[0], activity="0.123456789012345678901234567891")])
        self.assertFalse(self.compare()["effective_content_identical"])
        self.table(self.new, [dict(rows[0], output_row_id=" r1")])
        report = self.compare()
        self.assertEqual(report["added_ids"], [" r1"])
        self.assertEqual(report["removed_ids"], ["r1"])

    def test_duplicate_blank_missing_and_malformed_inputs_fail_closed(self):
        good = self.new.read_bytes()
        header = ",".join(self.fields)
        bad_inputs = ["", "output_row_id,output_row_id\na,b\n", "output_row_id,\na,b\n",
                      header + "\nr1,CCO,rat,g1,1,true,20,extra\n",
                      header + "\nr1,CCO,rat,g1\n",
                      header + '\n"unterminated,CCO,rat,g1,1,true,20\n',
                      header + "\n ,CCO,rat,g1,1,true,20\n",
                      header + "\nr1,CCO,rat,g1,1,true,20\nr1,CCC,rat,g2,2,true,20\n",
                      "output_row_id,parent_smiles,task_id,group_id\nr1,CCO,rat,g1\n"]
        for text in bad_inputs:
            with self.subTest(text=text):
                self.new.write_text(text, encoding="utf-8")
                with self.assertRaises(delta.DeltaError):
                    self.compare()
                self.assertFalse((self.root / "out").exists())
        self.new.write_bytes(b"\xff")
        with self.assertRaisesRegex(delta.DeltaError, "UTF-8"):
            self.compare()
        self.new.write_bytes(good)
        for column in ("", "output_row_id", None):
            with self.subTest(column=column), self.assertRaises(delta.DeltaError):
                delta.compare_datasets(self.old, self.new, target_column=column)

    def test_custom_column_bindings_are_explicit_and_hash_bound(self):
        columns = {"parent_smiles": "parent", "task_id": "endpoint", "group_id": "cluster", "eligible": "use_row"}
        rows = [{columns.get(key, key): value for key, value in row.items()} for row in self.rows]
        fields = list(rows[0])
        self.table(self.old, rows, fields)
        self.table(self.new, rows, fields)
        report = delta.compare_datasets(self.old, self.new, target_column="activity", structure_column="parent",
                                        task_column="endpoint", group_column="cluster", eligibility_column="use_row")
        self.assertTrue(report["effective_content_identical"])
        self.assertEqual(report["comparison"]["columns"]["eligibility"], "use_row")
        with self.assertRaisesRegex(delta.DeltaError, "Missing comparison"):
            self.compare()

    def test_fresh_destination_contains_json_only_and_preserves_originals(self):
        before = {path: path.read_bytes() for path in (self.old, self.new)}
        destination = self.root / "out"
        receipt = delta.write_delta(self.old, self.new, destination, target_column="activity", eligibility_column="eligible")
        self.assertEqual([path.name for path in destination.iterdir()], ["delta.json"])
        artifact = destination / "delta.json"
        report = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(report, self.compare())
        self.assertEqual(receipt["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        original_artifact = artifact.read_bytes()
        with self.assertRaisesRegex(delta.DeltaError, "fresh"):
            delta.write_delta(self.old, self.new, destination, target_column="activity")
        self.assertEqual(artifact.read_bytes(), original_artifact)
        empty = self.root / "existing-empty"
        empty.mkdir()
        with self.assertRaisesRegex(delta.DeltaError, "fresh"):
            delta.write_delta(self.old, self.new, empty, target_column="activity")
        self.assertEqual(list(empty.iterdir()), [])
        for path in (self.old, self.root):
            with self.subTest(path=path), self.assertRaisesRegex(delta.DeltaError, "overlaps"):
                delta.write_delta(self.old, self.new, path, target_column="activity")
        self.assertEqual(self.old.read_bytes(), before[self.old])

    def test_mutated_input_cannot_publish_stale_delta(self):
        original_snapshot = delta._snapshot
        def mutate_after_snapshot(path, columns):
            result = original_snapshot(path, columns)
            if Path(path) == self.new:
                self.new.write_bytes(self.new.read_bytes() + b"\n")
            return result
        with patch.object(delta, "_snapshot", side_effect=mutate_after_snapshot):
            with self.assertRaisesRegex(delta.DeltaError, "changed during"):
                delta.write_delta(self.old, self.new, self.root / "out", target_column="activity")
        self.assertFalse((self.root / "out").exists())

    def test_post_write_mutation_removes_only_new_evidence(self):
        verify = delta._verify_inputs
        calls = []
        def mutate_at_publish(bindings):
            calls.append(True)
            if len(calls) == 3:
                self.new.write_bytes(self.new.read_bytes() + b"\n")
            verify(bindings)
        with patch.object(delta, "_verify_inputs", side_effect=mutate_at_publish):
            with self.assertRaisesRegex(delta.DeltaError, "changed during"):
                delta.write_delta(self.old, self.new, self.root / "out", target_column="activity")
        self.assertFalse((self.root / "out/delta.json").exists())
        self.assertTrue(self.old.exists())
        self.assertTrue(self.new.exists())

    def test_output_collision_does_not_delete_concurrent_user_file(self):
        original_open = Path.open
        def competing_open(path, mode="r", *args, **kwargs):
            if path.name == "delta.json" and mode == "x":
                with original_open(path, "w", encoding="utf-8") as stream:
                    stream.write("concurrent user content")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", new=competing_open):
            with self.assertRaises(FileExistsError):
                delta.write_delta(self.old, self.new, self.root / "collision", target_column="activity")
        self.assertEqual((self.root / "collision/delta.json").read_text(encoding="utf-8"), "concurrent user content")

    def test_cli_stdout_and_errors_are_json_and_never_overwrite(self):
        args = ["--old", str(self.old), "--new", str(self.new), "--target-column", "activity",
                "--eligibility-column", "eligible", "--out", str(self.root / "cli")]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = delta.main(args)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["path"], str((self.root / "cli/delta.json").resolve()))
        original = (self.root / "cli/delta.json").read_bytes()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = delta.main(args)
        self.assertEqual(status, 2)
        self.assertIn("fresh", json.loads(stderr.getvalue())["error"])
        self.assertEqual((self.root / "cli/delta.json").read_bytes(), original)

    def test_comparison_has_no_chemistry_or_model_runtime_dependency(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"rdkit", "sklearn", "numpy"}:
                raise AssertionError("CSV delta is a pure diagnostic")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            self.assertTrue(self.compare()["bytes_identical"])


if __name__ == "__main__":
    unittest.main()
