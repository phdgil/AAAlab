"""Durable, runtime-gated local and shared-filesystem Slurm jobs.

The detached supervisor owns its child handles. Reconnecting clients never kill
PIDs. A successful scheduler allocation is not scientific acceptance.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid


class JobError(ValueError):
    """Invalid job request, identity, execution, or collection evidence."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def binding(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise JobError(f"Not a regular job artifact: {path}")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return {"path": str(path), "sha256": value.hexdigest(), "size": path.stat().st_size}


def verify(items):
    for item in items:
        current = binding(item["path"])
        if any(current.get(key) != value for key, value in item.items()):
            raise JobError("Changed job-bound artifact: " + item["path"])


def output_inventory(directory):
    root = Path(directory).resolve(strict=True)
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise JobError("Job output contains an unowned link: " + str(path))
        if path.is_file():
            files.append(binding(path))
    return files


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def dispatch_identity(dispatch):
    keys = ("dispatch_id", "run_id", "stage", "kind", "native_execution_id", "native_evidence",
            "bindings", "command", "command_sha256", "pipeline_identity", "upstream_tokens", "owned_output_dir")
    return {key: dispatch[key] for key in keys}


def read_request(path):
    request = load(path)
    expected = request.get("request_sha256")
    body = {key: value for key, value in request.items() if key != "request_sha256"}
    if digest(body) != expected:
        raise JobError("Job request hash mismatch")
    verify(request["bindings"])
    interpreter = request.get("supervisor_python")
    if (not isinstance(interpreter, str) or not Path(interpreter).is_absolute()
            or binding(interpreter) != request.get("supervisor_python_binding")):
        raise JobError("Supervisor interpreter path or executable binding changed")
    if digest(request["dispatch"]["command"]) != request["dispatch"]["command_sha256"]:
        raise JobError("Frozen command hash mismatch")
    return request


def scheduler_command(name, arguments):
    executable = shutil.which(name)
    if not executable:
        raise JobError(f"Slurm {name} is unavailable. Install/configure Slurm client tools and PATH on the submission host; no job was simulated.")
    try:
        result = subprocess.run([executable, *arguments], capture_output=True, text=True, shell=False,
                                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JobError(f"Slurm {name} could not run: {error}") from error
    if result.returncode:
        raise JobError(f"Slurm {name} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


class Jobs:
    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def _entry(state, dispatch):
        return state["external_evaluation"] if dispatch.get("evaluation_mode") == "external" else state["stages"][dispatch["stage"]]

    def _get(self, job_id, *, gated=True, verify_inputs=True):
        def action(state, events):
            job = state.get("jobs", {}).get(job_id)
            if not job:
                raise JobError("Unknown job ID")
            if gated:
                self.runtime._dispatch(state, job["dispatch_id"], "producer")
            verify([job["request"]])
            if verify_inputs:
                request = read_request(job["request"]["path"])
            else:
                request = load(job["request"]["path"])
                if request["request_sha256"] != job["request_sha256"]:
                    raise JobError("Job request identity mismatch")
            dispatch = state["dispatches"][job["dispatch_id"]]
            if request["dispatch"] != dispatch_identity(dispatch):
                raise JobError("Job no longer matches the native dispatch")
            return job
        return self.runtime._op(action)

    def prepare(self, dispatch_id, *, backend="local", shared_filesystem=None, resources=None):
        if backend not in {"local", "slurm"}:
            raise JobError("Backend must be local or slurm")
        if backend == "local" and (shared_filesystem is not None or resources):
            raise JobError("Slurm filesystem/resources options do not apply to local jobs")
        if resources is not None and not isinstance(resources, dict):
            raise JobError("Slurm resources must be an object")
        resources = dict(resources or {})
        allowed = {"partition", "account", "qos", "time", "mem", "cpus-per-task", "nodes", "gres", "constraint"}
        if set(resources) - allowed or any(not isinstance(v, (str, int)) or isinstance(v, bool) or not str(v) or "\n" in str(v) for v in resources.values()):
            raise JobError("Unsupported Slurm resources; use explicit partition/account/qos/time/mem/cpus-per-task/nodes/gres/constraint")
        def action(state, events):
            dispatch, entry = self.runtime._dispatch(state, dispatch_id, "producer")
            if dispatch["status"] != "bound" or not dispatch.get("command") or dispatch.get("execution") or dispatch.get("job_id"):
                raise JobError("Job preparation requires a bound, unexecuted native producer command")
            verify([dispatch["native_evidence"]])
            job_id = str(uuid.uuid4())
            folder = self.runtime.root / "jobs" / job_id
            folder.mkdir(mode=0o700, parents=True, exist_ok=False)
            request_path = folder / "request.json"
            files = {item["path"]: item for item in dispatch["bindings"] + [dispatch["native_evidence"], binding(__file__), binding(sys.executable)]}
            # Resolving a venv's interpreter symlink before launch selects the
            # base interpreter and silently loses that environment's packages.
            supervisor_python = str(Path(sys.executable).absolute())
            venv_config = Path(sys.prefix) / "pyvenv.cfg"
            if venv_config.is_file():
                config_binding = binding(venv_config)
                files[config_binding["path"]] = config_binding
            shared = None
            if backend == "slurm":
                if (not isinstance(shared_filesystem, dict)
                        or set(shared_filesystem) != {"bindings", "run_dir", "output_dir"}
                        or shared_filesystem["run_dir"] != str(self.runtime.root)
                        or shared_filesystem["output_dir"] != dispatch["owned_output_dir"]):
                    raise JobError("Slurm requires explicit shared_filesystem bindings, identical absolute run_dir and output_dir; path rewriting/relocation is not supported")
                supplied = shared_filesystem["bindings"]
                expected = [{"path": item["path"], "sha256": item["sha256"]} for item in files.values()]
                if (not isinstance(supplied, list) or len(supplied) != len(expected)
                        or sorted(supplied, key=canonical) != sorted(expected, key=canonical)):
                    raise JobError("Shared-filesystem paths/hashes must exactly match all dispatch, native-evidence, jobs-code and supervisor-Python bindings")
                verify(supplied)
                shared = shared_filesystem
                script = folder / "submit.sh"
                script.write_text("#!/bin/sh\nexec " + shlex.join([supervisor_python, str(Path(__file__).resolve()), "--worker", str(request_path)]) + "\n", encoding="utf-8", newline="\n")
                files[str(script)] = binding(script)
            request = {"schema_version": "1.0", "job_id": job_id, "backend": backend, "created_at": now(),
                       "dispatch": dispatch_identity(dispatch), "bindings": list(files.values()),
                       "shared_filesystem": shared, "resources": resources, "token": secrets.token_hex(32),
                       "supervisor_python": supervisor_python, "supervisor_python_binding": binding(supervisor_python), "job_dir": str(folder),
                       "job_name": "qsar-" + job_id}
            request["request_sha256"] = digest(request)
            atomic_json(request_path, request)
            job = {"job_id": job_id, "dispatch_id": dispatch_id, "backend": backend, "status": "prepared",
                   "created_at": request["created_at"], "request_sha256": request["request_sha256"],
                   "request": binding(request_path), "job_dir": str(folder), "job_name": request["job_name"],
                   "stdout": str(folder / "stdout.log"), "stderr": str(folder / "stderr.log"),
                   "worker_log": str(folder / "worker.log")}
            state.setdefault("jobs", {})[job_id] = job
            dispatch["job_id"] = job_id
            events.append(("job_prepared", {"job_id": job_id, "backend": backend, "request": job["request"]}))
            return job
        return self.runtime._op(action)

    def submit(self, job_id):
        job = self._get(job_id)
        if job["backend"] == "slurm":
            for name in ("sbatch", "sacct"):
                if not shutil.which(name):
                    raise JobError(f"Slurm {name} is unavailable; configure Slurm client tools/PATH before submitting")
        def reserve(state, events):
            current = state["jobs"][job_id]
            dispatch, entry = self.runtime._dispatch(state, current["dispatch_id"], "producer")
            if current["status"] != "prepared" or current.get("submission_started_at"):
                raise JobError("Job already submitted or submission uncertain; reconnect with status_job, never submit twice")
            current.update(status="queued", submission_started_at=now())
            dispatch["status"] = entry["status"] = "queued"
            events.append(("job_submission_reserved", {"job_id": job_id, "request_sha256": current["request_sha256"]}))
            return current
        job = self.runtime._op(reserve)
        request = read_request(job["request"]["path"])
        try:
            if job["backend"] == "local":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt":
                    flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                with Path(job["worker_log"]).open("ab") as log:
                    process = subprocess.Popen([request["supervisor_python"], str(Path(__file__).resolve()), "--worker", job["request"]["path"]],
                                               cwd=job["job_dir"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                               shell=False, creationflags=flags, start_new_session=os.name != "nt", close_fds=True)
                updates = {"launcher": {"pid": process.pid, "started_at": now(), "identity_policy": "Diagnostic only; cancellation uses authenticated supervisor and owned handles"}}
                # Retain the owned Popen handle until its detached supervisor
                # exits; reconnecting clients still never signal stored PIDs.
                threading.Thread(target=process.wait, name="qsar-supervisor-reaper", daemon=True).start()
            else:
                arguments = ["--parsable", "--job-name=" + request["job_name"], "--chdir=" + request["job_dir"],
                             "--output=" + str(Path(job["job_dir"]) / "scheduler.log"), "--error=" + str(Path(job["job_dir"]) / "scheduler-error.log")]
                arguments += ["--" + key + "=" + str(value) for key, value in sorted(request["resources"].items())]
                output = scheduler_command("sbatch", [*arguments, str(Path(job["job_dir"]) / "submit.sh")]).strip()
                scheduler_id = output.split(";", 1)[0]
                if not scheduler_id.isdigit():
                    raise JobError("sbatch returned no parsable allocation ID; submission may exist. Use status_job to reconcile the unique job name; do not resubmit")
                updates = {"scheduler_id": scheduler_id, "submission_response": output}
                if ";" in output:
                    updates["scheduler_cluster"] = output.split(";", 1)[1]
        except (JobError, OSError) as error:
            def failed(state, events):
                current = state["jobs"][job_id]
                current["submission_error"] = str(error)
                # Slurm may have accepted a request before its response was lost.
                if current["backend"] == "local":
                    current["status"] = "failed"
                    dispatch = state["dispatches"][current["dispatch_id"]]
                    dispatch["status"] = self._entry(state, dispatch)["status"] = "failed"
                events.append(("job_submission_error", {"job_id": job_id, "error": str(error)}))
                return current
            self.runtime._op(failed)
            raise JobError(str(error)) from error
        def record(state, events):
            current = state["jobs"][job_id]
            current.update(updates)
            events.append(("job_submitted", {"job_id": job_id, **updates}))
            return current
        return self.runtime._op(record)

    @staticmethod
    def _local_contact(job, operation):
        endpoint_path = Path(job["job_dir"]) / "supervisor.json"
        if not endpoint_path.is_file():
            raise JobError("Supervisor endpoint not yet available; inspect worker.log and reconnect. No PID will be killed or relaunched")
        endpoint, request = load(endpoint_path), load(job["request"]["path"])
        if endpoint.get("request_sha256") != job["request_sha256"] or endpoint.get("host") != "127.0.0.1":
            raise JobError("Supervisor endpoint/request identity mismatch; refusing contact")
        message = {"operation": operation, "token": request["token"], "job_id": job["job_id"], "request_sha256": job["request_sha256"]}
        try:
            with socket.create_connection((endpoint["host"], endpoint["port"]), timeout=2) as client:
                client.settimeout(2)
                client.sendall((canonical(message) + "\n").encode())
                response = _socket_message(client)
        except (OSError, ValueError) as error:
            raise JobError("Cannot reconnect to the request-bound supervisor; inspect worker.log/receipt. Refusing PID-based cancellation: " + str(error)) from error
        if response.get("job_id") != job["job_id"] or response.get("request_sha256") != job["request_sha256"] or response.get("error"):
            raise JobError("Supervisor rejected request identity")
        if response.get("status") != "running":
            raise JobError("Supervisor returned an invalid execution state")
        return response

    @staticmethod
    def _process_receipt(job):
        path = Path(job["job_dir"]) / "process_receipt.json"
        if not path.is_file():
            return None
        receipt = load(path)
        if (receipt.get("job_id") != job["job_id"] or receipt.get("request_sha256") != job["request_sha256"]
                or receipt.get("status") not in {"completed", "failed", "cancelled"}):
            raise JobError("Process receipt does not bind this exact job request")
        if job.get("process_receipt"):
            verify([job["process_receipt"]])
        verify(receipt["logs"])
        if receipt["status"] == "completed" and receipt.get("exit_code") != 0:
            raise JobError("Completed process receipt has a nonzero/missing exit code")
        return receipt

    def status(self, job_id):
        job = self._get(job_id, gated=False, verify_inputs=False)
        if job["status"] == "prepared":
            return job
        receipt = self._process_receipt(job)
        updates = {}
        if job["backend"] == "slurm":
            request = load(job["request"]["path"])
            selector = ["--jobs=" + job["scheduler_id"]] if job.get("scheduler_id") else ["--name=" + job["job_name"], "--starttime=" + request["created_at"][:10]]
            if job.get("scheduler_cluster"):
                selector.append("--clusters=" + job["scheduler_cluster"])
            output = scheduler_command("sacct", ["--noheader", "--parsable2", "--allocations", "--format=JobIDRaw,JobName%80,State,ExitCode", *selector])
            records = []
            for line in output.splitlines():
                fields = [field.strip() for field in line.strip().split("|")]
                if len(fields) >= 4 and fields[0].isdigit() and fields[1] == job["job_name"]:
                    if not job.get("scheduler_id") or fields[0] == job["scheduler_id"]:
                        records.append(fields)
            if len(records) > 1:
                raise JobError("Multiple Slurm allocations match the persisted unique request; refusing ambiguous reconciliation")
            if records:
                scheduler_id, _, raw_state, exit_code = records[0][:4]
                raw_state = raw_state.split()[0].rstrip("+")
                mapping = {"PENDING": "queued", "REQUEUED": "queued", "REQUEUE_FED": "queued", "RESIZING": "queued",
                           "RUNNING": "running", "COMPLETING": "running", "CONFIGURING": "queued", "SUSPENDED": "running",
                           "COMPLETED": "completed", "CANCELLED": "cancelled", "FAILED": "failed", "TIMEOUT": "failed",
                           "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed", "PREEMPTED": "failed", "BOOT_FAIL": "failed", "DEADLINE": "failed", "REVOKED": "failed"}
                if raw_state not in mapping:
                    raise JobError("Unrecognized Slurm state; inspect allocation " + scheduler_id + ": " + raw_state)
                status = mapping[raw_state]
                if status == "completed" and exit_code != "0:0":
                    status = "failed"
                updates = {"scheduler_id": scheduler_id, "scheduler_state": raw_state, "scheduler_exit_code": exit_code,
                           "status": status, "scheduler_observed_at": now()}
            else:
                updates["connection_error"] = "No sacct allocation record yet; accounting may lag. Request remains unresolved; never resubmit blindly"
        elif receipt is None and job["status"] not in {"failed", "cancelled", "completed"}:
            try:
                updates.update(self._local_contact(job, "status"))
            except JobError as error:
                updates["connection_error"] = str(error)
        if receipt:
            if (updates.get("status") not in {"failed", "cancelled"}
                    and (job["backend"] == "local" or updates.get("status") == "completed" or receipt["status"] != "completed")):
                updates["status"] = receipt["status"]
            updates["process_receipt"] = binding(Path(job["job_dir"]) / "process_receipt.json")
            updates["exit_code"] = receipt["exit_code"]
        updates.pop("job_id", None)
        updates.pop("request_sha256", None)
        def record(state, events):
            current = state["jobs"][job_id]
            previous = current["status"]
            current.pop("connection_error", None)
            current.update(updates)
            dispatch = state["dispatches"][current["dispatch_id"]]
            if not dispatch.get("invalidated") and dispatch["status"] not in {"submitted", "executed"}:
                dispatch["status"] = self._entry(state, dispatch)["status"] = current["status"]
            if current["status"] != previous:
                events.append(("job_status_changed", {"job_id": job_id, "from": previous, "status": current["status"]}))
            return current
        return self.runtime._op(record)

    def cancel(self, job_id):
        job = self._get(job_id, gated=False, verify_inputs=False)
        if job["status"] == "prepared":
            def cancel_prepared(state, events):
                current = state["jobs"][job_id]
                # Compete with submit's reservation in the same state transaction.
                if current["status"] == "prepared":
                    updates = {"status": "cancelled", "cancelled_at": now()}
                    current.update(updates)
                    dispatch = state["dispatches"][current["dispatch_id"]]
                    if not dispatch.get("invalidated"):
                        dispatch["status"] = self._entry(state, dispatch)["status"] = "cancelled"
                    events.append(("job_cancel_requested", {"job_id": job_id, **updates}))
                return current
            job = self.runtime._op(cancel_prepared)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        if job["backend"] == "local":
            if self._process_receipt(job):
                return self.status(job_id)
            try:
                self._local_contact(job, "cancel")
            except JobError:
                if self._process_receipt(job):
                    return self.status(job_id)
                raise
        else:
            if not job.get("scheduler_id"):
                job = self.status(job_id)
            if not job.get("scheduler_id"):
                raise JobError("Cannot safely cancel an unresolved Slurm submission; reconcile its unique job name with sacct first")
            # A fresh exact-name match and scancel's own name filter prevent ID reuse.
            observed = self.status(job_id)
            if observed.get("connection_error") or not observed.get("scheduler_observed_at"):
                raise JobError("Cannot safely cancel: sacct did not confirm this allocation's exact request-bound name")
            if observed["status"] in {"completed", "failed", "cancelled"}:
                return observed
            arguments = ["--name=" + job["job_name"]]
            if job.get("scheduler_cluster"):
                arguments.append("--clusters=" + job["scheduler_cluster"])
            scheduler_command("scancel", [*arguments, job["scheduler_id"]])
        updates = {"cancel_requested_at": now()}
        def record(state, events):
            current = state["jobs"][job_id]
            current.update(updates)
            events.append(("job_cancel_requested", {"job_id": job_id, **updates}))
            return current
        return self.runtime._op(record)

    def collect(self, job_id, manifest):
        job = self.status(job_id)
        self._get(job_id)
        receipt = self._process_receipt(job)
        if job["status"] != "completed" or not receipt or receipt["status"] != "completed" or receipt["exit_code"] != 0:
            raise JobError("Collection requires a verified completed process receipt; scheduler success alone is not scientific evidence")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("outputs"), list) or not manifest["outputs"]:
            raise JobError("Collection requires an ordinary producer manifest with concrete output files")
        if (not isinstance(manifest.get("summary"), str) or not manifest["summary"].strip()
                or not isinstance(manifest.get("checks"), list) or not manifest["checks"]):
            raise JobError("Collection requires an ordinary producer summary and performed checks")
        state = self.runtime.status()
        dispatch = state["dispatches"][job["dispatch_id"]]
        if dispatch["status"] == "submitted" or job.get("submitted_candidate_hash"):
            raise JobError("Job producer evidence has already been submitted")
        outputs = [binding(self.runtime._inside(path, Path(dispatch["owned_output_dir"]))) for path in manifest["outputs"]]
        verify(receipt.get("outputs", []))
        recorded = {item["path"]: item for item in receipt.get("outputs", [])}
        if any(recorded.get(item["path"]) != item for item in outputs):
            raise JobError("Collected outputs were not hash-bound by the completed supervisor")
        scientific = verify_trainer_outputs(dispatch, manifest, outputs)
        verify([job["request"], job["process_receipt"]])
        receipt_path = Path(job["job_dir"]) / "collection_receipt.json"
        collection = {"job_id": job_id, "request_sha256": job["request_sha256"], "process_receipt": job["process_receipt"],
                      "manifest_sha256": digest(manifest), "output_bindings": outputs, "scientific_checks": scientific,
                      "collected_at": now(), "acceptance": "Producer evidence only; an independent native PASS is still required"}
        try:
            with receipt_path.open("x", encoding="utf-8") as stream:
                stream.write(canonical(collection) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            existing = load(receipt_path)
            if any(existing.get(key) != value for key, value in collection.items() if key != "collected_at"):
                raise JobError("Collection is already bound to different evidence; do not rewrite its receipt")
            collection = existing
        def ready(state, events):
            current = state["jobs"][job_id]
            bound, entry = self.runtime._dispatch(state, current["dispatch_id"], "producer")
            if bound["status"] not in {"completed", "executed"} or current.get("submitted_candidate_hash"):
                raise JobError("Job is not an unconsumed completed producer")
            current["collection"] = collection
            bound["status"] = "executed"
            bound["execution"] = {"argv": bound["command"], "exit_code": 0, "started_at": receipt["started_at"],
                                  "finished_at": receipt["finished_at"], "log": receipt["logs"][0], "timed_out": False,
                                  "job_id": job_id, "request_sha256": current["request_sha256"],
                                  "job_bindings": [current["request"], current["process_receipt"], binding(receipt_path), *receipt["logs"], *receipt["outputs"]]}
            entry["status"] = "executed"
            events.append(("job_collected", {"job_id": job_id, "receipt": binding(receipt_path)}))
            return current
        self.runtime._op(ready)
        candidate = self.runtime.submit(job["dispatch_id"], manifest)
        def submitted(state, events):
            state["jobs"][job_id]["submitted_candidate_hash"] = candidate["candidate_hash"]
            return candidate
        return self.runtime._op(submitted)


def verify_trainer_outputs(dispatch, manifest, outputs):
    """Validate persisted trainer accounting without refitting or accepting a model."""
    stage = dispatch["stage"]
    if stage not in {"training", "training-preflight", "final-fit"}:
        return ["Process completed and output ownership/hashes verified; stage science requires independent review"]
    verify(outputs)
    collected = {item["path"]: item for item in outputs}

    def owns_trainer_artifact(item):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            return False
        produced = collected.get(item["path"])
        return produced is not None and {key: produced[key] for key in ("path", "sha256")} == item

    grouped = {}
    for item in outputs:
        grouped.setdefault(Path(item["path"]).name, []).append(item["path"])
    expected = {"training": ("checkpoint.json", "search_coverage.json", "final_selection.json", "trial_ledger.json", "fit_evidence.json",
                             "attempt_history.json", "selection_procedure_oof.csv", "selection_procedure_oof_manifest.json"),
                "training-preflight": ("preflight.json",), "final-fit": ("final_fit.json",)}[stage]
    if any(len(grouped.get(name, [])) != 1 for name in expected):
        raise JobError("Completed trainer collection requires outputs: " + ", ".join(expected))
    paths = {name: grouped[name][0] for name in expected}
    allowed = {(item["path"], item["sha256"]) for item in dispatch["bindings"]}
    primary = load(paths[expected[0]])
    if not isinstance(primary, dict) or primary.get("status") != "complete":
        raise JobError("Trainer output is incomplete or failed")
    if stage == "training-preflight":
        contract = primary.get("contract")
        if not isinstance(primary.get("source_hashes"), list) or not isinstance(primary.get("fixture_checkpoint"), dict):
            raise JobError("Preflight requires source and synthetic checkpoint bindings")
        sources = [primary.get("pipeline_identity", {}), *primary.get("source_hashes", [])]
        if (primary.get("synthetic_only") is not True or not primary.get("checks") or not isinstance(contract, dict)
                or (contract.get("path"), contract.get("sha256")) not in allowed
                or any(not isinstance(item, dict) or (item.get("path"), item.get("sha256")) not in allowed for item in sources)):
            raise JobError("Preflight is not completed, checked and bound to the reviewed contract")
        verify([contract, *sources, primary["fixture_checkpoint"]])
        checkpoint = Path(primary["fixture_checkpoint"]["path"])
        if not checkpoint.resolve().is_relative_to(Path(dispatch["owned_output_dir"]).resolve()):
            raise JobError("Preflight synthetic checkpoint is outside its owned output directory")
        coverage = load(checkpoint.with_name("search_coverage.json"))
        if (load(checkpoint).get("status") != "complete" or coverage.get("status") != "complete"
                or not coverage.get("cells") or coverage.get("expected_trials") != coverage.get("completed_trials")
                or any(cell.get("failed") != 0 or cell.get("incomplete") != 0 or cell.get("pending") != 0
                       or cell.get("complete") != cell.get("expected_search_trials", -1) + cell.get("expected_anchor_trials", -1)
                       for cell in coverage["cells"])):
            raise JobError("Preflight synthetic checkpoint is incomplete")
        if manifest.get("coverage") != primary:
            raise JobError("Producer coverage must equal the verified completed preflight report")
        return ["Completed synthetic preflight and bound contract/checkpoint verified"]
    identity = primary.get("identity", {})
    if not isinstance(identity, dict):
        raise JobError("Trainer input/code identity must be an object")
    inputs, code = identity.get("inputs", []), identity.get("code", [])
    if (not isinstance(inputs, list) or not isinstance(code, (dict, list))
            or not isinstance(identity.get("source_hashes", []), list)):
        raise JobError("Trainer input/code identity must contain binding lists")
    code = ([code] if isinstance(code, dict) else code) + identity.get("source_hashes", [])
    if len(inputs) < 4 or not code or any(not isinstance(item, dict) or (item.get("path"), item.get("sha256")) not in allowed for item in inputs + code):
        raise JobError("Trainer input/code identity is not fully bound by this native dispatch")
    verify(inputs + code)
    trainer_path = Path(__file__).with_name("qsar_train.py")
    if (str(trainer_path.resolve()), binding(trainer_path)["sha256"]) not in allowed:
        raise JobError("Collection requires the real trainer code as a dispatch dependency")
    # The real trainer validator owns fold, classification and trial semantics.
    if str(trainer_path.parent) not in sys.path:
        sys.path.insert(0, str(trainer_path.parent))
    spec = importlib.util.spec_from_file_location("qsar_job_trainer_validation", trainer_path)
    trainer = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(trainer)
    except ImportError as error:
        raise JobError("Completed trainer collection requires its actual scientific dependencies on the collection host: " + str(error)) from error
    try:
        training, folds, rows, target, features, current_identity = trainer.read_inputs(*(item["path"] for item in inputs[:4]))
        if current_identity != identity:
            raise JobError("Trainer identity/environment differs during collection")
        if stage == "training":
            coverage, selected = load(paths["search_coverage.json"]), load(paths["final_selection.json"])
            if coverage.get("status") != "complete" or selected.get("status") != "complete" or selected.get("identity") != identity or selected.get("tasks") != primary.get("final_selection"):
                raise JobError("Trainer completion/selection identity mismatch")
            if manifest.get("coverage") != coverage:
                raise JobError("Producer coverage differs from verified search_coverage.json")
            axes = trainer.load_fit_index_registry(primary["fit_index_registry"])
            if axes != trainer.frozen_fit_axes(folds):
                raise JobError("Trainer fit-index registry differs from frozen folds")
            trainer.validate_fit_contexts(primary["fit_evidence"], folds, axes)
            artifacts = primary["artifacts"] + [primary["fit_index_registry"]]
            if not all(owns_trainer_artifact(item) for item in artifacts):
                raise JobError("Trainer models and fit-index artifacts must be included as exact producer outputs")
            verify(artifacts)
            trainer.validate_completed_scopes(primary, training, folds, Path(paths["checkpoint.json"]).parent, target)
            ledger = primary["ledger"]
            expected_cells = []
            for task, task_spec in training["tasks"].items():
                for scope in ["outer-" + str(fold["fold"]) for fold in folds["tasks"][task]["outer_folds"]] + ["final-selection"]:
                    for cell in task_spec["cells"]:
                        expected_cells.append({"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                                               "expected_search_trials": cell["n_trials"], "expected_anchor_trials": 1,
                                               "complete": cell["n_trials"] + 1, "failed": 0, "incomplete": 0, "pending": 0})
            expected_trials = sum(cell["complete"] for cell in expected_cells)
            actual_cells = coverage.get("cells", [])
            cells_match = len(actual_cells) == len(expected_cells) and all(
                isinstance(actual, dict) and all(actual.get(key) == value and (type(value) is not int or type(actual.get(key)) is int)
                                                 for key, value in expected_cell.items())
                for actual, expected_cell in zip(actual_cells, expected_cells))
            history = primary.get("attempt_history", [])
            attempts = [record for attempt in history for record in attempt["ledger"]] + ledger
            if (not cells_match or coverage.get("expected_trials") != expected_trials
                    or coverage.get("completed_trials") != expected_trials or coverage.get("failed_trials") != 0
                    or coverage.get("incomplete_trials") != 0 or coverage.get("completed_scopes") != sorted(primary["completed"])
                    or coverage.get("failure_reason") is not None or load(paths["trial_ledger.json"]) != ledger
                    or load(paths["attempt_history.json"]) != history or coverage.get("attempted_trials") != len(attempts)
                    or coverage.get("failed_attempts") != sum(record["status"] == "failed" for record in attempts)
                    or coverage.get("attempt_seconds") != sum(record["seconds"] for record in attempts)):
                raise JobError("Trainer search coverage is incomplete or contradicts checkpoint/trial ledger")
            evidence = load(paths["fit_evidence.json"])
            if evidence.get("records") != primary["fit_evidence"] or evidence.get("fit_index_registry") != primary["fit_index_registry"]:
                raise JobError("Trainer fit evidence differs from checkpoint")
            import csv
            with Path(paths["selection_procedure_oof.csv"]).open(newline="", encoding="utf-8-sig") as stream:
                oof = list(csv.DictReader(stream))
            if len(oof) != len(primary["oof"]) or any(any(row.get(key) != str(value) for key, value in expected_row.items()) for row, expected_row in zip(oof, primary["oof"])):
                raise JobError("Trainer OOF output differs from verified checkpoint")
            oof_manifest = load(paths["selection_procedure_oof_manifest.json"])
            expected_tasks = {}
            for task, task_folds in folds["tasks"].items():
                entries = []
                for fold in task_folds["outer_folds"]:
                    scope = "outer-" + str(fold["fold"])
                    chosen = primary["completed"][task + ":" + scope]
                    artifact_path = str(Path(paths["checkpoint.json"]).parent / "outer_models" / (trainer.digest([task, scope]) + ".joblib"))
                    artifact = next(item for item in primary["artifacts"] if item["path"] == artifact_path)
                    entries.append({"fold": fold["fold"], "threshold": chosen.get("threshold"), "model": artifact,
                                    "selection": chosen, "training_identity_sha256": trainer.digest(identity),
                                    "frozen_recipe_sha256": trainer.frozen_recipe(chosen, chosen["parameters"], {**training, **chosen}),
                                    "threshold_provenance": {"source": "inner_cv" if "threshold_grid" in training else "contract_fixed",
                                                             "scope": scope, "trial_id": chosen["trial_id"],
                                                             "fit_axis_id": trainer.axis_reference(axes, fold["train_row_ids"])}})
                expected_tasks[task] = {"task_type": trainer.task_type(training),
                                        "positive_class": 1 if trainer.task_type(training) == "classification" else None, "outer_folds": entries}
            if (oof_manifest.get("status") != "complete" or oof_manifest.get("identity") != identity
                    or oof_manifest.get("inputs") != identity["inputs"] or oof_manifest.get("fit_index_registry") != primary["fit_index_registry"]
                    or oof_manifest.get("predictions") != trainer.binding(paths["selection_procedure_oof.csv"])
                    or oof_manifest.get("tasks") != expected_tasks):
                raise JobError("Trainer OOF manifest/model/threshold provenance mismatch")
        else:
            if manifest.get("coverage") != primary:
                raise JobError("Producer coverage must equal the verified completed final-fit report")
            verify([primary["selection"], primary["lock"]])
            if any((primary[key]["path"], primary[key]["sha256"]) not in allowed for key in ("selection", "lock")):
                raise JobError("Final-fit selection/STOP lock is not dispatch-bound")
            selection, lock = load(primary["selection"]["path"]), load(primary["lock"]["path"])
            if (selection.get("status") != "complete" or selection.get("identity") != identity or lock.get("decision") != "STOP"
                    or lock.get("selection") != primary["selection"] or lock.get("tasks") != {task: cell["candidate_id"] for task, cell in selection["tasks"].items()}
                    or set(primary["models"]) != set(training["tasks"])):
                raise JobError("Final-fit locked task coverage mismatch")
            if len(primary["fit_evidence"]) != len(training["tasks"]):
                raise JobError("Final-fit fit-evidence task coverage mismatch")
            artifacts = [model["artifact"] for model in primary["models"].values()]
            if not all(owns_trainer_artifact(item) for item in artifacts):
                raise JobError("Final model artifacts must match exact verified collected-output bindings")
            verify(artifacts)
            for task, model in primary["models"].items():
                evidence = [item for item in primary["fit_evidence"] if item["task_id"] == task]
                chosen = selection["tasks"][task]
                ids = folds["tasks"][task]["row_ids"]
                if (len(evidence) != 1 or evidence[0].get("fit_row_ids") != folds["tasks"][task]["row_ids"]
                        or evidence[0].get("nonconverged") is not False or model.get("candidate_id") != chosen["candidate_id"]
                        or model.get("family") != chosen["family"]):
                    raise JobError("Final-fit model/fit-row evidence mismatch")
                # Only worker-produced, hash-bound local artifacts reach pickle loading.
                fitted = trainer.joblib.load(model["artifact"]["path"])
                cell = next(cell for cell in training["tasks"][task]["cells"] if cell["candidate_id"] == chosen["candidate_id"])
                if (fitted.named_steps["audit"].fit_row_ids_ != ids or fitted.fit_training_row_ids_ != ids
                        or getattr(fitted, "training_identity_sha256_", None) != trainer.digest(identity)
                        or model.get("training_identity_sha256") != trainer.digest(identity)
                        or fitted.task_type_ != trainer.task_type(training) or model.get("task_type") != trainer.task_type(training)
                        or fitted.feature_route_ != trainer.feature_route(cell) or model.get("feature_route") != trainer.feature_route(cell)
                        or model.get("feature_names") != features.metadata[chosen["family"]]["feature_names"]):
                    raise JobError("Persisted final model training identities/type/feature route mismatch")
                provenance = trainer.model_provenance(fitted, cell)
                if model.get("pipeline_provenance") != provenance or any(evidence[0].get(key) != value for key, value in provenance.items()):
                    raise JobError("Persisted final model pipeline provenance mismatch")
                if trainer.task_type(training) == "classification" and (
                        type(model.get("positive_class")) is not int or model["positive_class"] != 1
                        or fitted.positive_class_ != 1 or fitted.threshold_ != chosen["threshold"] or model.get("threshold") != chosen["threshold"]):
                    raise JobError("Persisted final model classification threshold mismatch")
                domain = fitted.applicability_domain_
                if "applicability_domain" in training:
                    if domain is None or domain.training_row_ids != ids:
                        raise JobError("Persisted applicability-domain training identities mismatch")
                    domain_artifact = domain.to_dict()
                    domain_evidence = {key: domain_artifact[key] for key in ("artifact_sha256", "recipe_sha256", "training_sha256", "references_sha256", "calibration_sha256", "cutoff")}
                    if (model.get("applicability_domain") != domain_evidence or evidence[0].get("applicability_domain") != domain_evidence
                            or fitted.applicability_domain_sha256_ != domain_artifact["artifact_sha256"]):
                        raise JobError("Persisted applicability-domain provenance mismatch")
                elif domain is not None or model.get("applicability_domain") is not None or evidence[0].get("applicability_domain") is not None:
                    raise JobError("Unexpected final-model applicability domain")
    except (KeyError, TypeError, ValueError, OSError, AttributeError, StopIteration, ImportError) as error:
        raise JobError("Invalid completed trainer evidence: " + str(error)) from error
    return ["Completed trainer identity, frozen folds, fit evidence, output hashes and exact coverage verified"]


def _socket_message(connection):
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(4096)
        if not chunk or len(data) + len(chunk) > 65536:
            raise JobError("Invalid supervisor message")
        data.extend(chunk)
    message = json.loads(data.split(b"\n", 1)[0])
    if not isinstance(message, dict):
        raise JobError("Supervisor messages must be objects")
    return message


def _windows_job(process):
    """A kernel job owns the Windows child tree; handles, never reused PIDs."""
    import ctypes
    from ctypes import wintypes
    class Limits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Limits), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
            (name, wintypes.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        error = ctypes.WinError(ctypes.get_last_error())
        if handle:
            kernel.CloseHandle(handle)
        raise JobError("Cannot establish owner-safe Windows process-tree job: " + str(error))
    # The child was created suspended: establish tree ownership before it can
    # spawn descendants, then resume its owned primary thread via documented APIs.
    class ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    threads = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if threads == ctypes.c_void_p(-1).value:
        kernel.CloseHandle(handle)
        raise JobError("Cannot enumerate suspended owned child threads: " + str(ctypes.WinError(ctypes.get_last_error())))
    entry, resumed = ThreadEntry(), False
    entry.dwSize = ctypes.sizeof(entry)
    try:
        available = kernel.Thread32First(threads, ctypes.byref(entry))
        while available:
            if entry.th32OwnerProcessID == process.pid:
                thread = kernel.OpenThread(0x2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if not thread:
                    raise JobError("Cannot open suspended owned child thread")
                try:
                    if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                        raise JobError("Cannot resume suspended owned child thread")
                    resumed = True
                finally:
                    kernel.CloseHandle(thread)
            available = kernel.Thread32Next(threads, ctypes.byref(entry))
        if not resumed:
            raise JobError("Owned suspended child has no resumable thread")
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    finally:
        kernel.CloseHandle(threads)
    def close():
        try:
            if not kernel.TerminateJobObject(handle, 1):
                raise JobError("Cannot terminate owned Windows process-tree job: " + str(ctypes.WinError(ctypes.get_last_error())))
            deadline = time.monotonic() + 5
            while True:
                accounting = Accounting()
                if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise JobError("Cannot verify owned Windows process-tree cleanup: " + str(ctypes.WinError(ctypes.get_last_error())))
                if accounting.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise JobError("Owned Windows process-tree cleanup did not become quiescent")
                time.sleep(0.05)
        finally:
            kernel.CloseHandle(handle)
    return close


class _OwnedProcess:
    """Own one process group (POSIX) or kernel Job (Windows), not a sandbox.

    POSIX descendants that deliberately leave the group are outside this boundary.
    Never reap the group leader until all group signals and liveness checks finish:
    its unreaped identity prevents another process from reusing the PGID.
    """
    def __init__(self, command, *, cwd, stdout, stderr):
        self.windows = os.name == "nt"
        self.close_tree, self.closed = None, False
        if not self.windows:
            if any(not hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")):
                raise JobError("Owner-safe POSIX execution requires waitid with WNOWAIT")
            try:
                import psutil
            except ImportError as error:
                raise JobError("Owner-safe POSIX execution requires psutil to verify process-group cleanup") from error
            self.psutil = psutil
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                        shell=False, start_new_session=not self.windows,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | (0x4 if self.windows else 0))
        if self.windows:
            try:
                self.close_tree = _windows_job(self.process)
            except BaseException:
                # An unassigned suspended child cannot have spawned descendants.
                self.process.kill()
                self.process.wait()
                raise

    def exited(self):
        if self.windows:
            return self.process.poll() is not None
        try:
            return os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError as error:
            raise JobError("Owned POSIX leader was reaped before process-group cleanup; refusing PGID signals") from error

    def _signal_group(self, signum):
        self.exited()  # Confirm the leader is still our unreaped child, even if exited.
        try:
            os.killpg(self.process.pid, signum)
        except ProcessLookupError:
            pass

    def _live_group_members(self):
        live = []
        try:
            for member in self.psutil.process_iter():
                try:
                    if os.getpgid(member.pid) == self.process.pid and member.status() not in {
                            self.psutil.STATUS_ZOMBIE, self.psutil.STATUS_DEAD}:
                        live.append(member.pid)
                except (ProcessLookupError, self.psutil.NoSuchProcess):
                    continue
        except (OSError, self.psutil.Error) as error:
            raise JobError("Cannot verify owned POSIX process-group cleanup: " + str(error)) from error
        return live

    def _wait_group(self):
        deadline = time.monotonic() + 5
        while self._live_group_members():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def close(self):
        if self.closed:
            return
        try:
            if self.windows:
                self.close_tree()
            else:
                try:
                    self._signal_group(signal.SIGTERM)
                    self._wait_group()
                finally:
                    # Escalate for the whole group even if the leader exited on TERM.
                    self._signal_group(signal.SIGKILL)
                    if not self._wait_group():
                        raise JobError("Owned POSIX process-group cleanup did not become quiescent")
        finally:
            self.process.wait()
            self.closed = True


def run_owned(command, *, cwd, stdout, stderr, timeout=None):
    """Run with caller-owned log streams; finish tree cleanup before return/timeout."""
    owned = _OwnedProcess(command, cwd=cwd, stdout=stdout, stderr=stderr)
    try:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not owned.exited():
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.05)
    finally:
        owned.close()
    return subprocess.CompletedProcess(command, owned.process.returncode)


def worker(request_path):
    request = read_request(request_path)
    folder = Path(request["job_dir"])
    # An exclusive durable claim prevents duplicate launch, even after a client crash.
    claim = os.open(folder / "worker.claim", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(claim, "w", encoding="utf-8") as stream:
        stream.write(canonical({"request_sha256": request["request_sha256"], "supervisor_pid": os.getpid(), "started_at": now()}))
    cancelled, server, process, owned = False, None, None, None
    started, error, cleanup_failed = now(), None, False
    def stop(signum, frame):
        nonlocal cancelled
        cancelled = True
    for name in ("SIGTERM", "SIGINT"):
        signal.signal(getattr(signal, name), stop)
    try:
        if request["backend"] == "local":
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            server.settimeout(0.2)
            atomic_json(folder / "supervisor.json", {"host": "127.0.0.1", "port": server.getsockname()[1],
                        "request_sha256": request["request_sha256"], "supervisor_pid": os.getpid(), "started_at": started})
        with (folder / "stdout.log").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr:
            owned = _OwnedProcess(request["dispatch"]["command"], cwd=request["dispatch"]["owned_output_dir"], stdout=stdout, stderr=stderr)
            process = owned.process
            atomic_json(folder / "progress.json", {"status": "running", "request_sha256": request["request_sha256"], "started_at": started, "child_pid": process.pid})
            while not owned.exited() and not cancelled:
                if server is None:
                    time.sleep(0.2)
                    continue
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue
                with client:
                    client.settimeout(2)
                    try:
                        message = _socket_message(client)
                        if (not secrets.compare_digest(str(message.get("token", "")), request["token"])
                                or message.get("job_id") != request["job_id"] or message.get("request_sha256") != request["request_sha256"]
                                or message.get("operation") not in {"status", "cancel"}):
                            response = {"error": "Request identity mismatch"}
                        else:
                            cancelled = message["operation"] == "cancel"
                            response = {"job_id": request["job_id"], "request_sha256": request["request_sha256"], "status": "running", "child_pid": process.pid}
                        client.sendall((canonical(response) + "\n").encode())
                    except (OSError, ValueError):
                        continue
    except BaseException as caught:
        error = f"{type(caught).__name__}: {caught}"
        cleanup_failed = owned is None and isinstance(caught, JobError)
    finally:
        if server:
            server.close()
        if owned is not None:
            try:
                owned.close()
            except BaseException as caught:
                cleanup_failed = True
                error = f"Owned process-tree cleanup failed: {type(caught).__name__}: {caught}"
        for name in ("stdout.log", "stderr.log"):
            (folder / name).touch(exist_ok=True)
        outputs, logs = [], []
        if not cleanup_failed:
            try:
                verify(request["bindings"])
                outputs = output_inventory(request["dispatch"]["owned_output_dir"])
                logs = [binding(folder / "stdout.log"), binding(folder / "stderr.log")]
            except (OSError, ValueError) as caught:
                error = f"{type(caught).__name__}: {caught}"
        exit_code = process.returncode if process is not None else None
        status = "failed" if cleanup_failed else "cancelled" if cancelled else "completed" if exit_code == 0 and error is None else "failed"
        receipt = {"job_id": request["job_id"], "request_sha256": request["request_sha256"], "status": status,
                   "started_at": started, "finished_at": now(), "exit_code": exit_code, "error": error,
                   "logs": logs, "outputs": outputs}
        atomic_json(folder / "process_receipt.json", receipt)
        atomic_json(folder / "progress.json", receipt)
    return 0 if status == "completed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir")
    parser.add_argument("action", nargs="?", choices=("prepare", "submit", "status", "cancel", "collect"))
    parser.add_argument("--dispatch-id")
    parser.add_argument("--job-id")
    parser.add_argument("--backend", choices=("local", "slurm"), default="local")
    parser.add_argument("--shared-filesystem", help="JSON file containing exact shared path/hash bindings")
    parser.add_argument("--resources", help="JSON file containing Slurm resource options")
    parser.add_argument("--manifest", help="Ordinary producer evidence JSON file")
    args = parser.parse_args(argv)
    try:
        if args.worker:
            return worker(args.worker)
        if not args.run_dir or not args.action:
            parser.error("--run-dir and action are required")
        if args.action == "prepare" and not args.dispatch_id:
            parser.error("prepare requires --dispatch-id")
        if args.action != "prepare" and not args.job_id:
            parser.error("submit/status/cancel/collect require --job-id")
        if args.action == "collect" and not args.manifest:
            parser.error("collect requires --manifest")
        from qsar_runtime import Runtime
        runtime = Runtime(args.run_dir)
        if args.action == "prepare":
            result = runtime.prepare_job(args.dispatch_id, backend=args.backend,
                                         shared_filesystem=load(args.shared_filesystem) if args.shared_filesystem else None,
                                         resources=load(args.resources) if args.resources else None)
        elif args.action == "collect":
            result = runtime.collect_job(args.job_id, load(args.manifest))
        else:
            result = getattr(runtime, args.action + "_job")(args.job_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
