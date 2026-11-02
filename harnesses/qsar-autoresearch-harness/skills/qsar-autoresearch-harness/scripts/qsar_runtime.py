"""Transactional QSAR gates; local leader-attested provenance, not an OS sandbox.

The coordinator launches native agents and binds actual tool-returned execution
IDs. This module never fabricates a native launch or scientific review.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

STAGES = ("contract", "collection", "curation", "preprocessing", "features",
          "training-preflight", "training", "development-evaluation",
          "improvement", "final-fit", "terminal-evaluation", "release")


class GateError(ValueError):
    """Unmet or invalidated stage contract."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def snapshot(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise GateError(f"Not a regular file: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(p), "sha256": h.hexdigest(), "size": p.stat().st_size}


def snapshots(paths):
    result = {}
    for path in paths:
        item = snapshot(path)
        result[item["path"]] = item
    return list(result.values())


def verify(items):
    for item in items:
        try:
            current = snapshot(item["path"])
        except (OSError, ValueError) as error:
            raise GateError(f"Missing bound artifact: {item['path']}") from error
        if current != item:
            raise GateError(f"Changed bound artifact: {item['path']}")


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"Missing {label}")
    return value


class Runtime:
    def __init__(self, run_dir):
        self.root = Path(run_dir).expanduser().resolve()
        self.db = self.root / "runtime.sqlite3"
        self.harness = Path(__file__).resolve().parent.parent
        self.skills_root = self.harness.parent

    def _inside(self, path, parent=None):
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        boundary = (parent or self.root).resolve()
        if not p.is_relative_to(boundary) or p == boundary:
            raise GateError(f"Path outside owned directory: {p}")
        return p

    def _connect(self):
        if not self.db.is_file():
            raise GateError("Run is not initialized")
        return sqlite3.connect(self.db, timeout=30, isolation_level=None)

    @staticmethod
    def _events(con, events):
        con.executemany("INSERT INTO events(time,kind,payload) VALUES (?,?,?)",
                        [(now(), kind, canonical(data)) for kind, data in events])

    def initialize(self, contract_path, run_id=None, sealed_inputs=()):
        if self.db.exists():
            raise GateError("Existing run must be resumed, not reinitialized")
        contract = snapshot(contract_path)
        body = json.loads(Path(contract["path"]).read_text(encoding="utf-8-sig"))
        if not isinstance(body, dict):
            raise GateError("Contract must be a JSON object")
        sealed = snapshots(sealed_inputs)
        self.root.mkdir(parents=True, exist_ok=True)
        state = {"schema_version": 1, "run_id": run_id or str(uuid.uuid4()),
                 "created_at": now(), "contract": contract,
                 "contract_version": str(body.get("version", body.get("contract_version", "1"))),
                 "cycle": 0, "stages": {s: {"status": "pending", "attempt": 0} for s in STAGES},
                 "dispatches": {}, "sealed_inputs": sealed, "terminal_exposed": False,
                 "terminal_dispatch_id": None,
                 "provenance_boundary": "Leader-attested native evidence; not authentication or OS enforcement."}
        con = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            con.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)")
            con.execute("INSERT INTO state VALUES (1, ?)", (canonical(state),))
            self._events(con, [("initialized", {"run_id": state["run_id"], "contract": contract})])
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return self.status()

    def _invalidate(self, state, stage, reason, events):
        if stage not in STAGES:
            raise GateError(f"Unknown stage: {stage}")
        for name in STAGES[STAGES.index(stage):]:
            entry = state["stages"][name]
            for field in ("producer_dispatch", "review_dispatch"):
                if entry.get(field):
                    state["dispatches"][entry[field]]["invalidated"] = reason
            state["stages"][name] = {"status": "stale", "attempt": entry["attempt"], "reason": reason}
        events.append(("invalidated", {"from_stage": stage, "reason": reason}))

    def _sync(self, state, events):
        if "final-fit" not in state["stages"]:
            state["stages"]["final-fit"] = {"status": "pending", "attempt": 0}
            self._invalidate(state, "terminal-evaluation", "Final-fit review now required", events)
            events.append(("stage_graph_updated", {"added_stage": "final-fit"}))
        try:
            verify([state["contract"]])
        except GateError as error:
            if not state.get("contract_dirty"):
                state["contract_dirty"] = True
                self._invalidate(state, "contract", str(error), events)
            return
        for stage in STAGES:
            entry = state["stages"][stage]
            if entry["status"] in {"pending", "stale"}:
                continue
            try:
                producer = state["dispatches"].get(entry.get("producer_dispatch"))
                if producer:
                    verify(producer["bindings"])
                    if producer.get("native_evidence"):
                        verify([producer["native_evidence"]])
                    if producer.get("execution"):
                        verify([producer["execution"]["log"]])
                if entry.get("submission"):
                    verify(entry["submission"]["outputs"])
                    verify(entry["submission"].get("model_lock", []))
                if entry.get("review"):
                    verify(entry["review"]["evidence_bindings"])
                    verify(entry["review"].get("curation_evidence_bindings", []))
                    reviewer = state["dispatches"][entry["review_dispatch"]]
                    verify(reviewer["bindings"] + [reviewer["native_evidence"]])
            except GateError as error:
                self._invalidate(state, stage, str(error), events)
                return

    def _op(self, fn):
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            state = json.loads(con.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
            sync_events = []
            self._sync(state, sync_events)
            synced = copy.deepcopy(state)
            events = []
            try:
                result = fn(state, events)
            except Exception:
                # Staleness persists even when the requested action is rejected.
                if sync_events:
                    con.execute("UPDATE state SET payload=? WHERE id=1", (canonical(synced),))
                    self._events(con, sync_events)
                    con.commit()
                else:
                    con.rollback()
                raise
            con.execute("UPDATE state SET payload=? WHERE id=1", (canonical(state),))
            self._events(con, sync_events + events)
            con.commit()
            return copy.deepcopy(result)
        finally:
            con.close()

    def _contract_acceptance(self, state, candidate, descriptors):
        """Link external adapters to this run's actual accepted contract evidence.

        Native provenance remains leader-attested; file content or distinct IDs
        alone cannot substitute for the current stage and its evidence bindings.
        """
        if not descriptors:
            return
        entry = state['stages']['contract']
        if entry['status'] != 'accepted':
            raise GateError('Contract acceptance adapter requires current accepted contract')
        submission, review = entry['submission'], entry['review']
        expected = {'submission': submission['candidate_hash'], 'review': review['review_hash']}
        if candidate['upstream_tokens'].get('contract') != expected:
            raise GateError('Contract acceptance adapter has stale upstream contract')
        verify([state['contract'], *submission['outputs'], *review['evidence_bindings']])
        def pair(item):
            return (item['path'], item['sha256'])
        outputs = {pair(item) for item in submission['outputs']}
        reviewed = {pair(item) for item in review['evidence_bindings']}
        for descriptor in descriptors:
            current = snapshot(descriptor['path'])
            if pair(current) != pair(descriptor):
                raise GateError('Contract acceptance adapter changed')
            envelope = load_json(current['path'])
            if (envelope.get('stage') != 'contract' or envelope.get('status') != 'PASS'
                    or envelope.get('contract_sha256') != state['contract']['sha256']
                    or envelope.get('producer_execution_id') != submission['producer_execution_id']
                    or envelope.get('reviewer_execution_id') != review['reviewer_execution_id']):
                raise GateError('Contract acceptance adapter does not match accepted stage')
            if pair(envelope['candidate']) not in outputs:
                raise GateError('Contract acceptance candidate is not an accepted stage output')
            if pair(envelope['review_evidence']) not in reviewed:
                raise GateError('Contract acceptance review evidence is not accepted stage evidence')

    def _curation_evidence(self, state, candidate):
        accepted = state['stages']['curation']
        # A prior full validation is reusable only for its exact accepted
        # submission and unchanged complete dependencies. Do not parse all
        # native sources again at each downstream stage.
        if (accepted['status'] == 'accepted' and accepted.get('submission') == candidate
                and accepted.get('review', {}).get('curation_evidence_bindings')):
            verified = accepted['review']['curation_evidence_bindings']
            verify(verified)
            return [{key: item[key] for key in ('path', 'sha256')} for item in verified]
        spec = importlib.util.spec_from_file_location("qsar_curation_evidence", Path(__file__).with_name("qsar_curation_evidence.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            if not module.strict(module.load(state["contract"]["path"])):
                return []
            coverage = candidate.get("coverage", {})
            if coverage.get("status") == "blocked":
                raise GateError("Invalid curation evidence: blocked candidate coverage")
            manifests = [x for x in candidate["outputs"] if Path(x["path"]).name == "curation_manifest.json"]
            if len(manifests) != 1:
                raise GateError("Invalid curation evidence: exactly one current manifest required")
            collection = state["stages"]["collection"]
            expected = {"submission": collection["submission"]["candidate_hash"], "review": collection["review"]["review_hash"]}
            if collection["status"] != "accepted" or candidate["upstream_tokens"].get("collection") != expected:
                raise GateError("Invalid curation evidence: stale collection dependency")
            references = module.validate(state["contract"]["path"], manifests[0]["path"],
                                         candidate_outputs=candidate["outputs"], collection_outputs=collection["submission"]["outputs"])
            self._contract_acceptance(state, candidate, module.contract_acceptance_bindings(manifests[0]['path']))
            if coverage != module.load(manifests[0]["path"]):
                raise GateError("Invalid curation evidence: candidate coverage differs from bound manifest")
            return references
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise GateError("Invalid curation evidence: " + str(error)) from error

    def _prerequisites(self, state, stage, terminal_review=False):
        if stage not in STAGES:
            raise GateError(f"Unknown stage: {stage}")
        if state.get("contract_dirty"):
            raise GateError("Contract changed; replace_contract or initialize a new research version")
        if stage in {"final-fit", "terminal-evaluation"}:
            improvement = state["stages"]["improvement"]
            if improvement["status"] == "accepted":
                candidate = improvement["submission"]
                if candidate.get("decision") != "STOP" or not candidate.get("model_lock"):
                    raise GateError("Final fit and terminal evaluation require reviewed STOP and model lock")
                verify(candidate["model_lock"])
        tokens = {}
        for previous in STAGES[:STAGES.index(stage)]:
            entry = state["stages"][previous]
            if entry["status"] != "accepted":
                raise GateError(f"Independent PASS required for {previous} before {stage}")
            if previous == "curation":
                self._curation_evidence(state, entry["submission"])
            tokens[previous] = {"submission": entry["submission"]["candidate_hash"],
                                "review": entry["review"]["review_hash"]}
        if stage == "terminal-evaluation":
            improvement = state["stages"]["improvement"]["submission"]
            if improvement.get("decision") != "STOP" or not improvement.get("model_lock"):
                raise GateError("Terminal evaluation requires reviewed STOP and model lock")
            verify(improvement["model_lock"])
            if not state["sealed_inputs"]:
                raise GateError("Terminal evaluation requires registered sealed holdout files")
            verify(state["sealed_inputs"])
            if state["terminal_exposed"] and not terminal_review:
                raise GateError("Terminal cohort already exposed; no second evaluation in this run")
        return tokens

    def _roles(self, stage, kind, available_roles):
        stage_key = "training" if stage in {"training-preflight", "final-fit"} else stage
        registry = json.loads((self.harness / "references/agent-skills.json").read_text(encoding="utf-8-sig"))
        records = [s for s in registry["stages"] if s["id"] == stage_key]
        if not records:
            raise GateError(f"Unknown stage: {stage}")
        assignment = records[0]["producer" if kind == "producer" else "reviewer"]
        desired = assignment.get("native_agent_type", assignment["agent_type"])
        actual = desired if available_roles and desired in available_roles else assignment["agent_type"]
        if available_roles is None:
            raise GateError("Dispatch needs actual available_roles capability evidence")
        if actual not in available_roles:
            raise GateError(f"No supported role available for {stage}/{kind}")
        return desired, actual, assignment["skill"]

    def prepare_dispatch(self, stage, kind="producer", *, inputs=(), code=(), config=(),
                         command=None, context=None, available_roles=None, pipeline_identity=None):
        if kind not in {"producer", "review"}:
            raise GateError("Dispatch kind must be producer or review")
        context = dict(context or {})
        for key in ("objective", "acceptance_criteria", "stop_condition"):
            require_text(context.get(key), key)
        desired, role, skill = self._roles(stage, kind, available_roles)
        reads = snapshots([Path(__file__), self.harness / "references/agent-skills.json",
                           self.skills_root / "qsar-autoresearch/SKILL.md",
                           self.skills_root / "qsar-autoresearch/references/artifact-contracts.md",
                           self.skills_root / skill / "SKILL.md"])
        declared = snapshots([*inputs, *code, *config])
        pipeline = None
        if kind == "producer" and stage in {"training-preflight", "training"}:
            if not isinstance(pipeline_identity, dict):
                raise GateError("Training requires explicit pipeline_identity")
            pipeline = {}
            for category in ("inputs", "code", "config"):
                paths = pipeline_identity.get(category)
                if not isinstance(paths, list) or (category != "config" and not paths):
                    raise GateError(f"pipeline_identity requires {category} paths")
                pipeline[category] = sorted(snapshots(paths), key=lambda item: item["path"])
            if not isinstance(pipeline_identity.get("parameters"), dict):
                raise GateError("pipeline_identity requires explicit parameters object")
            pipeline["parameters"] = pipeline_identity["parameters"]
            for category, paths in (("inputs", inputs), ("code", code), ("config", config)):
                allowed = {item["path"] for item in pipeline[category]}
                if any(item["path"] not in allowed for item in snapshots(paths)):
                    raise GateError(f"Declared {category} is outside reviewed pipeline_identity")
            declared += [item for category in ("inputs", "code", "config") for item in pipeline[category]]
        if command is not None:
            if kind != "producer" or not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
                raise GateError("Command must be an explicit nonempty producer argv list")
            if not code:
                raise GateError("Command dispatch must bind its adapter code files")
            executable = shutil.which(command[0])
            if not executable:
                raise GateError(f"Command executable not found: {command[0]}")
            command = [str(Path(executable).resolve()), *command[1:]]
            declared += snapshots([command[0]])

        def action(state, events):
            if kind == "producer" and state["terminal_exposed"] and STAGES.index(stage) < STAGES.index("terminal-evaluation"):
                raise GateError("Development cannot resume after terminal exposure in the same run")
            tokens = self._prerequisites(state, stage, terminal_review=kind == "review")
            if stage == "training" and kind == "producer":
                approved = state["dispatches"][state["stages"]["training-preflight"]["producer_dispatch"]]
                if pipeline != approved["pipeline_identity"]:
                    raise GateError("Training pipeline_identity differs from accepted preflight")
            entry = state["stages"][stage]
            if kind == "producer":
                if entry["status"] not in {"pending", "stale", "revise", "blocked", "failed"}:
                    raise GateError("Stage already live/accepted; invalidate explicitly before replacing it")
                attempt = entry["attempt"] + 1
            else:
                if entry["status"] != "produced" or not entry.get("submission"):
                    raise GateError("Review needs a current producer submission")
                if entry.get("review_dispatch"):
                    raise GateError("Review already prepared for this candidate")
                attempt = entry["attempt"]
            sealed = {x["path"] for x in state["sealed_inputs"]}
            if stage not in {"terminal-evaluation", "release"} and sealed.intersection(x["path"] for x in declared):
                raise GateError("Sealed holdout files cannot be dispatched to development agents")
            dispatch_id = str(uuid.uuid4())
            folder = self.root / ("reviews" if kind == "review" else "stages") / stage / f"attempt-{attempt}" / dispatch_id
            replacements = {"{output_dir}": str(folder), "{run_dir}": str(self.root), "{dispatch_id}": dispatch_id}
            frozen_command = [replacements.get(arg, arg) for arg in command] if command else None
            terminal_bindings = state["sealed_inputs"] if stage == "terminal-evaluation" else []
            recipe_lock = state["stages"]["improvement"]["submission"]["model_lock"] if stage == "final-fit" else []
            envelope = {"dispatch_id": dispatch_id, "run_id": state["run_id"], "stage": stage,
                        "kind": kind, "attempt": attempt, "cycle": state["cycle"],
                        "contract_version": state["contract_version"], "requested_agent_type": desired,
                        "agent_type": role, "role_fallback": desired != role, "fork_turns": "none",
                        "required_reads": [x["path"] for x in reads], "owned_output_dir": str(folder),
                        "upstream_tokens": tokens, "bindings": [state["contract"]] + reads + declared + terminal_bindings + recipe_lock,
                        "recipe_lock": recipe_lock,
                        "context": context, "command": frozen_command, "command_sha256": digest(frozen_command),
                        "pipeline_identity": pipeline,
                        "status": "prepared", "created_at": now(),
                        "provenance": "Awaiting actual native tool-result binding; no agent launched."}
            if kind == "review":
                envelope["candidate_hash"] = entry["submission"]["candidate_hash"]
                envelope["producer_execution_id"] = entry["submission"]["producer_execution_id"]
                entry["review_dispatch"] = dispatch_id
            else:
                state["stages"][stage] = {"status": "prepared", "attempt": attempt, "producer_dispatch": dispatch_id}
            state["dispatches"][dispatch_id] = envelope
            if stage == "terminal-evaluation" and kind == "producer":
                state["terminal_exposed"] = True
                state["terminal_dispatch_id"] = dispatch_id
                events.append(("terminal_access_reserved", {"dispatch_id": dispatch_id, "exposure": "reserved_possible"}))
            events.append(("dispatch_prepared", {"dispatch_id": dispatch_id, "stage": stage, "kind": kind}))
            folder.mkdir(parents=True, exist_ok=False)
            return envelope
        return self._op(action)

    def _dispatch(self, state, dispatch_id, kind=None):
        dispatch = state["dispatches"].get(dispatch_id)
        if not dispatch or dispatch.get("invalidated"):
            raise GateError("Unknown or invalidated dispatch")
        if kind and dispatch["kind"] != kind:
            raise GateError(f"Expected {kind} dispatch")
        entry = state["stages"][dispatch["stage"]]
        key = "producer_dispatch" if dispatch["kind"] == "producer" else "review_dispatch"
        if entry.get(key) != dispatch_id:
            raise GateError("Dispatch is no longer the current stage attempt")
        verify(dispatch["bindings"])
        for stage, token in dispatch["upstream_tokens"].items():
            previous = state["stages"][stage]
            if previous["status"] != "accepted" or token != {
                    "submission": previous["submission"]["candidate_hash"], "review": previous["review"]["review_hash"]}:
                raise GateError("Upstream acceptance changed since dispatch")
            if stage == "curation":
                self._curation_evidence(state, previous["submission"])
        return dispatch, entry

    def bind_dispatch(self, dispatch_id, native_execution_id, evidence_path):
        require_text(native_execution_id, "actual native execution ID")
        evidence = snapshot(self._inside(evidence_path))
        if not evidence["size"]:
            raise GateError("Native evidence cannot be empty")
        def action(state, events):
            dispatch, entry = self._dispatch(state, dispatch_id)
            if dispatch["status"] != "prepared":
                raise GateError("Dispatch is already bound/consumed")
            if dispatch["kind"] == "review" and native_execution_id == dispatch["producer_execution_id"]:
                raise GateError("A producer cannot review its own stage")
            if dispatch["kind"] == "producer" and dispatch["stage"] == "terminal-evaluation":
                if state.get("terminal_dispatch_id") != dispatch_id:
                    raise GateError("Terminal access reserved for another dispatch")
            dispatch.update(native_execution_id=native_execution_id, native_evidence=evidence, status="bound",
                            provenance="Leader-attested native tool result; not authentication.")
            events.append(("dispatch_bound", {"dispatch_id": dispatch_id, "native_execution_id": native_execution_id, "evidence": evidence}))
            return dispatch
        return self._op(action)

    def execute(self, dispatch_id, *, timeout=None):
        """Execute the frozen explicit argv once, with hidden Windows children."""
        def reserve(state, events):
            dispatch, entry = self._dispatch(state, dispatch_id, "producer")
            if dispatch["status"] != "bound" or not dispatch.get("command"):
                raise GateError("Execution requires a bound, unexecuted command dispatch")
            if dispatch.get("execution"):
                raise GateError("Command already executed; create a new attempt")
            verify([dispatch["native_evidence"]])
            dispatch["status"] = entry["status"] = "running"
            events.append(("command_started", {"dispatch_id": dispatch_id, "argv": dispatch["command"]}))
            return dispatch
        dispatch = self._op(reserve)
        out = Path(dispatch["owned_output_dir"])
        argv = dispatch["command"]
        log = out / "execution.log"
        started = now()
        try:
            with log.open("wb") as handle:
                process = subprocess.run(argv, cwd=out, stdin=subprocess.DEVNULL, stdout=handle,
                                         stderr=subprocess.STDOUT, shell=False, timeout=timeout,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result = {"argv": argv, "exit_code": process.returncode, "started_at": started,
                      "finished_at": now(), "log": snapshot(log), "timed_out": False}
        except (OSError, subprocess.TimeoutExpired) as error:
            with log.open("ab") as handle:
                handle.write(("\n" + repr(error)).encode())
            result = {"argv": argv, "exit_code": None, "started_at": started,
                      "finished_at": now(), "log": snapshot(log),
                      "timed_out": isinstance(error, subprocess.TimeoutExpired)}
        def finish(state, events):
            current = state["dispatches"][dispatch_id]
            current["execution"] = result
            if current.get("invalidated"):
                events.append(("command_finished_after_invalidation", {"dispatch_id": dispatch_id, **result}))
                return {**result, "status": "stale"}
            current["status"] = "executed" if result["exit_code"] == 0 else "failed"
            state["stages"][current["stage"]]["status"] = current["status"]
            events.append(("command_finished", {"dispatch_id": dispatch_id, **result}))
            return {**result, "status": current["status"]}
        return self._op(finish)

    def submit(self, dispatch_id, manifest):
        """Submit output paths and checks. The runtime computes all hashes."""
        if not isinstance(manifest, dict):
            raise GateError("Producer manifest must be an object")
        require_text(manifest.get("summary"), "producer summary")
        if not isinstance(manifest.get("checks"), list) or not manifest["checks"]:
            raise GateError("Producer manifest needs performed checks")
        if not isinstance(manifest.get("outputs"), list) or not manifest["outputs"]:
            raise GateError("Producer manifest needs output files")
        def action(state, events):
            dispatch, entry = self._dispatch(state, dispatch_id, "producer")
            if dispatch["status"] not in {"bound", "executed"}:
                raise GateError("Submission requires a bound, unconsumed successful producer")
            if dispatch.get("command") and dispatch["status"] != "executed":
                raise GateError("Registered command must execute successfully before submission")
            verify([dispatch["native_evidence"]])
            outputs = snapshots([self._inside(x, Path(dispatch["owned_output_dir"])) for x in manifest["outputs"]])
            candidate = {"run_id": state["run_id"], "stage": dispatch["stage"], "attempt": dispatch["attempt"],
                         "cycle": state["cycle"], "contract_version": state["contract_version"],
                         "producer_execution_id": dispatch["native_execution_id"], "bindings": dispatch["bindings"],
                         "upstream_tokens": dispatch["upstream_tokens"], "outputs": outputs,
                         "command": dispatch["command"], "command_sha256": dispatch["command_sha256"],
                         "execution": dispatch.get("execution"),
                         "summary": manifest["summary"], "checks": manifest["checks"],
                         "limitations": manifest.get("limitations", []), "coverage": manifest.get("coverage", {})}
            if dispatch["stage"] == "improvement":
                decision = manifest.get("decision")
                if decision not in {"STOP", "CONTINUE"}:
                    raise GateError("Improvement must declare STOP or CONTINUE")
                candidate["decision"] = decision
                candidate["reason"] = require_text(manifest.get("reason"), "improvement reason")
                if decision == "STOP":
                    lock = snapshots(manifest.get("model_lock", []))
                    accepted = {x["path"]: x for name in ("features", "training")
                                for x in state["stages"][name]["submission"]["outputs"]}
                    if not lock or any(accepted.get(x["path"]) != x for x in lock):
                        raise GateError("Model lock must bind accepted feature/training output files")
                    candidate["model_lock"] = lock
                else:
                    target = manifest.get("restart_from")
                    if target not in STAGES[:STAGES.index("improvement")]:
                        raise GateError("CONTINUE needs an earlier restart_from stage")
                    candidate["restart_from"] = target
            candidate["candidate_hash"] = digest(candidate)
            entry.update(status="produced", submission=candidate)
            dispatch.update(status="submitted", submission=candidate)
            events.append(("producer_submitted", {"dispatch_id": dispatch_id, "candidate_hash": candidate["candidate_hash"]}))
            return candidate
        return self._op(action)

    def review(self, dispatch_id, receipt):
        if not isinstance(receipt, dict) or receipt.get("verdict") not in {"PASS", "REVISE", "BLOCK"}:
            raise GateError("Review verdict must be PASS, REVISE or BLOCK")
        require_text(receipt.get("summary"), "review summary")
        if not isinstance(receipt.get("checks"), list) or not receipt["checks"]:
            raise GateError("Review needs independent checks")
        if not isinstance(receipt.get("evidence"), list) or not receipt["evidence"]:
            raise GateError("Review needs concrete evidence files")
        def action(state, events):
            dispatch, entry = self._dispatch(state, dispatch_id, "review")
            if dispatch["status"] != "bound" or entry["status"] != "produced":
                raise GateError("Review needs a bound independent execution and current candidate")
            candidate = entry["submission"]
            verify(candidate["outputs"])
            curation_bindings = []
            if dispatch["stage"] == "curation" and receipt["verdict"] == "PASS":
                references = self._curation_evidence(state, candidate)
                curation_bindings = snapshots(item['path'] for item in references)
                expected = {(item['path'], item['sha256']) for item in references}
                if {(item['path'], item['sha256']) for item in curation_bindings} != expected:
                    raise GateError('Curation dependencies changed during acceptance')
            verify([dispatch["native_evidence"]])
            if receipt.get("candidate_hash") != candidate["candidate_hash"] or receipt.get("candidate_hash") != dispatch["candidate_hash"]:
                raise GateError("Review does not bind the exact current candidate")
            if receipt.get("producer_execution_id") != candidate["producer_execution_id"]:
                raise GateError("Review producer identity mismatch")
            if receipt.get("reviewer_execution_id") != dispatch["native_execution_id"]:
                raise GateError("Review reviewer identity mismatch")
            if receipt["producer_execution_id"] == receipt["reviewer_execution_id"]:
                raise GateError("Self-review cannot satisfy independent acceptance")
            evidence = snapshots([self._inside(x, Path(dispatch["owned_output_dir"])) for x in receipt["evidence"]])
            if any(not x["size"] for x in evidence):
                raise GateError("Review evidence cannot be empty")
            findings = receipt.get("findings", [])
            if not isinstance(findings, list):
                raise GateError("Review findings must be a list")
            if any(not isinstance(f, dict) or not isinstance(f.get("severity"), str)
                   or f["severity"].lower() not in {"critical", "high", "blocker", "medium", "low", "info"}
                   or type(f.get("resolved")) is not bool for f in findings):
                raise GateError("Findings require known severity and boolean resolved")
            if receipt["verdict"] == "PASS" and any(
                    isinstance(f, dict) and str(f.get("severity", "")).lower() in {"critical", "high", "blocker"}
                    and not f.get("resolved", False) for f in findings):
                raise GateError("PASS cannot retain unresolved blocking findings")
            record = {**receipt, "evidence_bindings": evidence, "run_id": state["run_id"], "stage": dispatch["stage"],
                      "attempt": dispatch["attempt"], "native_return_evidence": dispatch["native_evidence"],
                      "provenance": "Leader-attested separate native execution; not authentication."}
            if curation_bindings:
                record['curation_evidence_bindings'] = curation_bindings
            record["review_hash"] = digest(record)
            entry.update(status={"PASS": "accepted", "REVISE": "revise", "BLOCK": "blocked"}[receipt["verdict"]], review=record)
            dispatch.update(status="reviewed", receipt=record)
            events.append(("review_recorded", {"dispatch_id": dispatch_id, "verdict": receipt["verdict"], "review_hash": record["review_hash"]}))
            return record
        return self._op(action)

    def invalidate(self, stage, reason):
        require_text(reason, "invalidation reason")
        def action(state, events):
            self._invalidate(state, stage, reason, events)
            return state["stages"]
        return self._op(action)

    def replace_contract(self, contract_path, reason):
        require_text(reason, "contract revision reason")
        bound = snapshot(contract_path)
        contract = json.loads(Path(bound["path"]).read_text(encoding="utf-8-sig"))
        if not isinstance(contract, dict):
            raise GateError("Contract must be a JSON object")
        def action(state, events):
            if state["terminal_exposed"]:
                raise GateError("After terminal exposure initialize a new research version")
            self._invalidate(state, "contract", reason, events)
            state.update(contract=bound, contract_version=str(contract.get("version", contract.get("contract_version", "1"))),
                         contract_dirty=False)
            state["cycle"] += 1
            events.append(("contract_replaced", {"contract": bound, "reason": reason}))
            return state["stages"]
        return self._op(action)

    def advance_improvement(self):
        def action(state, events):
            entry = state["stages"]["improvement"]
            if entry["status"] != "accepted":
                raise GateError("Independent improvement PASS required")
            candidate = entry["submission"]
            if candidate["decision"] == "STOP":
                return {"next_stage": "final-fit", "model_lock": candidate["model_lock"]}
            if state["terminal_exposed"]:
                raise GateError("Cannot continue development after terminal exposure in the same run")
            target = candidate["restart_from"]
            self._invalidate(state, target, "Accepted improvement: " + candidate["reason"], events)
            state["cycle"] += 1
            events.append(("improvement_advanced", {"candidate_hash": candidate["candidate_hash"], "restart_from": target, "cycle": state["cycle"]}))
            return {"next_stage": target, "cycle": state["cycle"]}
        return self._op(action)

    def seal_holdout(self, paths):
        bound = snapshots(paths)
        if not bound:
            raise GateError("Holdout binding needs files")
        def action(state, events):
            if state["terminal_exposed"] or state["stages"]["training"]["attempt"]:
                raise GateError("Holdout must be sealed before training or terminal access")
            if state["sealed_inputs"] and state["sealed_inputs"] != bound:
                raise GateError("Holdout already bound; create a new contract/run to change it")
            state["sealed_inputs"] = bound
            events.append(("holdout_sealed", {"bindings": bound}))
            return bound
        return self._op(action)

    def status(self):
        """Revalidate all current bindings and return durable resume state."""
        return self._op(lambda state, events: state)

    def events(self):
        con = self._connect()
        try:
            return [{"sequence": row[0], "time": row[1], "kind": row[2], "payload": json.loads(row[3])}
                    for row in con.execute("SELECT sequence,time,kind,payload FROM events ORDER BY sequence")]
        finally:
            con.close()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init")
    p.add_argument("--contract", required=True)
    p.add_argument("--run-id")
    p = sub.add_parser("prepare")
    p.add_argument("--spec", required=True, help="JSON kwargs for prepare_dispatch")
    p = sub.add_parser("bind")
    p.add_argument("--dispatch-id", required=True)
    p.add_argument("--native-id", required=True)
    p.add_argument("--evidence", required=True)
    for command, argument in (("submit", "manifest"), ("review", "receipt")):
        p = sub.add_parser(command)
        p.add_argument("--dispatch-id", required=True)
        p.add_argument("--" + argument, required=True)
    p = sub.add_parser("execute")
    p.add_argument("--dispatch-id", required=True)
    p.add_argument("--timeout", type=float)
    p = sub.add_parser("invalidate")
    p.add_argument("--stage", choices=STAGES, required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("replace-contract")
    p.add_argument("--contract", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("seal-holdout")
    p.add_argument("files", nargs="+")
    for command in ("advance", "status", "events"):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    rt = Runtime(args.run_dir)
    try:
        if args.action == "init":
            result = rt.initialize(args.contract, args.run_id)
        elif args.action == "prepare":
            result = rt.prepare_dispatch(**load_json(args.spec))
        elif args.action == "bind":
            result = rt.bind_dispatch(args.dispatch_id, args.native_id, args.evidence)
        elif args.action == "submit":
            result = rt.submit(args.dispatch_id, load_json(args.manifest))
        elif args.action == "review":
            result = rt.review(args.dispatch_id, load_json(args.receipt))
        elif args.action == "execute":
            result = rt.execute(args.dispatch_id, timeout=args.timeout)
        elif args.action == "invalidate":
            result = rt.invalidate(args.stage, args.reason)
        elif args.action == "replace-contract":
            result = rt.replace_contract(args.contract, args.reason)
        elif args.action == "seal-holdout":
            result = rt.seal_holdout(args.files)
        elif args.action == "advance":
            result = rt.advance_improvement()
        elif args.action == "status":
            result = rt.status()
        else:
            result = rt.events()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (GateError, OSError, json.JSONDecodeError, sqlite3.Error) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
