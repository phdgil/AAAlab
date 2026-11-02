"""Behavioral gate tests using explicit synthetic artifact fixtures, never research data."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_runtime.py"
spec = importlib.util.spec_from_file_location("qsar_runtime", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Runtime, GateError, STAGES = mod.Runtime, mod.GateError, mod.STAGES
ROLES = ["analyst", "executor", "verifier", "critic"]
CONTEXT = {"objective": "Isolated runtime fixture", "acceptance_criteria": "Fixture accounting verified",
           "stop_condition": "Return the assigned fixture stage only"}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-runtime-test-")
        self.base = Path(self.temp.name)
        self.run = self.base / "run"
        self.contract = self.base / "contract.json"
        self.contract.write_text('{"version":"fixture-v1","objective":"gate tests"}')
        self.holdout = self.base / "sealed.csv"
        self.holdout.write_text("id,y\nheld,5\n")
        self.rt = Runtime(self.run)
        self.rt.initialize(self.contract, "fixture-run", [self.holdout])
        self.counter = 0

    def tearDown(self):
        # TemporaryDirectory owns only its generated, absolute fixture directory.
        self.temp.cleanup()

    def prepare(self, stage, kind="producer", **kw):
        if stage in {"training", "training-preflight"} and kind == "producer":
            kw.setdefault("pipeline_identity", {"inputs": [str(self.contract)], "code": [str(SCRIPT)],
                                                "config": [], "parameters": {"fixture": True}})
        return self.rt.prepare_dispatch(stage, kind, context=CONTEXT, available_roles=ROLES, **kw)

    def bind(self, dispatch, native_id=None):
        self.counter += 1
        identity = native_id or f"fixture-native-{self.counter}"
        evidence = self.run / "native-evidence" / f"{self.counter}.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text(json.dumps({"fixture_only": True, "actual_test_identity": identity}))
        return self.rt.bind_dispatch(dispatch["dispatch_id"], identity, evidence)

    @staticmethod
    def artifact(path):
        return {key: value for key, value in mod.snapshot(path).items() if key in {"path", "sha256"}}

    def preprocessing(self, dispatch, source, external=None):
        folder = Path(dispatch["owned_output_dir"])
        content = {
            "development.csv": "output_row_id,task_id,group_id,partition,target\ndev,oral,g1,development,1\n",
            "sealed_terminal.csv": "output_row_id,task_id,group_id,partition,target\nheld,oral,g2,terminal,5\n",
            "grouping_mapping.csv": "output_row_id,partition\ndev,development\nheld,terminal\n",
            "development_grouping_mapping.csv": "output_row_id,partition\ndev,development\n",
            "membership.csv": "output_row_id,partition\ndev,development\nheld,terminal\n",
            "folds.json": '{"tasks":{"oral":{"row_ids":["dev"]}}}',
            "exposure_ledger.json": '{"status":"not_previously_exposed","cohort":"terminal"}',
            "unfitted_pipeline.json": '{"imputation":"train-only"}'}
        for name, body in content.items():
            (folder / name).write_text(body)
        manifest = {"stage": "preprocessing", "status": "complete", "schema_version": "fixture",
                    "input": self.artifact(source), "contract": self.artifact(self.contract)}
        for key in ("development", "sealed_terminal", "grouping_mapping", "development_grouping_mapping",
                    "membership", "folds", "exposure_ledger", "unfitted_pipeline"):
            manifest[key] = self.artifact(folder / (key + (".json" if key in {"folds", "exposure_ledger", "unfitted_pipeline"} else ".csv")))
        manifest["custodian_only"] = [manifest[key] for key in ("input", "sealed_terminal", "grouping_mapping")]
        if external:
            (folder / "sealed_external.csv").write_bytes(external.read_bytes())
            (folder / "external_exposure_ledger.json").write_text('{"cohort":"external","prior_exposure":"unknown"}')
            (folder / "external_exclusions.csv").write_text("output_row_id,reason\nexcluded,external_overlap\n")
            manifest["external_cohort"] = {"input": self.artifact(external), "artifact": self.artifact(folder / "sealed_external.csv"),
                "exposure_ledger": self.artifact(folder / "external_exposure_ledger.json"),
                "exclusions": self.artifact(folder / "external_exclusions.csv"), "prior_exposure": {"status": "unknown"}}
            manifest["custodian_only"].extend(manifest["external_cohort"][key] for key in ("input", "artifact", "exposure_ledger", "exclusions"))
        path = folder / "preprocessing_manifest.json"
        path.write_text(json.dumps(manifest))
        return path

    def produce(self, stage, evaluation_mode="terminal", **extra):
        if stage == "preprocessing":
            source = self.rt.status()["stages"]["curation"]["submission"]["outputs"][0]["path"]
            external = getattr(self, "external_source", None)
            dispatch = self.bind(self.prepare(stage, inputs=[source] + ([external] if external else [])))
            output = self.preprocessing(dispatch, source, external)
        else:
            dispatch = self.bind(self.prepare(stage, evaluation_mode=evaluation_mode))
            output = Path(dispatch["owned_output_dir"]) / ("result.csv" if stage == "curation" else "result.json")
            output.write_text("output_row_id,task_id,group_id,target\ndev,oral,g1,1\nheld,oral,g2,5\n" if stage == "curation"
                              else json.dumps({"stage": stage, "fixture_only": True, "mode": evaluation_mode}))
        candidate = self.rt.submit(dispatch["dispatch_id"], {
            "outputs": [str(output)], "summary": "Fixture produced", "checks": ["fixture shape"], **extra})
        return dispatch, candidate

    def accept(self, stage, verdict="PASS", evaluation_mode="terminal", **extra):
        dispatch = self.bind(self.prepare(stage, "review", evaluation_mode=evaluation_mode))
        report = Path(dispatch["owned_output_dir"]) / "independent-check.json"
        report.write_text('{"fixture_only":true,"independent_check":"checked supplied fixture"}')
        receipt = {"candidate_hash": dispatch["candidate_hash"],
                   "producer_execution_id": dispatch["producer_execution_id"],
                   "reviewer_execution_id": dispatch["native_execution_id"],
                   "verdict": verdict, "summary": "Fixture independently reviewed",
                   "checks": ["fixture accounted"], "evidence": [str(report)], "findings": [], **extra}
        return self.rt.review(dispatch["dispatch_id"], receipt)

    def through(self, last, decision="STOP", restart="curation"):
        for stage in STAGES[:STAGES.index(last) + 1]:
            if self.rt.status()["stages"][stage]["status"] == "accepted":
                continue
            options = {}
            if stage == "improvement":
                options = {"decision": decision, "reason": "Fixture decision"}
                if decision == "STOP":
                    options["model_lock"] = [self.rt.status()["stages"]["training"]["submission"]["outputs"][0]["path"]]
                else:
                    options["restart_from"] = restart
            self.produce(stage, **options)
            self.accept(stage)

    def test_full_fixture_sequence_and_resume(self):
        self.through("release")
        resumed = Runtime(self.run).status()
        self.assertTrue(all(x["status"] == "accepted" for x in resumed["stages"].values()))
        self.assertTrue(resumed["terminal_exposed"])
        self.assertTrue(any(x["kind"] == "terminal_access_reserved" for x in self.rt.events()))
        with self.assertRaises(GateError):
            self.rt.initialize(self.contract)

    def test_strict_blocked_curation_cannot_receive_pass(self):
        self.contract.write_text(json.dumps({"version": "strict-fixture", "execution_policy": {
            "allow_training_with_unresolved_required_source_coverage": False}}))
        self.rt.replace_contract(self.contract, "Strict synthetic coverage fixture")
        self.through("collection")
        self.produce("curation", coverage={"status": "blocked", "blockers": [{
            "source_id": "hsdb", "required": True, "reason": "unresolved"}]})
        with self.assertRaisesRegex(GateError, "curation evidence"):
            self.accept("curation")
        self.assertEqual(self.rt.status()["stages"]["curation"]["status"], "produced")

    def test_missing_upstream_and_preflight_block_training(self):
        with self.assertRaisesRegex(GateError, "contract"):
            self.prepare("collection")
        self.through("features")
        with self.assertRaisesRegex(GateError, "training-preflight"):
            self.prepare("training")

    def test_fresh_context_and_actual_catalog(self):
        with self.assertRaisesRegex(GateError, "available_roles"):
            self.rt.prepare_dispatch("contract", context=CONTEXT)
        dispatch = self.prepare("contract")
        self.assertEqual(dispatch["fork_turns"], "none")
        self.assertEqual(dispatch["agent_type"], "analyst")
        self.assertEqual(len(dispatch["required_reads"]), 5)  # runtime/jobs, registry, shared and contracts

    def test_unbound_submission_rejected(self):
        dispatch = self.prepare("contract")
        output = Path(dispatch["owned_output_dir"]) / "x"
        output.write_text("x")
        with self.assertRaisesRegex(GateError, "bound"):
            self.rt.submit(dispatch["dispatch_id"], {"summary": "x", "checks": ["x"], "outputs": [str(output)]})

    def test_self_review_rejected_at_binding(self):
        producer, _ = self.produce("contract")
        review = self.prepare("contract", "review")
        with self.assertRaisesRegex(GateError, "own stage"):
            self.bind(review, producer["native_execution_id"])

    def test_foreign_output_rejected(self):
        dispatch = self.bind(self.prepare("contract"))
        with self.assertRaisesRegex(GateError, "outside owned"):
            self.rt.submit(dispatch["dispatch_id"], {"summary": "x", "checks": ["x"], "outputs": [str(self.contract)]})

    def test_wrong_candidate_and_empty_evidence_rejected(self):
        self.produce("contract")
        with self.assertRaisesRegex(GateError, "exact current"):
            self.accept("contract", candidate_hash="wrong")
        # The failed receipt did not consume the bound review.
        state = self.rt.status()
        d = state["dispatches"][state["stages"]["contract"]["review_dispatch"]]
        with self.assertRaisesRegex(GateError, "concrete evidence"):
            self.rt.review(d["dispatch_id"], {"verdict": "PASS", "summary": "x", "checks": ["x"], "evidence": []})

    def test_high_findings_cannot_pass(self):
        self.produce("contract")
        with self.assertRaisesRegex(GateError, "blocking findings"):
            self.accept("contract", findings=[{"severity": "high", "resolved": False, "description": "bad fixture"}])

    def test_block_preserved_and_prevents_downstream(self):
        self.produce("contract")
        self.accept("contract", verdict="BLOCK")
        with self.assertRaisesRegex(GateError, "PASS"):
            self.prepare("collection")
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "blocked")
        self.produce("contract")
        self.accept("contract")
        self.assertEqual(self.rt.status()["stages"]["contract"]["attempt"], 2)
        self.assertEqual(sum(e["kind"] == "review_recorded" for e in self.rt.events()), 2)

    def test_changed_output_invalidates_transitive_acceptance(self):
        self.through("curation")
        out = Path(self.rt.status()["stages"]["collection"]["submission"]["outputs"][0]["path"])
        out.write_text("changed after review")
        with self.assertRaisesRegex(GateError, "collection"):
            self.prepare("preprocessing")
        state = Runtime(self.run).status()
        self.assertEqual(state["stages"]["contract"]["status"], "accepted")
        self.assertEqual(state["stages"]["collection"]["status"], "stale")
        self.assertEqual(state["stages"]["curation"]["status"], "stale")

    def test_changed_source_code_and_review_evidence_detected(self):
        source = self.base / "source.csv"
        source.write_text("x\n1\n")
        dispatch = self.bind(self.prepare("contract", inputs=[source]))
        output = Path(dispatch["owned_output_dir"]) / "x"
        output.write_text("x")
        source.write_text("x\n2\n")
        with self.assertRaisesRegex(GateError, "invalidated"):
            self.rt.submit(dispatch["dispatch_id"], {"summary": "x", "checks": ["x"], "outputs": [str(output)]})
        self.produce("contract")
        review = self.accept("contract")
        Path(review["evidence_bindings"][0]["path"]).write_text("tampered")
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "stale")

    def test_contract_change_requires_explicit_revision(self):
        self.through("contract")
        self.contract.write_text('{"version":"fixture-v2"}')
        with self.assertRaisesRegex(GateError, "Contract changed"):
            self.prepare("collection")
        self.rt.replace_contract(self.contract, "Explicit fixture update")
        self.assertEqual(self.rt.status()["contract_version"], "fixture-v2")
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "stale")

    def test_sealed_input_not_dispatched_to_development(self):
        with self.assertRaisesRegex(GateError, "Sealed"):
            self.prepare("contract", inputs=[self.holdout])

    def test_improvement_continue_invalidates_only_affected_stages(self):
        self.through("improvement", decision="CONTINUE", restart="curation")
        with self.assertRaisesRegex(GateError, "STOP"):
            self.prepare("terminal-evaluation")
        result = self.rt.advance_improvement()
        self.assertEqual(result["next_stage"], "curation")
        state = self.rt.status()
        self.assertEqual(state["stages"]["collection"]["status"], "accepted")
        self.assertEqual(state["stages"]["curation"]["status"], "stale")
        self.assertEqual(state["stages"]["training"]["status"], "stale")
        self.assertEqual(state["cycle"], 1)

    def test_terminal_once_and_exposure_survives_invalidation(self):
        self.through("terminal-evaluation")
        self.rt.invalidate("terminal-evaluation", "Fixture repair")
        with self.assertRaisesRegex(GateError, "already exposed"):
            self.prepare("terminal-evaluation")
        self.rt.invalidate("training", "Cannot erase exposure")
        with self.assertRaisesRegex(GateError, "after terminal"):
            self.prepare("training")
        with self.assertRaisesRegex(GateError, "After terminal"):
            self.rt.replace_contract(self.contract, "No exposure reset")

    def test_terminal_preparation_consumes_attempt_before_binding(self):
        self.through("final-fit")
        self.prepare("terminal-evaluation")
        self.assertTrue(self.rt.status()["terminal_exposed"])
        self.rt.invalidate("terminal-evaluation", "Prepared but not launched")
        with self.assertRaisesRegex(GateError, "already exposed"):
            self.prepare("terminal-evaluation")

    def test_final_fit_requires_reviewed_stop_and_no_sealed_inputs(self):
        self.through("development-evaluation")
        with self.assertRaisesRegex(GateError, "improvement"):
            self.prepare("final-fit")
        self.through("improvement", decision="CONTINUE")
        with self.assertRaisesRegex(GateError, "STOP"):
            self.prepare("final-fit")
        self.rt.invalidate("improvement", "Fixture reviewed STOP revision")
        self.through("improvement")
        self.assertEqual(self.rt.advance_improvement()["next_stage"], "final-fit")
        with self.assertRaisesRegex(GateError, "Sealed"):
            self.prepare("final-fit", inputs=[self.holdout])
        dispatch = self.prepare("final-fit")
        self.assertEqual(dispatch["agent_type"], "executor")
        self.assertTrue(dispatch["recipe_lock"])
        self.assertTrue(all(item in dispatch["bindings"] for item in dispatch["recipe_lock"]))

    def test_terminal_requires_final_fit_independent_pass(self):
        self.through("improvement")
        with self.assertRaisesRegex(GateError, "final-fit"):
            self.prepare("terminal-evaluation")
        self.produce("final-fit")
        with self.assertRaisesRegex(GateError, "final-fit"):
            self.prepare("terminal-evaluation")
        self.accept("final-fit")
        self.assertEqual(self.prepare("terminal-evaluation")["stage"], "terminal-evaluation")

    def test_changed_fitted_model_invalidates_terminal_gate(self):
        self.through("final-fit")
        state = self.rt.status()
        fitted = Path(state["stages"]["final-fit"]["submission"]["outputs"][0]["path"])
        fitted.write_text("changed fitted model")
        with self.assertRaisesRegex(GateError, "final-fit"):
            self.prepare("terminal-evaluation")
        self.assertEqual(self.rt.status()["stages"]["final-fit"]["status"], "stale")

    def test_old_stage_graph_does_not_restore_terminal_access(self):
        state = self.rt.status()
        del state["stages"]["final-fit"]
        state["terminal_exposed"] = True
        events = []
        self.rt._sync(state, events)
        self.assertEqual(state["stages"]["final-fit"]["status"], "pending")
        self.assertEqual(state["stages"]["terminal-evaluation"]["status"], "stale")
        self.assertEqual(state["stages"]["release"]["status"], "stale")
        self.assertTrue(state["terminal_exposed"])
        self.assertTrue(any(kind == "stage_graph_updated" for kind, _ in events))

    def test_preflight_rejects_different_pipeline(self):
        self.through("training-preflight")
        alternate = self.base / "other.py"
        alternate.write_text("# different pipeline")
        for field in ("inputs", "code", "config", "parameters"):
            identity = {"inputs": [str(self.contract)], "code": [str(SCRIPT)],
                        "config": [], "parameters": {"fixture": True}}
            identity[field] = {"selection": "different"} if field == "parameters" else [str(alternate)]
            with self.assertRaisesRegex(GateError, "differs"):
                self.prepare("training", pipeline_identity=identity)

    def test_findings_require_boolean_resolution(self):
        self.produce("contract")
        with self.assertRaisesRegex(GateError, "boolean"):
            self.accept("contract", findings=[{"severity": "high", "resolved": "false"}])

    def test_frozen_command_execution_and_failure(self):
        code = self.base / "adapter.py"
        code.write_text("from pathlib import Path\nPath('result.txt').write_text('fixture result')\n")
        dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code), "{output_dir}"]))
        self.assertEqual(dispatch["command"][-1], dispatch["owned_output_dir"])
        bound_paths = {item["path"] for item in dispatch["bindings"]}
        self.assertIn(str(SCRIPT.resolve()), bound_paths)
        self.assertTrue(any(path.endswith("agent-skills.json") for path in bound_paths))
        result = self.rt.execute(dispatch["dispatch_id"], timeout=10)
        self.assertEqual(result["exit_code"], 0)
        with self.assertRaisesRegex(GateError, "unexecuted"):
            self.rt.execute(dispatch["dispatch_id"])
        out = Path(dispatch["owned_output_dir"]) / "result.txt"
        self.rt.submit(dispatch["dispatch_id"], {"summary": "x", "checks": ["x"], "outputs": [str(out)]})
        self.accept("contract")
        failing = self.base / "failure.py"
        failing.write_text("raise SystemExit(7)\n")
        dispatch = self.bind(self.prepare("collection", code=[failing], command=[sys.executable, str(failing)]))
        result = self.rt.execute(dispatch["dispatch_id"])
        self.assertEqual(result["exit_code"], 7)
        with self.assertRaisesRegex(GateError, "successful producer"):
            self.rt.submit(dispatch["dispatch_id"], {"summary": "x", "checks": ["x"], "outputs": [result["log"]["path"]]})

    def test_changed_command_code_rejected_before_execution(self):
        code = self.base / "adapter.py"
        code.write_text("print('old')\n")
        dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code)]))
        code.write_text("print('changed')\n")
        with self.assertRaisesRegex(GateError, "invalidated"):
            self.rt.execute(dispatch["dispatch_id"])

    def test_owned_runner_preserves_execution_and_failure_receipts(self):
        jobs = self.rt._jobs_module()
        code = self.base / "adapter.py"
        code.write_text("print('fixture')\n")
        outcomes = [(0, False), (7, False), (subprocess.TimeoutExpired(["fixture"], 3), True),
                    (OSError("fixture launch failure"), False), (jobs.JobError("fixture ownership failure"), False)]
        for outcome, timed_out in outcomes:
            with self.subTest(outcome=outcome):
                self.rt.invalidate("contract", "New owned-runner fixture attempt")
                dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code)]))
                def run(command, *, cwd, stdout, stderr, timeout):
                    self.assertEqual(command, dispatch["command"])
                    self.assertEqual(cwd, Path(dispatch["owned_output_dir"]))
                    self.assertEqual(stderr, subprocess.STDOUT)
                    self.assertEqual(timeout, 3)
                    stdout.write(b"owned fixture output\n")
                    if isinstance(outcome, Exception):
                        raise outcome
                    return subprocess.CompletedProcess(command, outcome)
                with mock.patch.object(jobs, "run_owned", side_effect=run) as runner:
                    result = self.rt.execute(dispatch["dispatch_id"], timeout=3)
                    runner.assert_called_once()
                self.assertEqual(result["exit_code"], None if isinstance(outcome, Exception) else outcome)
                self.assertEqual(result["timed_out"], timed_out)
                self.assertEqual(result["status"], "executed" if outcome == 0 else "failed")
                self.assertIn("owned fixture output", (Path(dispatch["owned_output_dir"]) / "execution.log").read_text())
                if isinstance(outcome, jobs.JobError):
                    self.assertIsNone(result["log"])
                    self.assertFalse(result["cleanup_verified"])
                else:
                    self.assertEqual(result["log"], mod.snapshot(result["log"]["path"]))
                current = self.rt.status()["dispatches"][dispatch["dispatch_id"]]
                self.assertEqual(current["command"], dispatch["command"])
                self.assertEqual(current["command_sha256"], dispatch["command_sha256"])
                self.assertEqual(current["bindings"], dispatch["bindings"])
                self.assertEqual(current["execution"]["log"], result["log"])
                with self.assertRaises(GateError):
                    self.rt.execute(dispatch["dispatch_id"])

    def test_successful_execution_cannot_omit_verified_log_binding(self):
        code = self.base / "adapter.py"
        code.write_text("print('fixture')\n")
        dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code)]))
        self.assertEqual(self.rt.execute(dispatch["dispatch_id"])["status"], "executed")
        def corrupt(state, events):
            state["dispatches"][dispatch["dispatch_id"]]["execution"]["log"] = None
        self.rt._op(corrupt)
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "stale")

    def test_synchronous_execution_cleans_descendants_before_receipt(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out), socket.socket() as server:
                self.rt.invalidate("contract", "New process-tree fixture attempt")
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                server.settimeout(10)
                child = ("import signal, socket\n"
                         "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                         f"with socket.create_connection({server.getsockname()!r}, timeout=10) as peer:\n"
                         "    peer.settimeout(None)\n"
                         "    peer.sendall(b'R')\n"
                         "    peer.recv(1)\n")
                code = self.base / f"tree-{timed_out}.py"
                code.write_text("from pathlib import Path\nimport subprocess, sys, time\n"
                                f"subprocess.Popen([sys.executable, '-c', {child!r}], stdin=subprocess.DEVNULL, "
                                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                                "print('parent started', flush=True)\n"
                                "while not Path('finish-parent').exists():\n    time.sleep(0.01)\n"
                                + ("time.sleep(60)\n" if timed_out else ""))
                dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code)]))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    execution = pool.submit(self.rt.execute, dispatch["dispatch_id"], timeout=5)
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(5)
                        self.assertEqual(connection.recv(1), b"R")
                        (Path(dispatch["owned_output_dir"]) / "finish-parent").write_text("exit parent")
                        result = execution.result(timeout=15)
                        connection.settimeout(2)
                        try:
                            closed = connection.recv(1)
                        except ConnectionResetError:
                            closed = b""
                        self.assertEqual(closed, b"", "Descendant survived the terminal execution receipt")
                self.assertEqual(result["timed_out"], timed_out)
                self.assertEqual(result["exit_code"], None if timed_out else 0)
                self.assertEqual(result["status"], "failed" if timed_out else "executed")
                self.assertIn("parent started", Path(result["log"]["path"]).read_text())
                self.assertEqual(result["log"], mod.snapshot(result["log"]["path"]))

    def test_preprocessing_pass_registers_exact_custodian_bindings(self):
        self.external_source = self.base / "original_external.csv"
        self.external_source.write_text("output_row_id,task_id,target\nexternal,oral,9\n")
        self.through("preprocessing")
        state = Runtime(self.run).status()
        manifest = mod.load_json(state["preprocessing_acceptance"]["manifest"]["path"])
        self.assertEqual({(item["path"], item["sha256"]) for item in state["custodian_only"]},
                         {(item["path"], item["sha256"]) for item in manifest["custodian_only"]})
        self.assertIn(manifest["sealed_terminal"]["path"], {item["path"] for item in state["sealed_inputs"]})
        self.assertNotIn(manifest["external_cohort"]["artifact"]["path"], {item["path"] for item in state["sealed_inputs"]})
        self.assertEqual(len(state["external_inputs"]), 4)
        for item in manifest["custodian_only"]:
            for category in ("inputs", "code", "config"):
                with self.subTest(path=item["path"], category=category):
                    with self.assertRaisesRegex(GateError, "Custodian-only|Sealed"):
                        self.prepare("features", **{category: [item["path"]]})
        safe = self.prepare("features", inputs=[manifest["development"]["path"], manifest["development_grouping_mapping"]["path"],
                                                state["preprocessing_acceptance"]["manifest"]["path"]])
        self.assertEqual(safe["stage"], "features")

    def test_protected_copy_reference_and_command_are_rejected(self):
        self.through("preprocessing")
        state = self.rt.status()
        source = state["custodian_only"][0]
        copied = self.base / "innocent.csv"
        copied.write_bytes(Path(source["path"]).read_bytes())
        with self.assertRaisesRegex(GateError, "Custodian-only"):
            self.prepare("features", inputs=[copied])
        indirect = self.base / "indirect.json"
        indirect.write_text(json.dumps({"features": {"artifact": {"path": source["path"], "sha256": source["sha256"]}}}))
        with self.assertRaisesRegex(GateError, "custodian-only"):
            self.prepare("features", inputs=[indirect])
        indirect.write_text(json.dumps({"dataset": source["path"]}))
        with self.assertRaisesRegex(GateError, "custodian-only"):
            self.prepare("features", config=[indirect])
        with self.assertRaisesRegex(GateError, "custodian-only"):
            self.prepare("features", code=[SCRIPT], command=[sys.executable, str(SCRIPT), "--input=" + source["path"]])
        with self.assertRaisesRegex(GateError, "directory exposes custodian-only"):
            self.prepare("features", code=[SCRIPT], command=[sys.executable, str(SCRIPT), "--input-dir=" + str(self.run)])

    def test_pipeline_identity_cannot_smuggle_original_curated_input(self):
        self.through("features")
        source = self.rt.status()["custodian_only"][0]["path"]
        with self.assertRaisesRegex(GateError, "Custodian-only"):
            self.prepare("training-preflight", pipeline_identity={"inputs": [source], "code": [str(SCRIPT)],
                                                                  "config": [], "parameters": {}})
        with self.assertRaisesRegex(GateError, "custodian-only"):
            self.prepare("training-preflight", pipeline_identity={"inputs": [str(self.contract)], "code": [str(SCRIPT)],
                                                                  "config": [], "parameters": {"dataset": source}})

    def test_relative_command_paths_cannot_bypass_custody(self):
        self.run = self.base / "relative-custody-run"
        self.run.mkdir()
        self.holdout = self.run / "sealed.csv"
        self.holdout.write_text("id,y\nheld,5\n")
        self.rt = Runtime(self.run)
        self.rt.initialize(self.contract, "relative-custody-fixture", [self.holdout])
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("preprocessing")
        state = self.rt.status()
        paths = {item["path"] for item in state["custodian_only"] + state["external_inputs"]}
        operands = ["../../../../sealed.csv"] + ["../../../../" + os.path.relpath(path, self.run) for path in sorted(paths)]
        for operand in operands:
            for argument in (operand, "--input=" + operand):
                with self.subTest(argument=argument), self.assertRaisesRegex(GateError, "custodian-only"):
                    self.prepare("features", code=[SCRIPT], command=[sys.executable, str(SCRIPT), argument])
        self.assertEqual(self.rt.status()["stages"]["features"]["status"], "pending")

    def test_relative_command_dependencies_use_execution_directory(self):
        self.through("preprocessing")
        source = self.run / "command-input.json"
        nested = self.run / "nested"
        nested.mkdir()
        dependency = nested / "input.txt"
        dependency.write_text("permitted command dependency")
        source.write_text(json.dumps({"artifact": "nested/input.txt"}))
        dispatch = self.bind(self.prepare("features", code=[SCRIPT],
                                         command=[sys.executable, str(SCRIPT), "--input=../../../../command-input.json"]))
        self.assertIn(mod.snapshot(source), dispatch["bindings"])
        self.assertIn(mod.snapshot(dependency), dispatch["bindings"])
        dependency.write_text("changed after command binding")
        with mock.patch.object(self.rt._jobs_module(), "run_owned") as runner:
            with self.assertRaisesRegex(GateError, "invalidated"):
                self.rt.execute(dispatch["dispatch_id"])
            runner.assert_not_called()
        self.assertEqual(self.rt.status()["stages"]["features"]["status"], "stale")

    def test_preprocessing_cannot_label_arbitrary_files_or_hide_external_artifacts(self):
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("curation")
        _, candidate = self.produce("preprocessing")
        path = Path(candidate["outputs"][0]["path"])
        original = mod.load_json(path)
        variants = []
        arbitrary = json.loads(json.dumps(original))
        arbitrary["custodian_only"].append(self.artifact(self.contract))
        variants.append(arbitrary)
        hidden = json.loads(json.dumps(original))
        hidden["custodian_only"] = hidden["custodian_only"][:-1]
        variants.append(hidden)
        forged = json.loads(json.dumps(original))
        forged["input"] = self.artifact(self.contract)
        forged["custodian_only"][0] = forged["input"]
        variants.append(forged)
        changed_hash = json.loads(json.dumps(original))
        changed_hash["external_cohort"]["input"]["sha256"] = "0" * 64
        variants.append(changed_hash)
        # Each changed manifest is a new producer attempt, not a post-submit edit.
        for manifest in variants:
            self.rt.invalidate("preprocessing", "Malformed fixture adapter")
            source = original["input"]["path"]
            dispatch = self.bind(self.prepare("preprocessing", inputs=[source, self.external_source]))
            current_path = self.preprocessing(dispatch, source, self.external_source)
            current = mod.load_json(current_path)
            if manifest is arbitrary:
                current["custodian_only"].append(self.artifact(self.contract))
            elif manifest is hidden:
                current["custodian_only"] = current["custodian_only"][:-1]
            elif manifest is forged:
                current["input"] = self.artifact(self.contract)
                current["custodian_only"][0] = current["input"]
            else:
                current["external_cohort"]["input"]["sha256"] = "0" * 64
            current_path.write_text(json.dumps(current))
            self.rt.submit(dispatch["dispatch_id"], {"outputs": [str(current_path)], "summary": "Malformed fixture", "checks": ["fixture shape"]})
            with self.assertRaises(GateError):
                self.accept("preprocessing")
            self.assertFalse(self.rt.status()["custodian_only"])

    def test_changed_preprocessing_dependency_invalidates_resume(self):
        self.through("preprocessing")
        path = self.rt.status()["custodian_only"][-1]["path"]
        Path(path).write_text("tampered after independent PASS")
        resumed = Runtime(self.run).status()
        self.assertEqual(resumed["stages"]["preprocessing"]["status"], "stale")
        self.assertEqual(resumed["stages"]["training"]["status"], "stale")
        self.assertTrue(resumed["custodian_only"])

    def test_external_reservation_is_separate_from_terminal(self):
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("final-fit")
        producer, _ = self.produce("terminal-evaluation", evaluation_mode="external")
        self.accept("terminal-evaluation", evaluation_mode="external")
        state = Runtime(self.run).status()
        self.assertTrue(state["external_exposed"])
        self.assertFalse(state["terminal_exposed"])
        self.assertEqual(state["external_evaluation"]["status"], "accepted")
        self.assertEqual(state["stages"]["terminal-evaluation"]["status"], "pending")
        self.assertEqual(state["external_dispatch_id"], producer["dispatch_id"])
        with self.assertRaisesRegex(GateError, "External cohort already exposed"):
            self.prepare("terminal-evaluation", evaluation_mode="external")
        terminal = self.prepare("terminal-evaluation")
        self.assertEqual(terminal["evaluation_mode"], "terminal")
        self.assertTrue(self.rt.status()["terminal_exposed"])

    def test_external_evaluation_requires_accepted_external_cohort(self):
        self.through("final-fit")
        with self.assertRaisesRegex(GateError, "accepted preprocessing external cohort"):
            self.prepare("terminal-evaluation", evaluation_mode="external")

    def test_external_reservation_freezes_development_before_terminal(self):
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("final-fit")
        external = self.prepare("terminal-evaluation", evaluation_mode="external")
        for stage in STAGES[:STAGES.index("terminal-evaluation")]:
            for kind in ("producer", "review"):
                with self.subTest(stage=stage, kind=kind), self.assertRaisesRegex(GateError, "external exposure"):
                    self.prepare(stage, kind)
        replacement = self.base / "replacement-contract.json"
        replacement.write_text('{"version":"fixture-v2"}')
        with self.assertRaisesRegex(GateError, "new research version.*retain exposure history"):
            self.rt.replace_contract(replacement, "External-informed adaptation")
        self.rt.invalidate("training", "Cannot erase external exposure")
        with self.assertRaisesRegex(GateError, "external exposure"):
            self.prepare("training")
        state = Runtime(self.run).status()
        self.assertTrue(state["external_exposed"])
        self.assertFalse(state["terminal_exposed"])
        self.assertEqual(state["external_dispatch_id"], external["dispatch_id"])
        self.assertEqual(state["contract"]["path"], str(self.contract.resolve()))
        self.assertTrue(any(event["kind"] == "external_access_reserved" and
                            event["payload"]["dispatch_id"] == external["dispatch_id"] for event in self.rt.events()))

    def test_bound_development_dispatch_cannot_run_after_external_reservation(self):
        code = self.base / "adapter.py"
        code.write_text("from pathlib import Path\nPath('executed.txt').write_text('ran')\n")
        dispatch = self.bind(self.prepare("contract", code=[code], command=[sys.executable, str(code)]))
        # A restored reservation flag is authoritative even for an older bound dispatch.
        def expose(state, events):
            state.update(external_exposed=True, external_dispatch_id="fixture-external-reservation")
            events.append(("external_access_reserved", {"dispatch_id": "fixture-external-reservation"}))
        self.rt._op(expose)
        with mock.patch.object(self.rt._jobs_module(), "run_owned") as runner:
            with self.assertRaisesRegex(GateError, "external exposure"):
                self.rt.execute(dispatch["dispatch_id"])
            runner.assert_not_called()
        with self.assertRaisesRegex(GateError, "external exposure"):
            self.rt.prepare_job(dispatch["dispatch_id"])
        output = Path(dispatch["owned_output_dir"]) / "executed.txt"
        self.assertFalse(output.exists())
        output.write_text("unexecuted fixture evidence")
        with self.assertRaisesRegex(GateError, "external exposure"):
            self.rt.submit(dispatch["dispatch_id"], {"outputs": [str(output)], "summary": "Fixture", "checks": ["fixture"]})
        state = self.rt.status()
        self.assertEqual(state["dispatches"][dispatch["dispatch_id"]]["status"], "bound")
        self.assertFalse(state["terminal_exposed"])

    def test_external_exposure_blocks_previously_accepted_continue(self):
        self.through("improvement", decision="CONTINUE", restart="features")
        def expose(state, events):
            state.update(external_exposed=True, external_dispatch_id="fixture-external-reservation")
            events.append(("external_access_reserved", {"dispatch_id": "fixture-external-reservation"}))
        self.rt._op(expose)
        with self.assertRaisesRegex(GateError, "external exposure.*retain exposure history"):
            self.rt.advance_improvement()
        state = self.rt.status()
        self.assertEqual(state["cycle"], 0)
        self.assertEqual(state["stages"]["improvement"]["status"], "accepted")
        self.assertTrue(state["external_exposed"])
        self.assertFalse(state["terminal_exposed"])

    def test_preprocessing_automatically_seals_without_manual_registration(self):
        self.run = self.base / "automatically-sealed-run"
        self.rt = Runtime(self.run)
        self.rt.initialize(self.contract, "automatic-custody-fixture")
        self.through("preprocessing")
        state = Runtime(self.run).status()
        self.assertEqual(len(state["sealed_inputs"]), 1)
        self.assertEqual(state["sealed_inputs"][0], state["preprocessing_acceptance"]["terminal"])
        with self.assertRaisesRegex(GateError, "Sealed"):
            self.prepare("features", inputs=[state["sealed_inputs"][0]["path"]])

    def test_evaluation_modes_cannot_expose_the_other_cohort(self):
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("final-fit")
        state = self.rt.status()
        terminal = state["preprocessing_acceptance"]["terminal"]["path"]
        external = state["external_inputs"][1]["path"]
        with self.assertRaisesRegex(GateError, "Custodian-only"):
            self.prepare("terminal-evaluation", evaluation_mode="external", inputs=[terminal])
        with self.assertRaisesRegex(GateError, "Custodian-only"):
            self.prepare("terminal-evaluation", inputs=[external])
        self.assertFalse(self.rt.status()["external_exposed"])
        self.assertFalse(self.rt.status()["terminal_exposed"])

    def test_accepted_external_review_is_bound_to_release_and_revalidated(self):
        self.external_source = self.base / "external.csv"
        self.external_source.write_text("output_row_id,task_id,target\next,oral,7\n")
        self.through("final-fit")
        self.produce("terminal-evaluation", evaluation_mode="external")
        review = self.accept("terminal-evaluation", evaluation_mode="external")
        self.produce("terminal-evaluation")
        self.accept("terminal-evaluation")
        release = self.prepare("release")
        self.assertIn("external-evaluation", release["upstream_tokens"])
        Path(review["evidence_bindings"][0]["path"]).write_text("external review changed after acceptance")
        state = Runtime(self.run).status()
        self.assertEqual(state["external_evaluation"]["status"], "stale")
        self.assertEqual(state["stages"]["release"]["status"], "stale")
        self.assertTrue(state["terminal_exposed"])
        self.assertTrue(state["external_exposed"])

    def test_cli_status_and_bad_prepare(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--run-dir", str(self.run), "status"],
                                capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["run_id"], "fixture-run")


if __name__ == "__main__":
    unittest.main()
