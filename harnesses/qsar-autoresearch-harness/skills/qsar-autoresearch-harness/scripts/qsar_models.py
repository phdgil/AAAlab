"""SMILES-native additive scorers and an optional frozen, local HF encoder.

Rule scorers learn descriptor ranges, bins, scaffold prevalence and vocabulary
only in ``fit``. SMARTS definitions are fixed contract inputs; their additive
weights are learned, not supplied by an expert. These are neither decision trees
nor claims of pure expert-rule inference. All optional chemistry/encoder imports
are lazy. No encoder fine-tuning or graph neural network is implemented here.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import hashlib
import importlib
import json
from numbers import Integral, Real
from pathlib import Path, PurePosixPath

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin, TransformerMixin
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.utils.validation import check_is_fitted


DEFAULT_DESCRIPTORS = ("MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors", "NumRotatableBonds")


class ChemicalModelError(ValueError):
    """Invalid chemistry, model contract or pinned checkpoint."""


class ModelDependencyError(ImportError):
    """An explicitly requested chemical adapter has missing dependencies."""


def _chemistry():
    try:
        return importlib.import_module("rdkit.Chem")
    except ImportError as error:
        raise ModelDependencyError("SMILES adapters require RDKit; no substitute features are generated") from error


def _smiles_values(X, column):
    if isinstance(X, Mapping) or hasattr(X, "columns"):
        if column not in X:
            raise ChemicalModelError("Missing SMILES column: " + str(column))
        values = np.asarray(X[column], dtype=object)
    else:
        values = np.asarray(X, dtype=object)
        if values.ndim == 2:
            if isinstance(column, Integral) and not isinstance(column, bool):
                if not 0 <= column < values.shape[1]:
                    raise ChemicalModelError("SMILES column index is outside the input matrix")
                values = values[:, column]
            elif values.shape[1] == 1:
                values = values[:, 0]
            else:
                raise ChemicalModelError("Multiple array columns require an integer smiles_column")
    if values.ndim != 1 or len(values) == 0:
        raise ChemicalModelError("Expected a nonempty one-dimensional SMILES column")
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ChemicalModelError(f"Invalid SMILES at row {index}: expected a nonempty string without whitespace")
    return values.tolist()


def _molecules(X, column):
    smiles = _smiles_values(X, column)
    Chem = _chemistry()
    parser = Chem.SmilesParserParams()
    parser.parseName = False
    parser.allowCXSMILES = False
    molecules = []
    for index, value in enumerate(smiles):
        molecule = Chem.MolFromSmiles(value, parser)
        if molecule is None or molecule.GetNumAtoms() == 0:
            raise ChemicalModelError(f"Invalid SMILES at row {index}: {value!r}")
        for atom in molecule.GetAtoms():
            atom.SetAtomMapNum(0)
        molecules.append(molecule)
    return smiles, molecules


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ChemicalModelError(name + " must be a positive integer")
    return int(value)


def _finite_number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
        raise ChemicalModelError(name + " must be a finite number")
    if positive and value <= 0:
        raise ChemicalModelError(name + " must be positive")
    return float(value)


def _smarts_contract(rules):
    if rules is None:
        return ()
    if not isinstance(rules, Mapping):
        raise ChemicalModelError("smarts_rules must map rule names to fixed SMARTS strings")
    Chem = _chemistry()
    result = []
    for name, smarts in rules.items():
        if not isinstance(name, str) or not name or not isinstance(smarts, str) or not smarts:
            raise ChemicalModelError("SMARTS names and definitions must be nonempty strings")
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is None or pattern.GetNumAtoms() == 0:
            raise ChemicalModelError("Invalid SMARTS contract rule: " + name)
        result.append((name, smarts))
    return tuple(sorted(result))


def _smarts_values(molecules, rules):
    Chem = _chemistry()
    patterns = [Chem.MolFromSmarts(smarts) for _, smarts in rules]
    return np.asarray([[float(mol.HasSubstructMatch(pattern)) for pattern in patterns]
                       for mol in molecules], dtype=np.float64).reshape(len(molecules), len(rules))


def _descriptor_values(molecules, names):
    try:
        descriptors = importlib.import_module("rdkit.Chem.Descriptors")
    except ImportError as error:
        raise ModelDependencyError("Chemical rule basis requires RDKit descriptors") from error
    functions = dict(descriptors._descList)
    if isinstance(names, str):
        raise ChemicalModelError("descriptor_names must be a sequence of distinct RDKit descriptor names")
    try:
        names = tuple(names)
    except TypeError as error:
        raise ChemicalModelError("descriptor_names must be a sequence") from error
    if any(not isinstance(name, str) or name not in functions for name in names) or len(names) != len(set(names)):
        raise ChemicalModelError("descriptor_names contains unknown or duplicate RDKit descriptors")
    values = np.empty((len(molecules), len(names)), dtype=np.float64)
    for row, mol in enumerate(molecules):
        for column, name in enumerate(names):
            try:
                values[row, column] = functions[name](mol)
            except Exception as error:
                raise ChemicalModelError(f"Descriptor {name} failed at row {row}") from error
    if not np.isfinite(values).all():
        raise ChemicalModelError("Nonfinite descriptor value; no imputation or row dropping is performed")
    return names, values


def _scaffolds(molecules):
    Chem = _chemistry()
    scaffold = importlib.import_module("rdkit.Chem.Scaffolds.MurckoScaffold")
    return [Chem.MolToSmiles(scaffold.GetScaffoldForMol(mol), isomericSmiles=False) for mol in molecules]


def _target(y, size, binary=False):
    try:
        values = np.asarray(y, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ChemicalModelError("Targets must be finite numeric values") from error
    if values.ndim != 1 or len(values) != size or not np.isfinite(values).all():
        raise ChemicalModelError("Targets must be a finite one-dimensional array matching the SMILES rows")
    if binary and not np.array_equal(np.unique(values), [0.0, 1.0]):
        raise ChemicalModelError("Binary fitting requires both labels 0 and 1; positive_class is 1")
    return values.astype(np.int64) if binary else values


def _weights(sample_weight, size):
    if sample_weight is None:
        return None
    try:
        weights = np.asarray(sample_weight, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ChemicalModelError("sample_weight must be finite and nonnegative") from error
    if (weights.ndim != 1 or len(weights) != size or not np.isfinite(weights).all()
            or np.any(weights < 0) or not np.any(weights > 0)):
        raise ChemicalModelError("sample_weight must match the rows, be finite/nonnegative and have positive total")
    return weights


class ChemicalRuleBasis(TransformerMixin, BaseEstimator):
    """Fold-local range-scaled descriptors, equal-width bins and scaffold basis.

    Values beyond fitted ranges clip the range coordinate and activate explicit
    below/above indicators. Constant descriptors have one bin and a zero range
    coordinate. SMARTS are binary presence indicators, ordered by contract name.
    Empty scaffolds mean absence, not a common chemical core. Nonempty unseen
    scaffolds have frequency zero and an explicit unseen indicator. Frequencies
    are counts divided by the total number of fitting rows (including repeats).
    """

    def __init__(self, descriptor_names=DEFAULT_DESCRIPTORS, n_bins=4, smarts_rules=None,
                 smiles_column="parent_smiles"):
        self.descriptor_names = descriptor_names
        self.n_bins = n_bins
        self.smarts_rules = smarts_rules
        self.smiles_column = smiles_column

    def fit(self, X, y=None):
        n_bins = _positive_integer(self.n_bins, "n_bins")
        _, molecules = _molecules(X, self.smiles_column)
        names, values = _descriptor_values(molecules, self.descriptor_names)
        rules = _smarts_contract(self.smarts_rules)
        scaffolds = Counter(value for value in _scaffolds(molecules) if value)
        ranges, edges, features = {}, {}, []
        for column, name in enumerate(names):
            low, high = float(values[:, column].min()), float(values[:, column].max())
            ranges[name] = (low, high)
            edges[name] = np.array([low, high]) if low == high else np.linspace(low, high, n_bins + 1)
            features.extend([f"descriptor:{name}:range", f"descriptor:{name}:below", f"descriptor:{name}:above"])
            features.extend(f"descriptor:{name}:bin:{i}" for i in range(len(edges[name]) - 1))
        features.extend("smarts:" + name for name, _ in rules)
        features.extend(["scaffold:training_frequency", "scaffold:absent", "scaffold:unseen"])
        features.extend("scaffold:is:" + value for value in sorted(scaffolds))
        self.descriptor_names_ = names
        self.descriptor_ranges_ = ranges
        self.descriptor_bin_edges_ = edges
        self.smarts_rules_ = dict(rules)
        self.scaffold_counts_ = dict(sorted(scaffolds.items()))
        self.scaffold_vocabulary_ = {value: index for index, value in enumerate(sorted(scaffolds))}
        self.scaffold_frequencies_ = {value: count / len(molecules) for value, count in self.scaffold_counts_.items()}
        self.feature_names_out_ = np.asarray(features, dtype=object)
        self.smiles_column_ = self.smiles_column
        self.n_fit_samples_ = len(molecules)
        self.n_features_in_ = 1
        return self

    def transform(self, X):
        check_is_fitted(self, "feature_names_out_")
        _, molecules = _molecules(X, self.smiles_column_)
        _, values = _descriptor_values(molecules, self.descriptor_names_)
        blocks = []
        for column, name in enumerate(self.descriptor_names_):
            raw = values[:, column]
            low, high = self.descriptor_ranges_[name]
            scaled = np.clip((raw - low) / (high - low), 0, 1) if high > low else np.zeros(len(raw))
            blocks.append(np.column_stack((scaled, raw < low, raw > high)))
            edges = self.descriptor_bin_edges_[name]
            bins = np.searchsorted(edges[1:-1], raw, side="right")
            indicators = np.zeros((len(raw), len(edges) - 1), dtype=np.float64)
            indicators[np.arange(len(raw)), bins] = 1.0
            blocks.append(indicators)
        blocks.append(_smarts_values(molecules, tuple(self.smarts_rules_.items())))
        scaffolds = _scaffolds(molecules)
        blocks.append(np.asarray([[self.scaffold_frequencies_.get(value, 0.0), not value,
                                   bool(value) and value not in self.scaffold_vocabulary_]
                                  for value in scaffolds], dtype=np.float64))
        vocabulary = np.zeros((len(molecules), len(self.scaffold_vocabulary_)), dtype=np.float64)
        for row, value in enumerate(scaffolds):
            if value in self.scaffold_vocabulary_:
                vocabulary[row, self.scaffold_vocabulary_[value]] = 1.0
        blocks.append(vocabulary)
        result = np.column_stack(blocks)
        if not np.isfinite(result).all():
            raise ChemicalModelError("Nonfinite chemical rule basis")
        return result

    def get_feature_names_out(self, input_features=None):
        check_is_fitted(self, "feature_names_out_")
        return self.feature_names_out_.copy()


def _adopt_basis(estimator, basis, model, kind):
    estimator.basis_ = basis
    estimator.model_ = model
    estimator.coef_ = model.coef_.copy()
    estimator.intercept_ = np.copy(model.intercept_)
    estimator.descriptor_ranges_ = basis.descriptor_ranges_
    estimator.descriptor_bin_edges_ = basis.descriptor_bin_edges_
    estimator.scaffold_vocabulary_ = basis.scaffold_vocabulary_
    estimator.scaffold_frequencies_ = basis.scaffold_frequencies_
    estimator.n_features_in_ = 1
    estimator.task_type_ = kind
    estimator.method_ = "fitted_rule_derived_additive_scorer"


def _explanation(estimator, X, classification=False):
    check_is_fitted(estimator, "model_")
    values = estimator.basis_.transform(X)
    coefficients = np.asarray(estimator.coef_).reshape(-1)
    contributions = values * coefficients
    intercept = float(np.asarray(estimator.intercept_).reshape(-1)[0])
    scores = intercept + contributions.sum(axis=1)
    result = {"method": estimator.scoring_method(), "feature_names": estimator.basis_.get_feature_names_out().tolist(),
              "feature_values": values.tolist(), "contributions": contributions.tolist(), "intercept": intercept,
              "score": scores.tolist(), "score_space": "class_1_log_odds" if classification else "regression_prediction"}
    if classification:
        probabilities = estimator.model_.predict_proba(values)
        result.update(classes=estimator.classes_.tolist(), probabilities=probabilities.tolist(),
                      prediction=(probabilities[:, 1] >= estimator.threshold_).astype(int).tolist())
    else:
        result["prediction"] = estimator.model_.predict(values).tolist()
    return result


class ChemicalRuleRegressor(RegressorMixin, BaseEstimator):
    """Learn additive chemical-basis weights using Ridge, with optional row weights."""

    def __init__(self, descriptor_names=DEFAULT_DESCRIPTORS, n_bins=4, smarts_rules=None,
                 alpha=1.0, fit_intercept=True, smiles_column="parent_smiles"):
        self.descriptor_names = descriptor_names
        self.n_bins = n_bins
        self.smarts_rules = smarts_rules
        self.alpha = alpha
        self.fit_intercept = fit_intercept
        self.smiles_column = smiles_column

    def fit(self, X, y, sample_weight=None):
        alpha = _finite_number(self.alpha, "alpha")
        if alpha < 0:
            raise ChemicalModelError("alpha must be nonnegative")
        basis = ChemicalRuleBasis(self.descriptor_names, self.n_bins, self.smarts_rules, self.smiles_column)
        values = basis.fit_transform(X)
        target = _target(y, len(values))
        weights = _weights(sample_weight, len(values))
        model = Ridge(alpha=alpha, fit_intercept=self.fit_intercept, solver="svd")
        model.fit(values, target, sample_weight=weights)
        _adopt_basis(self, basis, model, "regression")
        self.positive_class_ = None
        self.threshold_ = None
        return self

    def predict(self, X):
        check_is_fitted(self, "model_")
        return self.model_.predict(self.basis_.transform(X))

    def explain(self, X):
        return _explanation(self, X)

    def scoring_method(self):
        check_is_fitted(self, "model_")
        return {"method": self.method_, "coefficient_estimator": "Ridge", "task_type": self.task_type_,
                "expert_rules_only": False, "decision_tree": False}


class ChemicalRuleClassifier(ClassifierMixin, BaseEstimator):
    """Learn binary chemical-basis log odds using LogisticRegression.

    Labels are exactly 0/1 and probability columns follow ``classes_ == [0, 1]``.
    ``threshold`` affects predict only; no calibration or threshold fitting is
    claimed. sample_weight is supported; no other fit keyword is accepted.
    """

    def __init__(self, descriptor_names=DEFAULT_DESCRIPTORS, n_bins=4, smarts_rules=None,
                 C=1.0, fit_intercept=True, max_iter=1000, random_state=0, threshold=0.5,
                 smiles_column="parent_smiles"):
        self.descriptor_names = descriptor_names
        self.n_bins = n_bins
        self.smarts_rules = smarts_rules
        self.C = C
        self.fit_intercept = fit_intercept
        self.max_iter = max_iter
        self.random_state = random_state
        self.threshold = threshold
        self.smiles_column = smiles_column

    def fit(self, X, y, sample_weight=None):
        C = _finite_number(self.C, "C", positive=True)
        max_iter = _positive_integer(self.max_iter, "max_iter")
        threshold = _finite_number(self.threshold, "threshold")
        if not 0 <= threshold <= 1:
            raise ChemicalModelError("threshold must be between 0 and 1")
        basis = ChemicalRuleBasis(self.descriptor_names, self.n_bins, self.smarts_rules, self.smiles_column)
        values = basis.fit_transform(X)
        target = _target(y, len(values), binary=True)
        weights = _weights(sample_weight, len(values))
        if weights is not None and any(not np.any(weights[target == label] > 0) for label in (0, 1)):
            raise ChemicalModelError("Both binary classes require positive fitting weight")
        model = LogisticRegression(C=C, fit_intercept=self.fit_intercept, max_iter=max_iter,
                                   random_state=self.random_state, solver="lbfgs")
        model.fit(values, target, sample_weight=weights)
        _adopt_basis(self, basis, model, "classification")
        self.classes_ = model.classes_.copy()
        self.positive_class_ = 1
        self.threshold_ = threshold
        return self

    def predict_proba(self, X):
        check_is_fitted(self, "model_")
        return self.model_.predict_proba(self.basis_.transform(X))

    def decision_function(self, X):
        check_is_fitted(self, "model_")
        return self.model_.decision_function(self.basis_.transform(X))

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= self.threshold_).astype(np.int64)

    def explain(self, X):
        return _explanation(self, X, classification=True)

    def scoring_method(self):
        check_is_fitted(self, "model_")
        return {"method": self.method_, "coefficient_estimator": "LogisticRegression", "task_type": self.task_type_,
                "score_space": "class_1_log_odds", "expert_rules_only": False, "decision_tree": False}


class FixedExpertSmartsScorer(BaseEstimator):
    """Unfitted weighted SMARTS inference; intentionally has no fit method.

    Each fixed indicator contributes once when present, independent of match
    count. These are expert-supplied scores, not fitted estimates or calibrated
    probabilities. This class is not a trainable sklearn pipeline estimator.
    """

    def __init__(self, smarts_rules, weights, intercept=0.0, smiles_column="parent_smiles"):
        self.smarts_rules = smarts_rules
        self.weights = weights
        self.intercept = intercept
        self.smiles_column = smiles_column

    def explain(self, X):
        rules = _smarts_contract(self.smarts_rules)
        if not isinstance(self.weights, Mapping) or set(self.weights) != {name for name, _ in rules}:
            raise ChemicalModelError("Expert weights must name exactly the fixed SMARTS rules")
        weights = np.asarray([_finite_number(self.weights[name], "weight:" + name) for name, _ in rules])
        intercept = _finite_number(self.intercept, "intercept")
        _, molecules = _molecules(X, self.smiles_column)
        values = _smarts_values(molecules, rules)
        contributions = values * weights
        return {"method": self.scoring_method(), "feature_names": ["smarts:" + name for name, _ in rules],
                "feature_values": values.tolist(), "contributions": contributions.tolist(), "intercept": intercept,
                "score": (intercept + contributions.sum(axis=1)).tolist(), "score_space": "unfitted_expert_score"}

    def predict(self, X):
        return np.asarray(self.explain(X)["score"], dtype=np.float64)

    def scoring_method(self):
        return {"method": "fixed_expert_weighted_smarts_inference", "fitted": False,
                "expert_rules_only": True, "calibrated_probability": False}


def _checkpoint_files(checkpoint_dir, inventory):
    """Check an exact, portable relative-path -> SHA-256 checkpoint inventory."""
    root = Path(checkpoint_dir)
    if root.is_symlink() or not root.is_dir():
        raise ChemicalModelError("Local checkpoint directory is missing or is a symlink: " + str(root))
    root = root.resolve()
    if not isinstance(inventory, Mapping) or not inventory:
        raise ChemicalModelError("A nonempty pinned checkpoint_inventory of file SHA-256 hashes is required")
    expected = {}
    for name, value in inventory.items():
        if not isinstance(name, str) or not name or "\\" in name:
            raise ChemicalModelError("Checkpoint inventory paths must be relative POSIX file paths")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name or ":" in name:
            raise ChemicalModelError("Checkpoint inventory path is not a safe relative file: " + name)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ChemicalModelError("Checkpoint inventory requires SHA-256 for: " + name)
        expected[name] = value.lower()
    actual = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ChemicalModelError("Checkpoint inventory cannot contain symlinks: " + str(path))
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            actual[path.relative_to(root).as_posix()] = digest.hexdigest()
    if actual != expected:
        raise ChemicalModelError("Pinned checkpoint inventory mismatch (missing, extra or changed file)")
    return root, expected


def _encoder_dependencies():
    try:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        tokenizer = transformers.AutoTokenizer
        model = transformers.AutoModel
    except (ImportError, AttributeError) as error:
        raise ModelDependencyError(
            "FrozenSmilesEncoder requires optional torch and transformers (AutoTokenizer/AutoModel); "
            "no embeddings or pretrained results are fabricated") from error
    return torch, tokenizer, model


def _token_limit(max_length, tokenizer, model):
    limits = []
    # HuggingFace uses values >= 10**20 as unknown/unbounded tokenizer sentinels.
    for value in (getattr(tokenizer, "model_max_length", None),
                  getattr(model.config, "max_position_embeddings", None), getattr(model.config, "n_positions", None)):
        if isinstance(value, Integral) and not isinstance(value, bool) and 0 < value < 10**20:
            limits.append(int(value))
    if max_length is not None:
        requested = _positive_integer(max_length, "max_length")
        if limits and requested > min(limits):
            raise ChemicalModelError("max_length exceeds the pinned tokenizer/model positional limit")
        return requested
    if not limits:
        raise ChemicalModelError("Pinned checkpoint has no known positional limit; supply explicit max_length")
    return min(limits)


class FrozenSmilesEncoder(TransformerMixin, BaseEstimator):
    """Frozen local HuggingFace AutoModel embeddings, never encoder fine-tuning.

    ``checkpoint_inventory`` must list EVERY file as relative POSIX path ->
    SHA-256, including tokenizer/config/weights. Symlinks and unpinned extras
    fail closed. Both HF loaders use local_files_only and disable remote code.
    ``pooling`` is required: 'mean' averages all nonpadding tokens (including
    special tokens); 'cls' selects a verified leading CLS token. No input is
    silently truncated: max_length includes special tokens and excess fails.
    ``device`` and batch_size are explicit replay parameters. CPU is the default;
    bitwise equality across different devices/library versions is not claimed.

    fit only loads and freezes a pretrained checkpoint and records output width;
    it ignores y. Downstream regression/classification must be fitted separately.
    fit accepts no sample_weight or other training keyword arguments.
    """

    def __init__(self, checkpoint_dir, checkpoint_inventory, pooling, batch_size=32, device="cpu",
                 max_length=None, smiles_column="parent_smiles"):
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_inventory = checkpoint_inventory
        self.pooling = pooling
        self.batch_size = batch_size
        self.device = device
        self.max_length = max_length
        self.smiles_column = smiles_column

    def fit(self, X, y=None):
        if self.pooling not in {"mean", "cls"}:
            raise ChemicalModelError("pooling must be explicitly 'mean' or 'cls'")
        batch_size = _positive_integer(self.batch_size, "batch_size")
        if not isinstance(self.device, str) or not self.device:
            raise ChemicalModelError("device must be an explicit torch device string")
        root, inventory = _checkpoint_files(self.checkpoint_dir, self.checkpoint_inventory)
        smiles, _ = _molecules(X, self.smiles_column)
        torch, AutoTokenizer, AutoModel = _encoder_dependencies()
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
            model = AutoModel.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
            device = torch.device(self.device)
            model.to(device)
        except ImportError as error:
            raise ModelDependencyError("Pinned local encoder requires an additional tokenizer/model dependency: " + str(error)) from error
        except (OSError, ValueError, RuntimeError) as error:
            raise ChemicalModelError("Cannot load pinned local pretrained encoder: " + str(error)) from error
        token_limit = _token_limit(self.max_length, tokenizer, model)
        if self.pooling == "cls" and getattr(tokenizer, "cls_token_id", None) is None:
            raise ChemicalModelError("CLS pooling requires a tokenizer with an explicit CLS token")
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.eval()
        _checkpoint_files(root, inventory)
        self.tokenizer_ = tokenizer
        self.encoder_ = model
        self.checkpoint_dir_ = str(root)
        self.checkpoint_inventory_ = inventory
        self.checkpoint_fingerprint_ = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
        self.pooling_ = self.pooling
        self.batch_size_ = batch_size
        self.device_ = str(device)
        self.max_length_ = token_limit
        self.smiles_column_ = self.smiles_column
        self.n_features_in_ = 1
        # Validate every fitting sequence before any model forward operation.
        for start in range(0, len(smiles), batch_size):
            self._tokens(smiles[start:start + batch_size], torch, row_offset=start)
        first = self._embed(smiles[:1], torch)
        self.n_features_out_ = first.shape[1]
        self.parameters_frozen_ = True
        self.fine_tuned_ = False
        return self

    def _tokens(self, smiles, torch, row_offset=0):
        tokens = self.tokenizer_(smiles, add_special_tokens=True, padding=True, truncation=False,
                                 return_attention_mask=True, return_tensors="pt")
        if "input_ids" not in tokens or "attention_mask" not in tokens:
            raise ChemicalModelError("Pinned tokenizer must provide input_ids and attention_mask")
        ids, mask = tokens["input_ids"], tokens["attention_mask"]
        if ids.ndim != 2 or mask.shape != ids.shape or ids.shape[0] != len(smiles):
            raise ChemicalModelError("Pinned tokenizer returned inconsistent batch dimensions")
        lengths = mask.sum(dim=1)
        if torch.any(lengths <= 0).item():
            raise ChemicalModelError("Pinned tokenizer returned an empty token sequence")
        if torch.any(lengths > self.max_length_).item() or ids.shape[1] > self.max_length_:
            raise ChemicalModelError(
                f"Token sequence in batch starting at row {row_offset} exceeds max_length={self.max_length_}; truncation is forbidden")
        if self.pooling_ == "cls":
            if not torch.all((ids[:, 0] == self.tokenizer_.cls_token_id) & (mask[:, 0] != 0)).item():
                raise ChemicalModelError("CLS pooling requires a leading nonpadding CLS token in every sequence")
        return {name: value.to(self.device_) for name, value in tokens.items()}

    def _embed(self, smiles, torch, row_offset=0):
        tokens = self._tokens(smiles, torch, row_offset)
        self.encoder_.eval()
        with torch.no_grad():
            output = self.encoder_(**tokens, return_dict=True)
            hidden = getattr(output, "last_hidden_state", None)
            if hidden is None or hidden.ndim != 3 or tuple(hidden.shape[:2]) != tuple(tokens["input_ids"].shape):
                raise ChemicalModelError("Pinned AutoModel must return token-level last_hidden_state")
            if self.pooling_ == "cls":
                pooled = hidden[:, 0, :]
            else:
                mask = tokens["attention_mask"].unsqueeze(-1).to(dtype=hidden.dtype)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1)
            result = pooled.detach().to(device="cpu", dtype=torch.float32).numpy().copy()
        if result.ndim != 2 or result.shape[1] == 0 or not np.isfinite(result).all():
            raise ChemicalModelError("Pinned encoder returned empty or nonfinite embeddings")
        return result

    def transform(self, X):
        check_is_fitted(self, "n_features_out_")
        _checkpoint_files(self.checkpoint_dir_, self.checkpoint_inventory_)
        smiles, _ = _molecules(X, self.smiles_column_)
        torch, _, _ = _encoder_dependencies()
        blocks = [self._embed(smiles[start:start + self.batch_size_], torch, row_offset=start)
                  for start in range(0, len(smiles), self.batch_size_)]
        result = np.concatenate(blocks, axis=0)
        if result.shape != (len(smiles), self.n_features_out_):
            raise ChemicalModelError("Frozen encoder output width changed after fit")
        _checkpoint_files(self.checkpoint_dir_, self.checkpoint_inventory_)
        return result

    def get_feature_names_out(self, input_features=None):
        check_is_fitted(self, "n_features_out_")
        return np.asarray([f"frozen_smiles_embedding:{i}" for i in range(self.n_features_out_)], dtype=object)

    def encoding_method(self):
        check_is_fitted(self, "n_features_out_")
        return {"method": "frozen_pretrained_smiles_encoder", "encoder_fine_tuned": False, "gnn": False,
                "downstream_estimator_fitted": False, "pooling": self.pooling_, "device": self.device_,
                "batch_size": self.batch_size_, "max_length": self.max_length_,
                "checkpoint_fingerprint": self.checkpoint_fingerprint_}
