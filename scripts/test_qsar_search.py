"""Synthetic optional-Optuna ask/tell custody; never fits a scientific model."""
import copy
from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_search as search


HAS_OPTUNA = importlib.util.find_spec("optuna") is not None
IDENTITY = {"development": {"sha256": "a" * 64}, "folds": {"sha256": "b" * 64}}


def cell(n_trials=4):
    return {"search_space": {"model__alpha": {"type": "float", "low": 0.01, "high": 10.0, "log": True},
                             "model__depth": {"type": "int", "low": 1, "high": 5},
                             "select__k": [2, "all"]},
            "n_trials": n_trials,
            "anchor": {"model__alpha": 1.0, "model__depth": 3, "select__k": "all"}}


def spec(n_trials=4, direction="minimize", resource_policy=None):
    return search.validate_search_spec(cell(n_trials), 71, direction, resource_policy)


class SearchDeclarationTests(unittest.TestCase):
    def test_categorical_duplicate_numeric_equivalence_is_rejected(self):
        for values in ([1, 1.0], [False, 0], [True, 1.0], [None, None], ["all", "all"]):
            for declaration in (values, {"type": "categorical", "choices": values}):
                with self.subTest(declaration=declaration):
                    with self.assertRaisesRegex(search.SearchError, "Duplicate search"):
                        search.validate_search_spec({"search_space": {"x": declaration}, "n_trials": 1}, 7)

    def test_invalid_or_nonfinite_distributions_are_rejected(self):
        invalid = [[], [float("nan")], [float("inf")], [[1, 2]], [{"value": 1}],
                   {"type": "normal", "low": 0, "high": 1},
                   {"type": "float", "low": 2, "high": 1},
                   {"type": "float", "low": -1e308, "high": 1e308},
                   {"type": "float", "low": 0, "high": float("inf")},
                   {"type": "float", "low": float("nan"), "high": 1},
                   {"type": "float", "low": False, "high": 1},
                   {"type": "float", "low": 0, "high": 1, "log": True},
                   {"type": "float", "low": 1, "high": 2, "log": "yes"},
                   {"type": "float", "low": 1, "high": 2, "step": 0},
                   {"type": "float", "low": 1, "high": 2, "step": -0.1},
                   {"type": "float", "low": 1, "high": 2, "step": float("nan")},
                   {"type": "float", "low": 1, "high": 2, "step": 0.1, "log": True},
                   {"type": "float", "low": 0, "high": 1, "step": 0.3},
                   {"type": "float", "low": 1, "high": 2, "conditional": "other"},
                   {"type": "int", "low": 1.0, "high": 4},
                   {"type": "int", "low": 0, "high": 4, "step": 2, "log": True},
                   {"type": "int", "low": 1, "high": 4, "step": 2},
                   {"type": "int", "low": 1, "high": 4, "step": 1.0},
                   {"type": "int", "low": 1, "high": 4, "step": None},
                   {"type": "float", "low": 1},
                   {"type": "categorical", "choices": [1], "extra": True}]
        for declaration in invalid:
            with self.subTest(declaration=declaration), self.assertRaises(search.SearchError):
                search.validate_search_spec({"search_space": {"x": declaration}, "n_trials": 1}, 7)

    def test_valid_numeric_ranges_and_exact_decimal_step(self):
        declarations = {"linear": {"type": "float", "low": 0, "high": 0.3, "step": 0.1},
                        "log": {"type": "int", "low": 1, "high": 16, "log": True},
                        "fixed": {"type": "float", "low": 1.0, "high": 1.0},
                        "scalar": {"type": "categorical", "choices": [None, False, 2, "all"]}}
        validated = search.validate_search_spec({"search_space": declarations, "n_trials": 3}, 2,
                                                search.ObjectiveDirection.MAXIMIZE)
        self.assertEqual(validated.search_space, declarations)
        self.assertEqual(validated.direction, search.ObjectiveDirection.MAXIMIZE)
        self.assertEqual(validated.to_dict()["direction"], "maximize")

    def test_contract_seed_budget_direction_and_anchor_validation(self):
        for budget in (0, -1, True, 1.0, None):
            with self.subTest(budget=budget), self.assertRaises(search.SearchError):
                search.validate_search_spec(cell(budget), 71)
        for seed in (-1, 2**32, True, 1.0, None):
            with self.subTest(seed=seed), self.assertRaises(search.SearchError):
                search.validate_search_spec(cell(), seed)
        for direction in ("minimum", "MAXIMIZE", None, 1):
            with self.subTest(direction=direction), self.assertRaises(search.SearchError):
                spec(direction=direction)
        for anchor in ({}, {"unknown": 1}, {"model__alpha": float("inf")}, [1]):
            declaration = cell()
            declaration["anchor"] = anchor
            with self.subTest(anchor=anchor), self.assertRaises(search.SearchError):
                search.validate_search_spec(declaration, 71)
        for space in ({}, {"": [1]}, {1: [1]}, []):
            with self.subTest(space=space), self.assertRaises(search.SearchError):
                search.validate_search_spec({"search_space": space, "n_trials": 1}, 71)

    def test_validated_spec_does_not_share_mutable_contract_state(self):
        declaration = cell()
        validated = search.validate_search_spec(declaration, 71)
        expected = validated.to_dict()
        declaration["search_space"]["select__k"].append(999)
        declaration["anchor"]["model__alpha"] = 999
        returned = validated.search_space
        returned.clear()
        anchor = validated.anchor
        anchor.clear()
        self.assertEqual(validated.to_dict(), expected)

    def test_resource_policy_validation_and_boundary_checks(self):
        for policy in ({"maximum_trials": 0}, {"maximum_trials": True},
                       {"maximum_trials": 4, "concurrency": 0},
                       {"maximum_trials": 4, "concurrency": 1.0},
                       {"maximum_trials": 4, "concurrency": 5},
                       {"maximum_trials": 4, "deadline": "2026-01-01T12:00:00"},
                       {"maximum_trials": 4, "deadline": "not-a-date"},
                       {"maximum_trials": 4, "timeout": 12}):
            with self.subTest(policy=policy), self.assertRaises(search.SearchError):
                search.validate_resource_policy(policy)
        with self.assertRaisesRegex(search.SearchError, "exceeds"):
            spec(resource_policy={"maximum_trials": 3})
        policy = search.validate_resource_policy({"maximum_trials": 4, "concurrency": 2,
                                                  "deadline": "2026-01-01T12:00:00+00:00"})
        before = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        boundary = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        policy.check_start(1, 1, now=before)
        with self.assertRaisesRegex(search.SearchBudgetError, "deadline"):
            policy.check_start(1, 0, now=boundary)
        with self.assertRaisesRegex(search.SearchBudgetError, "maximum_trials"):
            policy.check_start(4, 0, now=before)
        with self.assertRaisesRegex(search.SearchBudgetError, "concurrency"):
            policy.check_start(2, 2, now=before)
        for attempted, running in ((-1, 0), (1, 2), (True, 0), (1, -1)):
            with self.subTest(attempted=attempted, running=running), self.assertRaises(search.SearchError):
                policy.check_start(attempted, running, now=before)
        with self.assertRaisesRegex(search.SearchError, "timezone-aware"):
            policy.check_start(1, 0, now=before.replace(tzinfo=None))

    def test_missing_dependency_is_operational_error_without_creating_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent.sqlite"
            with patch.object(search.importlib, "import_module", side_effect=ImportError("optuna not installed")):
                with self.assertRaisesRegex(search.SearchDependencyError, "no fallback"):
                    search.OptunaStudyCoordinator(path, spec(), IDENTITY)
            self.assertFalse(path.exists())


@unittest.skipUnless(HAS_OPTUNA, "Optional Optuna dependency is not installed")
class OptunaStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "study.sqlite"
        self.coordinators = []

    def tearDown(self):
        for coordinator in self.coordinators:
            coordinator.close()
        self.tmp.cleanup()

    def open_study(self, path=None, specification=None, identity=None, **kwargs):
        coordinator = search.OptunaStudyCoordinator(self.path if path is None else path,
                                                     spec() if specification is None else specification,
                                                     IDENTITY if identity is None else identity, **kwargs)
        self.coordinators.append(coordinator)
        return coordinator

    def test_real_optuna_ask_tell_preserves_anchor_and_distinct_coverage(self):
        specification = search.validate_search_spec(
            {"search_space": {"x": [1]}, "n_trials": 3, "anchor": {"x": 1}}, 71)
        coordinator = self.open_study(specification=specification)
        for value in (5.0, 2.0, 4.0):
            trial = coordinator.ask()
            self.assertEqual(trial["parameters"], {"x": 1})
            self.assertEqual(coordinator.snapshot()["trials"][-1]["status"], "running")
            outcome = coordinator.tell(trial, value)
            self.assertEqual(outcome["status"], "complete")
        evidence = coordinator.snapshot()
        self.assertEqual(evidence["best_trial_id"], "search-1")
        self.assertEqual(evidence["best_objective_value"], 2.0)
        self.assertEqual(evidence["coverage"]["attempted_search_trials"], 3)
        self.assertEqual(evidence["coverage"]["completed_search_trials"], 3)
        self.assertEqual(evidence["coverage"]["distinct_search_configurations"], 1)
        self.assertTrue(evidence["coverage"]["successful_search_coverage_complete"])
        self.assertFalse(evidence["anchor"]["included_in_search_budget"])
        self.assertEqual(evidence["anchor"]["parameters"], {"x": 1})
        self.assertEqual([record["kind"] for record in evidence["trials"]], ["search"] * 3)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            states = connection.execute("SELECT state FROM trials ORDER BY number").fetchall()
            values = connection.execute("SELECT value FROM trial_values ORDER BY trial_id").fetchall()
        self.assertEqual(states, [("COMPLETE",)] * 3)
        self.assertEqual(values, [(5.0,), (2.0,), (4.0,)])
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchBudgetError, "n_trials"):
            coordinator.ask()
        self.assertEqual(self.path.read_bytes(), before)

    def test_actual_numeric_and_categorical_distributions(self):
        declaration = {"search_space": {
            "continuous": {"type": "float", "low": -1, "high": 1},
            "log_float": {"type": "float", "low": 0.001, "high": 1, "log": True},
            "stepped_float": {"type": "float", "low": 0, "high": 0.3, "step": 0.1},
            "integer": {"type": "int", "low": 2, "high": 8, "step": 2},
            "log_integer": {"type": "int", "low": 1, "high": 16, "log": True},
            "category": {"type": "categorical", "choices": [None, False, "all"]}}, "n_trials": 2}
        coordinator = self.open_study(specification=search.validate_search_spec(declaration, 9))
        for _ in range(2):
            trial = coordinator.ask()
            params = trial["parameters"]
            self.assertTrue(-1 <= params["continuous"] <= 1)
            self.assertTrue(0.001 <= params["log_float"] <= 1)
            self.assertAlmostEqual(params["stepped_float"] / 0.1, round(params["stepped_float"] / 0.1))
            self.assertIn(params["integer"], [2, 4, 6, 8])
            self.assertIs(type(params["integer"]), int)
            self.assertTrue(1 <= params["log_integer"] <= 16)
            self.assertIn(params["category"], [None, False, "all"])
            coordinator.tell(trial, params["continuous"] ** 2)
        distribution = coordinator.snapshot()["trials"][0]["distributions"]["continuous"]
        self.assertEqual(distribution["name"], "FloatDistribution")

    def test_minimize_and_maximize_directions_are_real_study_directions(self):
        for direction, expected_id, expected_value in (("minimize", "search-0", 0.2),
                                                       ("maximize", "search-1", 0.9)):
            with self.subTest(direction=direction):
                coordinator = self.open_study(self.root / (direction + ".sqlite"), spec(2, direction))
                coordinator.tell(coordinator.ask(), 0.2)
                coordinator.tell(coordinator.ask(), 0.9)
                evidence = coordinator.snapshot()
                self.assertEqual(evidence["best_trial_id"], expected_id)
                self.assertEqual(evidence["best_objective_value"], expected_value)
                with closing(sqlite3.connect(coordinator.path)) as connection, connection:
                    actual = connection.execute("SELECT direction FROM study_directions").fetchone()[0]
                self.assertEqual(actual, direction.upper())

    def test_fit_evidence_digest_is_bound_before_completion_and_survives_resume(self):
        coordinator = self.open_study()
        trial = coordinator.ask()
        before = self.path.read_bytes()
        for invalid in ("short", "A" * 64, "z" * 64, 123):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(search.SearchError, "evidence_sha256"):
                coordinator.tell(trial, .2, evidence_sha256=invalid)
            self.assertEqual(self.path.read_bytes(), before)
        coordinator.tell(trial, .2, evidence_sha256="a" * 64)
        evidence = coordinator.snapshot()
        self.assertEqual(evidence["trials"][0]["user_attributes"]["qsar_fit_evidence_sha256"], "a" * 64)
        coordinator.close()
        resumed = self.open_study(resume=True, expected_snapshot=evidence)
        self.assertEqual(resumed.snapshot()["trials"][0]["user_attributes"]["qsar_fit_evidence_sha256"], "a" * 64)

    def test_failed_pruned_and_pending_trials_survive_resume(self):
        coordinator = self.open_study()
        failed = coordinator.ask()
        coordinator.tell(failed, failure="synthetic fit exception")
        pruned = coordinator.ask()
        coordinator.tell(pruned, pruned=True)
        pending = coordinator.ask()
        before = coordinator.snapshot()
        coordinator.close()
        resumed = self.open_study(resume=True, expected_snapshot=before)
        self.assertEqual(resumed.snapshot(), before)
        self.assertEqual(resumed.pending_trials(), [pending])
        resumed.tell(pending, 0.5)
        resumed.tell(resumed.ask(), 0.3)
        evidence = resumed.snapshot()
        self.assertEqual([record["status"] for record in evidence["trials"]],
                         ["failed", "pruned", "complete", "complete"])
        self.assertEqual(evidence["trials"][0]["failure_reason"], "synthetic fit exception")
        self.assertEqual(evidence["coverage"]["failed_search_trials"], 1)
        self.assertEqual(evidence["coverage"]["pruned_search_trials"], 1)
        self.assertEqual(evidence["coverage"]["remaining_search_trials"], 0)
        self.assertFalse(evidence["coverage"]["successful_search_coverage_complete"])
        self.assertEqual(evidence["best_trial_id"], "search-3")
        with self.assertRaises(search.SearchBudgetError):
            resumed.ask()

    def test_seed_and_resume_reproduce_ordered_adaptive_optuna_suggestions(self):
        # More than ten completed trials exercises TPE beyond its startup phase.
        specification = spec(14)
        uninterrupted = self.open_study(self.root / "full.sqlite", specification)
        expected = []
        for _ in range(14):
            trial = uninterrupted.ask()
            expected.append(trial["parameters"])
            uninterrupted.tell(trial, (trial["parameters"]["model__alpha"] - 0.4) ** 2)
        resumed = self.open_study(self.root / "resumed.sqlite", specification)
        actual = []
        for index in range(14):
            if index == 7:
                snapshot = resumed.snapshot()
                resumed.close()
                resumed = self.open_study(self.root / "resumed.sqlite", specification,
                                          resume=True, expected_snapshot=snapshot)
            trial = resumed.ask()
            actual.append(trial["parameters"])
            resumed.tell(trial, (trial["parameters"]["model__alpha"] - 0.4) ** 2)
        self.assertEqual(actual, expected)

    def test_changed_input_or_exact_search_spec_rejected_before_storage_mutation(self):
        coordinator = self.open_study()
        coordinator.tell(coordinator.ask(), 0.1)
        coordinator.close()
        original = self.path.read_bytes()
        variants = []
        for field, value in (("n_trials", 5), ("anchor", {"model__alpha": 2.0})):
            changed = cell()
            changed[field] = value
            variants.append((search.validate_search_spec(changed, 71), IDENTITY))
        changed = cell()
        changed["search_space"]["select__k"] = {"type": "categorical", "choices": [2, "all"]}
        variants.extend([(search.validate_search_spec(changed, 71), IDENTITY),
                         (search.validate_search_spec(cell(), 72), IDENTITY),
                         (spec(direction="maximize"), IDENTITY),
                         (spec(resource_policy={"maximum_trials": 5}), IDENTITY),
                         (spec(), {"development": {"sha256": "c" * 64}})])
        for specification, identity in variants:
            with self.subTest(specification=specification.to_dict(), identity=identity):
                with patch.object(search._optuna().storages, "RDBStorage", side_effect=AssertionError("must not open writable Optuna storage")):
                    with self.assertRaisesRegex(search.SearchError, "identity/search specification"):
                        self.open_study(specification=specification, identity=identity, resume=True)
                self.assertEqual(self.path.read_bytes(), original)

    def test_snapshot_and_sqlite_trial_tampering_fail_without_new_mutation(self):
        coordinator = self.open_study()
        coordinator.tell(coordinator.ask(), 0.1)
        snapshot = coordinator.snapshot()
        coordinator.close()
        changed = copy.deepcopy(snapshot)
        changed["trials"][0]["parameters"]["model__alpha"] = 999
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "evidence hash"):
            self.open_study(resume=True, expected_snapshot=changed)
        self.assertEqual(self.path.read_bytes(), before)
        # Even a self-consistent replacement external snapshot is not the database receipt.
        changed["snapshot_sha256"] = search._hash({key: value for key, value in changed.items() if key != "snapshot_sha256"})
        with self.assertRaisesRegex(search.SearchError, "differs"):
            self.open_study(resume=True, expected_snapshot=changed)
        self.assertEqual(self.path.read_bytes(), before)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE trial_params SET param_value=999 WHERE param_name='model__alpha'")
        tampered = self.path.read_bytes()
        with patch.object(search._optuna().storages, "RDBStorage", side_effect=AssertionError("must not open writable Optuna storage")):
            with self.assertRaisesRegex(search.SearchError, "storage hash"):
                self.open_study(resume=True)
        self.assertEqual(self.path.read_bytes(), tampered)

    def test_persisted_receipt_hash_tampering_is_rejected(self):
        coordinator = self.open_study()
        coordinator.close()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            receipt = json.loads(connection.execute("SELECT snapshot_json FROM qsar_search_evidence").fetchone()[0])
            receipt["coverage"]["attempted_search_trials"] = 999
            connection.execute("UPDATE qsar_search_evidence SET snapshot_json=?", (json.dumps(receipt),))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "evidence hash"):
            self.open_study(resume=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_unbound_or_existing_studies_cannot_be_overwritten(self):
        with self.assertRaisesRegex(search.SearchError, "existing"):
            self.open_study(resume=True)
        self.assertFalse(self.path.exists())
        coordinator = self.open_study()
        coordinator.close()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "nonexistent"):
            self.open_study()
        with self.assertRaisesRegex(search.SearchError, "identity/search specification"):
            self.open_study(study_name="another-study", resume=True)
        self.assertEqual(self.path.read_bytes(), before)
        unbound = self.root / "unbound.sqlite"
        with closing(sqlite3.connect(unbound)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
        before = unbound.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "unbound"):
            self.open_study(path=unbound, resume=True)
        self.assertEqual(unbound.read_bytes(), before)

    def test_invalid_tells_and_terminal_overwrites_do_not_mutate_the_study(self):
        coordinator = self.open_study()
        trial = coordinator.ask()
        bad_handle = {**trial, "parameters": {"model__alpha": 999}}
        foreign_handle = {**trial, "study_sha256": "0" * 64}
        numeric_equivalent = copy.deepcopy(trial)
        numeric_equivalent["parameters"]["model__depth"] = float(trial["parameters"]["model__depth"])
        cases = [(trial, {}), (trial, {"result": float("nan")}), (trial, {"result": float("inf")}),
                 (trial, {"result": True}), (trial, {"result": "0.2"}),
                 (trial, {"result": 0.1, "failure": "fit failed"}),
                 (trial, {"pruned": True, "failure": "fit failed"}),
                 (trial, {"pruned": 1}), (trial, {"failure": ""}),
                 (bad_handle, {"result": 0.1}), (foreign_handle, {"result": 0.1}),
                 (numeric_equivalent, {"result": 0.1}),
                 ({**trial, "number": True}, {"result": 0.1}), ("search-0", {"result": 0.1})]
        for handle, outcome in cases:
            before = self.path.read_bytes()
            with self.subTest(handle=handle, outcome=outcome), self.assertRaises(search.SearchError):
                coordinator.tell(handle, **outcome)
            self.assertEqual(self.path.read_bytes(), before)
        coordinator.tell(trial, 0.2)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "already terminal"):
            coordinator.tell(trial, failure="cannot rewrite success")
        self.assertEqual(self.path.read_bytes(), before)

    def test_suggestion_failure_is_preserved_not_replaced(self):
        coordinator = self.open_study()
        with patch.object(search._optuna().trial.Trial, "suggest_float", side_effect=RuntimeError("synthetic sampling failure")):
            with self.assertRaisesRegex(search.SearchError, "evidence was preserved"):
                coordinator.ask()
        evidence = coordinator.snapshot()
        self.assertEqual(evidence["trials"][0]["status"], "failed")
        self.assertIn("synthetic sampling failure", evidence["trials"][0]["failure_reason"])
        self.assertEqual(coordinator.ask()["trial_id"], "search-1")

    def test_resource_deadline_helper_does_not_claim_worker_enforcement(self):
        specification = spec(resource_policy={"maximum_trials": 4, "concurrency": 1,
                                               "deadline": "2000-01-01T00:00:00+00:00"})
        with self.assertRaisesRegex(search.SearchBudgetError, "deadline"):
            specification.resource_policy.check_start(0, 0)
        coordinator = self.open_study(specification=specification)
        # Explicitly exercise the policy-only contract, not fictitious cancellation.
        first, second = coordinator.ask(), coordinator.ask()
        self.assertEqual(coordinator.snapshot()["resource_policy_enforcement"], "caller_managed")
        self.assertEqual(coordinator.pending_trials(), [first, second])

    def test_interrupted_operation_claim_fails_closed(self):
        coordinator = self.open_study()
        coordinator.ask()
        coordinator.close()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE qsar_search_evidence SET operation_token='interrupted-writer'")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(search.SearchError, "interrupted"):
            self.open_study(resume=True)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
