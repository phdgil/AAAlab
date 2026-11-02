"""Synthetic-only split/feature regression tests; no original research sources."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import Descriptors
from rdkit.Chem import rdFingerprintGenerator as F

SCRIPT = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_prepare.py"
SPEC = importlib.util.spec_from_file_location("qsar_prepare", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-prepare-fixture-")
        self.root = Path(self.temp.name)
        self.curated = self.root / "curated.csv"
        self.contract = self.root / "contract.json"
        self.rows = [{"output_row_id": f"{task}-{group}", "task_id": task, "group_id": f"g{group}",
                      "parent_smiles": "C" * (group + 1), "target_pLD50": str(group / 7)}
                     for task in ("rat", "mouse") for group in range(12)]
        self.config = {"preparation": {"target_column": "target_pLD50", "group_column": "group_id",
            "seed": 9, "terminal_fraction": 0.25, "outer_folds": 3, "inner_folds": 2,
            "intended_generalization": "Synthetic molecule fixture", "terminal_custodian": "fixture evaluator",
            "prior_exposure": {"status": "unknown", "evidence": [], "description": "Synthetic fixture only"},
            "unfitted_transforms": {"imputation": "inner-training-fold only", "scaling": "inner-training-fold only"}},
            "features": {"families": {"RDKit2D": {}, "Morgan2": {"n_bits": 64, "use_chirality": True},
                "Morgan3": {"n_bits": 64, "use_chirality": False},
                "RDKitpath": {"n_bits": 64, "min_path": 1, "max_path": 5}, "MACCS": {},
                "atom_pair": {"n_bits": 64, "min_distance": 1, "max_distance": 15, "use_chirality": False},
                "torsion": {"n_bits": 64, "torsion_atom_count": 4, "use_chirality": False}, "Avalon": {"n_bits": 64}}}}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        MODULE.write_csv(self.curated, self.rows, list(self.rows[0]))
        MODULE.dump(self.contract, self.config)

    def split(self, folder="split"):
        return MODULE.prepare_splits(self.curated, self.contract, self.root / folder)

    def test_strict_contract_rejects_bare_csv_before_output(self):
        self.config["execution_policy"] = {"allow_training_with_unresolved_required_source_coverage": False}
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "curation evidence"):
            self.split()
        self.assertFalse((self.root / "split").exists())

    def features(self, folder="features", split=None):
        split = split or self.split()
        return MODULE.generate_features(split["development"]["path"], self.root / "split/preprocessing_manifest.json",
                                        self.contract, self.root / folder)

    def test_mutation_during_split_rejected_before_manifest(self):
        original = MODULE.read_rows
        def mutating(path, *args):
            result = original(path, *args)
            if Path(path) == self.curated:
                with self.curated.open("a", encoding="utf-8") as stream:
                    stream.write("extra,rat,new,CO,2\n")
            return result
        with patch.object(MODULE, "read_rows", side_effect=mutating):
            with self.assertRaisesRegex(MODULE.PreparationError, "changed during"):
                self.split()
        self.assertFalse((self.root / "split/preprocessing_manifest.json").exists())

    def test_mutation_during_features_rejected_before_manifest(self):
        split = self.split()
        original = MODULE.read_rows
        def mutating(path, *args):
            result = original(path, *args)
            with Path(path).open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return result
        with patch.object(MODULE, "read_rows", side_effect=mutating):
            with self.assertRaisesRegex(MODULE.PreparationError, "changed during"):
                self.features(split=split)
        self.assertFalse((self.root / "features/feature_manifest.json").exists())

    def test_fixed_features_reused_without_dropping_repeated_records(self):
        split = self.split()
        _, rows = MODULE.read_rows(split["development"]["path"])
        calls = []
        def descriptor(mol):
            calls.append(mol.GetNumAtoms())
            return float(mol.GetNumAtoms())
        with patch.object(Descriptors, "_descList", [("counter", descriptor)]):
            self.features(split=split)
        with np.load(self.root / "features/RDKit2D.npz") as archive:
            self.assertEqual(archive["row_ids"].tolist(), [r["output_row_id"] for r in rows])
            self.assertEqual(archive["X"][:, 0].tolist(), [float(len(r["parent_smiles"])) for r in rows])
        self.assertEqual(len(calls), len({r["parent_smiles"] for r in rows}))

    def test_reused_nonfinite_features_keep_every_record_ledger(self):
        split = self.split()
        _, rows = MODULE.read_rows(split["development"]["path"])
        calls = []
        def descriptor(mol):
            calls.append(1)
            return float("inf")
        with patch.object(Descriptors, "_descList", [("counter", descriptor)]):
            self.features(split=split)
        with (self.root / "features/nonfinite_ledger.csv").open(newline="", encoding="utf-8") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertEqual({r["output_row_id"] for r in ledger}, {r["output_row_id"] for r in rows})
        self.assertEqual(len(calls), len({r["parent_smiles"] for r in rows}))

    def test_all_rows_group_disjoint_nested_folds(self):
        manifest = self.split()
        self.assertEqual(manifest["input_rows"], len(self.rows))
        self.assertEqual(manifest["development_rows"] + manifest["terminal_rows"], len(self.rows))
        _, dev = MODULE.read_rows(manifest["development"]["path"])
        _, terminal = MODULE.read_rows(manifest["sealed_terminal"]["path"])
        self.assertFalse({r["group_id"] for r in dev} & {r["group_id"] for r in terminal})
        self.assertEqual({r["output_row_id"] for r in dev + terminal}, {r["output_row_id"] for r in self.rows})
        lookup = {r["output_row_id"]: r["group_id"] for r in dev}
        folds = MODULE.load(manifest["folds"]["path"])
        for task in folds["tasks"].values():
            validation = []
            for outer in task["outer_folds"]:
                train, test = set(outer["train_row_ids"]), set(outer["validation_row_ids"])
                self.assertEqual(train | test, set(task["row_ids"]))
                self.assertFalse({lookup[x] for x in train} & {lookup[x] for x in test})
                validation.extend(test)
                inner_validation = []
                for inner in outer["inner_folds"]:
                    a, b = set(inner["train_row_ids"]), set(inner["validation_row_ids"])
                    self.assertEqual(a | b, train)
                    self.assertFalse({lookup[x] for x in a} & {lookup[x] for x in b})
                    self.assertFalse((a | b) & test)
                    inner_validation.extend(b)
                self.assertCountEqual(inner_validation, train)
            self.assertCountEqual(validation, task["row_ids"])

    def test_deterministic_replay_and_targets_do_not_affect_membership(self):
        first = self.split()
        second = self.split("replay")
        self.assertEqual(first["development"]["sha256"], second["development"]["sha256"])
        self.assertEqual(first["folds"]["sha256"], second["folds"]["sha256"])
        for row in self.rows:
            row["target_pLD50"] = str(-100 * float(row["target_pLD50"]))
        self.save()
        perturbed = self.split("perturbed")
        self.assertEqual(first["membership"]["sha256"], perturbed["membership"]["sha256"])
        self.assertEqual(first["folds"]["sha256"], perturbed["folds"]["sha256"])

    def test_large_replicate_population_has_no_row_cap(self):
        originals = self.rows
        self.rows = [dict(row, output_row_id=row["output_row_id"] + f":replicate{replicate}")
                     for replicate in range(125) for row in originals]
        self.save()
        manifest = self.split()
        self.assertEqual(manifest["input_rows"], 3000)
        self.assertEqual(manifest["development_rows"] + manifest["terminal_rows"], 3000)
        membership = MODULE.load(manifest["folds"]["path"])
        self.assertEqual(sum(len(task["row_ids"]) for task in membership["tasks"].values()), manifest["development_rows"])

    def test_duplicate_id_invalid_structure_and_bad_group_fail(self):
        original = [dict(row) for row in self.rows]
        for key, value in (("output_row_id", self.rows[0]["output_row_id"]),
                           ("parent_smiles", "not a molecule"), ("group_id", "wrong-group")):
            self.rows = [dict(row) for row in original]
            self.rows[12][key] = value
            self.save()
            with self.assertRaises(MODULE.PreparationError):
                self.split()

    def test_sparse_task_blocks_with_feasibility_evidence(self):
        self.config["preparation"]["inner_folds"] = 20
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "Infeasible"):
            self.split()
        evidence = MODULE.load(self.root / "split/feasibility.json")
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(evidence["excluded_rows"], 0)
        self.assertFalse((self.root / "split/development.csv").exists())

    def test_full_inventory_all_families_axis_and_replay(self):
        split = self.split()
        manifest = self.features(split=split)
        replay = self.features("feature-replay", split)
        self.assertEqual(manifest["requested_families"], manifest["generated_families"])
        expected = [name for name, _ in Descriptors._descList]
        self.assertEqual(manifest["families"]["RDKit2D"]["feature_names"], expected)
        for family, record in manifest["families"].items():
            with np.load(record["artifact"]["path"], allow_pickle=False) as data, np.load(replay["families"][family]["artifact"]["path"]) as other:
                self.assertEqual(data["row_ids"].tolist(), manifest["row_ids"])
                self.assertEqual(data["feature_names"].tolist(), record["feature_names"])
                self.assertEqual(data["X"].shape, tuple(record["shape"]))
                np.testing.assert_array_equal(data["X"], other["X"])
                if family != "RDKit2D":
                    self.assertTrue(np.isin(data["X"], [0, 1]).all())
            self.assertEqual(record["shape"][1], len(expected) if family == "RDKit2D" else 167 if family == "MACCS" else 64)

    def test_nonfinite_retained_and_ledgered_not_zeroed(self):
        self.config["features"]["families"] = {"RDKit2D": {}}
        self.save()
        with patch.object(Descriptors, "_descList", [("fixture_nonfinite", lambda mol: float("nan"))]):
            manifest = self.features()
        record = manifest["families"]["RDKit2D"]
        self.assertEqual(record["nonfinite_count"], manifest["rows"])
        with np.load(record["artifact"]["path"]) as data:
            self.assertTrue(np.isnan(data["X"]).all())
        with Path(manifest["nonfinite_ledger"]["path"]).open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), manifest["rows"])

    def test_sealed_or_mutated_development_rejected(self):
        split = self.split()
        with self.assertRaisesRegex(MODULE.PreparationError, "frozen split"):
            MODULE.generate_features(split["sealed_terminal"]["path"], self.root / "split/preprocessing_manifest.json",
                                     self.contract, self.root / "bad")
        dev = Path(split["development"]["path"])
        dev.write_text(dev.read_text() + "\n")
        with self.assertRaisesRegex(MODULE.PreparationError, "frozen split"):
            self.features(split=split)

    def test_parent_policy_merges_connectivity_without_splitting_declared_groups(self):
        self.rows[12]["group_id"] = "g1"
        self.rows[12]["parent_smiles"] = "[CH4]"
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "conflicting groups"):
            self.split("declared-conflict")
        self.config["preparation"]["grouping_policy"] = "parent"
        self.save()
        manifest = self.split()
        with Path(manifest["grouping_mapping"]["path"]).open(newline="", encoding="utf-8") as stream:
            mapping = list(csv.DictReader(stream))
        self.assertEqual(len(mapping), len(self.rows))
        joined = [row for row in mapping if row["declared_group_id"] in {"g0", "g1"}]
        self.assertEqual(len({row["effective_group_id"] for row in joined}), 1)
        self.assertEqual(len({row["partition"] for row in joined}), 1)
        self.assertEqual({row["canonical_parent_smiles"] for row in joined}, {"C", "CC"})
        self.assertTrue(all(row["grouping_basis"] == "parent_connectivity" for row in mapping))
        self.assertEqual(manifest["grouping_mapping"], MODULE.binding(manifest["grouping_mapping"]["path"]))

    def test_murcko_coarsening_is_transitive_and_ignores_stereo(self):
        rows = [{"output_row_id": str(i), "task_id": "rat", "group_id": group, "parent_smiles": smiles}
                for i, (group, smiles) in enumerate((("a", "c1ccccc1"), ("b", "Cc1ccccc1"),
                    ("b", "C1CCCCC1"), ("c", "OC1CCCCC1"),
                    ("d", "F[C@H](Cl)Br"), ("e", "F[C@@H](Cl)Br")))]
        mapping = MODULE.group_rows(rows, "bemis_murcko", "parent")
        self.assertEqual(len({row["group_id"] for row in rows[:4]}), 1)
        self.assertEqual(rows[4]["group_id"], rows[5]["group_id"])
        self.assertNotEqual(rows[0]["group_id"], rows[4]["group_id"])
        self.assertEqual(mapping[0]["scaffold_smiles"], "c1ccccc1")
        self.assertEqual(mapping[4]["scaffold_smiles"], "")
        self.assertEqual(mapping[4]["grouping_basis"], "empty_scaffold_parent_connectivity")

    def test_empty_scaffold_requires_explicit_policy_and_parent_fallback_is_not_shared(self):
        self.config["preparation"]["grouping_policy"] = "bemis_murcko"
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "explicit empty_scaffold_policy"):
            self.split("missing-empty-policy")
        self.config["preparation"]["empty_scaffold_policy"] = "error"
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "Empty Bemis-Murcko"):
            self.split("reject-acyclic")
        self.config["preparation"]["empty_scaffold_policy"] = "parent"
        self.save()
        manifest = self.split()
        _, dev = MODULE.read_rows(manifest["development"]["path"])
        _, held = MODULE.read_rows(manifest["sealed_terminal"]["path"])
        self.assertEqual(len({row["group_id"] for row in dev + held}), 12)
        self.assertFalse({row["group_id"] for row in dev} & {row["group_id"] for row in held})
        with Path(manifest["grouping_mapping"]["path"]).open(newline="", encoding="utf-8") as stream:
            mapping = list(csv.DictReader(stream))
        self.assertTrue(all(row["scaffold_smiles"] == "" for row in mapping))
        self.assertEqual(len({row["effective_group_id"] for row in mapping if row["canonical_parent_smiles"] in {"C", "CC"}}), 2)

    def make_external(self, records=None, exclude_scaffolds=False):
        self.external_csv = self.root / "external.csv"
        records = records or [{"output_row_id": "external-rat", "task_id": "rat", "group_id": "external-cc",
                               "parent_smiles": "C(C)", "target_pLD50": "-9", "source_note": "untouched source value"},
                              {"output_row_id": "external-mouse", "task_id": "mouse", "group_id": "external-cc",
                               "parent_smiles": "CC", "target_pLD50": "12", "source_note": "repeated parent retained"}]
        MODULE.write_csv(self.external_csv, records, list(records[0]))
        self.external_csv.write_bytes(b"\xef\xbb\xbf" + self.external_csv.read_bytes())
        self.config["preparation"]["external_cohort"] = {
            "prior_exposure": {"status": "exposed", "evidence": ["synthetic fixture"], "description": "Prior exposure retained"},
            "exclude_scaffold_overlap": exclude_scaffolds}
        self.save()
        return self.external_csv

    def test_external_bytes_rows_exposure_and_parent_exclusions_are_bound(self):
        external = self.make_external()
        before = external.read_bytes()
        manifest = MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        cohort = manifest["external_cohort"]
        self.assertEqual(external.read_bytes(), before)
        self.assertEqual(Path(cohort["artifact"]["path"]).read_bytes(), before)
        self.assertEqual(cohort["input"], MODULE.binding(external))
        self.assertEqual(cohort["artifact"]["sha256"], cohort["input"]["sha256"])
        self.assertEqual(cohort["rows"], 2)
        self.assertEqual(cohort["prior_exposure"], self.config["preparation"]["external_cohort"]["prior_exposure"])
        self.assertEqual(MODULE.load(cohort["exposure_ledger"]["path"])["status"], "exposed")
        self.assertEqual(manifest["excluded_rows"], 2)
        self.assertEqual(manifest["development_rows"] + manifest["terminal_rows"] + manifest["excluded_rows"], len(self.rows))
        _, dev = MODULE.read_rows(manifest["development"]["path"])
        _, held = MODULE.read_rows(manifest["sealed_terminal"]["path"])
        self.assertFalse({row["output_row_id"] for row in dev + held} & {"rat-1", "mouse-1"})
        self.assertFalse(any(row["parent_smiles"] == "CC" for row in dev + held))
        with Path(cohort["exclusions"]["path"]).open(newline="", encoding="utf-8") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertEqual({row["output_row_id"] for row in ledger}, {"rat-1", "mouse-1"})
        self.assertTrue(all(json.loads(row["reasons"]) == ["canonical_parent_overlap"] for row in ledger))
        self.assertTrue(all(json.loads(row["matched_external_row_ids"]) == ["external-mouse", "external-rat"] for row in ledger))
        for task in ("rat", "mouse"):
            self.assertEqual(manifest["task_counts"][task]["external"], 1)
            self.assertEqual(manifest["task_counts"][task]["excluded"], 1)
        expected_sensitive = [MODULE.binding(self.curated), manifest["sealed_terminal"], manifest["grouping_mapping"],
                              cohort["input"], cohort["artifact"], cohort["exposure_ledger"], cohort["exclusions"]]
        self.assertEqual(manifest["custodian_only"], expected_sensitive)
        self.assertNotIn(manifest["development_grouping_mapping"], manifest["custodian_only"])
        self.assertEqual(manifest["development_grouping_mapping"], MODULE.binding(manifest["development_grouping_mapping"]["path"]))
        with Path(manifest["development_grouping_mapping"]["path"]).open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            self.assertNotIn("target_pLD50", reader.fieldnames)
            public_mapping = list(reader)
        self.assertEqual([row["output_row_id"] for row in public_mapping], [row["output_row_id"] for row in dev])
        self.assertTrue(all(row["partition"] == "development" and row["cohort"] == "curated" for row in public_mapping))
        self.assertFalse({row["output_row_id"] for row in public_mapping} & {row["output_row_id"] for row in held + ledger})
        with self.assertRaisesRegex(MODULE.PreparationError, "frozen split"):
            MODULE.generate_features(cohort["artifact"]["path"], self.root / "split/preprocessing_manifest.json",
                                     self.contract, self.root / "external-features")

    def test_external_overlap_does_not_remove_other_declared_group_members(self):
        for row in self.rows:
            if row["group_id"] == "g2":
                row["group_id"] = "g1"
        external = self.make_external()
        manifest = MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        _, dev = MODULE.read_rows(manifest["development"]["path"])
        _, held = MODULE.read_rows(manifest["sealed_terminal"]["path"])
        surviving = [row for row in dev + held if row["output_row_id"] in {"rat-2", "mouse-2"}]
        self.assertEqual(len(surviving), 2)
        self.assertEqual({row["group_id"] for row in surviving}, {"g1"})

    def test_external_parent_overlap_ignores_stereo_without_rewriting_source(self):
        for row in self.rows:
            if row["group_id"] == "g1":
                row["parent_smiles"] = "F[C@H](Cl)Br"
        external = self.make_external([{"output_row_id": "external-stereo", "task_id": "rat", "group_id": "external",
                                        "parent_smiles": "F[C@@H](Cl)Br", "target_pLD50": "1"}])
        before = external.read_bytes()
        manifest = MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        self.assertEqual(manifest["excluded_rows"], 2)
        self.assertEqual(Path(manifest["external_cohort"]["artifact"]["path"]).read_bytes(), before)
        with Path(manifest["external_cohort"]["exclusions"]["path"]).open(newline="", encoding="utf-8") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertEqual({row["output_row_id"] for row in ledger}, {"rat-1", "mouse-1"})
        self.assertEqual(len({row["canonical_parent_smiles"] for row in ledger}), 1)

    def test_atom_maps_do_not_bypass_external_identity_exclusion(self):
        external = self.make_external([{"output_row_id": "external-mapped", "task_id": "rat",
                                       "group_id": "external", "parent_smiles": "[CH3:1][CH3:2]",
                                       "target_pLD50": "1"}])
        before = external.read_bytes()
        manifest = MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        self.assertEqual(manifest["excluded_rows"], 2)
        self.assertEqual(Path(manifest["external_cohort"]["artifact"]["path"]).read_bytes(), before)
        with Path(manifest["external_cohort"]["exclusions"]["path"]).open(newline="", encoding="utf-8") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertEqual({row["output_row_id"] for row in ledger}, {"rat-1", "mouse-1"})

    def test_atom_maps_do_not_separate_parent_or_scaffold_groups(self):
        for policy, plain, mapped in (
                ("parent", "CCO", "[CH3:1][CH2:2][OH:3]"),
                ("bemis_murcko", "c1ccccc1", "[cH:1]1[cH:2][cH:3][cH:4][cH:5][cH:6]1")):
            rows = [{"output_row_id": str(i), "task_id": "t", "group_id": str(i), "parent_smiles": smi}
                    for i, smi in enumerate((plain, mapped))]
            mapping = MODULE.group_rows(rows, policy, empty_scaffold_policy="parent")
            with self.subTest(policy=policy):
                self.assertEqual(rows[0]["group_id"], rows[1]["group_id"])
                self.assertEqual(mapping[0]["canonical_parent_smiles"], mapping[1]["canonical_parent_smiles"])
                self.assertEqual(mapping[0]["scaffold_smiles"], mapping[1]["scaffold_smiles"])

    def test_external_exclusions_that_remove_a_task_block_without_dropping_rows(self):
        external = self.make_external([dict(row, output_row_id="external:" + row["output_row_id"]) for row in self.rows])
        before = external.read_bytes()
        with self.assertRaisesRegex(MODULE.PreparationError, "Infeasible task"):
            MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        evidence = MODULE.load(self.root / "split/feasibility.json")
        self.assertEqual(evidence["input_rows"], len(self.rows))
        self.assertEqual(evidence["excluded_rows"], len(self.rows))
        with (self.root / "split/external_exclusions.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), len(self.rows))
        self.assertEqual(external.read_bytes(), before)
        self.assertFalse((self.root / "split/preprocessing_manifest.json").exists())

    def test_external_scaffold_exclusion_is_opt_in_and_empty_scaffolds_do_not_match(self):
        for row in self.rows:
            if row["group_id"] in {"g0", "g1"}:
                row["parent_smiles"] = "Cc1ccccc1" if row["group_id"] == "g0" else "Oc1ccccc1"
        records = [{"output_row_id": "external-ring", "task_id": "rat", "group_id": "ring", "parent_smiles": "c1ccccc1", "target_pLD50": "1"},
                   {"output_row_id": "external-acyclic", "task_id": "mouse", "group_id": "acyclic", "parent_smiles": "CO", "target_pLD50": "2"}]
        external = self.make_external(records)
        default = MODULE.prepare_splits(self.curated, self.contract, self.root / "default", external_csv=external)
        self.assertEqual(default["excluded_rows"], 0)
        self.config["preparation"]["external_cohort"]["exclude_scaffold_overlap"] = True
        self.save()
        explicit = MODULE.prepare_splits(self.curated, self.contract, self.root / "explicit", external_csv=external)
        self.assertEqual(explicit["excluded_rows"], 4)
        self.assertEqual(explicit["development_rows"] + explicit["terminal_rows"], 20)
        with Path(explicit["external_cohort"]["exclusions"]["path"]).open(newline="", encoding="utf-8") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertTrue(all(json.loads(row["reasons"]) == ["bemis_murcko_overlap"] for row in ledger))

    def test_external_source_mutation_blocks_manifest(self):
        external = self.make_external()
        original = MODULE.read_rows
        def mutating(path, *args):
            result = original(path, *args)
            if Path(path) == external:
                with external.open("a", encoding="utf-8") as stream:
                    stream.write("\n")
            return result
        with patch.object(MODULE, "read_rows", side_effect=mutating):
            with self.assertRaisesRegex(MODULE.PreparationError, "changed during"):
                MODULE.prepare_splits(self.curated, self.contract, self.root / "split", external_csv=external)
        self.assertFalse((self.root / "split/preprocessing_manifest.json").exists())

    def test_external_requires_own_exposure_and_rejects_surviving_id_collision(self):
        external = self.make_external()
        del self.config["preparation"]["external_cohort"]["prior_exposure"]
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "External cohort exposure"):
            MODULE.prepare_splits(self.curated, self.contract, self.root / "bad-exposure", external_csv=external)
        external = self.make_external([{"output_row_id": "rat-0", "task_id": "rat", "group_id": "external-ring",
                                        "parent_smiles": "c1ccccc1", "target_pLD50": "1"}])
        with self.assertRaisesRegex(MODULE.PreparationError, "collision"):
            MODULE.prepare_splits(self.curated, self.contract, self.root / "collision", external_csv=external)
        with self.assertRaisesRegex(MODULE.PreparationError, "explicit external CSV"):
            self.split("missing-external")

    def test_split_cli_passes_external_input(self):
        external = self.make_external()
        argv = [str(SCRIPT), "split", "--curated", str(self.curated), "--contract", str(self.contract),
                "--external", str(external), "--output-dir", str(self.root / "split")]
        with patch.object(sys, "argv", argv), patch.object(MODULE, "prepare_splits", return_value={"stage": "preprocessing", "status": "complete"}) as prepare:
            self.assertEqual(MODULE.main(), 0)
        prepare.assert_called_once_with(str(self.curated), str(self.contract), str(self.root / "split"), None, str(external))

    def classification_fixture(self):
        self.rows = [dict(row, output_row_id=row["output_row_id"] + f":class{label}", target_label=str(label))
                     for row in self.rows for label in (0, 1)]
        self.config["training"] = {"task_type": "classification"}
        self.config["preparation"].update(target_column="target_label", stratify=True)
        self.save()

    def test_stratified_classification_nested_folds_preserve_groups_and_both_classes(self):
        self.classification_fixture()
        manifest = self.split()
        replay = self.split("replay")
        self.assertEqual(manifest["folds"]["sha256"], replay["folds"]["sha256"])
        lookup = {row["output_row_id"]: row for row in self.rows}
        for task in MODULE.load(manifest["folds"]["path"])["tasks"].values():
            outer_validation = []
            for outer in task["outer_folds"]:
                outer_validation.extend(outer["validation_row_ids"])
                for fold in [outer] + outer["inner_folds"]:
                    train, valid = fold["train_row_ids"], fold["validation_row_ids"]
                    self.assertEqual({lookup[row_id]["target_label"] for row_id in train}, {"0", "1"})
                    self.assertEqual({lookup[row_id]["target_label"] for row_id in valid}, {"0", "1"})
                    self.assertFalse({lookup[row_id]["group_id"] for row_id in train} & {lookup[row_id]["group_id"] for row_id in valid})
                self.assertCountEqual([row_id for fold in outer["inner_folds"] for row_id in fold["validation_row_ids"]], outer["train_row_ids"])
            self.assertCountEqual(outer_validation, task["row_ids"])
        self.assertEqual(manifest["stratification"]["scope"], "development folds only; terminal reservation is label-blind")

    def test_stratification_never_uses_terminal_labels(self):
        self.classification_fixture()
        first = self.split()
        _, terminal = MODULE.read_rows(first["sealed_terminal"]["path"])
        held_ids = {row["output_row_id"] for row in terminal}
        for row in self.rows:
            if row["output_row_id"] in held_ids:
                row["target_label"] = "0"
        self.save()
        changed = self.split("changed-terminal-labels")
        self.assertEqual(first["membership"]["sha256"], changed["membership"]["sha256"])
        self.assertEqual(first["folds"]["sha256"], changed["folds"]["sha256"])

    def test_stratification_matches_native_grouped_allocator(self):
        from sklearn.model_selection import StratifiedGroupKFold
        rows = [{"output_row_id": f"g{group}:r{replicate}", "group_id": f"g{group}", "label": str(group % 2)}
                for group in range(24) for replicate in range(1 + group % 3)]
        seed, scope = 29, "classification-reference"
        scoped_seed = int.from_bytes(hashlib.sha256(json.dumps([seed, scope], separators=(",", ":")).encode()).digest()[:4], "big")
        native = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=scoped_seed)
        expected = [{"fold": index, "train_row_ids": [rows[i]["output_row_id"] for i in train],
                     "validation_row_ids": [rows[i]["output_row_id"] for i in valid]}
                    for index, (train, valid) in enumerate(native.split([0] * len(rows), [int(row["label"]) for row in rows],
                                                                       [row["group_id"] for row in rows]))]
        self.assertEqual(MODULE.fold_sets(rows, 3, seed, scope, "label", True), expected)

    def test_stratification_invalid_labels_and_sparse_class_fail_closed(self):
        self.config["preparation"]["stratify"] = True
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "requires training.task_type=classification"):
            self.split("regression-stratification")
        self.classification_fixture()
        self.rows[0]["target_label"] = "0.5"
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "binary labels"):
            self.split("nonbinary")
        remaining = MODULE.ordered_groups([row["group_id"] for row in self.rows], 9, "terminal")[3:]
        positive_group = remaining[0]
        for row in self.rows:
            row["target_label"] = "1" if row["group_id"] == positive_group else "0"
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "Infeasible.*class 1"):
            self.split("sparse-class")
        evidence = MODULE.load(self.root / "sparse-class/feasibility.json")
        self.assertEqual(evidence["excluded_rows"], 0)
        self.assertFalse((self.root / "sparse-class/development.csv").exists())

    def test_morgan_count_feature_invariants_and_evaluator_replay(self):
        families = ("Morgan2", "Morgan3", "Morgan2Count", "Morgan3Count", "FCFP2", "FCFP3", "FCFP2Count", "FCFP3Count")
        self.config["features"]["families"] = {"RDKit2D": {}, **{name: {"n_bits": 64, "use_chirality": True} for name in families}}
        development_group = MODULE.ordered_groups([row["group_id"] for row in self.rows], 9, "terminal")[3]
        for row in self.rows:
            if row["group_id"] == development_group:
                row["parent_smiles"] = "C" * 300
        self.save()
        split = self.split()
        with patch.object(Descriptors, "_descList", [("atom_count", lambda mol: float(mol.GetNumAtoms()))]):
            manifest = self.features(split=split)
        with patch.object(sys, "path", [str(SCRIPT.parent)] + sys.path):
            import qsar_finish as finish
        _, rows = MODULE.read_rows(split["development"]["path"])
        smiles = [row["parent_smiles"] for row in rows]
        for family in families:
            record = manifest["families"][family]
            recipe = record["recipe"]
            arguments = dict(recipe["arguments"])
            if family.startswith("FCFP"):
                invariants = recipe["atom_invariants_generator"]
                arguments["atomInvariantsGenerator"] = getattr(F, invariants["factory"])(**invariants["arguments"])
            native = getattr(F, recipe["factory"])(**arguments)
            expected = np.empty(tuple(record["shape"]), dtype=record["dtype"])
            for index, smi in enumerate(smiles):
                vector = getattr(native, recipe["method"])(Chem.MolFromSmiles(smi))
                DataStructs.ConvertToNumpyArray(vector, expected[index])
            with np.load(record["artifact"]["path"], allow_pickle=False) as data:
                np.testing.assert_array_equal(data["X"], expected)
                np.testing.assert_array_equal(data["X"], finish.replay(smiles, family, record, manifest["environment"]))
                self.assertEqual(str(data["X"].dtype), "uint32" if family.endswith("Count") else "uint8")
                self.assertEqual(data["feature_names"].tolist(), MODULE.fingerprint_feature_names(family, 64, recipe))
                self.assertEqual(data["row_ids"].tolist(), manifest["row_ids"])
                if family.endswith("Count"):
                    self.assertGreater(int(data["X"].max()), 255)
                else:
                    self.assertTrue(np.isin(data["X"], [0, 1]).all())
        with self.assertRaisesRegex(MODULE.PreparationError, "Unsupported family parameters"):
            MODULE.fingerprint_generator("Morgan2", {"n_bits": 64, "use_chirality": True, "counts": True})

    def test_missing_exposure_and_unknown_family_fail_closed(self):
        del self.config["preparation"]["prior_exposure"]
        self.save()
        with self.assertRaisesRegex(MODULE.PreparationError, "exposure"):
            self.split()
        with self.assertRaisesRegex(MODULE.PreparationError, "Unsupported"):
            MODULE.fingerprint_generator("imaginary", {"n_bits": 64})



class OrganicPrincipalPreparationTests(unittest.TestCase):
    def test_shared_parent_group_all_rows_and_all_feature_families(self):
        from scripts.test_qsar_material_integration import MaterialCurationTests, data
        fixture=PreparationTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        maker=MaterialCurationTests(); self.addCleanup(maker.doCleanups)
        f=maker.fixture(organic_graph='CC[NH3+].O=C([O-])C(=O)O',principal='CC[NH3+]',legacy_sibling=True,
                        feature_families=fixture.config['features']['families'])
        data.curate(f.contract,f.registry_path,f.out,material_config_path=f.packet['path'])
        _,rows=MODULE.read_rows(f.out/'curated.csv')
        same=[r for r in rows if r['parent_smiles']=='CC[NH3+]']
        self.assertEqual(len(same),2)
        self.assertEqual(len({r['group_id'] for r in same}),1)
        self.assertNotEqual(same[0]['target_pLD50'],same[1]['target_pLD50'])
        split=MODULE.prepare_splits(f.out/'curated.csv',f.contract,f.root/'split',f.manifest)
        with (f.root/'split/membership.csv').open(newline='',encoding='utf-8') as stream:
            members=list(csv.DictReader(stream))
        self.assertEqual(len(members),11)
        self.assertEqual(len({r['partition'] for r in members if r['output_row_id'] in {s['output_row_id'] for s in same}}),1)
        manifest=MODULE.generate_features(split['development']['path'],f.root/'split/preprocessing_manifest.json',f.contract,f.root/'features')
        self.assertEqual(set(manifest['families']),set(fixture.config['features']['families']))


if __name__ == '__main__':
    unittest.main()
