"""Deterministic synthetic-only training validation; no scientific dataset access."""
import csv
from contextlib import closing
import importlib.util
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
    try:
        return train.run_search(root / "development.csv", root / "folds.json", root / "features.json",
                                root / "contract.json", root / name, resume)
    except train.TrainingError as error:
        checkpoint = root / name / "checkpoint.json"
        if checkpoint.is_file():
            state = train.load(checkpoint)
            failures = [{key: record.get(key) for key in ("scope", "candidate_id", "trial_id", "failure_reason")}
                        for record in state.get("ledger", []) if record.get("status") == "failed"]
            error.add_note("Synthetic checkpoint failures: " + json.dumps(failures))
        raise


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_checkpoint_commit_retries_only_transient_windows_replace_conflicts(self):
        from unittest.mock import patch
        path = self.root / "commit.json"
        train.dump(path, {"old": 1})
        original = Path.replace
        attempts = []
        error = PermissionError("synthetic Windows sharing conflict")
        error.winerror = 32
        def replace(source, destination):
            attempts.append(source)
            self.assertEqual(train.load(path), {"old": 1})
            if len(attempts) < 3:
                raise error
            return original(source, destination)
        with patch.object(Path, "replace", autospec=True, side_effect=replace), patch.object(train.time, "sleep") as sleep:
            train.dump(path, {"new": 2})
        self.assertEqual(train.load(path), {"new": 2})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(list(self.root.glob("commit.json.*.tmp")), [])

    def test_checkpoint_commit_failures_preserve_previous_evidence(self):
        from unittest.mock import patch
        path = self.root / "commit.json"
        train.dump(path, {"old": 1})
        for code, expected_attempts in ((5, 8), (33, 8), (None, 1)):
            error = PermissionError("persistent synthetic write denial")
            if code is not None:
                error.winerror = code
            with self.subTest(winerror=code), patch.object(Path, "replace", side_effect=error) as replace, \
                    patch.object(train.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    train.dump(path, {"new": 2})
                self.assertEqual(replace.call_count, expected_attempts)
                self.assertEqual(sleep.call_count, expected_attempts - 1)
            self.assertEqual(train.load(path), {"old": 1})
            self.assertEqual(list(self.root.glob("commit.json.*.tmp")), [])

    def test_checkpoint_flush_failure_never_replaces_previous_evidence(self):
        from unittest.mock import patch
        path = self.root / "commit.json"
        train.dump(path, {"old": 1})
        with patch.object(train.os, "fsync", side_effect=OSError("synthetic disk flush failure")), \
                patch.object(Path, "replace") as replace:
            with self.assertRaisesRegex(OSError, "flush failure"):
                train.dump(path, {"new": 2})
            replace.assert_not_called()
        self.assertEqual(train.load(path), {"old": 1})
        self.assertEqual(list(self.root.glob("commit.json.*.tmp")), [])

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
        # Replay metadata must not change the frozen regression scientific values.
        scientific = {"oof": result["oof"], "final_selection": {
                          task: {k: v for k, v in chosen.items() if k != "task_type"}
                          for task, chosen in result["final_selection"].items()},
                      "trials": [{k: v for k, v in r.items() if k not in {"seconds", "warnings", "nonconverged"}} for r in result["ledger"]]}
        self.assertEqual(result["final_selection"]["oral"]["task_type"], "regression")
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


class ClassificationTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(self.tmp.name, "classification")

    def tearDown(self):
        self.tmp.cleanup()

    def update_training(self, **values):
        contract = train.load(self.root / "contract.json")
        contract["training"].update(values)
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)

    def update_labels(self, labels):
        path = self.root / "development.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            if row["output_row_id"] in labels:
                row["target_class"] = labels[row["output_row_id"]]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        rebind_fixture(self.root)

    def test_explicit_binary_labels_required_before_fitting(self):
        from unittest.mock import patch
        for value in (2, -1, 0.5, "active", "NaN"):
            with self.subTest(label=value):
                self.update_labels({"r0": value})
                with patch.object(train, "pipeline") as fitting:
                    with self.assertRaises(train.TrainingError):
                        execute(self.root, "invalid-label")
                    fitting.assert_not_called()
                self.assertFalse((self.root / "invalid-label" / "checkpoint.json").exists())

    def test_single_class_development_and_fold_feasibility_fail_before_fitting(self):
        from unittest.mock import patch
        folds = train.load(self.root / "folds.json")["tasks"]["oral"]
        original = {f"r{i}": (i // 3) % 2 for i in range(24)}
        cases = [("development", folds["row_ids"]),
                 ("train_row_ids", folds["outer_folds"][0]["inner_folds"][0]["train_row_ids"]),
                 ("validation_row_ids", folds["outer_folds"][0]["inner_folds"][0]["validation_row_ids"])]
        for metric in ("mcc", "balanced_accuracy", "roc_auc", "average_precision"):
            self.update_training(metric=metric)
            for context, ids in cases:
                with self.subTest(metric=metric, context=context):
                    self.update_labels({**original, **{r: 0 for r in ids}})
                    with patch.object(train, "pipeline") as fitting:
                        with self.assertRaisesRegex(train.TrainingError, "requires both classes 0/1"):
                            execute(self.root, "single-class")
                        fitting.assert_not_called()

    def test_classification_metrics_use_probabilities_and_declared_threshold(self):
        labels = [0, 0, 1, 1]
        probabilities = [0.1, 0.8, 0.4, 0.9]
        expected = {"mcc": 0.0, "balanced_accuracy": 0.5, "roc_auc": 0.75, "average_precision": 5 / 6}
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                self.assertAlmostEqual(train.classification_score(metric, labels, probabilities, 0.5), value)
        self.assertAlmostEqual(train.classification_score("balanced_accuracy", labels, probabilities, 0.4), 0.75)
        self.assertAlmostEqual(train.classification_score("mcc", labels, probabilities, 0.4), 1 / np.sqrt(3))

    def test_probability_validation_and_positive_class_column(self):
        from types import SimpleNamespace
        X = np.ones((2, 1))
        model = SimpleNamespace(classes_=np.array([1, 0]), predict_proba=lambda X: np.array([[0.8, 0.2], [0.1, 0.9]]))
        np.testing.assert_array_equal(train.positive_probability(model, X), [0.8, 0.1])
        for probabilities in (np.array([0.8, 0.1]), np.array([[1.1, -0.1], [0.1, 0.9]]),
                              np.array([[np.nan, np.nan], [0.1, 0.9]]), np.array([[0.8, 0.8], [0.1, 0.9]])):
            with self.subTest(probabilities=probabilities):
                model.predict_proba = lambda X: probabilities
                with self.assertRaisesRegex(train.TrainingError, "invalid row-aligned probabilities"):
                    train.positive_probability(model, X)
        with self.assertRaisesRegex(train.TrainingError, "score-only"):
            train.positive_probability(SimpleNamespace(decision_function=lambda X: np.zeros(len(X))), X)
        model.classes_ = np.array([0])
        with self.assertRaisesRegex(train.TrainingError, "exactly classes 0/1"):
            train.positive_probability(model, X)

    def test_threshold_and_task_contract_validation(self):
        spec = train.load(self.root / "contract.json")["training"]
        invalid = [{"task_type": "multiclass"}, {"metric": "rmse"}, {"positive_class": 0}, {"positive_class": True},
                   {"threshold": True}, {"threshold": -0.1}, {"threshold": 1.1}, {"threshold": float("nan")},
                   {"threshold_grid": []}, {"threshold_grid": [0.5, 0.5]}, {"threshold_grid": [0.5, "0.8"]},
                   {"threshold_grid": [0.5, float("inf")]}, {"metric": "roc_auc", "threshold_grid": [0.2, 0.8]},
                   {"metric": "average_precision", "threshold_grid": [0.2, 0.8]}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(train.TrainingError):
                train.validate_training_spec({**spec, **change})
        with self.assertRaisesRegex(train.TrainingError, "require task_type=classification"):
            train.validate_training_spec({**spec, "task_type": "regression", "metric": "rmse", "threshold": 0.5})

    def test_threshold_selection_is_pooled_and_ties_use_declared_grid_order(self):
        spec = {"task_type": "classification", "metric": "balanced_accuracy", "threshold_grid": [0.35, 0.5]}
        cv = [{"fold": 0, "validation_row_ids": ["a", "b"]},
              {"fold": 1, "validation_row_ids": ["c", "d", "e", "f"]}]
        target = dict(zip("abcdef", [0, 1, 0, 0, 1, 1]))
        record = {"fold_predictions": [{"fold": 0, "prediction": [0.4, 0.45]},
                                        {"fold": 1, "prediction": [0.1, 0.3, 0.6, 0.9]}]}
        result = train.classification_summary(record, cv, target, spec)
        self.assertEqual(result["threshold"], 0.35)
        self.assertAlmostEqual(result["score"], 5 / 6)
        self.assertEqual([f["score"] for f in result["fold_scores"]], [0.5, 1.0])

    def test_operating_constraints_filter_inner_thresholds_without_changing_metrics(self):
        spec = {"task_type": "classification", "metric": "balanced_accuracy", "threshold_grid": [0.35, 0.5],
                "selection_constraints": {"min_specificity": .9}}
        cv = [{"fold": 0, "validation_row_ids": ["a", "b"]},
              {"fold": 1, "validation_row_ids": ["c", "d", "e", "f"]}]
        target = dict(zip("abcdef", [0, 1, 0, 0, 1, 1]))
        record = {"fold_predictions": [{"fold": 0, "prediction": [0.4, 0.45]},
                                       {"fold": 1, "prediction": [0.1, 0.3, 0.6, 0.9]}]}
        result = train.classification_summary(record, cv, target, spec)
        self.assertEqual(result["threshold"], .5)
        self.assertTrue(result["selection_eligible"])
        self.assertFalse(result["threshold_scores"][0]["selection_eligible"])
        spec["selection_constraints"]["min_sensitivity"] = .9
        result = train.classification_summary(record, cv, target, spec)
        self.assertFalse(result["selection_eligible"])
        self.assertTrue(np.isfinite(result["score"]))
        for invalid in ({}, {"min_sensitivity": 1.1}, {"unknown": .2}, {"min_specificity": True}):
            with self.subTest(invalid=invalid), self.assertRaises(train.TrainingError):
                train.validate_training_spec({**spec, "seed": 71, "selection_constraints": invalid})

    def test_all_primary_metrics_complete_search_and_maximize_selection(self):
        from sklearn.metrics import average_precision_score, balanced_accuracy_score, matthews_corrcoef, roc_auc_score
        functions = {"mcc": matthews_corrcoef, "balanced_accuracy": balanced_accuracy_score,
                     "roc_auc": roc_auc_score, "average_precision": average_precision_score}
        for metric in functions:
            with self.subTest(metric=metric):
                self.update_training(metric=metric)
                state = execute(self.root, metric)
                self.assertEqual(state["status"], "complete")
                self.assertEqual(len(state["oof"]), 24)
                self.assertEqual(len(state["ledger"]), 20)
                for row in state["oof"]:
                    self.assertEqual((row["task_type"], row["positive_class"], row["threshold"]), ("classification", 1, 0.5))
                    self.assertTrue(0 <= row["prediction"] <= 1)
                spec, folds, _, target, _, _ = train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                                              self.root / "features.json", self.root / "contract.json")
                for scope, chosen in state["completed"].items():
                    records = [r for r in state["ledger"] if "oral:" + r["scope"] == scope]
                    best = min(records, key=lambda r: (-r["score"], r["candidate_id"], r["trial_id"]))
                    self.assertEqual(chosen["trial_id"], best["trial_id"])
                    outer = folds["tasks"]["oral"]["outer_folds"]
                    cv = outer if scope == "oral:final-selection" else outer[int(scope.rsplit("-", 1)[1])]["inner_folds"]
                    for record in records:
                        labels = [target[r] for fold in cv for r in fold["validation_row_ids"]]
                        probabilities = np.array([p for fold in record["fold_predictions"] for p in fold["prediction"]])
                        values = probabilities >= 0.5 if metric in ("mcc", "balanced_accuracy") else probabilities
                        self.assertAlmostEqual(record["score"], functions[metric](labels, values))

    def test_builtin_classifier_fits_and_reloads_with_probabilities(self):
        self.check_classifier_fits({
            "LogisticRegression": {"model__max_iter": 500}, "SVC": {},
            "RandomForest": {"model__n_estimators": 8}, "ExtraTrees": {"model__n_estimators": 8},
            "HistGradientBoosting": {"model__max_iter": 8, "model__min_samples_leaf": 2}, "KNN": {},
            "MLP": {"model__solver": "lbfgs", "model__hidden_layer_sizes": (8,), "model__max_iter": 2000},
            "GaussianNB": {}})

    def test_optional_classifier_fits_and_reloads_with_probabilities(self):
        import importlib.util
        choices = {"XGBoost": ("xgboost", {"model__n_estimators": 8, "model__max_depth": 2}),
                   "CatBoost": ("catboost", {"model__iterations": 8, "model__depth": 2}),
                   "LightGBM": ("lightgbm", {"model__n_estimators": 8, "model__min_child_samples": 2, "model__verbosity": -1})}
        for algorithm, (package, parameters) in choices.items():
            with self.subTest(algorithm=algorithm):
                if importlib.util.find_spec(package) is None:
                    self.skipTest(package + " is not installed; no substitute estimator")
                self.check_classifier_fits({algorithm: parameters})

    def check_classifier_fits(self, algorithms):
        spec, folds, _, target, features, _ = train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                                              self.root / "features.json", self.root / "contract.json")
        fold = folds["tasks"]["oral"]["outer_folds"][0]
        axes = train.frozen_fit_axes(folds)
        for algorithm, parameters in algorithms.items():
            with self.subTest(algorithm=algorithm):
                cell = {**spec["tasks"]["oral"]["cells"][0], "algorithm": algorithm}
                model, probabilities = train.fit_predict(cell, {"select__k": "all", **parameters}, fold["train_row_ids"],
                    fold["validation_row_ids"], target, features, spec["seed"], [], {}, axes, task_spec=spec)
                self.assertEqual(model.task_type_, "classification")
                self.assertEqual(model.positive_class_, 1)
                self.assertEqual(model.threshold_, 0.5)
                self.assertIs(model.named_steps["select"].score_func, train.f_classif)
                path = self.root / (algorithm + ".joblib")
                train.joblib.dump(model, path)
                reloaded = train.joblib.load(path)
                X, index = features[cell["family"]]
                np.testing.assert_array_equal(train.positive_probability(reloaded, X[[index[r] for r in fold["validation_row_ids"]]]), probabilities)

    def test_svc_cannot_disable_training_only_probability_calibration(self):
        cell = train.load(self.root / "contract.json")["training"]["tasks"]["oral"]["cells"][0]
        cell["algorithm"] = "SVC"
        with self.assertRaisesRegex(train.TrainingError, "score-only"):
            train.pipeline(cell, {"select__k": "all", "model__probability": False}, 71, "classification")

    def test_outer_model_replay_and_inner_only_threshold_selection(self):
        self.update_training(threshold_grid=[0.2, 0.5, 0.8])
        state = execute(self.root)
        spec, folds, _, target, features, _ = train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                                              self.root / "features.json", self.root / "contract.json")
        axes = train.frozen_fit_axes(folds)
        for fold in folds["tasks"]["oral"]["outer_folds"]:
            scope = "outer-" + str(fold["fold"])
            chosen = state["completed"]["oral:" + scope]
            path = self.root / "out" / "outer_models" / (train.digest(["oral", scope]) + ".joblib")
            model = train.joblib.load(path)
            X, index = features[chosen["family"]]
            predictions = train.positive_probability(model, X[[index[r] for r in fold["validation_row_ids"]]])
            rows = [r for r in state["oof"] if r["outer_fold"] == fold["fold"]]
            self.assertEqual([r["output_row_id"] for r in rows], fold["validation_row_ids"])
            np.testing.assert_array_equal(predictions, [r["prediction"] for r in rows])
            self.assertEqual({r["threshold"] for r in rows}, {model.threshold_, chosen["threshold"]})
            altered = {r: 1 - value if r in fold["validation_row_ids"] else value for r, value in target.items()}
            selected = train.search_scope("oral", scope, spec["tasks"]["oral"]["cells"], fold["inner_folds"],
                                           altered, features, spec, [], [], axes)
            self.assertEqual(chosen, selected)
            _, same = train.fit_predict(selected, selected["parameters"], fold["train_row_ids"], fold["validation_row_ids"],
                                        altered, features, spec["seed"], [], {}, axes)
            np.testing.assert_array_equal(predictions, same)

    def test_classification_row_alignment_and_completed_resume(self):
        first = execute(self.root)
        self.assertEqual(first, execute(self.root, resume=True))
        with np.load(self.root / "features.npz") as data:
            arrays = {k: data[k].copy() for k in data.files}
        for key in ("X", "row_ids", "task_ids"):
            arrays[key] = arrays[key][::-1]
        np.savez(self.root / "features.npz", **arrays)
        manifest = train.load(self.root / "features.json")
        manifest["families"]["synthetic"]["artifact"] = train.binding(self.root / "features.npz")
        train.dump(self.root / "features.json", manifest)
        self.assertEqual(first["oof"], execute(self.root, "reordered")["oof"])

    def test_resume_rejects_threshold_probability_and_metric_tampering_without_writes(self):
        import copy
        self.update_training(threshold_grid=[0.2, 0.5, 0.8])
        original = execute(self.root)
        mutations = [lambda s: s["ledger"][0].update(score=0.123456),
                     lambda s: s["ledger"][0].update(threshold=0.123456),
                     lambda s: s["ledger"][0]["threshold_scores"][0].update(score=0.123456),
                     lambda s: s["ledger"][0]["fold_predictions"][0]["prediction"].pop(),
                     lambda s: s["ledger"][0].update(positive_class=0),
                     lambda s: s["completed"]["oral:outer-0"].update(threshold=0.123456),
                     lambda s: s["final_selection"]["oral"].update(task_type="regression"),
                     lambda s: s["oof"][0].update(threshold=0.123456),
                     lambda s: s["oof"][0].update(prediction=1.2),
                     lambda s: s["oof"][0].update(task_type="regression")]
        path = self.root / "out" / "checkpoint.json"
        for mutate in mutations:
            state = copy.deepcopy(original)
            mutate(state)
            train.dump(path, state)
            before = path.read_bytes()
            with self.subTest(mutation=mutate), self.assertRaises(train.TrainingError):
                execute(self.root, resume=True)
            self.assertEqual(path.read_bytes(), before)

    def test_fold_progress_is_durable_and_interrupted_scope_is_audited_on_retry(self):
        import copy
        from unittest.mock import patch
        original = train.fit_predict
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                state = train.load(self.root / "out" / "checkpoint.json")
                self.assertEqual(state["status"], "incomplete")
                self.assertEqual(len(state["ledger"]), 1)
                self.assertEqual(state["ledger"][0]["status"], "incomplete")
                self.assertEqual(len(state["ledger"][0]["fold_scores"]), 1)
                self.assertEqual(len(state["fit_evidence"]), 1)
                self.assertFalse(state["completed"])
                raise KeyboardInterrupt()
            return original(*args, **kwargs)
        with patch.object(train, "fit_predict", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                execute(self.root)
        interrupted_state = train.load(self.root / "out" / "checkpoint.json")
        checkpoint = self.root / "out" / "checkpoint.json"
        for mutate in (lambda s: s["ledger"][0].update(threshold=0.25),
                       lambda s: s["ledger"][0]["fold_predictions"][0]["prediction"].pop(),
                       lambda s: s["fit_evidence"].clear()):
            corrupted = copy.deepcopy(interrupted_state)
            mutate(corrupted)
            train.dump(checkpoint, corrupted)
            before = checkpoint.read_bytes()
            with self.subTest(mutation=mutate), self.assertRaises(train.TrainingError):
                execute(self.root, resume=True)
            self.assertEqual(checkpoint.read_bytes(), before)
        train.dump(checkpoint, interrupted_state)
        result = execute(self.root, resume=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["attempt_history"][0]["ledger"], interrupted_state["ledger"])
        self.assertEqual(result["attempt_history"][0]["fit_evidence"], interrupted_state["fit_evidence"])
        coverage = train.load(self.root / "out" / "search_coverage.json")
        self.assertEqual(coverage["completed_trials"], 20)
        self.assertEqual(coverage["attempted_trials"], 21)
        self.assertEqual(coverage["failed_attempts"], 0)

    def test_complete_trial_is_checkpointed_before_next_trial_fit(self):
        from unittest.mock import patch
        original = train.fit_predict
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                durable = train.load(self.root / "out" / "checkpoint.json")
                self.assertEqual(durable["ledger"][0]["status"], "complete")
                self.assertEqual(len(durable["ledger"][0]["fold_scores"]), 2)
                self.assertEqual(len(durable["fit_evidence"]), 2)
                self.assertFalse(durable["completed"])
                raise KeyboardInterrupt()
            return original(*args, **kwargs)
        with patch.object(train, "fit_predict", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                execute(self.root)
        self.assertEqual(execute(self.root, resume=True)["status"], "complete")

    def test_final_fit_replays_locked_threshold_and_rejects_invalid_metadata(self):
        self.update_training(threshold=0.65)
        execute(self.root)
        selected_path = self.root / "out" / "final_selection.json"
        selected = train.load(selected_path)
        lock_path = self.root / "lock.json"
        def locked_fit(name):
            train.dump(lock_path, {"decision": "STOP", "selection": train.binding(selected_path),
                                   "tasks": {"oral": "logistic-synthetic"}})
            return train.final_fit(self.root / "development.csv", self.root / "folds.json", self.root / "features.json",
                                   self.root / "contract.json", selected_path, lock_path, self.root / name)
        for change in ({"threshold": 0.5}, {"positive_class": 0}, {"task_type": "regression"}):
            altered = json.loads(json.dumps(selected))
            altered["tasks"]["oral"].update(change)
            train.dump(selected_path, altered)
            with self.subTest(change=change), self.assertRaisesRegex(train.TrainingError, "Locked"):
                locked_fit("bad-" + next(iter(change)))
        train.dump(selected_path, selected)
        report = locked_fit("final")
        model = train.joblib.load(report["models"]["oral"]["artifact"]["path"])
        self.assertEqual((model.task_type_, model.positive_class_, model.threshold_), ("classification", 1, 0.65))
        self.assertEqual(report["models"]["oral"]["threshold"], 0.65)
        _, folds, _, _, features, _ = train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                                      self.root / "features.json", self.root / "contract.json")
        ids = folds["tasks"]["oral"]["row_ids"]
        X, index = features["synthetic"]
        predictions = train.positive_probability(model, X[[index[r] for r in ids]])
        self.assertEqual(len(predictions), len(ids))
        self.assertEqual(model.named_steps["audit"].fit_row_ids_, ids)

    def test_classification_preflight_uses_binary_synthetic_paths(self):
        report = train.run_preflight(self.root / "preflight", self.root / "contract.json")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["task_type"], "classification")
        state = train.load(report["fixture_checkpoint"]["path"])
        self.assertEqual(state["final_selection"]["oral"]["task_type"], "classification")


class ChemicalTrainingTests(unittest.TestCase):
    parents = ["C", "CC", "CCC", "CCCC", "CCO", "CCN", "CO", "CN", "CCCl", "CCBr", "CCF", "COC",
               "CCOC", "CCCO", "CCCN", "CCS", "c1ccccc1", "C1CCCCC1", "c1ccncc1", "C1CCCC1",
               "CC(=O)O", "CC(=O)N", "CC(C)O", "CC(C)N"]

    def setUp(self):
        import importlib.util
        if importlib.util.find_spec("rdkit") is None:
            self.skipTest("Real chemical training requires RDKit; no substitute chemistry")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture(self.tmp.name)
        self.write_parents()

    def write_parents(self):
        path = self.root / "development.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row, smiles in zip(rows, self.parents):
            row["parent_smiles"] = smiles
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        rebind_fixture(self.root)

    def configure_rule(self, classification=False, domain=False):
        if classification:
            fixture(self.root, "classification")
            self.write_parents()
        contract = train.load(self.root / "contract.json")
        regularizer = "model__C" if classification else "model__alpha"
        cell = {"candidate_id": "chemical-rules", "family": "SMILES", "feature_route": "smiles",
                "algorithm": "ChemicalRuleClassifier" if classification else "ChemicalRuleRegressor",
                "preprocessing": {}, "rule": {"descriptor_names": ["MolWt", "MolLogP"]},
                "search_space": {"model__n_bins": [2, 3], regularizer: [0.1, 1.0]},
                "n_trials": 4, "anchor": {"model__n_bins": 3, regularizer: 1.0}}
        contract["training"]["tasks"]["oral"]["cells"] = [cell]
        if classification:
            contract["training"]["threshold_grid"] = [0.25, 0.5, 0.75]
        if domain:
            contract["training"]["applicability_domain"] = self.domain_recipe()
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        return cell

    @staticmethod
    def domain_recipe():
        return {"radius": 2, "n_bits": 128, "use_chirality": True, "similarity": "max", "min_references": 1,
                "cutoff": {"method": "training_quantile", "quantile": 0.1}}

    def inputs(self):
        return train.read_inputs(self.root / "development.csv", self.root / "folds.json",
                                 self.root / "features.json", self.root / "contract.json")

    def locked_final_fit(self, state):
        selection_path = self.root / "out" / "final_selection.json"
        lock_path = self.root / "lock.json"
        train.dump(lock_path, {"decision": "STOP", "selection": train.binding(selection_path),
                               "tasks": {t: c["candidate_id"] for t, c in state["final_selection"].items()}})
        return train.final_fit(self.root / "development.csv", self.root / "folds.json", self.root / "features.json",
                               self.root / "contract.json", selection_path, lock_path, self.root / "final")

    def test_nested_rules_replay_and_bind_model_sources_without_numeric_features(self):
        self.configure_rule()
        state = execute(self.root)
        self.assertEqual(state["status"], "complete")
        spec, folds, rows, target, features, identity = self.inputs()
        self.assertEqual(set(features), {"SMILES"})
        X, index = features["SMILES"]
        self.assertEqual(X.shape, (24,))
        self.assertEqual(X.tolist(), self.parents)
        self.assertEqual({Path(b["path"]).name for b in identity["source_hashes"]},
                         {"qsar_models.py", "qsar_domain.py", "qsar_calibration.py", "qsar_search.py"})
        self.assertIn("rdkit", identity["environment"])
        for fold in folds["tasks"]["oral"]["outer_folds"]:
            scope = "outer-" + str(fold["fold"])
            model_path = self.root / "out" / "outer_models" / (train.digest(["oral", scope]) + ".joblib")
            model = train.joblib.load(model_path)
            self.assertIsInstance(model.named_steps["model"], train.qsar_models.ChemicalRuleRegressor)
            self.assertEqual(model.feature_route_, "smiles")
            self.assertEqual(model.fit_training_row_ids_, fold["train_row_ids"])
            self.assertNotIn("select", model.named_steps)
            predictions = model.predict(X[[index[r] for r in fold["validation_row_ids"]]])
            saved = [r["prediction"] for r in state["oof"] if r["outer_fold"] == fold["fold"]]
            np.testing.assert_array_equal(predictions, saved)
        self.assertEqual(execute(self.root, resume=True), state)
        report = self.locked_final_fit(state)
        final = train.joblib.load(report["models"]["oral"]["artifact"]["path"])
        self.assertEqual(final.fit_training_row_ids_, folds["tasks"]["oral"]["row_ids"])
        self.assertEqual(report["models"]["oral"]["feature_names"], ["parent_smiles"])
        self.assertEqual(report["models"]["oral"]["feature_route"], "smiles")
        self.assertTrue(np.isfinite(final.predict(X)).all())
        changed = json.loads(json.dumps(identity))
        changed["source_hashes"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(train.TrainingError, "hash changed"):
            train.verify_identity(changed)

    def test_rule_vocabulary_bins_and_weights_are_training_only(self):
        cell = self.configure_rule()
        spec, folds, _, target, features, _ = self.inputs()
        train_ids = ["r" + str(i) for i in range(16)]
        valid_ids = ["r" + str(i) for i in range(16, 24)]
        axes = {}
        train.register_axis(axes, train_ids)
        train.register_axis(axes, valid_ids)
        evidence = []
        model, first = train.fit_predict(cell, cell["anchor"], train_ids, valid_ids, target, features, 71,
                                         evidence, {}, axes, task_spec=spec)
        rule = model.named_steps["model"]
        self.assertEqual(rule.scaffold_vocabulary_, {})
        self.assertEqual(rule.basis_.n_fit_samples_, 16)
        self.assertEqual(evidence[0]["learned_steps"], ["chemical_basis", "model"])
        changed = {r: value + 100000 if r in valid_ids else value for r, value in target.items()}
        second, prediction = train.fit_predict(cell, cell["anchor"], train_ids, valid_ids, changed, features, 71,
                                               [], {}, axes, task_spec=spec)
        np.testing.assert_array_equal(rule.coef_, second.named_steps["model"].coef_)
        np.testing.assert_array_equal(first, prediction)
        self.assertEqual(rule.descriptor_ranges_, second.named_steps["model"].descriptor_ranges_)
        for name, edges in rule.descriptor_bin_edges_.items():
            np.testing.assert_array_equal(edges, second.named_steps["model"].descriptor_bin_edges_[name])

    def test_rule_classifier_outer_threshold_manifest_and_final_probability_replay(self):
        self.configure_rule(classification=True)
        state = execute(self.root)
        manifest = train.load(self.root / "out" / "selection_procedure_oof_manifest.json")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["predictions"], train.binding(self.root / "out" / "selection_procedure_oof.csv"))
        self.assertEqual(manifest["inputs"], state["identity"]["inputs"])
        task = manifest["tasks"]["oral"]
        self.assertEqual((task["task_type"], task["positive_class"]), ("classification", 1))
        for fold in task["outer_folds"]:
            chosen = state["completed"]["oral:outer-" + str(fold["fold"])]
            self.assertEqual(fold["threshold"], chosen["threshold"])
            self.assertEqual(fold["model"], train.binding(fold["model"]["path"]))
            self.assertEqual(fold["threshold_provenance"]["source"], "inner_cv")
            model = train.joblib.load(fold["model"]["path"])
            self.assertEqual((model.task_type_, model.positive_class_, model.threshold_), ("classification", 1, chosen["threshold"]))
            self.assertEqual(model.named_steps["model"].threshold_, chosen["threshold"])
        report = self.locked_final_fit(state)
        model = train.joblib.load(report["models"]["oral"]["artifact"]["path"])
        X, _ = self.inputs()[4]["SMILES"]
        self.assertEqual(len(train.positive_probability(model, X)), 24)
        np.testing.assert_array_equal(model.predict(X), (train.positive_probability(model, X) >= model.threshold_).astype(int))

    def test_rule_specific_contract_and_missing_fit_evidence_cannot_bypass_resume(self):
        import copy
        cell = self.configure_rule()
        invalid = copy.deepcopy(cell)
        invalid["search_space"]["select__k"] = [1, "all"]
        with self.assertRaisesRegex(train.TrainingError, "not numeric select__k"):
            train.trials(invalid, 71)
        invalid = copy.deepcopy(cell)
        del invalid["search_space"]["model__n_bins"]
        with self.assertRaisesRegex(train.TrainingError, "n_bins and regularization"):
            train.trials(invalid, 71)
        invalid = copy.deepcopy(cell)
        del invalid["feature_route"]
        with self.assertRaisesRegex(train.TrainingError, "explicit feature_route"):
            train.trials(invalid, 71)
        original = execute(self.root)
        for field in ("chemical_basis_sha256", "scoring_method", "feature_route", "learned_steps", "selected_feature_count"):
            state = copy.deepcopy(original)
            state["fit_evidence"][0].pop(field)
            checkpoint = self.root / "out" / "checkpoint.json"
            train.dump(checkpoint, state)
            before = checkpoint.read_bytes()
            with self.subTest(field=field), self.assertRaisesRegex(train.TrainingError, "Completed scope"):
                execute(self.root, resume=True)
            self.assertEqual(checkpoint.read_bytes(), before)

    def test_domain_is_fitted_only_to_outer_or_final_training_ids_for_both_routes(self):
        self.configure_rule(domain=True)
        state = execute(self.root)
        folds = train.load(self.root / "folds.json")["tasks"]["oral"]
        for fold in folds["outer_folds"]:
            path = self.root / "out" / "outer_models" / (train.digest(["oral", "outer-" + str(fold["fold"])]) + ".joblib")
            model = train.joblib.load(path)
            domain = model.applicability_domain_
            self.assertEqual(domain.training_row_ids, fold["train_row_ids"])
            self.assertFalse(set(domain.training_row_ids) & set(fold["validation_row_ids"]))
            self.assertEqual(model.applicability_domain_sha256_, domain.to_dict()["artifact_sha256"])
        inner = [r for r in state["fit_evidence"] if r.get("phase") != "outer-refit"]
        self.assertTrue(all("applicability_domain" not in r for r in inner))
        final = self.locked_final_fit(state)
        model = train.joblib.load(final["models"]["oral"]["artifact"]["path"])
        self.assertEqual(model.applicability_domain_.training_row_ids, folds["row_ids"])
        self.assertEqual(final["models"]["oral"]["applicability_domain"]["artifact_sha256"], model.applicability_domain_sha256_)
        numeric = fixture(self.root / "numeric")
        with (numeric / "development.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row, smiles in zip(rows, self.parents):
            row["parent_smiles"] = smiles
        with (numeric / "development.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        contract = train.load(numeric / "contract.json")
        contract["training"]["applicability_domain"] = self.domain_recipe()
        train.dump(numeric / "contract.json", contract)
        rebind_fixture(numeric)
        result = execute(numeric)
        model = train.joblib.load(result["artifacts"][0]["path"])
        self.assertEqual(model.feature_route_, "numeric")
        self.assertEqual(model.applicability_domain_.training_row_ids, model.fit_training_row_ids_)

    def configure_encoder(self, checkpoint, inventory):
        contract = train.load(self.root / "contract.json")
        cell = {"candidate_id": "frozen-local", "family": "SMILES", "feature_route": "smiles",
                "algorithm": "FrozenSmilesEncoder", "head": {"algorithm": "Ridge"},
                "encoder": {"checkpoint_dir": str(checkpoint), "checkpoint_inventory": inventory,
                            "pooling": "mean", "max_length": 32, "batch_size": 8, "device": "cpu"},
                "preprocessing": {"scale": True, "variance_threshold": 0.0},
                "search_space": {"model__alpha": [1.0], "select__k": [2, "all"]},
                "anchor": {"model__alpha": 1.0, "select__k": "all"}, "n_trials": 1}
        contract["training"]["tasks"]["oral"]["cells"] = [cell]
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        return cell

    def test_encoder_inventory_and_missing_dependencies_fail_without_substitute_predictions(self):
        from unittest.mock import patch
        checkpoint = self.root / "invalid-checkpoint"
        checkpoint.mkdir()
        config = checkpoint / "config.json"
        config.write_text('{"failure_fixture_only": true}', encoding="utf-8")
        inventory = {"config.json": train.binding(config)["sha256"]}
        self.configure_encoder(checkpoint, inventory)
        original_version = train.importlib.metadata.version
        optional = {"torch", "transformers", "tokenizers", "huggingface-hub", "safetensors"}
        def version(name):
            return "failure-fixture-only" if name in optional else original_version(name)
        with patch.object(train.importlib.metadata, "version", side_effect=version):
            _, _, _, _, _, identity = self.inputs()
            self.assertEqual(identity["checkpoints"][0]["checkpoint_inventory"], inventory)
            self.assertIn(train.binding(config), identity["inputs"])
            (checkpoint / "extra.txt").write_text("unpinned", encoding="utf-8")
            with self.assertRaisesRegex(train.qsar_models.ChemicalModelError, "inventory mismatch"):
                train.verify_identity(identity)
            (checkpoint / "extra.txt").unlink()
            with patch.object(train.qsar_models, "_encoder_dependencies", side_effect=train.qsar_models.ModelDependencyError("missing real encoder dependency")):
                with self.assertRaisesRegex(train.TrainingError, "failed trials"):
                    execute(self.root)
            state = train.load(self.root / "out" / "checkpoint.json")
            self.assertEqual(state["status"], "failed")
            self.assertFalse(state["completed"])
            self.assertFalse(state["oof"])
            self.assertTrue(all("ModelDependencyError" in r["failure_reason"] for r in state["ledger"]))
            config.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(train.qsar_models.ChemicalModelError, "inventory mismatch"):
                self.inputs()
        def absent(name):
            if name == "torch":
                raise train.importlib.metadata.PackageNotFoundError(name)
            return version(name)
        inventory = {"config.json": train.binding(config)["sha256"]}
        self.configure_encoder(checkpoint, inventory)
        with patch.object(train.importlib.metadata, "version", side_effect=absent):
            with self.assertRaisesRegex(train.TrainingError, "dependency unavailable: torch"):
                self.inputs()

    def test_label_free_encoder_delegates_no_y_to_actual_feature_stage(self):
        from unittest.mock import patch
        encoder = train.LabelFreeFrozenSmilesEncoder("unused", {"unused": "0" * 64}, "mean")
        with patch.object(train.qsar_models.FrozenSmilesEncoder, "fit", autospec=True, return_value=encoder) as fit:
            encoder.fit(["CC", "CO"], [123456, -99999])
            fit.assert_called_once_with(encoder, ["CC", "CO"])

    def test_real_local_encoder_nested_head_and_final_replay(self):
        try:
            import torch
            from tokenizers import Tokenizer
            from tokenizers.models import WordPiece
            from tokenizers.pre_tokenizers import Whitespace
            from tokenizers.processors import TemplateProcessing
            from transformers import BertConfig, BertModel, PreTrainedTokenizerFast
        except ImportError as error:
            self.skipTest("Optional real local encoder mechanics require torch/transformers/tokenizers: " + str(error))
        checkpoint = self.root / "random-mechanics-only-checkpoint"
        checkpoint.mkdir()
        vocabulary = {token: i for i, token in enumerate(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "C", "O", "N"])}
        backend = Tokenizer(WordPiece(vocab=vocabulary, unk_token="[UNK]"))
        backend.pre_tokenizer = Whitespace()
        backend.post_processor = TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", pad_token="[PAD]",
                                            cls_token="[CLS]", sep_token="[SEP]", model_max_length=32)
        tokenizer.save_pretrained(checkpoint)
        config = BertConfig(vocab_size=len(vocabulary), hidden_size=8, num_hidden_layers=1, num_attention_heads=2,
                            intermediate_size=16, max_position_embeddings=32, pad_token_id=0)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(91)
            BertModel(config).save_pretrained(checkpoint, safe_serialization=True)
        inventory = {p.relative_to(checkpoint).as_posix(): train.binding(p)["sha256"] for p in checkpoint.rglob("*") if p.is_file()}
        self.configure_encoder(checkpoint, inventory)
        state = execute(self.root)
        self.assertEqual(state["status"], "complete")
        for evidence in state["fit_evidence"]:
            self.assertEqual(evidence["encoder"]["truncation"], "forbidden")
            self.assertEqual(evidence["encoder"]["pooling"], "mean")
            self.assertFalse(evidence["encoder"]["encoder_fine_tuned"])
        self.assertEqual(execute(self.root, resume=True), state)
        report = self.locked_final_fit(state)
        model = train.joblib.load(report["models"]["oral"]["artifact"]["path"])
        self.assertIsInstance(model.named_steps["encoder"], train.qsar_models.FrozenSmilesEncoder)
        self.assertIsInstance(model.named_steps["model"], train.Ridge)
        self.assertTrue(all(not p.requires_grad for p in model.named_steps["encoder"].encoder_.parameters()))
        X, _ = self.inputs()[4]["SMILES"]
        before = model.predict(X)
        replay_path = self.root / "encoder-replay.joblib"
        train.joblib.dump(model, replay_path)
        np.testing.assert_array_equal(before, train.joblib.load(replay_path).predict(X))
        config_path = checkpoint / "config.json"
        config_path.write_bytes(config_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(train.qsar_models.ChemicalModelError, "inventory mismatch"):
            model.predict(X)

    def test_rule_contract_preflight_uses_real_smiles_and_fitted_basis(self):
        self.configure_rule()
        report = train.run_preflight(self.root / "preflight", self.root / "contract.json")
        check = report["declared_cell_checks"][0]
        self.assertEqual(check["status"], "complete")
        self.assertTrue(check["fit_evidence"])
        self.assertTrue(all(r["learned_steps"] == ["chemical_basis", "model"] for r in check["fit_evidence"]))


class TrainingControlsTests(unittest.TestCase):
    """Exercise real synthetic pipeline paths, never scientific performance."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(self.tmp.name, "classification")

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, cell_options=None, **options):
        contract = train.load(self.root / "contract.json")
        spec = contract["training"]
        spec.update(options)
        cell = spec["tasks"]["oral"]["cells"][0]
        cell["n_trials"] = 2
        cell.update(cell_options or {})
        train.dump(self.root / "contract.json", contract)
        rebind_fixture(self.root)
        return spec, cell

    def expanded_groups(self):
        rng = np.random.default_rng(225)
        X = rng.normal(size=(96, 5))
        y = np.tile([0, 0, 0, 1], 24)
        X[:, 0] += y * 0.5
        X[::9, 1] = np.nan
        ids = ["expanded-" + str(i) for i in range(96)]
        rows = [{"output_row_id": r, "task_id": "oral", "group_id": "molecule-" + str(i // 4),
                 "partition": "development", "parent_smiles": "C", "target_class": int(y[i])}
                for i, r in enumerate(ids)]
        with (self.root / "development.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        def partitions(members, count):
            groups = list(dict.fromkeys(ids.index(r) // 4 for r in members))
            return [{"fold": k, "train_row_ids": [r for r in members if groups.index(ids.index(r) // 4) % count != k],
                     "validation_row_ids": [r for r in members if groups.index(ids.index(r) // 4) % count == k]}
                    for k in range(count)]
        outer = partitions(ids, 3)
        for fold in outer:
            fold["inner_folds"] = partitions(fold["train_row_ids"], 2)
        train.dump(self.root / "folds.json", {"schema_version": "1.0", "tasks": {"oral": {"row_ids": ids, "outer_folds": outer}}})
        np.savez(self.root / "features.npz", X=X, row_ids=np.asarray(ids), task_ids=np.asarray(["oral"] * 96),
                 feature_names=np.asarray(["f" + str(i) for i in range(5)]))
        manifest = train.load(self.root / "features.json")
        manifest["families"]["synthetic"].update(artifact=train.binding(self.root / "features.npz"), shape=[96, 5],
                                                 nonfinite_count=int(np.isnan(X).sum()))
        train.dump(self.root / "features.json", manifest)
        split = train.load(self.root / "split_manifest.json")
        split["development_rows"] = 96
        train.dump(self.root / "split_manifest.json", split)
        rebind_fixture(self.root)

    def inputs(self):
        return train.read_inputs(*(self.root / name for name in ("development.csv", "folds.json", "features.json", "contract.json")))

    def final(self, state, name="final", resume=False):
        selected = self.root / "out" / "final_selection.json"
        lock = self.root / "lock.json"
        train.dump(lock, {"decision": "STOP", "selection": train.binding(selected),
                          "tasks": {task: chosen["candidate_id"] for task, chosen in state["final_selection"].items()}})
        return train.final_fit(*(self.root / n for n in ("development.csv", "folds.json", "features.json", "contract.json")),
                               selected, lock, self.root / name, resume=resume)

    def test_grouped_calibration_is_nested_and_threshold_uses_only_scope_inner_oof(self):
        self.expanded_groups()
        self.configure({"calibration": {"method": "sigmoid", "n_splits": 2}}, threshold_grid=[0.2, 0.5, 0.8])
        spec, folds, rows, target, features, _ = self.inputs()
        cell = spec["tasks"]["oral"]["cells"][0]
        outer = folds["tasks"]["oral"]["outer_folds"][0]
        axes = train.frozen_fit_axes(folds)
        def select(labels):
            ledger, evidence = [], []
            chosen = train.search_scope("oral", "outer-0", [cell], outer["inner_folds"], labels, features,
                                        spec, ledger, evidence, axes)
            return chosen, ledger, evidence
        first = select(target)
        altered = {r: (1 - value if r in outer["validation_row_ids"] else value) for r, value in target.items()}
        second = select(altered)
        self.assertEqual(first[0], second[0])
        self.assertEqual([r["fold_predictions"] for r in first[1]], [r["fold_predictions"] for r in second[1]])
        self.assertEqual(first[2], second[2])
        X, index = features["synthetic"]
        for fit in first[2]:
            train_ids = axes[fit["fit_axis_id"]]["row_ids"]
            self.assertEqual(fit["calibration"]["calibrator_row_ids"], train_ids)
            self.assertTrue(set(train_ids).isdisjoint(outer["validation_row_ids"]))
            for clone in fit["calibration"]["folds"]:
                self.assertTrue(clone["independent_pipeline_clone"])
                self.assertFalse(set(clone["train_groups"]) & set(clone["validation_groups"]))
                expected = np.nanmedian(X[[index[r] for r in clone["train_row_ids"]]], axis=0)
                np.testing.assert_allclose(clone["preprocessing"]["impute"]["statistics_"], expected)
        state = execute(self.root)
        self.assertEqual(state["status"], "complete")
        for artifact in state["artifacts"]:
            model = train.joblib.load(artifact["path"])
            self.assertFalse(hasattr(model, "calibration_estimators_"))
            self.assertEqual((model.task_type_, model.positive_class_), ("classification", 1))
        self.assertEqual(execute(self.root, resume=True), state)

    def test_svc_explicit_calibration_disables_native_probability_and_rejects_override(self):
        spec, cell = self.configure({"algorithm": "SVC", "calibration": {"method": "isotonic", "n_splits": 2}})
        calibrated = train.pipeline(cell, cell["anchor"], spec["seed"], "classification")
        self.assertFalse(calibrated.base_estimator.named_steps["model"].probability)
        with self.assertRaisesRegex(train.TrainingError, "probability=False"):
            train.pipeline(cell, {**cell["anchor"], "model__probability": True}, spec["seed"], "classification")
        without = {key: value for key, value in cell.items() if key != "calibration"}
        self.assertTrue(train.pipeline(without, without["anchor"], spec["seed"], "classification").named_steps["model"].probability)
        state = execute(self.root)
        self.assertEqual(state["status"], "complete")
        self.assertTrue(all(r["calibration"]["method"] == "isotonic" for r in state["fit_evidence"]))

    def test_requested_optional_dependencies_fail_operationally_without_fallback(self):
        from unittest.mock import patch
        spec, cell = self.configure({"sampling": {"method": "smote", "k_neighbors": 1}})
        with patch.dict(sys.modules, {"imblearn": None, "imblearn.pipeline": None, "imblearn.over_sampling": None}):
            with self.assertRaisesRegex(train.qsar_calibration.CalibrationDependencyError, "no fallback"):
                train.pipeline(cell, cell["anchor"], spec["seed"], "classification")
        self.configure({"sampling": None, "backend": "optuna"})
        with patch.dict(sys.modules, {"optuna": None}):
            with self.assertRaises(train.qsar_search.SearchDependencyError):
                execute(self.root)
        for options in ({"resources": {"threads": 0}}, {"resources": {"concurrent_fits": 0}},
                        {"resources": {"deadline": "2030-01-01T00:00:00"}}):
            with self.subTest(options=options), self.assertRaises((train.TrainingError, train.qsar_search.SearchError)):
                train.validate_training_spec({**spec, **options})

    @unittest.skipUnless(importlib.util.find_spec("imblearn"), "Real optional imbalanced-learn is not installed")
    def test_real_smote_executes_after_original_audit_and_only_in_training(self):
        from imblearn.over_sampling import SMOTE
        from unittest.mock import patch
        self.expanded_groups()
        self.configure({"calibration": {"method": "sigmoid", "n_splits": 2},
                        "sampling": {"method": "smote", "k_neighbors": 1}})
        sampled, audits = [], []
        original_sample, original_audit = SMOTE.fit_resample, train.FitAudit.fit
        def sample(model, X, y, **kwargs):
            self.assertTrue(np.isfinite(X).all())
            result = original_sample(model, X, y, **kwargs)
            sampled.append((len(y), len(result[1])))
            return result
        def audit(model, X, y=None, row_ids=None):
            audits.append(list(row_ids))
            return original_audit(model, X, y, row_ids=row_ids)
        with patch.object(SMOTE, "fit_resample", new=sample), patch.object(train.FitAudit, "fit", new=audit):
            state = execute(self.root)
        self.assertEqual(len(audits), len(sampled))
        self.assertTrue(all(before < after for before, after in sampled))
        self.assertEqual([len(ids) for ids in audits], [before for before, _ in sampled])
        for fit in state["fit_evidence"]:
            sampler = fit["sampling"][0]
            self.assertIsNone(sampler["synthetic_row_ids"])
            self.assertIsNone(sampler["synthetic_parent_row_ids"])
            self.assertIn(sampler["original_fit_row_ids"], audits)
        final = self.final(state)
        model = train.joblib.load(final["models"]["oral"]["artifact"]["path"])
        X, _ = self.inputs()[4]["synthetic"]
        with patch.object(SMOTE, "fit_resample", side_effect=AssertionError("Replay resampled")):
            self.assertEqual(model.predict_proba(X[:3]).shape, (3, 2))

    def test_expired_deadline_never_starts_search_and_preserves_incomplete_state(self):
        from unittest.mock import patch
        self.configure(resources={"threads": 1, "concurrent_fits": 1, "deadline": "2000-01-01T00:00:00+00:00"})
        with patch.object(train, "pipeline", side_effect=AssertionError("Expired deadline started a fit")):
            state = execute(self.root)
            resumed = execute(self.root, resume=True)
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(resumed["status"], "incomplete")
        self.assertEqual(state["ledger"], [])
        self.assertEqual(state["completed"], {})
        self.assertIn("deadline", state["failure_reason"])
        self.assertEqual(train.load(self.root / "out" / "search_coverage.json")["completed_trials"], 0)

    def test_budget_checks_each_calibration_clone_and_retains_partial_audits(self):
        from unittest.mock import patch
        self.expanded_groups()
        self.configure({"calibration": {"method": "sigmoid", "n_splits": 2}})
        original = train.check_deadline
        def stop(spec, cell=None, context=None):
            if context == {"phase": "calibration-fold", "calibration_fold": 1}:
                raise train.TrainingBudgetExhausted("synthetic fit-start deadline")
            return original(spec, cell, context)
        with patch.object(train, "check_deadline", side_effect=stop):
            partial = execute(self.root)
        self.assertEqual(partial["status"], "incomplete")
        record = partial["ledger"][0]
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(len(record["partial_calibration"]["folds"]), 1)
        self.assertEqual(record["fold_scores"], [])
        resumed = execute(self.root, resume=True)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["attempt_history"][0]["ledger"][0], record)

    def test_thread_limits_apply_to_estimator_and_actual_fit_pool_with_sequential_ceiling(self):
        from threadpoolctl import threadpool_info
        from unittest.mock import patch
        spec, cell = self.configure({"algorithm": "RandomForest", "search_space": {"model__n_estimators": [2, 3], "select__k": [2, "all"]},
                                    "anchor": {"model__n_estimators": 2, "select__k": "all"}},
                                   resources={"threads": 2, "concurrent_fits": 3})
        original = train.FitAudit.fit
        active, maximum, pools = 0, 0, []
        def audit(model, X, y=None, row_ids=None):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            pools.extend(item["num_threads"] for item in threadpool_info())
            try:
                return original(model, X, y, row_ids=row_ids)
            finally:
                active -= 1
        with patch.object(train.FitAudit, "fit", new=audit):
            state = execute(self.root)
        self.assertEqual(maximum, 1)
        self.assertTrue(pools)
        self.assertTrue(all(count <= 2 for count in pools))
        for artifact in state["artifacts"]:
            self.assertEqual(train.joblib.load(artifact["path"]).named_steps["model"].n_jobs, 2)
        cell["search_space"]["model__n_jobs"] = [1, 2]
        with self.assertRaisesRegex(train.TrainingError, "Only model parameters"):
            train.trials(cell, spec["seed"])

    @unittest.skipUnless(importlib.util.find_spec("optuna"), "Real optional Optuna is not installed")
    def test_optuna_asks_only_after_actual_cv_tell_and_resume_is_exact(self):
        from unittest.mock import patch
        self.configure({"backend": "optuna", "n_trials": 3,
            "search_space": {"model__C": {"type": "float", "low": 0.1, "high": 3.0, "log": True},
                             "model__max_iter": {"type": "int", "low": 200, "high": 300, "step": 50},
                             "select__k": {"type": "categorical", "choices": [2, "all"]}}})
        original_ask = train.qsar_search.OptunaStudyCoordinator.ask
        original_tell = train.qsar_search.OptunaStudyCoordinator.tell
        original_fit = train.fit_predict
        events = []
        def ask(coordinator):
            self.assertFalse(coordinator.pending_trials())
            handle = original_ask(coordinator)
            events.append(("ask", coordinator.study_name, handle["trial_id"]))
            return handle
        def tell(coordinator, handle, *args, **kwargs):
            events.append(("tell", coordinator.study_name, handle["trial_id"]))
            return original_tell(coordinator, handle, *args, **kwargs)
        def fit(*args, **kwargs):
            context = args[8]
            events.append(("fit", context.get("scope"), context.get("trial_id")))
            return original_fit(*args, **kwargs)
        with patch.object(train.qsar_search.OptunaStudyCoordinator, "ask", new=ask), \
                patch.object(train.qsar_search.OptunaStudyCoordinator, "tell", new=tell), \
                patch.object(train, "fit_predict", side_effect=fit):
            state = execute(self.root)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(sum(event[0] == "ask" for event in events), 12)
        for index, event in enumerate(events):
            if event[0] == "ask":
                end = next(i for i in range(index + 1, len(events)) if events[i][0] == "tell")
                self.assertTrue(any(item[0] == "fit" and item[2] == event[2] for item in events[index + 1:end]))
                self.assertFalse(any(item[0] == "ask" for item in events[index + 1:end]))
        for saved in state["studies"].values():
            snapshot = saved["snapshot"]
            self.assertEqual(snapshot["coverage"]["attempted_search_trials"], 3)
            self.assertEqual(len(snapshot["trials"]), 3)
            self.assertNotIn("anchor", [t["trial_id"] for t in snapshot["trials"]])
            self.assertTrue(all(t["user_attributes"].get("qsar_fit_evidence_sha256") for t in snapshot["trials"]))
        with patch.object(train, "fit_predict", side_effect=AssertionError("Completed study was refit")):
            self.assertEqual(execute(self.root, resume=True), state)
        coverage = train.load(self.root / "out" / "search_coverage.json")
        self.assertTrue(all(c["distinct_search_configurations"] <= c["attempted_search_trials"] for c in coverage["cells"]))

    @unittest.skipUnless(importlib.util.find_spec("optuna"), "Real optional Optuna is not installed")
    def test_optuna_partial_scope_reuses_only_bound_terminal_trials(self):
        from unittest.mock import patch
        self.configure({"backend": "optuna", "n_trials": 3})
        original = train.check_deadline
        starts = 0
        def stop(spec, cell=None, context=None):
            nonlocal starts
            if isinstance(context, dict) and context.get("phase") == "trial-start":
                starts += 1
                if starts == 2:
                    raise train.TrainingBudgetExhausted("synthetic trial-start budget")
            return original(spec, cell, context)
        with patch.object(train, "check_deadline", side_effect=stop):
            partial = execute(self.root)
        self.assertEqual(partial["status"], "incomplete")
        original_fit = train.fit_predict
        reused = []
        def fit(*args, **kwargs):
            context = args[8]
            if context.get("scope") == "outer-0" and context.get("trial_id") == "search-0":
                reused.append(context)
            return original_fit(*args, **kwargs)
        with patch.object(train, "fit_predict", side_effect=fit):
            complete = execute(self.root, resume=True)
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(reused, [])
        first = next(r for r in partial["ledger"] if r["trial_id"] == "search-0")
        self.assertIn(first, complete["ledger"])
        self.assertTrue(complete["attempt_history"])

    @unittest.skipUnless(importlib.util.find_spec("optuna"), "Real optional Optuna is not installed")
    def test_optuna_resume_rejects_ledger_evidence_snapshot_and_sqlite_tampering(self):
        import copy
        import sqlite3
        self.configure({"backend": "optuna"})
        original = execute(self.root)
        checkpoint = self.root / "out" / "checkpoint.json"
        def mutate_params(state):
            next(r for r in state["ledger"] if r["trial_id"] == "search-0")["parameters"]["model__C"] = 987.0
        def mutate_fit(state):
            next(r for r in state["fit_evidence"] if r.get("trial_id") == "search-0")["selected_feature_count"] = 999
        def mutate_prediction(state):
            record = next(r for r in state["ledger"] if r["trial_id"] == "search-0")
            record["fold_predictions"][0]["prediction"][0] = 0.123
            record["evidence_sha256"] = train.trial_receipt(record, state["fit_evidence"])
        for mutate in (mutate_params, mutate_fit, mutate_prediction):
            state = copy.deepcopy(original)
            mutate(state)
            train.dump(checkpoint, state)
            before = checkpoint.read_bytes()
            with self.assertRaises((train.TrainingError, train.qsar_search.SearchError)):
                execute(self.root, resume=True)
            self.assertEqual(checkpoint.read_bytes(), before)
        train.dump(checkpoint, original)
        saved = next(iter(original["studies"].values()))
        snapshot_path = Path(saved["snapshot_artifact"]["path"])
        previous = snapshot_path.read_bytes()
        snapshot = train.load(snapshot_path)
        snapshot["trials"][0]["objective_value"] = 0.456
        train.dump(snapshot_path, snapshot)
        with self.assertRaisesRegex(train.TrainingError, "snapshot hash"):
            execute(self.root, resume=True)
        snapshot_path.write_bytes(previous)
        with closing(sqlite3.connect(saved["artifact"]["path"])) as connection, connection:
            connection.execute("UPDATE trial_params SET param_value=param_value+1")
        with self.assertRaises((train.TrainingError, train.qsar_search.SearchError)):
            execute(self.root, resume=True)

    def test_final_calibrated_recipe_is_frozen_deterministic_and_resumable_without_refitting(self):
        from unittest.mock import patch
        self.expanded_groups()
        self.configure({"calibration": {"method": "isotonic", "n_splits": 2}}, threshold_grid=[0.2, 0.5, 0.8])
        state = execute(self.root)
        first, second = self.final(state), self.final(state, "final-repeat")
        left = train.joblib.load(first["models"]["oral"]["artifact"]["path"])
        right = train.joblib.load(second["models"]["oral"]["artifact"]["path"])
        X, _ = self.inputs()[4]["synthetic"]
        np.testing.assert_array_equal(left.predict_proba(X), right.predict_proba(X))
        self.assertEqual(left.frozen_recipe_sha256_, right.frozen_recipe_sha256_)
        self.assertEqual(left.threshold_, state["final_selection"]["oral"]["threshold"])
        self.assertEqual(left.calibration_audit_, right.calibration_audit_)
        self.assertFalse(hasattr(left, "calibration_estimators_"))
        with patch.object(train, "fit_training_model", side_effect=AssertionError("Completed final task refit")):
            self.assertEqual(self.final(state, resume=True), first)

    def test_outer_resume_rejects_rebound_model_with_wrong_training_identity(self):
        state = execute(self.root)
        artifact = state["artifacts"][0]
        model = train.joblib.load(artifact["path"])
        self.assertEqual(model.training_identity_sha256_, train.digest(state["identity"]))
        model.training_identity_sha256_ = "0" * 64
        train.joblib.dump(model, artifact["path"])
        state["artifacts"][0] = train.binding(artifact["path"])
        train.dump(self.root / "out" / "checkpoint.json", state)
        with self.assertRaises(train.TrainingError):
            execute(self.root, resume=True)

    def test_final_resume_rejects_old_weights_after_same_identity_rows_receive_new_labels(self):
        self.configure({"n_trials": 1, "search_space": {"model__C": [1.0], "select__k": [5, "all"]},
                        "anchor": {"model__C": 1.0, "select__k": "all"}})
        original = execute(self.root)
        old_report = self.final(original)
        old_model = train.joblib.load(old_report["models"]["oral"]["artifact"]["path"])
        with (self.root / "development.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            row["target_class"] = str(1 - int(row["target_class"]))
        with (self.root / "development.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        rebind_fixture(self.root)
        updated = execute(self.root, "updated")
        selected = self.root / "updated" / "final_selection.json"
        lock = self.root / "updated-lock.json"
        train.dump(lock, {"decision": "STOP", "selection": train.binding(selected),
                          "tasks": {task: chosen["candidate_id"] for task, chosen in updated["final_selection"].items()}})
        args = [self.root / name for name in ("development.csv", "folds.json", "features.json", "contract.json")]
        args.extend([selected, lock, self.root / "updated-final"])
        report = train.final_fit(*args)
        fresh = train.joblib.load(report["models"]["oral"]["artifact"]["path"])
        self.assertEqual(fresh.fit_training_row_ids_, old_model.fit_training_row_ids_)
        self.assertEqual(fresh.frozen_recipe_sha256_, old_model.frozen_recipe_sha256_)
        self.assertNotEqual(fresh.training_identity_sha256_, old_model.training_identity_sha256_)
        report["models"]["oral"]["artifact"] = old_report["models"]["oral"]["artifact"]
        train.dump(self.root / "updated-final" / "final_fit.json", report)
        with self.assertRaises(train.TrainingError):
            train.final_fit(*args, resume=True)


if __name__ == "__main__":
    unittest.main()
