"""Synthetic training-reference domain fixtures; no evaluation-derived fitting."""
import builtins
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"))
import qsar_domain as domain


def recipe(**updates):
    value = {"radius": 2, "n_bits": 256, "use_chirality": True, "similarity": "max",
             "min_references": 1, "cutoff": {"method": "fixed", "value": .5}}
    value.update(updates)
    return value


def fingerprints(smiles, spec):
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=spec["radius"], fpSize=spec["n_bits"],
                                                          includeChirality=spec["use_chirality"])
    return [generator.GetFingerprint(Chem.MolFromSmiles(value)) for value in smiles]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def rehash(artifact):
    for field, hash_field in (("recipe", "recipe_sha256"), ("training_rows", "training_sha256"),
                              ("references", "references_sha256"), ("calibration", "calibration_sha256")):
        artifact[hash_field] = digest(artifact[field])
    artifact["artifact_sha256"] = digest({key: value for key, value in artifact.items() if key != "artifact_sha256"})
    return artifact


class ApplicabilityDomainTests(unittest.TestCase):
    structures = ["CCO", "OCC", "CCC", "c1ccccc1"]
    ids = ["ethanol-a", "ethanol-b", "propane", "benzene"]

    def fit(self, **updates):
        return domain.fit(self.structures, self.ids, recipe(**updates))

    def test_real_max_tanimoto_and_all_training_identities_persist(self):
        spec = recipe()
        model = self.fit()
        query = fingerprints(["CCN"], spec)[0]
        expected = max(DataStructs.BulkTanimotoSimilarity(query, fingerprints(["CCO", "CCC", "c1ccccc1"], spec)))
        scored = model.score(["CCN"], ["query"])[0]
        self.assertAlmostEqual(scored["similarity"], expected)
        self.assertEqual(scored["row_id"], "query")
        self.assertEqual(scored["in_domain"], expected >= .5)
        self.assertEqual(scored["reference_count"], 3)
        self.assertEqual(scored["cutoff"], .5)
        artifact = model.to_dict()
        self.assertEqual(model.training_row_ids, self.ids)
        ethanol = next(ref for ref in artifact["references"] if ref["parent_smiles"] == "CCO")
        self.assertEqual(ethanol["row_ids"], self.ids[:2])
        self.assertEqual(ethanol["on_bits"], list(fingerprints(["CCO"], spec)[0].GetOnBits()))
        self.assertEqual(len(artifact["training_rows"]), 4)

    def test_knn_mean_uses_exact_configured_k(self):
        spec = recipe(similarity="knn_mean", k=2)
        model = self.fit(similarity="knn_mean", k=2)
        query = fingerprints(["CCN"], spec)[0]
        similarities = sorted(DataStructs.BulkTanimotoSimilarity(query, fingerprints(["CCO", "CCC", "c1ccccc1"], spec)), reverse=True)
        self.assertAlmostEqual(model.score(["CCN"])[0]["similarity"], sum(similarities[:2]) / 2)
        with self.assertRaisesRegex(domain.DomainError, "k"):
            self.fit(similarity="knn_mean")
        with self.assertRaisesRegex(domain.DomainError, "Infeasible"):
            self.fit(similarity="knn_mean", k=4)

    def test_training_quantile_is_leave_parent_out_and_not_replicate_weighted(self):
        spec = recipe(cutoff={"method": "training_quantile", "quantile": .5})
        model = self.fit(cutoff=spec["cutoff"])
        fps = fingerprints(["CCO", "CCC", "c1ccccc1"], spec)
        expected = [max(DataStructs.BulkTanimotoSimilarity(fp, fps[:i] + fps[i + 1:])) for i, fp in enumerate(fps)]
        self.assertEqual(model.cutoff, sorted(expected)[1])
        calibration = model.to_dict()["calibration"]
        self.assertEqual(calibration["exclusion"], "canonical_parent")
        self.assertEqual(calibration["quantile_rule"], "lower_order_statistic")
        self.assertEqual(len(calibration["similarities"]), 3)
        self.assertTrue(all(row["reference_count"] == 2 for row in calibration["similarities"]))
        independent = domain.fit(["CCO", "CCC", "c1ccccc1"], ["a", "b", "c"], spec)
        self.assertEqual(model.cutoff, independent.cutoff)
        original = model.to_dict()
        model.score(["CCO", "N#N", "invalid SMILES"], ["evaluation-1", "evaluation-2", "evaluation-3"])
        self.assertEqual(original, model.to_dict())

    def test_lower_quantile_endpoints_and_exact_knn_calibration(self):
        for quantile in (0, .25, 1):
            with self.subTest(quantile=quantile):
                model = self.fit(similarity="knn_mean", k=2,
                                 cutoff={"method": "training_quantile", "quantile": quantile})
                values = sorted(row["similarity"] for row in model.to_dict()["calibration"]["similarities"])
                self.assertEqual(model.cutoff, values[int((len(values) - 1) * quantile)])
        with self.assertRaisesRegex(domain.DomainError, "Infeasible"):
            self.fit(similarity="knn_mean", k=3, cutoff={"method": "training_quantile", "quantile": .1})

    def test_exclusion_is_opt_in_and_removes_all_parent_replicates(self):
        model = self.fit()
        regular = model.score(["OCC"], ["ethanol-a"])[0]
        diagnostic = model.score(["OCC"], ["ethanol-a"], exclude_own_identity=True)[0]
        self.assertEqual(regular["similarity"], 1)
        self.assertEqual(regular["reference_count"], 3)
        self.assertEqual(diagnostic["reference_count"], 2)
        self.assertLess(diagnostic["similarity"], 1)
        self.assertEqual(model.score(["CCO"], exclude_own_identity=True)[0]["reference_count"], 2)
        with self.assertRaisesRegex(domain.DomainError, "identity/structure mismatch"):
            model.score(["CCN"], ["ethanol-a"], exclude_own_identity=True)
        self.assertEqual(len(model.score(["CCN"], ["ethanol-a"])), 1)

    def test_invalid_and_ood_rows_abstain_or_report_without_dropping(self):
        model = self.fit(cutoff={"method": "fixed", "value": 1})
        queries = ["CCO", "N#N", "not a molecule", "", None]
        values = model.score(queries, ["a", "b", "c", "d", "e"])
        self.assertEqual(len(values), len(queries))
        self.assertIs(values[0]["in_domain"], True)
        self.assertEqual(values[0]["reason"], "within_cutoff")
        self.assertIs(values[1]["in_domain"], False)
        self.assertEqual(values[1]["reason"], "below_cutoff")
        for value in values[2:]:
            self.assertIsNone(value["similarity"])
            self.assertIsNone(value["in_domain"])
            self.assertEqual(value["reason"], "invalid_query_structure")
        self.assertEqual(model.score([]), [])
        self.assertIsNone(model.score(["CCO"])[0]["row_id"])

    def test_diagnostic_reference_shortage_does_not_lower_k_or_cutoff(self):
        model = domain.fit(["CCO"], ["one"], recipe())
        result = model.score(["CCO"], ["one"], exclude_own_identity=True)[0]
        self.assertIsNone(result["in_domain"])
        self.assertIsNone(result["similarity"])
        self.assertEqual(result["reason"], "insufficient_references")
        self.assertEqual(result["reference_count"], 0)
        self.assertEqual(result["cutoff"], .5)
        knn = domain.fit(["CCO", "CCC"], ["one", "two"], recipe(similarity="knn_mean", k=2))
        shortage = knn.score(["CCO"], ["one"], exclude_own_identity=True)[0]
        self.assertEqual(shortage["reference_count"], 1)
        self.assertIsNone(shortage["similarity"])
        self.assertEqual(shortage["reason"], "insufficient_references")
        with self.assertRaisesRegex(domain.DomainError, "Infeasible"):
            domain.fit(["CCO", "OCC"], ["a", "b"], recipe(min_references=2))

    def test_invalid_training_inputs_fail_without_silent_exclusions(self):
        for structures, ids in ((["bad"], ["bad-id"]), (["CCO"], []), (["CCO", "CCC"], ["a", "a"]),
                                 (["CCO"], [""]), ([], []), ("CCO", ["a"])):
            with self.subTest(structures=structures, ids=ids), self.assertRaises(domain.DomainError):
                domain.fit(structures, ids, recipe())
        for ids in (["a", "a"], ["a"], [None, "b"]):
            with self.subTest(ids=ids), self.assertRaises(domain.DomainError):
                self.fit().score(["CCO", "CCC"], ids)

    def test_invalid_recipes_reject_implicit_or_ignored_choices(self):
        changes = [{"radius": True}, {"n_bits": 0}, {"use_chirality": 1}, {"min_references": 0},
                   {"similarity": "average"}, {"k": 2}, {"similarity": "knn_mean", "k": True},
                   {"cutoff": {"method": "fixed", "value": float("nan")}},
                   {"cutoff": {"method": "fixed", "value": True}},
                   {"cutoff": {"method": "training_quantile", "quantile": -.1}},
                   {"cutoff": {"method": ["fixed"], "value": .5}},
                   {"cutoff": {"method": "fixed", "value": .5, "evaluation_quantile": .1}}, {"ignored": True}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(domain.DomainError):
                self.fit(**change)
        missing = recipe()
        del missing["radius"]
        with self.assertRaises(domain.DomainError):
            domain.fit(self.structures, self.ids, missing)

    def test_parent_normalization_removes_atom_maps_but_not_stereochemistry(self):
        model = domain.fit(["[CH3:1][CH2:2][OH:3]", "OCC"], ["mapped", "unmapped"], recipe())
        self.assertEqual(model.reference_count, 1)
        self.assertEqual(model.to_dict()["references"][0]["parent_smiles"], "CCO")
        stereo = domain.fit(["F[C@](Cl)(Br)I", "F[C@@](Cl)(Br)I"], ["left", "right"], recipe(n_bits=2048))
        self.assertEqual(stereo.reference_count, 2)
        bits = [row["on_bits"] for row in stereo.to_dict()["references"]]
        self.assertNotEqual(bits[0], bits[1])

    def test_roundtrip_verifies_every_hash_and_protects_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "domain.json"
            model = self.fit(cutoff={"method": "training_quantile", "quantile": .25})
            receipt = domain.save(model, path)
            original = path.read_bytes()
            self.assertEqual(receipt["sha256"], hashlib.sha256(original).hexdigest())
            restored = domain.load(path)
            self.assertEqual(restored.to_dict(), model.to_dict())
            self.assertEqual(restored.score(["CCN", "OCC"]), model.score(["CCN", "OCC"]))
            with self.assertRaisesRegex(domain.DomainError, "exists"):
                model.save(path)
            self.assertEqual(original, path.read_bytes())
            for field in ("recipe_sha256", "training_sha256", "references_sha256", "calibration_sha256", "artifact_sha256"):
                tampered = copy.deepcopy(model.to_dict())
                tampered[field] = "0" * 64
                if field != "artifact_sha256":
                    tampered["artifact_sha256"] = digest({key: value for key, value in tampered.items() if key != "artifact_sha256"})
                with self.subTest(field=field), self.assertRaisesRegex(domain.DomainError, "hash mismatch"):
                    domain.ApplicabilityDomain(tampered)

    def test_pickle_roundtrip_rebuilds_native_generator_and_validates_artifact(self):
        model = self.fit(cutoff={"method": "training_quantile", "quantile": .25})
        restored = pickle.loads(pickle.dumps(model))
        self.assertEqual(restored.to_dict(), model.to_dict())
        self.assertEqual(restored.score(["CCN", "OCC"]), model.score(["CCN", "OCC"]))
        model._artifact["cutoff"] = .123456
        with self.assertRaisesRegex(domain.DomainError, "hash mismatch"):
            pickle.loads(pickle.dumps(model))

    def test_rehashed_false_bits_cutoff_and_runtime_versions_rejected(self):
        artifact = self.fit().to_dict()
        corrupt = copy.deepcopy(artifact)
        corrupt["references"][0]["on_bits"] = []
        with self.assertRaisesRegex(domain.DomainError, "reproduce"):
            domain.ApplicabilityDomain(rehash(corrupt))
        corrupt = copy.deepcopy(artifact)
        corrupt["cutoff"] = .99
        corrupt["calibration"]["value"] = .99
        with self.assertRaisesRegex(domain.DomainError, "reproduce"):
            domain.ApplicabilityDomain(rehash(corrupt))
        for field in ("rdkit_version", "recipe_version"):
            corrupt = copy.deepcopy(artifact)
            corrupt[field] = "different-runtime"
            with self.subTest(field=field), self.assertRaisesRegex(domain.DomainError, "version mismatch"):
                domain.ApplicabilityDomain(rehash(corrupt))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
            with self.assertRaisesRegex(domain.DomainError, "Duplicate JSON key"):
                domain.load(path)
            model = self.fit()
            model._artifact["references"][0]["on_bits"] = []
            with self.assertRaises(domain.DomainError):
                domain.save(model, Path(folder) / "not-created.json")
            self.assertFalse((Path(folder) / "not-created.json").exists())

    def test_fit_and_score_do_not_import_sklearn(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == "sklearn" or name.startswith("sklearn."):
                raise AssertionError("Domain must be sklearn-independent")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            model = self.fit()
            self.assertEqual(model.score(["CCO"])[0]["similarity"], 1)

    def test_cli_emits_json_and_keeps_invalid_queries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "train.csv").write_text("output_row_id,parent_smiles\na,CCO\nb,CCC\n", encoding="utf-8")
            (root / "recipe.json").write_text(json.dumps(recipe()), encoding="utf-8")
            (root / "queries.csv").write_text("output_row_id,parent_smiles\nq,CCO\nr,invalid\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = domain.main(["fit", "--training", str(root / "train.csv"), "--recipe", str(root / "recipe.json"),
                                    "--out", str(root / "domain.json")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["path"], str((root / "domain.json").resolve()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = domain.main(["score", "--artifact", str(root / "domain.json"), "--queries", str(root / "queries.csv"),
                                    "--out", str(root / "scores.json")])
            self.assertEqual(code, 0)
            scores = json.loads((root / "scores.json").read_text(encoding="utf-8"))["scores"]
            self.assertEqual(len(scores), 2)
            self.assertEqual(scores[1]["reason"], "invalid_query_structure")


if __name__ == "__main__":
    unittest.main()
