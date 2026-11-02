"""Optional Optuna ask/tell search; fitting and resource enforcement belong to the caller.

A SQLite file contains exactly one hash-bound study. Suggestions are not model
executions, anchors never enter the study, and failed/pruned trials consume the
attempt budget. TPE may revisit a configuration; distinct coverage is reported
separately. Determinism requires the same ordered ask/tell history. Each ask
uses a trial-number-derived seed, so restarting does not reset an RNG stream.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
import hashlib
import importlib
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4


class SearchError(ValueError):
    """Invalid search declaration, outcome, or persisted evidence."""


class SearchDependencyError(ImportError):
    """An explicitly selected operational backend is unavailable."""


class SearchBudgetError(SearchError):
    """No further trial may be started under the declared budget/policy."""


class ObjectiveDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


_SCHEMA_VERSION = "1.0"
_RECEIPT_TABLE = "qsar_search_evidence"


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SearchError("Search evidence must be finite JSON") from error


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _copy(value):
    return json.loads(_json(value))


def _positive_integer(value, name):
    if type(value) is not int or value < 1:
        raise SearchError(name + " must be a positive integer")
    return value


def _finite_number(value, name):
    if type(value) not in (int, float):
        raise SearchError(name + " must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise SearchError(name + " must be a finite number")
    return value


def _deadline(value):
    if not isinstance(value, str) or not value.strip():
        raise SearchError("deadline must be an ISO-8601 timestamp with an explicit timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SearchError("deadline must be an ISO-8601 timestamp with an explicit timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SearchError("deadline requires an explicit timezone")
    return parsed


@dataclass(frozen=True)
class ResourcePolicy:
    """Validated start-boundary policy, not a timer, scheduler or cancellation system."""

    maximum_trials: int
    concurrency: int = 1
    deadline: str | None = None

    def __post_init__(self):
        _positive_integer(self.maximum_trials, "maximum_trials")
        _positive_integer(self.concurrency, "concurrency")
        if self.concurrency > self.maximum_trials:
            raise SearchError("concurrency cannot exceed maximum_trials")
        if self.deadline is not None:
            _deadline(self.deadline)

    def to_dict(self):
        return {"maximum_trials": self.maximum_trials, "concurrency": self.concurrency, "deadline": self.deadline}

    def check_start(self, attempted_trials, running_trials, *, now=None):
        """Raise at a proposed start boundary; running work is never stopped here."""
        for name, value in (("attempted_trials", attempted_trials), ("running_trials", running_trials)):
            if type(value) is not int or value < 0:
                raise SearchError(name + " must be a nonnegative integer")
        if running_trials > attempted_trials:
            raise SearchError("running_trials cannot exceed attempted_trials")
        if attempted_trials >= self.maximum_trials:
            raise SearchBudgetError("Resource maximum_trials exhausted")
        if running_trials >= self.concurrency:
            raise SearchBudgetError("Resource concurrency limit reached")
        if self.deadline is not None:
            now = datetime.now(timezone.utc) if now is None else now
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise SearchError("Policy evaluation requires a timezone-aware datetime")
            if now >= _deadline(self.deadline):
                raise SearchBudgetError("Resource deadline reached; no new trial may start")


def validate_resource_policy(policy=None, *, n_trials=None):
    if isinstance(policy, ResourcePolicy):
        policy = policy.to_dict()
    if policy is None:
        if n_trials is None:
            raise SearchError("Resource policy requires maximum_trials")
        policy = {"maximum_trials": n_trials}
    if not isinstance(policy, Mapping) or set(policy) - {"deadline", "maximum_trials", "concurrency"}:
        raise SearchError("Resource policy accepts only deadline, maximum_trials and concurrency")
    maximum = policy.get("maximum_trials", n_trials)
    result = ResourcePolicy(maximum, policy.get("concurrency", 1), policy.get("deadline"))
    if n_trials is not None and _positive_integer(n_trials, "n_trials") > result.maximum_trials:
        raise SearchError("n_trials exceeds resource maximum_trials")
    return result


def _choices(values, name):
    if not isinstance(values, list) or not values:
        raise SearchError("Categorical search dimension must be a nonempty list: " + name)
    for index, value in enumerate(values):
        if value is not None and type(value) not in (str, bool, int, float):
            raise SearchError("Categorical choices must be JSON scalars: " + name)
        if type(value) in (int, float):
            _finite_number(value, name)
        if any(value == prior for prior in values[:index]):
            raise SearchError("Duplicate search dimension values: " + name)


def _dimension(name, declaration):
    if not isinstance(name, str) or not name.strip():
        raise SearchError("Search dimension names must be nonempty strings")
    if isinstance(declaration, list):
        _choices(declaration, name)
        return
    if not isinstance(declaration, Mapping):
        raise SearchError("Search dimensions require a list or a typed distribution: " + name)
    kind = declaration.get("type")
    if kind == "categorical":
        if set(declaration) != {"type", "choices"}:
            raise SearchError("Categorical distributions accept only type and choices: " + name)
        _choices(declaration["choices"], name)
        return
    if kind not in ("int", "float") or set(declaration) - {"type", "low", "high", "step", "log"}:
        raise SearchError("Unsupported search distribution: " + name)
    if not {"low", "high"}.issubset(declaration):
        raise SearchError("Numeric distributions require low and high: " + name)
    low = _finite_number(declaration["low"], name + ".low")
    high = _finite_number(declaration["high"], name + ".high")
    log = declaration.get("log", False)
    if type(log) is not bool or low > high or (log and low <= 0):
        raise SearchError("Invalid numeric distribution range/log policy: " + name)
    _finite_number(high - low, name + ".range")
    if kind == "int" and (type(low) is not int or type(high) is not int):
        raise SearchError("Integer distribution bounds must be integers: " + name)
    step = declaration.get("step", 1 if kind == "int" else None)
    if step is not None:
        _finite_number(step, name + ".step")
        if step <= 0 or (kind == "int" and type(step) is not int):
            raise SearchError("Invalid distribution step: " + name)
        if log and (kind == "float" or step != 1):
            raise SearchError("Log distributions cannot use this step: " + name)
        # Optuna otherwise silently lowers high; the exact contract must prevail.
        if ((Fraction(str(high)) - Fraction(str(low))) / Fraction(str(step))).denominator != 1:
            raise SearchError("Distribution range must be exactly divisible by step: " + name)
    elif kind == "int":
        raise SearchError("Integer distribution step cannot be null: " + name)


@dataclass(frozen=True)
class SearchSpec:
    """Immutable JSON-backed specification returned by validate_search_spec."""

    _definition_json: str

    def to_dict(self):
        return json.loads(self._definition_json)

    @property
    def search_space(self):
        return self.to_dict()["search_space"]

    @property
    def anchor(self):
        return self.to_dict()["anchor"]

    @property
    def n_trials(self):
        return self.to_dict()["n_trials"]

    @property
    def seed(self):
        return self.to_dict()["seed"]

    @property
    def direction(self):
        return ObjectiveDirection(self.to_dict()["direction"])

    @property
    def resource_policy(self):
        return validate_resource_policy(self.to_dict()["resource_policy"], n_trials=self.n_trials)

    @property
    def sha256(self):
        return _hash(self.to_dict())


def validate_search_spec(cell, seed, direction=ObjectiveDirection.MINIMIZE, resource_policy=None):
    """Validate a cell's search_space, n_trials and separate optional anchor.

    Numeric dimensions use {type: int|float, low, high, log?, step?}; categorical
    dimensions use a list or {type: categorical, choices: [...]}. No conditional
    distributions, implicit coercion or automatic range adjustment is performed.
    Other training-cell fields are the trainer's concern and are not interpreted.
    """
    if not isinstance(cell, Mapping):
        raise SearchError("Search cell must be an object")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise SearchError("Search seed must be an integer in [0, 2**32)")
    try:
        direction = ObjectiveDirection(direction)
    except (ValueError, TypeError) as error:
        raise SearchError("Objective direction must be minimize or maximize") from error
    n_trials = _positive_integer(cell.get("n_trials"), "n_trials")
    space = cell.get("search_space")
    if not isinstance(space, Mapping) or not space:
        raise SearchError("search_space must be a nonempty object")
    for name, declaration in space.items():
        _dimension(name, declaration)
    anchor = cell.get("anchor")
    if anchor is not None:
        if not isinstance(anchor, Mapping) or not anchor or set(anchor) - set(space):
            raise SearchError("Separate anchor must be a nonempty object using declared parameter names")
        for name, value in anchor.items():
            _choices([value], "anchor." + name)
    policy = validate_resource_policy(resource_policy, n_trials=n_trials)
    definition = {"search_space": dict(space), "n_trials": n_trials, "anchor": anchor,
                  "seed": seed, "direction": direction.value, "resource_policy": policy.to_dict(),
                  "sampler": "TPESampler", "sampler_seed_policy": "sha256(seed,trial_number):uint32"}
    return SearchSpec(_json(definition))


def _optuna():
    try:
        return importlib.import_module("optuna")
    except ImportError as error:
        raise SearchDependencyError("Optuna search requires the optional optuna dependency; no fallback sampler is used") from error


def _storage_hash(connection):
    """Hash Optuna's actual schema/rows before opening its writable storage API."""
    tables = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != ? ORDER BY name",
        (_RECEIPT_TABLE,),
    ).fetchall()
    contents = []
    for name, schema in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        rows = connection.execute("SELECT * FROM " + quoted + " ORDER BY rowid").fetchall()
        contents.append({"table": name, "schema": schema, "rows": rows})
    return _hash(contents)


def _sealed(payload):
    return {**payload, "snapshot_sha256": _hash(payload)}


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, Mapping) or "snapshot_sha256" not in snapshot:
        raise SearchError("Study snapshot is missing its evidence hash")
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if snapshot["snapshot_sha256"] != _hash(payload):
        raise SearchError("Study snapshot evidence hash mismatch")


class OptunaStudyCoordinator:
    """Persistent, single-study SQLite custody for public Optuna ask/tell calls.

    The n_trials attempt budget is enforced here. ResourcePolicy is deliberately
    caller-managed: there is no walltime enforcement or worker scheduling. A
    short persisted operation claim serializes coordinator writes; an interrupted
    mutation fails closed rather than discarding/retrying unknown trial history.
    """

    def __init__(self, sqlite_path, spec, input_identity, *, study_name="qsar-search", resume=False,
                 expected_snapshot=None):
        if not isinstance(spec, SearchSpec):
            raise SearchError("A validated SearchSpec is required")
        checked = validate_search_spec(spec.to_dict(), spec.seed, spec.direction, spec.resource_policy)
        if checked.to_dict() != spec.to_dict():
            raise SearchError("SearchSpec differs from its validated declaration")
        if not isinstance(input_identity, Mapping) or not input_identity:
            raise SearchError("A nonempty input identity object is required")
        if not isinstance(study_name, str) or not study_name.strip() or len(study_name) > 512:
            raise SearchError("study_name must contain 1 to 512 characters")
        if type(resume) is not bool:
            raise SearchError("resume must be boolean")
        self.spec = checked
        self.path = Path(sqlite_path).resolve()
        self.study_name = study_name
        self._optuna = _optuna()
        self._definition = {"schema_version": _SCHEMA_VERSION, "backend": "optuna",
                            "optuna_version": self._optuna.__version__, "study_name": study_name,
                            "input_identity": _copy(dict(input_identity)), "specification": checked.to_dict()}
        self.study_sha256 = _hash(self._definition)
        self._storage = None
        self._study = None
        self._closed = False
        if resume:
            if not self.path.is_file():
                raise SearchError("Resume requires an existing SQLite study")
            before = self._verified_snapshot()
            if expected_snapshot is not None:
                _validate_snapshot(expected_snapshot)
                if _json(expected_snapshot) != _json(before):
                    raise SearchError("Resume snapshot differs from persisted study evidence")
        else:
            if expected_snapshot is not None:
                raise SearchError("expected_snapshot is valid only for resume")
            if self.path.exists():
                raise SearchError("New study requires a nonexistent SQLite path; use resume for existing evidence")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive reservation prevents two creators from sharing a fresh file.
            try:
                self.path.open("xb").close()
            except FileExistsError as error:
                raise SearchError("Study path was created concurrently") from error
        try:
            self._storage = self._optuna.storages.RDBStorage(
                "sqlite:///" + self.path.as_posix(), engine_kwargs={"connect_args": {"timeout": 30}},
            )
            if resume:
                self._study = self._optuna.load_study(study_name=study_name, storage=self._storage)
                actual = self._make_snapshot(before)
                if _json(actual) != _json(before):
                    raise SearchError("Persisted study state differs from its snapshot")
            else:
                self._study = self._optuna.create_study(
                    study_name=study_name, storage=self._storage, direction=self.spec.direction.value,
                    sampler=self._optuna.samplers.TPESampler(seed=self.spec.seed),
                    pruner=self._optuna.pruners.NopPruner(),
                )
                snapshot = self._make_snapshot()
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute(
                        "CREATE TABLE " + _RECEIPT_TABLE + " (id INTEGER PRIMARY KEY CHECK (id=1), "
                        "definition_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, operation_token TEXT)"
                    )
                    connection.execute("INSERT INTO " + _RECEIPT_TABLE + " VALUES (1, ?, ?, NULL)",
                                       (_json(self._definition), _json(snapshot)))
        except Exception:
            self.close()
            raise

    def _read_record(self, connection):
        try:
            records = connection.execute(
                "SELECT definition_json, snapshot_json, operation_token FROM " + _RECEIPT_TABLE
            ).fetchall()
            if len(records) != 1:
                raise SearchError("Study requires exactly one immutable binding receipt")
            definition_json, snapshot_json, token = records[0]
            definition, snapshot = json.loads(definition_json), json.loads(snapshot_json)
            if _json(definition) != _json(self._definition):
                raise SearchError("Study input identity/search specification/version mismatch; no mutation performed")
            _validate_snapshot(snapshot)
            if snapshot.get("study_sha256") != self.study_sha256:
                raise SearchError("Study binding hash mismatch")
            if token is not None:
                raise SearchError("Study mutation is in progress or was interrupted; evidence cannot be overwritten")
            if snapshot.get("storage_sha256") != _storage_hash(connection):
                raise SearchError("SQLite study storage hash mismatch")
            return snapshot
        except (sqlite3.Error, json.JSONDecodeError, TypeError, KeyError) as error:
            raise SearchError("Invalid or unbound SQLite study evidence") from error

    def _verified_snapshot(self):
        if self._closed:
            raise SearchError("Study coordinator is closed")
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection, connection:
                return self._read_record(connection)
        except sqlite3.Error as error:
            raise SearchError("Cannot read persisted SQLite study evidence") from error

    def _make_snapshot(self, prior=None, failures=None):
        failures = {} if failures is None else failures
        reasons = {} if prior is None else {item["number"]: item["failure_reason"] for item in prior["trials"]}
        reasons.update(failures)
        if self._study.direction.name.lower() != self.spec.direction.value:
            raise SearchError("Optuna objective direction differs from its bound specification")
        trials = []
        states = {"RUNNING": "running", "COMPLETE": "complete", "FAIL": "failed", "PRUNED": "pruned"}
        for trial in self._study.get_trials(deepcopy=True):
            if trial.state.name not in states:
                raise SearchError("Undeclared Optuna trial state: " + trial.state.name)
            values = trial.values
            value = None if values is None else values[0]
            if values is not None and (len(values) != 1 or not math.isfinite(value)):
                raise SearchError("Optuna study contains an invalid objective value")
            trials.append({"trial_id": "search-" + str(trial.number), "number": trial.number,
                           "study_sha256": self.study_sha256, "kind": "search", "parameters": trial.params,
                           "status": states[trial.state.name], "objective_value": value,
                           "failure_reason": reasons.get(trial.number), "user_attributes": trial.user_attrs,
                           "system_attributes": trial.system_attrs,
                           "distributions": {name: json.loads(self._optuna.distributions.distribution_to_json(distribution))
                                             for name, distribution in sorted(trial.distributions.items())}})
        if [trial["number"] for trial in trials] != list(range(len(trials))) or len(trials) > self.spec.n_trials:
            raise SearchError("Optuna trial numbers/budget differ from the declared search")
        completed = [trial for trial in trials if trial["status"] == "complete"]
        best = self._study.best_trial if completed else None
        coverage = {"expected_search_trials": self.spec.n_trials, "attempted_search_trials": len(trials),
                    "completed_search_trials": len(completed),
                    "failed_search_trials": sum(trial["status"] == "failed" for trial in trials),
                    "pruned_search_trials": sum(trial["status"] == "pruned" for trial in trials),
                    "running_search_trials": sum(trial["status"] == "running" for trial in trials),
                    "remaining_search_trials": self.spec.n_trials - len(trials),
                    "distinct_search_configurations": len({_json(trial["parameters"]) for trial in trials
                                                           if set(trial["parameters"]) == set(self.spec.search_space)}),
                    "budget_exhausted": len(trials) == self.spec.n_trials,
                    "successful_search_coverage_complete": len(completed) == self.spec.n_trials}
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection, connection:
            storage_sha256 = _storage_hash(connection)
        return _sealed({**self._definition, "study_sha256": self.study_sha256,
                        "specification_sha256": self.spec.sha256,
                        "input_identity_sha256": _hash(self._definition["input_identity"]),
                        "storage_sha256": storage_sha256, "direction": self.spec.direction.value,
                        "resource_policy_enforcement": "caller_managed",
                        "anchor": None if self.spec.anchor is None else {
                            "trial_id": "anchor", "kind": "anchor", "parameters": self.spec.anchor,
                            "included_in_search_budget": False, "status": "not_managed_by_study"},
                        "coverage": coverage, "trials": trials,
                        "best_trial_id": None if best is None else "search-" + str(best.number),
                        "best_objective_value": None if best is None else best.value})

    def _claim(self, expected):
        token = uuid4().hex
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            actual = self._read_record(connection)
            if _json(actual) != _json(expected):
                raise SearchError("Study changed before mutation; refresh the snapshot")
            connection.execute("UPDATE " + _RECEIPT_TABLE + " SET operation_token=? WHERE id=1", (token,))
        return token

    def _finish(self, token, prior, failures=None):
        snapshot = self._make_snapshot(prior, failures)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT operation_token FROM " + _RECEIPT_TABLE + " WHERE id=1").fetchone()
            if row != (token,):
                raise SearchError("Study operation claim changed; refusing to overwrite evidence")
            connection.execute("UPDATE " + _RECEIPT_TABLE + " SET snapshot_json=?, operation_token=NULL WHERE id=1",
                               (_json(snapshot),))
        return snapshot

    def ask(self):
        """Persist one running suggestion. No fitting or objective evaluation occurs."""
        before = self._verified_snapshot()
        number = before["coverage"]["attempted_search_trials"]
        if number >= self.spec.n_trials:
            raise SearchBudgetError("Declared n_trials attempt budget exhausted")
        seed = int.from_bytes(hashlib.sha256(_json([self.spec.seed, number]).encode("utf-8")).digest()[:4], "big")
        token = self._claim(before)
        trial = None
        try:
            self._study.sampler = self._optuna.samplers.TPESampler(seed=seed)
            trial = self._study.ask()
            trial.set_user_attr("qsar_search", {"trial_id": "search-" + str(number),
                                               "study_sha256": self.study_sha256, "sampler_seed": seed})
            for name, declaration in sorted(self.spec.search_space.items()):
                if isinstance(declaration, list):
                    trial.suggest_categorical(name, declaration)
                elif declaration["type"] == "categorical":
                    trial.suggest_categorical(name, declaration["choices"])
                elif declaration["type"] == "int":
                    trial.suggest_int(name, declaration["low"], declaration["high"],
                                      step=declaration.get("step", 1), log=declaration.get("log", False))
                else:
                    trial.suggest_float(name, declaration["low"], declaration["high"],
                                        step=declaration.get("step"), log=declaration.get("log", False))
        except Exception as error:
            if trial is not None:
                self._study.tell(trial, state=self._optuna.trial.TrialState.FAIL)
            self._finish(token, before, {number: "Suggestion failed: " + str(error)})
            raise SearchError("Optuna suggestion failed; attempted trial evidence was preserved") from error
        self._finish(token, before)
        return {"trial_id": "search-" + str(trial.number), "number": trial.number,
                "parameters": _copy(trial.params), "study_sha256": self.study_sha256}

    def tell(self, trial, result=None, *, failure=None, pruned=False, evidence_sha256=None):
        """Persist exactly one finite caller-supplied objective, failure or pruning."""
        if evidence_sha256 is not None and (
                not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
                or any(character not in "0123456789abcdef" for character in evidence_sha256)):
            raise SearchError("evidence_sha256 must be a lowercase SHA-256 hex digest")
        if type(pruned) is not bool:
            raise SearchError("pruned must be boolean")
        if failure is not None and (not isinstance(failure, str) or not failure.strip()):
            raise SearchError("failure must be a nonempty reason")
        if sum((result is not None, failure is not None, pruned)) != 1:
            raise SearchError("tell requires exactly one result, failure, or pruned=True")
        if result is not None:
            result = float(_finite_number(result, "Objective result"))
        before = self._verified_snapshot()
        required = {"trial_id", "number", "parameters", "study_sha256"}
        if not isinstance(trial, Mapping) or set(trial) != required or type(trial["number"]) is not int:
            raise SearchError("tell requires the unchanged trial handle returned by ask")
        matches = [record for record in before["trials"] if record["number"] == trial["number"]]
        if len(matches) != 1 or _json({name: matches[0][name] for name in required}) != _json(dict(trial)):
            raise SearchError("Trial handle/parameters do not match the bound study")
        if matches[0]["status"] != "running":
            raise SearchError("Trial is already terminal; its outcome cannot be overwritten")
        state = (self._optuna.trial.TrialState.FAIL if failure is not None else
                 self._optuna.trial.TrialState.PRUNED if pruned else self._optuna.trial.TrialState.COMPLETE)
        token = self._claim(before)
        try:
            if evidence_sha256 is not None:
                frozen = next(item for item in self._study.get_trials(deepcopy=False)
                              if item.number == trial["number"])
                live = self._optuna.trial.Trial(self._study, frozen._trial_id)
                live.set_user_attr("qsar_fit_evidence_sha256", evidence_sha256)
            self._study.tell(trial["number"], values=result, state=state)
        except Exception as error:
            self._finish(token, before)
            raise SearchError("Optuna rejected the trial outcome; persisted evidence was retained") from error
        after = self._finish(token, before, {trial["number"]: failure})
        return _copy(after["trials"][trial["number"]])

    def snapshot(self):
        """Return self-hashed evidence bound to actual persisted Optuna rows."""
        return _copy(self._verified_snapshot())

    def pending_trials(self):
        return [{name: record[name] for name in ("trial_id", "number", "parameters", "study_sha256")}
                for record in self.snapshot()["trials"] if record["status"] == "running"]

    def close(self):
        if self._storage is not None:
            self._storage.remove_session()
            self._storage.engine.dispose()
        self._closed = True

    def __enter__(self):
        if self._closed:
            raise SearchError("Study coordinator is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
