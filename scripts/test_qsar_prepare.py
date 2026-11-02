"""Synthetic-only split/feature regression tests; no original research sources."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from rdkit.Chem import Descriptors

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
