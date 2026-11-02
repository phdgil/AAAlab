"""Deterministic synthetic-only training validation; no scientific dataset access."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import sys
SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_train as train


fixture = train.synthetic_fixture


def rebind_fixture(root):
    split = train.load(root / "split_manifest.json")
    split.update(development=train.binding(root / "development.csv"), folds=train.binding(root / "folds.json"),
                 contract=train.binding(root / "contract.json"))
    train.dump(root / "split_manifest.json", split)
    manifest = train.load(root / "features.json")
    manifest.update(input=split["development"], contract=split["contract"], split_manifest=train.binding(root / "split_manifest.json"))
    train.dump(root / "features.json", manifest)


def execute(root, name="out", resume=False):
    return train.run_search(root / "development.csv", root / "folds.json", root / "features.json",
                            root / "contract.json", root / name, resume)


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resume_rejects_missing_or_duplicate_search_evidence(self):
        import copy
        original = execute(self.root)
        for field in ("ledger", "fit_evidence"):
            for mode in ("missing", "duplicate"):
                with self.subTest(field=field, mode=mode):
                    state = copy.deepcopy(original)
                    if mode == "missing":
                        state[field] = []
                    else:
                        state[field].append(copy.deepcopy(state[field][0]))
                    train.dump(self.root / "out" / "checkpoint.json", state)
                    with self.assertRaisesRegex(train.TrainingError, "Completed scope"):
                        execute(self.root, resume=True)

    def test_resume_rejects_corrupt_completion_without_writing(self):
        import copy
        original = execute(self.root)
        mutations = [
            lambda s: s["artifacts"].clear(),
            lambda s: s["oof"][0].update(output_row_id="unknown"),
            lambda s: s["completed"].update({"unknown:scope": {}}),
            lambda s: s["ledger"][0].update(parameters={}),
            lambda s: s["ledger"][0].update(status="failed"),
            lambda s: s["ledger"][0]["fold_scores"].clear(),
        ]
        checkpoint = self.root / "out" / "checkpoint.json"
        for mutate in mutations:
            state = copy.deepcopy(original)
            mutate(state)
            train.dump(checkpoint, state)
            before = checkpoint.read_bytes()
            with self.assertRaisesRegex(train.TrainingError, "Completed scope"):
                execute(self.root, resume=True)
            self.assertEqual(checkpoint.read_bytes(), before)

    def test_resume_rejects_unknown_ledger_and_invalid_history(self):
        import copy
        original = execute(self.root)
        for mode in ("unknown-active", "unknown-history", "negative-cost", "nonfinite-cost", "wrong-parameters"):
            state = copy.deepcopy(original)
            record = copy.deepcopy(state["ledger"][0])
            if mode == "unknown-active":
                record["task_id"] = "undeclared"
                state["ledger"].append(record)
            else:
                if mode == "unknown-history":
                    record["scope"] = "undeclared"
                elif mode == "negative-cost":
                    record["seconds"] = -1
                elif mode == "nonfinite-cost":
                    record["seconds"] = "Infinity"
                else:
                    record["parameters"] = {}
                state["attempt_history"] = [{"task_id": "oral", "scope": "outer-0", "ledger": [record], "fit_evidence": []}]
            train.dump(self.root / "out" / "checkpoint.json", state)
            with self.subTest(mode=mode), self.assertRaises(train.TrainingError):
                execute(self.root, resume=True)

    def test_retry_preserves_failed_attempt(self):
        from unittest.mock import patch
        original = train.fit_predict
        calls = 0
        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient fixture failure")
            return original(*args, **kwargs)
        with patch.object(train, "fit_predict", side_effect=fail_once):
            with self.assertRaises(train.TrainingError):
                execute(self.root)
        failed = train.load(self.root / "out" / "checkpoint.json")
        result = execute(self.root, resume=True)
        self.assertEqual(result["attempt_history"][0]["ledger"], failed["ledger"])
        self.assertEqual(result["attempt_history"][0]["fit_evidence"], failed["fit_evidence"])
        coverage = train.load(self.root / "out" / "search_coverage.json")
        self.assertEqual(coverage["attempted_trials"], 25)
        self.assertEqual(coverage["failed_attempts"], 1)
        self.assertEqual(coverage["completed_trials"], 20)

    def test_duplicate_search_values_rejected(self):
        cell = train.load(self.root / "contract.json")["training"]["tasks"]["oral"]["cells"][0]
        cell["search_space"]["model__alpha"] = [1, 1.0]
        with self.assertRaisesRegex(train.TrainingError, "Duplicate search"):
            train.trials(cell, 71)

    def test_training_target_scale_must_match_preparation(self):
        contract = train.load(self.root / "contract.json")
        contract["preparation"]["target_column"] = "target_mg_kg"
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        with self.assertRaisesRegex(train.TrainingError, "target scale"):
            execute(self.root)

    def test_audit_cleanup_preserves_scientific_results(self):
        result = execute(self.root)
        scientific = {"oof": result["oof"], "final_selection": result["final_selection"],
                      "trials": [{k: v for k, v in r.items() if k not in {"seconds", "warnings", "nonconverged"}} for r in result["ledger"]]}
        rounded = json.loads(json.dumps(scientific), parse_float=lambda value: round(float(value), 10))
        self.assertEqual(train.digest(rounded), "b2bb15f62eba5289da992d44075fe9da1ce90dc09dcb5a14c68d02e1c367479d")

    def test_fit_axes_deduplicate_and_preserve_exact_order(self):
        result = execute(self.root)
        axes = train.load_fit_index_registry(result["fit_index_registry"])
        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(sum(a["count"] for a in axes.values()), 72)
        folds = train.load(self.root / "folds.json")["tasks"]["oral"]["outer_folds"]
        expected = {tuple(f[k]) for outer in folds for f in [outer, *outer["inner_folds"]]
                    for k in ("train_row_ids", "validation_row_ids")}
        self.assertEqual({tuple(a["row_ids"]) for a in axes.values()}, expected)
        for entry in result["fit_evidence"]:
            self.assertNotIn("fit_row_ids", entry)
            self.assertNotIn("transform_row_ids", entry)
            for field in ("fit_axis_id", "transform_axis_id"):
                axis = axes[entry[field]]
                self.assertEqual(axis["count"], len(axis["row_ids"]))
                self.assertEqual(entry[field], train.digest(axis["row_ids"]))
        persisted = train.load(self.root / "out" / "fit_evidence.json")
        self.assertEqual(persisted["records"], result["fit_evidence"])
        self.assertEqual(persisted["fit_index_registry"], result["fit_index_registry"])

    def test_legacy_checkpoint_and_modified_axis_registry_rejected(self):
        state = execute(self.root)
        state["schema_version"] = "1.0"
        train.dump(self.root / "out" / "checkpoint.json", state)
        with self.assertRaisesRegex(train.TrainingError, "Unsupported checkpoint format"):
            execute(self.root, resume=True)
        state["schema_version"] = "2.0"
        train.dump(self.root / "out" / "checkpoint.json", state)
        axis_path = Path(state["fit_index_registry"]["path"])
        axes = train.load(axis_path)
        first = next(iter(axes["axes"].values()))
        first["row_ids"].reverse()
        train.dump(axis_path, axes)
        with self.assertRaisesRegex(train.TrainingError, "registry hash mismatch"):
            execute(self.root, resume=True)

    def test_axis_registry_written_once_across_search_and_resume(self):
        from unittest.mock import patch
        writes = []
        original = train.dump
        def recorded(path, value):
            if Path(path).name == "fit_index_registry.json":
                writes.append(str(path))
            return original(path, value)
        with patch.object(train, "dump", side_effect=recorded):
            first = execute(self.root)
            second = execute(self.root, resume=True)
        self.assertEqual(first, second)
        self.assertEqual(len(writes), 1)

    def test_resume_rejects_existing_but_wrong_axis_for_each_fit_context(self):
        original = execute(self.root)
        contexts = {
            "inner": lambda r: r["scope"] == "outer-0" and "phase" not in r,
            "outer-refit": lambda r: r.get("phase") == "outer-refit",
            "final-selection": lambda r: r["scope"] == "final-selection",
        }
        for name, matches in contexts.items():
            for corruption in ("fit-equals-validation", "disjoint-swapped-pair", "different-fold-pair"):
                with self.subTest(context=name, corruption=corruption):
                    state = json.loads(json.dumps(original))
                    record = next(r for r in state["fit_evidence"] if matches(r))
                    if corruption == "fit-equals-validation":
                        record["fit_axis_id"] = record["transform_axis_id"]
                    elif corruption == "disjoint-swapped-pair":
                        record["fit_axis_id"], record["transform_axis_id"] = record["transform_axis_id"], record["fit_axis_id"]
                    else:
                        other = next(r for r in state["fit_evidence"]
                                     if r.get("phase") == record.get("phase")
                                     and (r["fit_axis_id"], r["transform_axis_id"]) != (record["fit_axis_id"], record["transform_axis_id"]))
                        record["fit_axis_id"], record["transform_axis_id"] = other["fit_axis_id"], other["transform_axis_id"]
                    train.dump(self.root / "out" / "checkpoint.json", state)
                    with self.assertRaisesRegex(train.TrainingError, "frozen task/scope/fold"):
                        execute(self.root, resume=True)

    def test_single_family_cache_and_compact_development_rows(self):
        import copy
        import weakref
        manifest = train.load(self.root / "features.json")
        second_path = self.root / "second.npz"
        second_path.write_bytes((self.root / "features.npz").read_bytes())
        manifest["families"]["second"] = copy.deepcopy(manifest["families"]["synthetic"])
        manifest["families"]["second"]["artifact"] = train.binding(second_path)
        train.dump(self.root / "features.json", manifest)
        contract = train.load(self.root / "contract.json")
        cell = copy.deepcopy(contract["training"]["tasks"]["oral"]["cells"][0])
        cell.update(candidate_id="second-ridge", family="second")
        contract["training"]["tasks"]["oral"]["cells"].append(cell)
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        _, _, rows, _, features, _ = train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                                     self.root / "features.json", self.root / "contract.json")
        self.assertIsInstance(features, train.FeatureStore)
        self.assertEqual(features.resident_families, ())
        self.assertNotIn("parent_smiles", next(iter(rows.values())))
        first, index = features["synthetic"]
        reference = weakref.ref(first)
        expected = first.copy()
        self.assertEqual(first.dtype, np.float64)
        del first
        second, _ = features["second"]
        self.assertEqual(features.resident_families, ("second",))
        self.assertIsNone(reference())
        np.testing.assert_array_equal(second, expected)
        del second
        np.testing.assert_array_equal(features["synthetic"][0], expected)
        self.assertEqual(features.peak_resident_bytes, expected.nbytes)
        with second_path.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(train.TrainingError, "Feature artifact hash mismatch"):
            features["second"]

    def test_warning_ledger_and_nonconvergence_failure(self):
        import warnings
        from unittest.mock import patch
        from sklearn.exceptions import ConvergenceWarning
        original = train.Ridge.fit
        def warning_fit(model, *args, **kwargs):
            warnings.warn("fixture did not converge", ConvergenceWarning)
            return original(model, *args, **kwargs)
        with patch.object(train.Ridge, "fit", warning_fit):
            with self.assertRaises(train.TrainingError):
                execute(self.root)
        result = train.load(self.root / "out" / "checkpoint.json")
        self.assertEqual(result["status"], "failed")
        for trial in result["ledger"]:
            self.assertEqual(trial["status"], "failed")
            self.assertTrue(trial["nonconverged"])
            self.assertTrue(any(w["category"] == "ConvergenceWarning" and w["fold"] == 0
                                and w["message"] == "fixture did not converge" for w in trial["warnings"]))

    def test_nonconvergence_policy_cannot_silently_allow(self):
        contract = train.load(self.root / "contract.json")
        contract["training"]["nonconvergence_policy"] = "ignore"
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        with self.assertRaisesRegex(train.TrainingError, "nonconvergence_policy"):
            execute(self.root)

    def test_nonconvergence_free_warning_is_preserved_per_fold(self):
        import warnings
        from unittest.mock import patch
        original = train.Ridge.fit
        def warning_fit(model, *args, **kwargs):
            warnings.warn("fixture diagnostic", UserWarning)
            return original(model, *args, **kwargs)
        with patch.object(train.Ridge, "fit", warning_fit):
            result = execute(self.root)
        self.assertEqual(result["status"], "complete")
        for trial in result["ledger"]:
            self.assertFalse(trial["nonconverged"])
            self.assertEqual(len(trial["warnings"]), len(trial["fold_scores"]))
            self.assertEqual({w["fold"] for w in trial["warnings"]}, {r["fold"] for r in trial["fold_scores"]})
            self.assertTrue(all(w["category"] == "UserWarning" and w["message"] == "fixture diagnostic" for w in trial["warnings"]))

    def test_lazy_loading_schema_drift_and_all_families_prechecked(self):
        import copy
        manifest = train.load(self.root / "features.json")
        with np.load(self.root / "features.npz") as archive:
            arrays = {name: archive[name].copy() for name in archive.files}
        arrays["feature_names"] = arrays["feature_names"][::-1]
        np.savez(self.root / "second.npz", **arrays)
        manifest["families"]["second"] = copy.deepcopy(manifest["families"]["synthetic"])
        manifest["families"]["second"]["artifact"] = train.binding(self.root / "second.npz")
        train.dump(self.root / "features.json", manifest)
        contract = train.load(self.root / "contract.json")
        cell = copy.deepcopy(contract["training"]["tasks"]["oral"]["cells"][0])
        cell.update(candidate_id="bad-second", family="second")
        contract["training"]["tasks"]["oral"]["cells"].append(cell)
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        with self.assertRaisesRegex(train.TrainingError, "axis mismatch"):
            execute(self.root)
        self.assertFalse((self.root / "out" / "checkpoint.json").exists())

    def test_coverage_anchor_feature_search_and_fit_boundaries(self):
        result = execute(self.root)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["oof"]), 24)
        self.assertEqual({r["candidate_id"] for r in result["oof"]}, {"selection_procedure"})
        self.assertEqual(len(result["ledger"]), 20)
        self.assertEqual(sum(r["kind"] == "anchor" for r in result["ledger"]), 4)
        self.assertEqual({r["parameters"]["select__k"] for r in result["ledger"] if r["kind"] == "search"}, {2, "all"})
        axes = train.load_fit_index_registry(result["fit_index_registry"])
        for evidence in result["fit_evidence"]:
            fit_ids = axes[evidence["fit_axis_id"]]["row_ids"]
            transform_ids = axes[evidence["transform_axis_id"]]["row_ids"]
            self.assertFalse(set(fit_ids) & set(transform_ids))
            self.assertLess(len(fit_ids), 24)
        self.assertIn("oral", result["final_selection"])
        self.assertEqual(len(result["artifacts"]), 3)

    def test_row_alignment(self):
        first = execute(self.root)
        with np.load(self.root / "features.npz") as data:
            arrays = {k: data[k].copy() for k in data.files}
        for k in ("X", "row_ids", "task_ids"):
            arrays[k] = arrays[k][::-1]
        np.savez(self.root / "features.npz", **arrays)
        manifest = train.load(self.root / "features.json")
        manifest["families"]["synthetic"]["artifact"] = train.binding(self.root / "features.npz")
        train.dump(self.root / "features.json", manifest)
        second = execute(self.root, "reordered")
        self.assertEqual(first["oof"], second["oof"])

    def test_outer_holdout_label_perturbation(self):
        spec, folds, rows, target, features, _ = train.read_inputs(
            self.root / "development.csv", self.root / "folds.json", self.root / "features.json", self.root / "contract.json")
        outer = folds["tasks"]["oral"]["outer_folds"][0]
        axes = train.frozen_fit_axes(folds)
        def selected(y):
            evidence, ledger = [], []
            result = train.search_scope("oral", "outer-0", spec["tasks"]["oral"]["cells"],
                                        outer["inner_folds"], y, features, spec, ledger, evidence, axes)
            model, predictions = train.fit_predict(result, result["parameters"], outer["train_row_ids"],
                                                   outer["validation_row_ids"], y, features, spec["seed"], evidence, {}, axes)
            return result, predictions, model.named_steps["impute"].statistics_
        first = selected(target)
        altered = dict(target)
        for r in outer["validation_row_ids"]:
            altered[r] += 100000
        second = selected(altered)
        self.assertEqual(first[0], second[0])
        np.testing.assert_array_equal(first[1], second[1])
        np.testing.assert_array_equal(first[2], second[2])

    def test_failure_is_explicit_and_not_substituted(self):
        contract = train.load(self.root / "contract.json")
        contract["training"]["tasks"]["oral"]["cells"][0]["search_space"]["model__nonexistent"] = [1]
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        with self.assertRaises(train.TrainingError):
            execute(self.root)
        state = train.load(self.root / "out" / "checkpoint.json")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["oof"], [])
        self.assertTrue(any(r["status"] == "failed" for r in state["ledger"]))

    def test_resume_and_changed_hash_rejected(self):
        first = execute(self.root)
        second = execute(self.root, resume=True)
        self.assertEqual(first, second)
        with (self.root / "development.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(train.TrainingError, "lineage mismatch"):
            execute(self.root, resume=True)

    def test_fold_group_leakage_rejected(self):
        folds = train.load(self.root / "folds.json")
        folds["tasks"]["oral"]["outer_folds"][0]["train_row_ids"].append("r0")
        train.dump(self.root / "folds.json", folds)
        rebind_fixture(self.root)
        with self.assertRaisesRegex(train.TrainingError, "boundary"):
            execute(self.root)

    def test_feature_hash_and_task_alignment_rejected(self):
        manifest = train.load(self.root / "features.json")
        manifest["families"]["synthetic"]["artifact"]["sha256"] = "0" * 64
        train.dump(self.root / "features.json", manifest)
        with self.assertRaisesRegex(train.TrainingError, "hash mismatch"):
            execute(self.root)
        manifest["families"]["synthetic"]["artifact"] = train.binding(self.root / "features.npz")
        train.dump(self.root / "features.json", manifest)
        with np.load(self.root / "features.npz") as data:
            arrays = {k: data[k].copy() for k in data.files}
        arrays["task_ids"][0] = "bad"
        np.savez(self.root / "features.npz", **arrays)
        manifest["families"]["synthetic"]["artifact"] = train.binding(self.root / "features.npz")
        train.dump(self.root / "features.json", manifest)
        with self.assertRaisesRegex(train.TrainingError, "axis mismatch"):
            execute(self.root)

    def test_resume_code_hash_and_model_tampering_rejected(self):
        state = execute(self.root)
        state["identity"]["code"]["sha256"] = "0" * 64
        train.dump(self.root / "out" / "checkpoint.json", state)
        with self.assertRaisesRegex(train.TrainingError, "hash mismatch"):
            execute(self.root, resume=True)
        state["identity"]["code"] = train.binding(train.__file__)
        train.dump(self.root / "out" / "checkpoint.json", state)
        with Path(state["artifacts"][0]["path"]).open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(train.TrainingError, "artifact hash mismatch"):
            execute(self.root, resume=True)

    def test_all_cells_are_required(self):
        contract = train.load(self.root / "contract.json")
        cell = dict(contract["training"]["tasks"]["oral"]["cells"][0])
        cell.update(candidate_id="unavailable-cell", algorithm="NotAnAlgorithm")
        contract["training"]["tasks"]["oral"]["cells"].append(cell)
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        with self.assertRaises(train.TrainingError):
            execute(self.root)
        state = train.load(self.root / "out" / "checkpoint.json")
        self.assertEqual({r["candidate_id"] for r in state["ledger"]}, {"ridge-synthetic", "unavailable-cell"})
        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["completed"])

    def test_final_fit_requires_exact_lock_and_uses_all_development(self):
        execute(self.root)
        selected = self.root / "out" / "final_selection.json"
        lock = {"decision": "CONTINUE", "selection": train.binding(selected), "tasks": {"oral": "ridge-synthetic"}}
        train.dump(self.root / "lock.json", lock)
        def fit():
            return train.final_fit(self.root / "development.csv", self.root / "folds.json", self.root / "features.json",
                                   self.root / "contract.json", selected, self.root / "lock.json", self.root / "final")
        with self.assertRaisesRegex(train.TrainingError, "requires STOP"):
            fit()
        lock["decision"] = "STOP"
        lock["selection"]["sha256"] = "0" * 64
        train.dump(self.root / "lock.json", lock)
        with self.assertRaisesRegex(train.TrainingError, "requires STOP"):
            fit()
        lock["selection"] = train.binding(selected)
        train.dump(self.root / "lock.json", lock)
        result = fit()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["fit_evidence"][0]["fit_row_ids"]), 24)

    def test_interruption_remains_incomplete_then_resumes(self):
        from unittest.mock import patch
        real = train.search_scope
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return real(*args, **kwargs)
        with patch.object(train, "search_scope", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                execute(self.root)
        state = train.load(self.root / "out" / "checkpoint.json")
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(len(state["completed"]), 1)
        finished = execute(self.root, resume=True)
        self.assertEqual(finished["status"], "complete")
        self.assertEqual(len(finished["ledger"]), 20)

    def test_split_binding_and_terminal_rows_rejected(self):
        manifest = train.load(self.root / "features.json")
        manifest.pop("split_manifest")
        train.dump(self.root / "features.json", manifest)
        with self.assertRaisesRegex(train.TrainingError, "split_manifest"):
            execute(self.root)
        rebind_fixture(self.root)
        path = self.root / "development.csv"
        path.write_text(path.read_text().replace("development", "terminal"), encoding="utf-8")
        rebind_fixture(self.root)
        with self.assertRaisesRegex(train.TrainingError, "frozen development"):
            execute(self.root)

    def test_inputs_changed_during_search_cannot_complete(self):
        from unittest.mock import patch
        original = train.search_scope
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.root / "development.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return result
        with patch.object(train, "search_scope", side_effect=change):
            with self.assertRaisesRegex(train.TrainingError, "changed during"):
                execute(self.root)
        self.assertEqual(train.load(self.root / "out" / "checkpoint.json")["status"], "failed")

    def test_packaged_preflight_is_self_contained(self):
        report = train.run_preflight(self.root / "preflight", self.root / "contract.json")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["declared_cell_checks"]), 1)


if __name__ == "__main__":
    unittest.main()
