"""Behavioral gate tests using explicit synthetic artifact fixtures, never research data."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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

    def produce(self, stage, **extra):
        dispatch = self.bind(self.prepare(stage))
        output = Path(dispatch["owned_output_dir"]) / "result.json"
        output.write_text(json.dumps({"stage": stage, "fixture_only": True}))
        candidate = self.rt.submit(dispatch["dispatch_id"], {
            "outputs": [str(output)], "summary": "Fixture produced", "checks": ["fixture shape"], **extra})
        return dispatch, candidate

    def accept(self, stage, verdict="PASS", **extra):
        dispatch = self.bind(self.prepare(stage, "review"))
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
        self.assertEqual(len(dispatch["required_reads"]), 4)  # runtime, registry, shared and contracts

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

    def test_cli_status_and_bad_prepare(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--run-dir", str(self.run), "status"],
                                capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["run_id"], "fixture-run")


if __name__ == "__main__":
    unittest.main()
