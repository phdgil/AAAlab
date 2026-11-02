"""Durable-job regression fixtures: real tiny local children, mocked Slurm only."""
import importlib.util
import importlib
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
spec = importlib.util.spec_from_file_location("qsar_jobs_runtime", SCRIPTS / "qsar_jobs.py")
jobs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = jobs
spec.loader.exec_module(jobs)
spec = importlib.util.spec_from_file_location("qsar_jobs_test_runtime", SCRIPTS / "qsar_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
Runtime, GateError = runtime.Runtime, runtime.GateError
CONTEXT = {"objective": "Synthetic job fixture", "acceptance_criteria": "Actual child output and exact request verified",
           "stop_condition": "Return one fixture only"}
ROLES = ["analyst", "executor", "verifier", "critic"]


def descendant_program(parent_exits):
    child = ("from pathlib import Path\nimport os, signal, time\n"
             "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
             "Path('descendant.pid').write_text(str(os.getpid()))\n"
             "deadline = time.monotonic() + 15\n"
             "while time.monotonic() < deadline:\n"
             "    Path('heartbeat.txt').write_text(str(time.monotonic()))\n"
             "    time.sleep(0.02)\n")
    return ("from pathlib import Path\nimport subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "deadline = time.monotonic() + 5\n"
            "while not Path('heartbeat.txt').exists():\n"
            "    if time.monotonic() >= deadline:\n"
            "        raise RuntimeError('Descendant did not start')\n"
            "    time.sleep(0.01)\n"
            "Path('result.txt').write_text('descendant started')\n"
            + ("" if parent_exits else "time.sleep(60)\n"))


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-owned-process-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def posix_mocks(self):
        patch = mock.patch.multiple(jobs.os, name="posix", P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
                                    waitid=mock.DEFAULT, killpg=mock.DEFAULT, getpgid=mock.DEFAULT, create=True)
        posix = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(jobs.signal, "SIGKILL", 9, create=True)
        patch.start()
        self.addCleanup(patch.stop)
        error = type("ProcessError", (Exception,), {})
        missing = type("NoSuchProcess", (error,), {})
        psutil = SimpleNamespace(process_iter=mock.Mock(return_value=[]), STATUS_ZOMBIE="zombie", STATUS_DEAD="dead",
                                 Error=error, NoSuchProcess=missing)
        patch = mock.patch.dict(sys.modules, {"psutil": psutil})
        patch.start()
        self.addCleanup(patch.stop)
        process = mock.Mock(pid=45123, returncode=None)
        def reaped():
            process.returncode = 0
            return 0
        process.wait.side_effect = reaped
        posix["waitid"].return_value = SimpleNamespace(si_pid=process.pid)
        posix["getpgid"].return_value = process.pid
        patch = mock.patch.object(jobs.subprocess, "Popen", return_value=process)
        launch = patch.start()
        self.addCleanup(patch.stop)
        return posix, psutil, process, launch

    def test_posix_exited_leader_stays_unreaped_through_orphan_escalation(self):
        posix, psutil, process, launch = self.posix_mocks()
        events, child_live = [], [True]
        child = mock.Mock(pid=45124)
        child.status.side_effect = lambda: "running" if child_live[0] else "zombie"
        psutil.process_iter.return_value = [child]
        def signal_group(pid, signum):
            self.assertEqual(pid, process.pid)
            process.wait.assert_not_called()
            events.append(signum)
            if signum == jobs.signal.SIGKILL:
                child_live[0] = False
        posix["killpg"].side_effect = signal_group
        with mock.patch.object(jobs.time, "monotonic", side_effect=[0, 6, 7]):
            result = jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [jobs.signal.SIGTERM, jobs.signal.SIGKILL])
        self.assertGreaterEqual(child.status.call_count, 2)
        process.poll.assert_not_called()
        process.wait.assert_called_once_with()
        self.assertTrue(all(call.args[2] & jobs.os.WNOWAIT for call in posix["waitid"].call_args_list))
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(launch.call_args.kwargs["shell"])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_posix_timeout_waits_for_group_cleanup_even_when_leader_exits_on_term(self):
        posix, psutil, process, launch = self.posix_mocks()
        terminated = []
        posix["waitid"].side_effect = lambda *args: SimpleNamespace(si_pid=process.pid) if terminated else None
        def signal_group(pid, signum):
            process.wait.assert_not_called()
            terminated.append(signum)
        posix["killpg"].side_effect = signal_group
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None, timeout=0)
        self.assertEqual(raised.exception.cmd, ["producer"])
        self.assertEqual(terminated, [jobs.signal.SIGTERM, jobs.signal.SIGKILL])
        process.wait.assert_called_once_with()
        process.poll.assert_not_called()

    def test_posix_lost_child_identity_never_signals_a_reusable_group(self):
        posix, psutil, process, launch = self.posix_mocks()
        posix["waitid"].side_effect = ChildProcessError("already reaped")
        with self.assertRaisesRegex(jobs.JobError, "refusing PGID signals"):
            jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        posix["killpg"].assert_not_called()

    def test_posix_missing_quiescence_dependency_rejects_before_launch(self):
        posix, psutil, process, launch = self.posix_mocks()
        with mock.patch.dict(sys.modules, {"psutil": None}):
            with self.assertRaisesRegex(jobs.JobError, "requires psutil"):
                jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        launch.assert_not_called()

    def test_posix_quiescence_failure_escalates_but_does_not_return_success(self):
        posix, psutil, process, launch = self.posix_mocks()
        child = mock.Mock(pid=45124)
        child.status.side_effect = psutil.Error("access denied")
        psutil.process_iter.return_value = [child]
        with self.assertRaisesRegex(jobs.JobError, "Cannot verify owned POSIX"):
            jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        self.assertEqual([call.args[1] for call in posix["killpg"].call_args_list],
                         [jobs.signal.SIGTERM, jobs.signal.SIGKILL])
        process.wait.assert_called_once_with()

    def test_windows_job_remains_owned_after_direct_child_exit(self):
        process = mock.Mock(pid=45123, returncode=0)
        process.poll.return_value = 0
        events = []
        process.wait.side_effect = lambda: events.append("reaped")
        with mock.patch.object(jobs.os, "name", "nt"), mock.patch.object(jobs.subprocess, "Popen", return_value=process) as launch, \
                mock.patch.object(jobs, "_windows_job", return_value=lambda: events.append("job-empty")) as own:
            result = jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        self.assertEqual(events, ["job-empty", "reaped"])
        self.assertEqual(result.returncode, 0)
        own.assert_called_once_with(process)
        self.assertTrue(launch.call_args.kwargs["creationflags"] & 0x4)
        self.assertFalse(launch.call_args.kwargs["start_new_session"])

    def test_windows_ownership_setup_failure_kills_only_owned_handle(self):
        process = mock.Mock(pid=45123)
        with mock.patch.object(jobs.os, "name", "nt"), mock.patch.object(jobs.subprocess, "Popen", return_value=process), \
                mock.patch.object(jobs, "_windows_job", side_effect=jobs.JobError("ownership failed")):
            with self.assertRaisesRegex(jobs.JobError, "ownership failed"):
                jobs.run_owned(["producer"], cwd=str(self.base), stdout=None, stderr=None)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def worker_request(self):
        output = self.base / "outputs"
        output.mkdir()
        return {"job_dir": str(self.base), "backend": "slurm", "job_id": "fixture", "request_sha256": "fixture-hash",
                "bindings": [], "dispatch": {"command": ["producer"], "owned_output_dir": str(output)}}

    def test_worker_uses_shared_owner_and_cleans_before_output_hashing(self):
        request = self.worker_request()
        owned = mock.Mock(process=mock.Mock(pid=45123, returncode=0))
        owned.exited.return_value = True
        def inventory(directory):
            owned.close.assert_called_once_with()
            return []
        with mock.patch.object(jobs, "read_request", return_value=request), mock.patch.object(jobs.signal, "signal"), \
                mock.patch.object(jobs, "_OwnedProcess", return_value=owned) as owner, \
                mock.patch.object(jobs, "output_inventory", side_effect=inventory):
            self.assertEqual(jobs.worker("unused-request"), 0)
        owner.assert_called_once()
        owned.process.poll.assert_not_called()
        self.assertEqual(jobs.load(self.base / "process_receipt.json")["status"], "completed")

    def test_worker_cleanup_failure_receipt_omits_unstable_output_and_log_hashes(self):
        request = self.worker_request()
        owned = mock.Mock(process=mock.Mock(pid=45123, returncode=0))
        owned.exited.return_value = True
        owned.close.side_effect = jobs.JobError("group is not quiescent")
        with mock.patch.object(jobs, "read_request", return_value=request), mock.patch.object(jobs.signal, "signal"), \
                mock.patch.object(jobs, "_OwnedProcess", return_value=owned), mock.patch.object(jobs, "binding") as hash_file, \
                mock.patch.object(jobs, "output_inventory") as inventory:
            self.assertEqual(jobs.worker("unused-request"), 1)
        hash_file.assert_not_called()
        inventory.assert_not_called()
        receipt = jobs.load(self.base / "process_receipt.json")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["logs"], [])
        self.assertEqual(receipt["outputs"], [])
        self.assertIn("cleanup failed", receipt["error"])

    def test_worker_ownership_setup_failure_never_hashes_outputs(self):
        request = self.worker_request()
        with mock.patch.object(jobs, "read_request", return_value=request), mock.patch.object(jobs.signal, "signal"), \
                mock.patch.object(jobs, "_OwnedProcess", side_effect=jobs.JobError("ownership setup failed")), \
                mock.patch.object(jobs, "binding") as hash_file, mock.patch.object(jobs, "output_inventory") as inventory:
            self.assertEqual(jobs.worker("unused-request"), 1)
        hash_file.assert_not_called()
        inventory.assert_not_called()
        receipt = jobs.load(self.base / "process_receipt.json")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["logs"], [])
        self.assertEqual(receipt["outputs"], [])

    @unittest.skipIf(os.name == "nt", "Real process-group integration requires POSIX")
    def test_real_posix_sync_timeout_cleans_ignoring_term_descendant(self):
        command = [sys.executable, "-c", descendant_program(parent_exits=False)]
        with (self.base / "stdout.log").open("wb") as stdout, (self.base / "stderr.log").open("wb") as stderr:
            with self.assertRaises(subprocess.TimeoutExpired):
                jobs.run_owned(command, cwd=self.base, stdout=stdout, stderr=stderr, timeout=1)
        heartbeat = jobs.binding(self.base / "heartbeat.txt")
        time.sleep(0.2)
        self.assertEqual(jobs.binding(self.base / "heartbeat.txt"), heartbeat)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-jobs-test-")
        self.base = Path(self.temp.name)
        self.run = self.base / "run"
        self.contract = self.base / "contract.json"
        self.contract.write_text('{"version":"synthetic-job-fixture"}')
        self.rt = Runtime(self.run)
        self.rt.initialize(self.contract, "jobs-fixture")
        self.sequence = 0
        self.local_jobs = []

    def tearDown(self):
        # Do not leave a real child running when an assertion fails.
        for job_id in self.local_jobs:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = self.rt.status_job(job_id)
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                try:
                    self.rt.cancel_job(job_id)
                except GateError:
                    pass  # A just-started supervisor may not have published its endpoint yet.
                time.sleep(0.05)
            else:
                self.fail("Fixture supervisor did not terminate; refusing temporary-directory removal")
        self.temp.cleanup()

    def dispatch(self, body="from pathlib import Path\nprint('progress', flush=True)\nPath('result.txt').write_text('actual child result')\n"):
        self.sequence += 1
        code = self.base / f"adapter-{self.sequence}.py"
        code.write_text(body)
        dispatch = self.rt.prepare_dispatch("contract", inputs=[self.contract], code=[code],
                                             command=[sys.executable, str(code)], context=CONTEXT, available_roles=ROLES)
        evidence = self.run / f"native-{self.sequence}.json"
        evidence.write_text(json.dumps({"fixture_native_tool_result": f"native-job-{self.sequence}"}))
        return self.rt.bind_dispatch(dispatch["dispatch_id"], f"native-job-{self.sequence}", evidence)

    def local(self, dispatch):
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        self.local_jobs.append(job["job_id"])
        return self.rt.submit_job(job["job_id"])

    def wait(self, job_id, terminal=True):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            # Recreate the Runtime on every poll: no in-memory process ownership is needed.
            job = Runtime(self.run).status_job(job_id)
            if job["status"] in ({"completed", "failed", "cancelled"} if terminal else {"running"}):
                return job
            time.sleep(0.05)
        self.fail("Timed out waiting for fixture job: " + repr(job))

    def manifest(self, dispatch):
        return {"outputs": [str(Path(dispatch["owned_output_dir"]) / "result.txt")],
                "summary": "Tiny process produced a real artifact", "checks": ["Child output read and compared"],
                "limitations": ["Synthetic fixture only"], "coverage": {"fixture_rows": 1}}

    def shared(self, dispatch):
        files = {item["path"]: item for item in dispatch["bindings"] + [dispatch["native_evidence"], jobs.binding(jobs.__file__), jobs.binding(sys.executable)]}
        config = Path(sys.prefix) / "pyvenv.cfg"
        if config.is_file():
            item = jobs.binding(config)
            files[item["path"]] = item
        return {"run_dir": str(self.run.resolve()), "output_dir": dispatch["owned_output_dir"],
                "bindings": [{"path": item["path"], "sha256": item["sha256"]} for item in files.values()]}

    def slurm(self, dispatch):
        return self.rt.prepare_job(dispatch["dispatch_id"], backend="slurm", shared_filesystem=self.shared(dispatch),
                                   resources={"cpus-per-task": 2, "mem": "1G", "time": "00:01:00"})

    @staticmethod
    def response(stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_job_preparation_requires_bound_native_producer(self):
        code = self.base / "unbound.py"
        code.write_text("print('fixture')")
        dispatch = self.rt.prepare_dispatch("contract", code=[code], command=[sys.executable, str(code)],
                                             context=CONTEXT, available_roles=ROLES)
        with self.assertRaisesRegex(GateError, "bound, unexecuted"):
            self.rt.prepare_job(dispatch["dispatch_id"])

    def test_supervisor_preserves_venv_launch_path_and_binds_configuration(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        request = jobs.read_request(job["request"]["path"])
        self.assertEqual(request["supervisor_python"], str(Path(sys.executable).absolute()))
        self.assertEqual(request["supervisor_python_binding"], jobs.binding(sys.executable))
        config = Path(sys.prefix) / "pyvenv.cfg"
        if config.is_file():
            self.assertIn(jobs.binding(config), request["bindings"])
        request["supervisor_python_binding"]["sha256"] = "0" * 64
        request["request_sha256"] = jobs.digest({key: value for key, value in request.items() if key != "request_sha256"})
        tampered = self.base / "tampered-request.json"
        jobs.atomic_json(tampered, request)
        with self.assertRaisesRegex(jobs.JobError, "interpreter path or executable binding"):
            jobs.read_request(tampered)

    def test_real_tiny_local_process_reconnect_collect_and_independent_gate(self):
        dispatch = self.dispatch("from pathlib import Path\nimport time\nprint('progress', flush=True)\nPath('result.txt').write_text('actual child result')\ntime.sleep(0.4)\n")
        job = self.local(dispatch)
        self.assertEqual(job["status"], "queued")
        completed = self.wait(job["job_id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(Path(job["stdout"]).read_text().strip(), "progress")
        self.assertEqual(Path(self.manifest(dispatch)["outputs"][0]).read_text(), "actual child result")
        receipt = jobs.load(completed["process_receipt"]["path"])
        self.assertEqual(receipt["request_sha256"], job["request_sha256"])
        self.assertEqual(receipt["outputs"][0]["sha256"], jobs.binding(self.manifest(dispatch)["outputs"][0])["sha256"])
        with self.assertRaises(GateError):
            self.rt.submit(dispatch["dispatch_id"], self.manifest(dispatch))
        candidate = Runtime(self.run).collect_job(job["job_id"], self.manifest(dispatch))
        self.assertEqual(candidate["execution"]["job_id"], job["job_id"])
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "produced")
        with self.assertRaisesRegex(GateError, "Independent PASS"):
            self.rt.prepare_dispatch("collection", context=CONTEXT, available_roles=ROLES)
        with self.assertRaises(GateError):
            self.rt.submit_job(job["job_id"])

    def test_real_failed_local_process_cannot_collect(self):
        dispatch = self.dispatch("print('failure detail', flush=True)\nraise SystemExit(7)\n")
        job = self.local(dispatch)
        failed = self.wait(job["job_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["exit_code"], 7)
        with self.assertRaisesRegex(GateError, "completed process receipt"):
            self.rt.collect_job(job["job_id"], self.manifest(dispatch))

    def test_real_local_cancel_uses_supervisor_not_pid(self):
        dispatch = self.dispatch("from pathlib import Path\nimport time\nPath('result.txt').write_text('started')\nprint('started', flush=True)\ntime.sleep(60)\n")
        job = self.local(dispatch)
        self.wait(job["job_id"], terminal=False)
        Runtime(self.run).cancel_job(job["job_id"])
        cancelled = self.wait(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled", cancelled)
        with self.assertRaisesRegex(GateError, "completed process receipt"):
            self.rt.collect_job(job["job_id"], self.manifest(dispatch))

    @unittest.skipIf(os.name == "nt", "Real process-group integration requires POSIX")
    def test_real_posix_parent_exit_cleans_ignoring_term_descendant_before_receipt(self):
        dispatch = self.dispatch(descendant_program(parent_exits=True))
        job = self.local(dispatch)
        completed = self.wait(job["job_id"])
        self.assertEqual(completed["status"], "completed", completed)
        receipt = jobs.load(completed["process_receipt"]["path"])
        heartbeat = Path(dispatch["owned_output_dir"]) / "heartbeat.txt"
        recorded = next(item for item in receipt["outputs"] if item["path"] == str(heartbeat.resolve()))
        self.assertEqual(jobs.binding(heartbeat), recorded)
        time.sleep(0.2)
        self.assertEqual(jobs.binding(heartbeat), recorded)

    @unittest.skipIf(os.name == "nt", "Real process-group integration requires POSIX")
    def test_real_posix_cancel_cleans_ignoring_term_descendant_before_receipt(self):
        dispatch = self.dispatch(descendant_program(parent_exits=False))
        job = self.local(dispatch)
        self.wait(job["job_id"], terminal=False)
        started = Path(dispatch["owned_output_dir"]) / "result.txt"
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(started.exists(), "Descendant fixture did not become ready")
        self.rt.cancel_job(job["job_id"])
        cancelled = self.wait(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled", cancelled)
        receipt = jobs.load(cancelled["process_receipt"]["path"])
        heartbeat = Path(dispatch["owned_output_dir"]) / "heartbeat.txt"
        recorded = next(item for item in receipt["outputs"] if item["path"] == str(heartbeat.resolve()))
        time.sleep(0.2)
        self.assertEqual(jobs.binding(heartbeat), recorded)

    def test_foreign_supervisor_identity_cannot_trigger_any_pid_kill(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        def queued(state, events):
            state["jobs"][job["job_id"]].update(status="queued", launcher={"pid": os.getpid()})
        self.rt._op(queued)
        jobs.atomic_json(Path(job["job_dir"]) / "supervisor.json", {"host": "127.0.0.1", "port": 1,
                        "request_sha256": "foreign-request", "supervisor_pid": os.getpid()})
        with mock.patch.object(jobs.os, "kill") as kill:
            with self.assertRaisesRegex(GateError, "identity mismatch"):
                self.rt.cancel_job(job["job_id"])
            kill.assert_not_called()
        self.assertEqual(self.rt.status_job(job["job_id"])["status"], "queued")

    def test_changed_code_invalidates_job_before_launch(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        code = next(item for item in dispatch["bindings"] if Path(item["path"]).name.startswith("adapter-"))
        Path(code["path"]).write_text("print('different implementation')\n")
        with self.assertRaisesRegex(GateError, "invalidated"):
            self.rt.submit_job(job["job_id"])

    def test_changed_request_is_rejected_without_launching_any_child(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        request_path = Path(job["request"]["path"])
        request = jobs.load(request_path)
        request["dispatch"]["command"][-1] = "different-unbound-command.py"
        jobs.atomic_json(request_path, request)
        with mock.patch.object(jobs.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(GateError, "invalidated"):
                self.rt.submit_job(job["job_id"])
            launch.assert_not_called()

    def test_invalidated_live_job_can_still_be_cancelled_but_not_collected(self):
        dispatch = self.dispatch("import time\nprint('running', flush=True)\ntime.sleep(60)\n")
        job = self.local(dispatch)
        self.wait(job["job_id"], terminal=False)
        self.rt.invalidate("contract", "Fixture native attempt revoked")
        Runtime(self.run).cancel_job(job["job_id"])
        self.assertEqual(self.wait(job["job_id"])["status"], "cancelled")
        self.assertEqual(self.rt.status()["stages"]["contract"]["status"], "stale")
        with self.assertRaisesRegex(GateError, "invalidated"):
            self.rt.collect_job(job["job_id"], self.manifest(dispatch))

    def test_modified_completed_output_cannot_be_collected(self):
        dispatch = self.dispatch()
        job = self.local(dispatch)
        self.wait(job["job_id"])
        Path(self.manifest(dispatch)["outputs"][0]).write_text("different output after execution")
        with self.assertRaisesRegex(GateError, "Changed job-bound artifact"):
            self.rt.collect_job(job["job_id"], self.manifest(dispatch))

    def test_prepared_cancellation_prevents_execution(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        self.assertEqual(self.rt.cancel_job(job["job_id"])["status"], "cancelled")
        with self.assertRaisesRegex(GateError, "already submitted"):
            self.rt.submit_job(job["job_id"])
        with self.assertRaisesRegex(GateError, "durable job"):
            self.rt.execute(dispatch["dispatch_id"])

    def test_cancel_prepared_snapshot_loses_to_submit_and_contacts_active_owner(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        service = jobs.Jobs(self.rt)
        get_job = service._get
        def stale_prepared(*args, **kwargs):
            prepared = get_job(*args, **kwargs)
            self.assertEqual(prepared["status"], "prepared")
            jobs.Jobs(self.rt).submit(job["job_id"])
            return prepared
        with mock.patch.object(service, "_get", side_effect=stale_prepared), \
                mock.patch.object(jobs.subprocess, "Popen", return_value=mock.Mock(pid=45123)), \
                mock.patch.object(service, "_local_contact", return_value={}) as contact:
            cancelled = service.cancel(job["job_id"])
        contact.assert_called_once()
        self.assertEqual(contact.call_args.args[1], "cancel")
        self.assertEqual(cancelled["status"], "queued")
        self.assertIn("cancel_requested_at", cancelled)
        self.assertNotIn("cancelled_at", cancelled)
        self.assertEqual(self.rt.status()["dispatches"][dispatch["dispatch_id"]]["status"], "queued")

    def test_cancel_submit_race_without_endpoint_never_claims_live_job_cancelled(self):
        dispatch = self.dispatch()
        job = self.rt.prepare_job(dispatch["dispatch_id"])
        service = jobs.Jobs(self.rt)
        get_job = service._get
        def stale_prepared(*args, **kwargs):
            prepared = get_job(*args, **kwargs)
            jobs.Jobs(self.rt).submit(job["job_id"])
            return prepared
        with mock.patch.object(service, "_get", side_effect=stale_prepared), \
                mock.patch.object(jobs.subprocess, "Popen", return_value=mock.Mock(pid=45123)):
            with self.assertRaisesRegex(jobs.JobError, "endpoint not yet available"):
                service.cancel(job["job_id"])
        current = self.rt.status()["jobs"][job["job_id"]]
        self.assertEqual(current["status"], "queued")
        self.assertNotIn("cancelled_at", current)

    def test_slurm_requires_explicit_identical_content_bound_shared_paths(self):
        dispatch = self.dispatch()
        with self.assertRaisesRegex(GateError, "explicit shared_filesystem"):
            self.rt.prepare_job(dispatch["dispatch_id"], backend="slurm")
        shared = self.shared(dispatch)
        shared["output_dir"] = "/rewritten/output"
        with self.assertRaisesRegex(GateError, "path rewriting/relocation"):
            self.rt.prepare_job(dispatch["dispatch_id"], backend="slurm", shared_filesystem=shared)
        shared = self.shared(dispatch)
        shared["bindings"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(GateError, "exactly match"):
            self.rt.prepare_job(dispatch["dispatch_id"], backend="slurm", shared_filesystem=shared)

    def test_mocked_slurm_lifecycle_does_not_turn_scheduler_success_into_science(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run") as run:
            run.return_value = self.response("402;fixture-cluster\n")
            queued = self.rt.submit_job(job["job_id"])
            self.assertEqual(queued["scheduler_id"], "402")
            self.assertIn("--cpus-per-task=2", run.call_args.args[0])
            for scheduler_state, expected in (("PENDING", "queued"), ("RUNNING", "running"), ("COMPLETED", "completed")):
                run.return_value = self.response(f"402|{job['job_name']}|{scheduler_state}|0:0\n")
                self.assertEqual(Runtime(self.run).status_job(job["job_id"])["status"], expected)
            with self.assertRaisesRegex(GateError, "scheduler success alone"):
                self.rt.collect_job(job["job_id"], self.manifest(dispatch))
            with self.assertRaises(GateError):
                self.rt.submit(dispatch["dispatch_id"], self.manifest(dispatch))
            self.assertNotEqual(self.rt.status()["stages"]["contract"]["status"], "accepted")
            run.return_value = self.response(f"402|{job['job_name']}|COMPLETED|1:0\n")
            self.assertEqual(self.rt.status_job(job["job_id"])["status"], "failed")

    def test_mocked_slurm_foreign_worker_receipt_cannot_collect(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run", return_value=self.response("403\n")):
            self.rt.submit_job(job["job_id"])
        jobs.atomic_json(Path(job["job_dir"]) / "process_receipt.json", {"job_id": job["job_id"],
                         "request_sha256": "not-this-request", "status": "completed", "exit_code": 0})
        with self.assertRaisesRegex(GateError, "exact job request"):
            self.rt.collect_job(job["job_id"], self.manifest(dispatch))

    def test_mocked_slurm_missing_clients_and_command_errors_are_actionable(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", return_value=None):
            with self.assertRaisesRegex(GateError, "sbatch is unavailable"):
                self.rt.submit_job(job["job_id"])
        self.assertEqual(self.rt.status()["jobs"][job["job_id"]]["status"], "prepared")
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run", return_value=self.response("", 1, "Invalid account")):
            with self.assertRaisesRegex(GateError, "Invalid account"):
                self.rt.submit_job(job["job_id"])
        with mock.patch.object(jobs.shutil, "which", return_value=None):
            with self.assertRaisesRegex(GateError, "sacct is unavailable"):
                self.rt.status_job(job["job_id"])

    def test_mocked_slurm_reconciles_uncertain_submit_without_resubmission(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run") as run:
            run.return_value = self.response("not-a-parsable-id\n")
            with self.assertRaisesRegex(GateError, "do not resubmit"):
                self.rt.submit_job(job["job_id"])
            run.return_value = self.response(f"509|{job['job_name']}|RUNNING|0:0\n")
            resumed = Runtime(self.run).status_job(job["job_id"])
            self.assertEqual(resumed["scheduler_id"], "509")
            self.assertEqual(resumed["status"], "running")
            with self.assertRaisesRegex(GateError, "never submit twice"):
                self.rt.submit_job(job["job_id"])
            self.assertEqual(sum(Path(call.args[0][0]).name == "sbatch" for call in run.call_args_list), 1)

    def test_mocked_slurm_cancel_filters_allocation_by_request_name(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run") as run:
            run.return_value = self.response("613\n")
            self.rt.submit_job(job["job_id"])
            run.side_effect = [self.response(f"613|{job['job_name']}|RUNNING|0:0\n"), self.response("")]
            self.rt.cancel_job(job["job_id"])
            self.assertEqual(run.call_args.args[0], ["/mock/scancel", "--name=" + job["job_name"], "613"])
            run.side_effect = None
            run.return_value = self.response(f"613|{job['job_name']}|CANCELLED by 1|0:15\n")
            self.assertEqual(self.rt.status_job(job["job_id"])["status"], "cancelled")

    def test_mocked_slurm_does_not_cancel_recycled_or_unobserved_id(self):
        dispatch = self.dispatch()
        job = self.slurm(dispatch)
        with mock.patch.object(jobs.shutil, "which", side_effect=lambda name: "/mock/" + name), mock.patch.object(jobs.subprocess, "run") as run:
            run.return_value = self.response("721\n")
            self.rt.submit_job(job["job_id"])
            run.return_value = self.response("721|unrelated-allocation|RUNNING|0:0\n")
            with self.assertRaisesRegex(GateError, "Cannot safely cancel"):
                self.rt.cancel_job(job["job_id"])
            self.assertFalse(any(Path(call.args[0][0]).name == "scancel" for call in run.call_args_list))

    def test_incomplete_trainer_and_forged_identity_are_not_completed_evidence(self):
        output = self.base / "trainer-outputs"
        output.mkdir()
        names = ("checkpoint.json", "search_coverage.json", "final_selection.json", "trial_ledger.json", "fit_evidence.json",
                 "attempt_history.json", "selection_procedure_oof.csv", "selection_procedure_oof_manifest.json")
        for name in names:
            (output / name).write_text(json.dumps({"status": "incomplete"}))
        outputs = [jobs.binding(output / name) for name in names]
        dispatch = {"stage": "training", "bindings": []}
        with self.assertRaisesRegex(jobs.JobError, "incomplete or failed"):
            jobs.verify_trainer_outputs(dispatch, {}, outputs)
        (output / "checkpoint.json").write_text(json.dumps({"status": "complete", "identity": {"inputs": [], "code": []}}))
        outputs = [jobs.binding(output / name) for name in names]
        with self.assertRaisesRegex(jobs.JobError, "not fully bound"):
            jobs.verify_trainer_outputs(dispatch, {}, outputs)

    def test_all_final_model_bindings_are_checked_before_any_pickle_load(self):
        output = self.base / "final-output"
        output.mkdir()
        inputs = []
        for index in range(4):
            path = self.base / f"input-{index}.json"
            path.write_text("{}")
            inputs.append(jobs.binding(path))
        trainer_code = jobs.binding(SCRIPTS / "qsar_train.py")
        identity = {"inputs": inputs, "code": trainer_code}
        owned = []
        for name in ("first", "second"):
            path = output / (name + ".joblib")
            path.write_bytes(pickle.dumps({"fixture": name}))
            owned.append(jobs.binding(path))
        external = self.base / "unowned.joblib"
        external.write_bytes(pickle.dumps({"fixture": "not supervisor-owned"}))
        tasks = {"first": {"candidate_id": "candidate-first"}, "second": {"candidate_id": "candidate-second"}}
        selection_path = self.base / "selection.json"
        jobs.atomic_json(selection_path, {"status": "complete", "identity": identity, "tasks": tasks})
        selection = jobs.binding(selection_path)
        lock_path = self.base / "lock.json"
        jobs.atomic_json(lock_path, {"decision": "STOP", "selection": selection,
                                    "tasks": {task: chosen["candidate_id"] for task, chosen in tasks.items()}})
        lock = jobs.binding(lock_path)
        dispatch = {"stage": "final-fit", "bindings": inputs + [trainer_code, selection, lock], "owned_output_dir": str(output)}
        fake_trainer = SimpleNamespace(read_inputs=mock.Mock(return_value=({"tasks": tasks}, {}, [], {}, None, identity)),
                                       joblib=SimpleNamespace(load=mock.Mock(side_effect=AssertionError("Pickle loaded before custody validation"))))
        validation_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        first = {key: owned[0][key] for key in ("path", "sha256")}
        mismatched = {"path": owned[1]["path"], "sha256": "0" * 64}
        foreign = jobs.binding(external)
        for artifact in ({"path": foreign["path"], "sha256": foreign["sha256"]}, mismatched):
            with self.subTest(artifact=artifact):
                report = {"status": "complete", "identity": identity, "selection": selection, "lock": lock,
                          "models": {"first": {"artifact": first}, "second": {"artifact": artifact}},
                          "fit_evidence": [{"task_id": task} for task in tasks]}
                report_path = output / "final_fit.json"
                jobs.atomic_json(report_path, report)
                with mock.patch.object(jobs.importlib.util, "spec_from_file_location", return_value=validation_spec), \
                        mock.patch.object(jobs.importlib.util, "module_from_spec", return_value=fake_trainer):
                    with self.assertRaisesRegex(jobs.JobError, "exact verified collected-output bindings"):
                        jobs.verify_trainer_outputs(dispatch, {"coverage": report}, [jobs.binding(report_path), *owned])
                fake_trainer.joblib.load.assert_not_called()

    def test_actual_completed_trainer_accounting_and_final_model_are_verified(self):
        # Real numerical fixture, not a mocked scientific validator or review.
        sys.path.insert(0, str(SCRIPTS))
        trainer = importlib.import_module("qsar_train")
        fixture = trainer.synthetic_fixture(self.base / "numerical-fixture")
        inputs = [fixture / name for name in ("development.csv", "folds.json", "features.json", "contract.json")]
        out = self.base / "search-output"
        state = trainer.run_search(*inputs, out)
        identity = state["identity"]
        bound = identity["inputs"] + [identity["code"]] + identity["source_hashes"]
        dispatch = {"stage": "training", "bindings": bound, "owned_output_dir": str(out)}
        coverage_path = out / "search_coverage.json"
        coverage = jobs.load(coverage_path)
        manifest = {"coverage": coverage}
        self.assertTrue(jobs.verify_trainer_outputs(dispatch, manifest, jobs.output_inventory(out)))
        forged = json.loads(json.dumps(coverage))
        forged["cells"][0]["pending"] = 1
        jobs.atomic_json(coverage_path, forged)
        with self.assertRaisesRegex(jobs.JobError, "coverage is incomplete"):
            jobs.verify_trainer_outputs(dispatch, {"coverage": forged}, jobs.output_inventory(out))
        jobs.atomic_json(coverage_path, coverage)
        with self.assertRaisesRegex(jobs.JobError, "Producer coverage differs"):
            jobs.verify_trainer_outputs(dispatch, {"coverage": {"status": "complete"}}, jobs.output_inventory(out))
        oof_path = out / "selection_procedure_oof_manifest.json"
        original_oof = jobs.load(oof_path)
        forged_oof = json.loads(json.dumps(original_oof))
        forged_oof["tasks"]["oral"]["outer_folds"][0]["threshold_provenance"]["source"] = "terminal"
        jobs.atomic_json(oof_path, forged_oof)
        with self.assertRaisesRegex(jobs.JobError, "OOF manifest/model/threshold"):
            jobs.verify_trainer_outputs(dispatch, manifest, jobs.output_inventory(out))
        jobs.atomic_json(oof_path, original_oof)
        selection_path = out / "final_selection.json"
        selection = jobs.load(selection_path)
        lock_path = self.base / "reviewed-lock-fixture.json"
        jobs.atomic_json(lock_path, {"decision": "STOP", "selection": trainer.binding(selection_path),
                                    "tasks": {task: chosen["candidate_id"] for task, chosen in selection["tasks"].items()}})
        final_out = self.base / "final-fit-output"
        final = trainer.final_fit(*inputs, selection_path, lock_path, final_out)
        final_dispatch = {"stage": "final-fit", "bindings": bound + [trainer.binding(selection_path), trainer.binding(lock_path)],
                          "owned_output_dir": str(final_out)}
        self.assertTrue(jobs.verify_trainer_outputs(final_dispatch, {"coverage": final}, jobs.output_inventory(final_out)))
        model_path = Path(final["models"]["oral"]["artifact"]["path"])
        model = trainer.joblib.load(model_path)
        original_identity = model.training_identity_sha256_
        model.training_identity_sha256_ = "foreign-training-identity"
        trainer.joblib.dump(model, model_path)
        final["models"]["oral"]["artifact"] = trainer.binding(model_path)
        jobs.atomic_json(final_out / "final_fit.json", final)
        with self.assertRaisesRegex(jobs.JobError, "Persisted final model training identities"):
            jobs.verify_trainer_outputs(final_dispatch, {"coverage": final}, jobs.output_inventory(final_out))
        model.training_identity_sha256_ = original_identity
        model.fit_training_row_ids_ = model.fit_training_row_ids_[:-1]
        trainer.joblib.dump(model, model_path)
        final["models"]["oral"]["artifact"] = trainer.binding(model_path)
        jobs.atomic_json(final_out / "final_fit.json", final)
        with self.assertRaisesRegex(jobs.JobError, "Persisted final model training identities"):
            jobs.verify_trainer_outputs(final_dispatch, {"coverage": final}, jobs.output_inventory(final_out))


if __name__ == "__main__":
    unittest.main()
