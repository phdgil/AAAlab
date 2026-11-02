"""Synthetic SMILES adapter checks; no scientific data or downloaded checkpoints.

Optional encoder checks generate a tiny random local model to verify adapter
mechanics only, never pretrained scientific quality. No downloads, fabricated
embeddings or claims of encoder fine-tuning are used.
"""
import hashlib
import io
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.base import clone, is_classifier, is_regressor
from sklearn.exceptions import NotFittedError
from sklearn.pipeline import Pipeline

SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_models as models


SMILES = ["C", "CC", "CCO", "CCN", "c1ccccc1", "Oc1ccccc1", "c1ccncc1", "CC(=O)O"]
TARGET = np.array([0.1, 0.4, 1.0, 1.4, 1.8, 2.3, 2.7, 1.2])
LABELS = np.array([0, 0, 0, 1, 0, 1, 1, 0])
RULES = {"hydroxyl": "[OX2H]", "nitrogen": "[#7]"}


class ChemicalRuleTests(unittest.TestCase):
    def test_clone_parameters_and_sklearn_tags(self):
        for estimator in (models.ChemicalRuleRegressor(smarts_rules=RULES),
                          models.ChemicalRuleClassifier(smarts_rules=RULES),
                          models.ChemicalRuleBasis(smarts_rules=RULES)):
            cloned = clone(estimator)
            self.assertEqual(cloned.get_params(), estimator.get_params())
            self.assertIs(cloned.set_params(n_bins=3), cloned)
            self.assertEqual(cloned.get_params()["n_bins"], 3)
            self.assertFalse(hasattr(cloned, "model_"))
        self.assertTrue(is_regressor(models.ChemicalRuleRegressor()))
        self.assertTrue(is_classifier(models.ChemicalRuleClassifier()))
        with self.assertRaises(ValueError):
            models.ChemicalRuleRegressor().set_params(undeclared_option=True)

    def test_descriptor_ranges_bins_and_scaffolds_are_fit_local(self):
        basis = models.ChemicalRuleBasis(descriptor_names=("MolWt",), n_bins=2, smarts_rules=RULES)
        basis.fit(["CC", "CCO", "c1ccccc1", "Oc1ccccc1"])
        before = pickle.dumps((basis.descriptor_ranges_, basis.descriptor_bin_edges_, basis.scaffold_counts_,
                               basis.scaffold_vocabulary_, basis.scaffold_frequencies_))
        values = basis.transform(["C", "CCCCCCCCCCCCCCCCCCCC", "c1ccncc1", "CC"])
        names = basis.get_feature_names_out().tolist()
        self.assertEqual(values[0, names.index("descriptor:MolWt:below")], 1)
        self.assertEqual(values[1, names.index("descriptor:MolWt:above")], 1)
        self.assertEqual(values[0, names.index("descriptor:MolWt:range")], 0)
        self.assertEqual(values[1, names.index("descriptor:MolWt:range")], 1)
        bins = [i for i, name in enumerate(names) if name.startswith("descriptor:MolWt:bin:")]
        np.testing.assert_array_equal(values[:, bins].sum(axis=1), np.ones(4))
        self.assertEqual(values[2, names.index("scaffold:unseen")], 1)
        self.assertEqual(values[2, names.index("scaffold:training_frequency")], 0)
        self.assertEqual(values[3, names.index("scaffold:absent")], 1)
        self.assertEqual(values[3, names.index("scaffold:unseen")], 0)
        self.assertEqual(basis.scaffold_counts_, {"c1ccccc1": 2})
        self.assertEqual(basis.scaffold_frequencies_, {"c1ccccc1": 0.5})
        self.assertEqual(before, pickle.dumps((basis.descriptor_ranges_, basis.descriptor_bin_edges_, basis.scaffold_counts_,
                                              basis.scaffold_vocabulary_, basis.scaffold_frequencies_)))

    def test_atom_map_annotations_do_not_change_scaffold_or_rule_features(self):
        basis = models.ChemicalRuleBasis(descriptor_names=("MolWt",), n_bins=2, smarts_rules=RULES)
        basis.fit(["CC", "CCO", "c1ccccc1"])
        plain = basis.transform(["c1ccccc1", "CCO"])
        mapped = basis.transform(["[cH:1]1[cH:2][cH:3][cH:4][cH:5][cH:6]1", "[CH3:7][CH2:8][OH:9]"])
        np.testing.assert_allclose(mapped, plain)

    def test_constant_descriptor_has_one_bin_and_no_division_artifact(self):
        basis = models.ChemicalRuleBasis(descriptor_names=("NumHDonors",), n_bins=5).fit(["C", "CC"])
        names = basis.get_feature_names_out().tolist()
        values = basis.transform(["C", "CCO"])
        bins = [i for i, name in enumerate(names) if name.startswith("descriptor:NumHDonors:bin:")]
        self.assertEqual(len(bins), 1)
        np.testing.assert_array_equal(values[:, bins], np.ones((2, 1)))
        np.testing.assert_array_equal(values[:, names.index("descriptor:NumHDonors:range")], [0, 0])
        self.assertEqual(values[1, names.index("descriptor:NumHDonors:above")], 1)
        self.assertTrue(np.isfinite(values).all())

    def test_smarts_presence_is_fixed_not_occurrence_count_or_target_discovery(self):
        contract = {"oxygen": "[#8]", "nitrogen": "[#7]"}
        basis = models.ChemicalRuleBasis(descriptor_names=(), smarts_rules=contract).fit(SMILES, TARGET)
        contract["oxygen"] = "[#6]"
        names = basis.get_feature_names_out().tolist()
        values = basis.transform(["OCCO", "C", "CCN"])
        np.testing.assert_array_equal(values[:, names.index("smarts:oxygen")], [1, 0, 0])
        np.testing.assert_array_equal(values[:, names.index("smarts:nitrogen")], [0, 0, 1])
        self.assertEqual(basis.smarts_rules_["oxygen"], "[#8]")
        self.assertEqual(names[:2], ["smarts:nitrogen", "smarts:oxygen"])

    def test_regression_is_real_ridge_with_exact_additive_explanation(self):
        model = models.ChemicalRuleRegressor(smarts_rules=RULES, alpha=0.3).fit(SMILES, TARGET)
        from sklearn.linear_model import Ridge
        self.assertIsInstance(model.model_, Ridge)
        values = model.basis_.transform(SMILES)
        reference = Ridge(alpha=0.3, solver="svd").fit(values, TARGET)
        np.testing.assert_allclose(model.predict(SMILES), reference.predict(values), rtol=0, atol=0)
        explanation = model.explain(["CCO", "c1ccncc1"])
        reconstructed = explanation["intercept"] + np.asarray(explanation["contributions"]).sum(axis=1)
        np.testing.assert_allclose(reconstructed, model.predict(["CCO", "c1ccncc1"]), atol=1e-12)
        self.assertEqual(explanation["score_space"], "regression_prediction")
        self.assertEqual(model.scoring_method()["method"], "fitted_rule_derived_additive_scorer")
        self.assertFalse(model.scoring_method()["expert_rules_only"])
        self.assertFalse(model.scoring_method()["decision_tree"])
        self.assertFalse(hasattr(model, "predict_proba"))

    def test_binary_probabilities_threshold_and_log_odds_explanation(self):
        model = models.ChemicalRuleClassifier(smarts_rules=RULES, C=2.0, threshold=0.8).fit(SMILES, LABELS)
        from sklearn.linear_model import LogisticRegression
        self.assertIsInstance(model.model_, LogisticRegression)
        np.testing.assert_array_equal(model.classes_, [0, 1])
        self.assertEqual(model.positive_class_, 1)
        probabilities = model.predict_proba(SMILES)
        np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(len(SMILES)))
        np.testing.assert_array_equal(model.predict(SMILES), (probabilities[:, 1] >= 0.8).astype(int))
        explanation = model.explain(SMILES)
        scores = explanation["intercept"] + np.asarray(explanation["contributions"]).sum(axis=1)
        np.testing.assert_allclose(scores, model.decision_function(SMILES), atol=1e-12)
        np.testing.assert_allclose(1 / (1 + np.exp(-scores)), probabilities[:, 1], atol=1e-12)
        self.assertEqual(explanation["score_space"], "class_1_log_odds")
        np.testing.assert_allclose(explanation["probabilities"], probabilities)
        with self.assertRaisesRegex(models.ChemicalModelError, "both labels"):
            models.ChemicalRuleClassifier().fit(SMILES, np.zeros(len(SMILES)))
        with self.assertRaisesRegex(models.ChemicalModelError, "both labels"):
            models.ChemicalRuleClassifier().fit(SMILES, np.arange(len(SMILES)))

    def test_repeated_fit_and_pickle_joblib_replay_are_deterministic(self):
        probe = ["CCO", "c1ccccc1", "c1ccncc1", "C1CCCCC1"]
        for estimator, target in ((models.ChemicalRuleRegressor(smarts_rules=RULES), TARGET),
                                  (models.ChemicalRuleClassifier(smarts_rules=RULES), LABELS)):
            with self.subTest(type=type(estimator).__name__):
                fitted = estimator.fit(SMILES, target)
                replay = clone(estimator).fit(SMILES, target)
                np.testing.assert_array_equal(fitted.predict(probe), replay.predict(probe))
                payload = io.BytesIO()
                joblib.dump(fitted, payload)
                payload.seek(0)
                restored_models = [pickle.loads(pickle.dumps(fitted)), joblib.load(payload)]
                for restored in restored_models:
                    np.testing.assert_array_equal(restored.predict(probe), fitted.predict(probe))
                    self.assertEqual(restored.explain(probe), fitted.explain(probe))
                    if hasattr(fitted, "predict_proba"):
                        np.testing.assert_array_equal(restored.predict_proba(probe), fitted.predict_proba(probe))

    def test_sklearn_pipeline_and_column_inputs(self):
        pipe = Pipeline([("rules", models.ChemicalRuleClassifier(smarts_rules=RULES))])
        pipe.fit(np.asarray(SMILES, dtype=object).reshape(-1, 1), LABELS)
        reference = pipe.predict_proba(SMILES)
        np.testing.assert_array_equal(reference, pipe.predict_proba({"parent_smiles": SMILES}))
        array = np.asarray([["ignored", value] for value in SMILES], dtype=object)
        model = models.ChemicalRuleClassifier(smarts_rules=RULES, smiles_column=1).fit(array, LABELS)
        np.testing.assert_array_equal(reference, model.predict_proba(array))
        with self.assertRaisesRegex(models.ChemicalModelError, "integer smiles_column"):
            models.ChemicalRuleBasis().fit(array)
        with self.assertRaisesRegex(models.ChemicalModelError, "Missing SMILES"):
            pipe.predict_proba({"another_column": SMILES})

    def test_sample_weights_have_real_effect_and_invalid_weights_fail(self):
        weights = np.array([1, 1, 1, 1, 1, 1, 10, 1], dtype=float)
        for estimator, target in ((models.ChemicalRuleRegressor(alpha=3), TARGET),
                                  (models.ChemicalRuleClassifier(C=0.3), LABELS)):
            with self.subTest(type=type(estimator).__name__):
                baseline = clone(estimator).fit(SMILES, target)
                weighted = clone(estimator).fit(SMILES, target, sample_weight=weights)
                self.assertFalse(np.allclose(baseline.coef_, weighted.coef_))
                for invalid in (np.zeros(8), [-1] + [1] * 7, [float("nan")] * 8, [1, 2]):
                    with self.assertRaisesRegex(models.ChemicalModelError, "sample_weight"):
                        clone(estimator).fit(SMILES, target, sample_weight=invalid)
        with self.assertRaisesRegex(models.ChemicalModelError, "Both binary classes"):
            models.ChemicalRuleClassifier().fit(SMILES, LABELS, sample_weight=1 - LABELS)

    def test_strict_invalid_smiles_rejects_whole_fit_and_prediction(self):
        invalid = [None, "", " ", float("nan"), "not_smiles", "C trailing-name", "C\tname", "C(C", "[CH5]"]
        fitted = models.ChemicalRuleRegressor().fit(SMILES, TARGET)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(models.ChemicalModelError, "row 1"):
                    models.ChemicalRuleRegressor().fit(["CC", value], [0.0, 1.0])
                with self.assertRaisesRegex(models.ChemicalModelError, "row 1"):
                    fitted.predict(["CC", value])
        with self.assertRaisesRegex(models.ChemicalModelError, "nonempty"):
            fitted.predict([])
        with self.assertRaisesRegex(models.ChemicalModelError, "one-dimensional"):
            fitted.predict("CC")
        with self.assertRaises(NotFittedError):
            models.ChemicalRuleClassifier().predict(["CC"])
        with self.assertRaises(NotFittedError):
            models.ChemicalRuleRegressor().scoring_method()

    def test_invalid_basis_contract_targets_and_thresholds_fail(self):
        for options in ({"smarts_rules": {"broken": "["}}, {"descriptor_names": ("missing",)},
                        {"descriptor_names": ("MolWt", "MolWt")}, {"n_bins": 0}, {"n_bins": True}):
            with self.subTest(options=options), self.assertRaises(models.ChemicalModelError):
                models.ChemicalRuleBasis(**options).fit(SMILES)
        for target in ([float("nan")] * 8, [0.0], [[0.0]] * 8):
            with self.assertRaisesRegex(models.ChemicalModelError, "Targets"):
                models.ChemicalRuleRegressor().fit(SMILES, target)
        for value in (-0.1, 1.1, float("nan")):
            with self.assertRaisesRegex(models.ChemicalModelError, "threshold"):
                models.ChemicalRuleClassifier(threshold=value).fit(SMILES, LABELS)
        with self.assertRaisesRegex(models.ChemicalModelError, "Nonfinite descriptor"):
            descriptors = models.importlib.import_module("rdkit.Chem.Descriptors")
            with patch.object(descriptors, "_descList", [("broken", lambda mol: float("nan"))]):
                models.ChemicalRuleBasis(descriptor_names=("broken",)).fit(SMILES)

    def test_missing_chemistry_dependency_does_not_create_fallback_features(self):
        original = models.importlib.import_module
        def without_rdkit(name, *args, **kwargs):
            if name == "rdkit.Chem":
                raise ModuleNotFoundError("synthetic missing RDKit dependency path")
            return original(name, *args, **kwargs)
        with patch.object(models.importlib, "import_module", side_effect=without_rdkit):
            with self.assertRaisesRegex(models.ModelDependencyError, "require RDKit"):
                models.ChemicalRuleRegressor().fit(SMILES, TARGET)


class FixedExpertTests(unittest.TestCase):
    def test_fixed_expert_inference_is_separate_and_never_fitted(self):
        expert = models.FixedExpertSmartsScorer({"oxygen": "[#8]", "nitrogen": "[#7]"},
                                               {"oxygen": 2.0, "nitrogen": -3.0}, intercept=0.25)
        self.assertFalse(hasattr(expert, "fit"))
        self.assertFalse(hasattr(expert, "predict_proba"))
        self.assertFalse(expert.scoring_method()["fitted"])
        np.testing.assert_array_equal(expert.predict(["OCCO", "CCN", "C"]), [2.25, -2.75, 0.25])
        explanation = expert.explain(["OCCO", "CCN", "C"])
        np.testing.assert_array_equal(explanation["intercept"] + np.asarray(explanation["contributions"]).sum(axis=1),
                                      expert.predict(["OCCO", "CCN", "C"]))
        self.assertEqual(explanation["score_space"], "unfitted_expert_score")
        self.assertEqual(clone(expert).get_params(), expert.get_params())
        with self.assertRaisesRegex(models.ChemicalModelError, "exactly"):
            models.FixedExpertSmartsScorer(RULES, {"hydroxyl": 1}).predict(["CCO"])
        with self.assertRaisesRegex(models.ChemicalModelError, "finite"):
            models.FixedExpertSmartsScorer({"oxygen": "[#8]"}, {"oxygen": float("inf")}).predict(["CCO"])


class FrozenEncoderFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qsar-encoder-failure-fixture-")
        self.root = Path(self.temporary.name)
        # This is deliberately NOT a usable model. No positive inference is mocked.
        self.checkpoint = self.root / "checkpoint"
        self.checkpoint.mkdir()
        self.config = self.checkpoint / "config.json"
        self.config.write_text('{"failure_fixture_only": true}\n', encoding="utf-8")
        self.inventory = {"config.json": hashlib.sha256(self.config.read_bytes()).hexdigest()}

    def tearDown(self):
        self.temporary.cleanup()

    def encoder(self, **options):
        parameters = {"checkpoint_dir": self.checkpoint, "checkpoint_inventory": self.inventory, "pooling": "mean"}
        parameters.update(options)
        return models.FrozenSmilesEncoder(**parameters)

    def test_missing_checkpoint_fails_before_loading_optional_dependencies(self):
        with patch.object(models, "_encoder_dependencies") as dependencies:
            with self.assertRaisesRegex(models.ChemicalModelError, "checkpoint directory is missing"):
                self.encoder(checkpoint_dir=self.root / "absent").fit(["CC"])
            dependencies.assert_not_called()
        with self.assertRaises(NotFittedError):
            self.encoder().transform(["CC"])
        with self.assertRaises(NotFittedError):
            self.encoder().encoding_method()

    def test_inventory_is_exact_hashed_and_path_safe_before_dependency_loading(self):
        invalid = [{}, {"config.json": "bad-hash"}, {"config.json": "0" * 64},
                   {"../config.json": self.inventory["config.json"]},
                   {"C:/config.json": self.inventory["config.json"]},
                   {"nested\\config.json": self.inventory["config.json"]}]
        for inventory in invalid:
            with self.subTest(inventory=inventory), patch.object(models, "_encoder_dependencies") as dependencies:
                with self.assertRaises(models.ChemicalModelError):
                    self.encoder(checkpoint_inventory=inventory).fit(["CC"])
                dependencies.assert_not_called()
        (self.checkpoint / "unpinned-file.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(models.ChemicalModelError, "inventory mismatch"):
            self.encoder().fit(["CC"])

    def test_mutated_pinned_file_is_rejected_without_model_loading(self):
        self.config.write_text("changed", encoding="utf-8")
        with patch.object(models, "_encoder_dependencies") as dependencies:
            with self.assertRaisesRegex(models.ChemicalModelError, "inventory mismatch"):
                self.encoder().fit(["CC"])
            dependencies.assert_not_called()

    def test_missing_torch_or_transformers_is_explicit_not_fake_embeddings(self):
        original = models.importlib.import_module
        for missing in ("torch", "transformers"):
            def dependency(name, *args, missing=missing, **kwargs):
                if name == missing:
                    raise ModuleNotFoundError("synthetic missing optional dependency path: " + missing)
                return original(name, *args, **kwargs)
            with self.subTest(missing=missing), patch.object(models.importlib, "import_module", side_effect=dependency):
                encoder = self.encoder()
                with self.assertRaisesRegex(models.ModelDependencyError, "requires optional torch and transformers"):
                    encoder.fit(["CC"])
                self.assertFalse(hasattr(encoder, "n_features_out_"))
                self.assertFalse(hasattr(encoder, "fine_tuned_"))

    def test_pooling_and_batch_size_are_explicit_contracts(self):
        for options in ({"pooling": "implicit"}, {"batch_size": 0}, {"batch_size": True}, {"device": ""}):
            with self.subTest(options=options), patch.object(models, "_encoder_dependencies") as dependencies:
                with self.assertRaises(models.ChemicalModelError):
                    self.encoder(**options).fit(["CC"])
                dependencies.assert_not_called()
        with self.assertRaises(TypeError):
            models.FrozenSmilesEncoder(self.checkpoint, self.inventory)
        encoder = self.encoder(max_length=128, batch_size=2)
        self.assertEqual(clone(encoder).get_params(), encoder.get_params())
        self.assertIs(encoder.set_params(batch_size=3), encoder)
        self.assertEqual(encoder.batch_size, 3)


class FrozenEncoderLocalMechanicsTests(unittest.TestCase):
    """Real random HF weights exercise mechanics, not pretrained QSAR ability."""

    @classmethod
    def setUpClass(cls):
        try:
            import torch
            from tokenizers import Tokenizer
            from tokenizers.models import WordPiece
            from tokenizers.pre_tokenizers import Whitespace
            from tokenizers.processors import TemplateProcessing
            from transformers import BertConfig, BertModel, PreTrainedTokenizerFast
        except ImportError as error:
            raise unittest.SkipTest("Optional local encoder mechanics require torch/transformers/tokenizers: " + str(error))
        cls.torch = torch
        cls.temporary = tempfile.TemporaryDirectory(prefix="qsar-real-local-random-encoder-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.checkpoint = Path(cls.temporary.name) / "checkpoint"
        cls.checkpoint.mkdir()
        special = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]
        characters = ["C", "N", "O", "c", "n", "1", "(", ")", "="]
        vocabulary = {value: index for index, value in enumerate(special + characters + ["##" + c for c in characters])}
        backend = Tokenizer(WordPiece(vocab=vocabulary, unk_token="[UNK]"))
        backend.pre_tokenizer = Whitespace()
        backend.post_processor = TemplateProcessing(single="[CLS] $A [SEP]",
                                                    special_tokens=[("[CLS]", vocabulary["[CLS]"]),
                                                                    ("[SEP]", vocabulary["[SEP]"])])
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", pad_token="[PAD]",
                                            cls_token="[CLS]", sep_token="[SEP]", mask_token="[MASK]",
                                            model_max_length=16)
        tokenizer.save_pretrained(cls.checkpoint)
        config = BertConfig(vocab_size=len(vocabulary), hidden_size=8, num_hidden_layers=1,
                            num_attention_heads=2, intermediate_size=16, max_position_embeddings=16,
                            pad_token_id=vocabulary["[PAD]"], hidden_dropout_prob=0.5,
                            attention_probs_dropout_prob=0.5)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(13)
            model = BertModel(config)
        model.save_pretrained(cls.checkpoint, safe_serialization=True)
        cls.inventory = {path.relative_to(cls.checkpoint).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in cls.checkpoint.rglob("*") if path.is_file()}

    def encoder(self, **options):
        parameters = {"checkpoint_dir": self.checkpoint, "checkpoint_inventory": self.inventory,
                      "pooling": "mean", "batch_size": 2, "device": "cpu", "max_length": 16}
        parameters.update(options)
        return models.FrozenSmilesEncoder(**parameters)

    def test_real_frozen_mean_and_cls_pooling_match_direct_local_model(self):
        smiles = ["C", "CCO", "CCN"]
        for pooling in ("mean", "cls"):
            with self.subTest(pooling=pooling):
                encoder = self.encoder(pooling=pooling).fit(smiles, y=[0, 1, 0])
                actual = encoder.transform(smiles)
                self.assertEqual(actual.shape, (3, 8))
                self.assertEqual(actual.dtype, np.dtype(np.float32))
                self.assertTrue(np.isfinite(actual).all())
                self.assertFalse(encoder.encoder_.training)
                self.assertTrue(all(not parameter.requires_grad for parameter in encoder.encoder_.parameters()))
                encoded = encoder.tokenizer_(smiles, padding=True, truncation=False, return_tensors="pt")
                with self.torch.no_grad():
                    hidden = encoder.encoder_(**encoded, return_dict=True).last_hidden_state
                    if pooling == "cls":
                        expected = hidden[:, 0, :]
                    else:
                        mask = encoded["attention_mask"].unsqueeze(-1).to(dtype=hidden.dtype)
                        expected = (hidden * mask).sum(dim=1) / mask.sum(dim=1)
                np.testing.assert_allclose(actual, expected.numpy(), rtol=1e-5, atol=1e-6)
                self.assertEqual(encoder.get_feature_names_out().shape, (8,))
                self.assertFalse(encoder.encoding_method()["encoder_fine_tuned"])
                self.assertFalse(encoder.encoding_method()["gnn"])
                self.assertFalse(encoder.encoding_method()["downstream_estimator_fitted"])

    def test_repeated_and_serialized_embeddings_replay_without_dropout(self):
        smiles = ["C", "CCO", "CCN"]
        encoder = self.encoder().fit(smiles)
        actual = encoder.transform(smiles)
        np.testing.assert_array_equal(actual, encoder.transform(smiles))
        np.testing.assert_array_equal(actual, clone(encoder).fit_transform(smiles))
        # Deliberately re-enable training mode; transform must restore eval mode.
        encoder.encoder_.train()
        np.testing.assert_array_equal(actual, encoder.transform(smiles))
        np.testing.assert_array_equal(actual, pickle.loads(pickle.dumps(encoder)).transform(smiles))
        self.assertTrue(all(parameter.grad is None for parameter in encoder.encoder_.parameters()))

    def test_oversized_sequences_fail_before_model_forward_without_truncation(self):
        encoder = self.encoder(max_length=8).fit(["CC"])
        self.assertGreater(len(encoder.tokenizer_("C" * 12)["input_ids"]), 8)
        with patch.object(encoder.encoder_, "forward", wraps=encoder.encoder_.forward) as forward:
            with self.assertRaisesRegex(models.ChemicalModelError, "truncation is forbidden"):
                encoder.transform(["C" * 12])
            forward.assert_not_called()
        with self.assertRaisesRegex(models.ChemicalModelError, "truncation is forbidden"):
            self.encoder(max_length=8).fit(["C" * 12])
        with self.assertRaisesRegex(models.ChemicalModelError, "positional limit"):
            self.encoder(max_length=17).fit(["CC"])

    def test_checkpoint_tampering_after_fit_fails_before_forward(self):
        encoder = self.encoder().fit(["CC"])
        path = self.checkpoint / "config.json"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n")
            with patch.object(encoder.encoder_, "forward", wraps=encoder.encoder_.forward) as forward:
                with self.assertRaisesRegex(models.ChemicalModelError, "inventory mismatch"):
                    encoder.transform(["CC"])
                forward.assert_not_called()
            with self.assertRaisesRegex(models.ChemicalModelError, "inventory mismatch"):
                self.encoder().fit(["CC"])
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
