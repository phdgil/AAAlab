# Local QSAR stage driver

`qsar_runtime.py` uses only Python's standard library. Import `Runtime` and
`GateError`, or run `python qsar_runtime.py --help`. State and an append-only
event history are stored transactionally in `<run_dir>/runtime.sqlite3`.

```python
rt = Runtime(run_dir)
rt.initialize(contract_path, sealed_inputs=[holdout_path])
d = rt.prepare_dispatch(
    "contract", inputs=[source_path], code=[adapter_path, local_module_path],
    config=[config_path], command=[python_path, adapter_path,
                                "--output-dir", "{output_dir}"],
    available_roles=["analyst", "executor", "verifier", "critic"],
    context={"objective": "...", "acceptance_criteria": "...",
             "stop_condition": "..."})
# Leader launches a fresh native execution using returned agent_type,
# fork_turns='none', required_reads, context and owned_output_dir.
# Save the actual native tool response under run_dir, then bind it:
rt.bind_dispatch(d["dispatch_id"], actual_native_id, tool_result_path)
rt.execute(d["dispatch_id"])
candidate = rt.submit(d["dispatch_id"], {
    "outputs": [output_path], "summary": "...", "checks": ["..."],
    "limitations": [], "coverage": {}})
```

`available_roles` must describe the current native catalog. The returned
`agent_type` is the available domain role or explicit generic fallback;
`requested_agent_type` and `role_fallback` preserve that distinction. Preparing
a dispatch does not launch an agent. Exact argv tokens `{output_dir}`,
`{run_dir}`, and `{dispatch_id}` are expanded and frozen at preparation.
All local imported adapter modules must be listed in `code`; the driver does
not discover arbitrary Python imports. Runtime code, role registry, shared
protocol, artifact contracts, assigned skill, contract and executable are
automatically hashed as dispatch dependencies.

Prepare the independent reviewer with `kind="review"`, the same required
context fields, and current capabilities. Bind its distinct actual native ID.
Call `review(dispatch_id, receipt)` with:

```json
{
  "candidate_hash": "exact value from review dispatch",
  "producer_execution_id": "exact actual producer ID",
  "reviewer_execution_id": "exact bound reviewer ID",
  "verdict": "PASS",
  "summary": "Concrete findings",
  "checks": ["Checks actually performed"],
  "evidence": ["absolute report path inside reviewer owned_output_dir"],
  "findings": []
}
```

Verdicts are `PASS`, `REVISE`, or `BLOCK`. Unresolved high, critical, or blocker
findings prevent PASS. Failed attempts and reviews remain in history. Output
and evidence paths must be concrete files within their assigned directories;
native binding evidence must be a nonempty file under the run root. Raw source
paths may be outside the run root and are read as inputs.

Stages run in order: contract, collection, curation, preprocessing, features,
training-preflight, training, development-evaluation, improvement,
final-fit, terminal-evaluation, release. Training-preflight is an independent accepted
review of the proposed search before training can execute. Scientific adapters
and reviewers must supply substantive checks; the runtime cannot establish
scientific correctness from a role label or a PASS string.

Producer dispatches for training-preflight and training require the same
`pipeline_identity` object: `inputs` and `code` are nonempty path lists,
`config` is a path list, and `parameters` is an explicit object containing
search and selection options. Include both entrypoint files and their local
imports in `code`. The driver snapshots all files and requires exact identity
equality with the accepted preflight. Declared inputs/code/config must be
members of their corresponding identity lists. This binds the reviewed
pipeline despite different preflight and training entrypoints; reviewers must
verify that parameters describe the actual commands and implementation.

Improvement submission adds `decision` and `reason`. `CONTINUE` requires
`restart_from`; after acceptance call `advance_improvement()` to invalidate
that stage and all descendants. `STOP` requires `model_lock`, a list of already
accepted feature/training output paths (the immutable final selection recipe and
configuration, before final fitting). Reviewed STOP advances to `final-fit`,
which uses the training producer and reviewer assignments. Its dispatch
automatically includes the reviewed `recipe_lock` as hashed dependencies and
rejects declared sealed inputs. Submit its fitted models and final-fit evidence
as outputs, then obtain a separate independent PASS. No `reviewed` boolean in an
adapter input can replace this runtime review.

Terminal evaluation requires accepted STOP, an independently accepted final-fit
stage, and unchanged recipe and fitted-model artifacts. The fitted models are
bound through final-fit submission hashes and the downstream acceptance tokens.
The training adapter's STOP lock object (`decision`, `selection` path/hash, and
per-task candidate IDs) is scientific input; its referenced selection/config
artifacts must be in the runtime's reviewed `model_lock` path list. Accepted preprocessing
automatically seals its terminal artifact. Additional holdout files can be registered at init or
using `seal_holdout(paths)` before training. They cannot be declared development
inputs. Preparing the terminal producer permanently reserves possible exposure, even if
evaluation fails; it cannot be reset or retried within this run.

`status()` revalidates transitive artifacts for resume. Changes invalidate the
earliest affected stage and descendants. Use `invalidate(stage, reason)` to
restart failed work; use `replace_contract(path, reason)` for explicit contract
revision before either terminal or external exposure. Either reservation freezes
development, including already prepared or bound producer/reviewer dispatches,
contract replacement and improvement `CONTINUE`. Invalidation cannot erase either
exposure flag or its reservation history. Adaptation requires a new research version
that retains the prior run's exposure history and cohort exposure ledger; previously
reserved or observed data must not be relabelled untouched.

Blocking `execute()` and durable jobs share owner-held process-tree supervision.
Owned process-group descendants are stopped before execution logs are hashed or terminal status and
timeout receipts are recorded, including when the direct child exits normally.
POSIX execution requires `psutil` and unreaped-leader process-group ownership;
Windows execution uses a kernel Job Object. This is not a sandbox or a guarantee
about processes deliberately escaping the owned session. If quiescence cannot
be verified, execution fails without asserting output/log hash stability.
Prefer the durable job API below for long-running commands: blocking `execute()`
has no reconnect endpoint for a lost caller.

Opening an older state without final-fit inserts the pending gate and invalidates
terminal/release acceptance. Existing terminal exposure remains recorded; an
exposed older run cannot silently gain a fresh terminal attempt.

Execution uses frozen argv, `shell=False`, `DEVNULL` stdin, hidden Windows child
processes and recorded logs/exit codes. The dispatch's `owned_output_dir` is the
execution working directory and the base for explicit relative command operands
and their discovered dependencies. External producer execution is also supported by
omitting `command` and submitting its output manifest. These gates govern this
driver, not arbitrary OS access. Native IDs and tool-response files are
leader-attested provenance, not authentication. Independent scientific review,
source read-only discipline, and holdout isolation remain responsibilities of
the orchestrator and execution environment.

Candidate-v2 curation uses a full-contract acceptance adapter. After the
curation guard validates its content and bound files, the runtime also requires
the adapter to match this run's current accepted contract: exact contract hash,
producer and reviewer executions, upstream submission/review token, candidate
file among accepted contract outputs, and review evidence among that stage's
accepted evidence files. Copying the same bytes to an unrelated path does not
establish stage membership. These checks run on curation PASS and subsequent
reuse through the existing curation gate. They strengthen provenance linkage;
they do not authenticate native execution or replace scientific review.
On curation PASS, the runtime also persists the guard-discovered file bindings
in the review. Status/resume rehashes those dependencies and marks curation and
its descendants stale if one changes, without repeating native interpretation.
Downstream reuse of the exact accepted submission also verifies these complete
bindings instead of repeating native parsing. New or revised curation candidates
still require full validation before PASS; legacy receipts without these bindings
use the full validation path.

## Accepted preprocessing custody

Preprocessing PASS requires exactly one `preprocessing_manifest.json` among the
producer outputs. Its `input` must be the exact path/hash of an accepted curation
output and a bound preprocessing input; its `contract` must match the run.
Generated development, terminal, fold, membership, mapping, exposure and pipeline
bindings must resolve inside that producer's owned directory and match their
hashes. External source input must have been bound at dispatch; the external copy
must be byte-identical. Producer-supplied sensitivity labels are not sufficient:
`custodian_only` must be exactly the curated source, sealed terminal, combined
grouping mapping, and all four external input/artifact/exposure/exclusion bindings
when present. Every referenced dependency is frozen in the independent review.

PASS registers flat `{path, sha256, size}` snapshots in `custodian_only` and
separately in `external_inputs`. Only the terminal enters `sealed_inputs`.
Custody history is monotonic even after invalidation; old sensitive paths and
hashes remain prohibited. Modeling producers and reviewers cannot receive these
files as inputs, code, config, pipeline identity, copied bytes, JSON artifact
references, or explicit command operands, including relative paths from the
dispatch's execution directory. Referenced files are bound from that same directory
and revalidated before execution. The accepted preprocessing manifest
itself is permissible metadata, not authorization to open its protected targets.
`development` and `development_grouping_mapping` remain permissible. These are
driver gates, not OS access controls or a detector for arbitrarily transformed
leaked data; custodian permissions and independent scientific review remain required.

## Durable jobs

Bind an actual native producer execution first, exactly as for `execute()`. A job
cannot replace native binding, preflight, reviewed STOP, final-fit, or independent
review. A dispatch has at most one durable job and cannot also use `execute()`.

```python
job = rt.prepare_job(d["dispatch_id"], backend="local")
rt.submit_job(job["job_id"])
# Reopen Runtime(run_dir) in a later process; no in-memory Popen is needed.
progress = Runtime(run_dir).status_job(job["job_id"])
# Only after progress["status"] == "completed":
candidate = rt.collect_job(job["job_id"], producer_manifest)
# Prepare/bind a separate native reviewer and call review() as usual.
```

States are `prepared`, `queued`, `running`, `completed`, `failed`, and `cancelled`.
They describe execution, not scientific acceptance. Request, frozen argv, native
evidence, code and input hashes are persisted under `jobs/<job_id>/request.json`.
`stdout.log`, `stderr.log`, `worker.log`, and `progress.json` expose progress without
polling a PID. The detached supervisor writes a content-bound process receipt and
output inventory after the child exits and rechecks input/code hashes. Collection
rejects outputs changed since completion and persists an immutable collection
receipt before submitting ordinary producer evidence. Resume revalidates these
receipts and output dependencies. Interrupted submissions must be reconciled with
`status_job`, never blindly resubmitted.

`cancel_job(job_id)` contacts the local loopback supervisor using the persisted
request hash and an unguessable token. The supervisor terminates only its owned
child/process group (a kernel Job Object on Windows); clients never kill a saved
PID. A missing or mismatched endpoint produces an actionable reconnect error,
not a PID-kill fallback. Cancellation is asynchronous: poll until `cancelled`.
Cancellation/status remain available for an invalidated dispatch so its old job
can be stopped safely; invalidated jobs cannot collect evidence.

For Slurm, use `backend="slurm"` and explicitly declare:

```json
{"bindings": [{"path": "same absolute file path", "sha256": "actual hash"}],
 "run_dir": "same absolute run directory",
 "output_dir": "same absolute dispatch-owned output directory"}
```

`bindings` must exactly cover the dispatch dependencies, native evidence,
`qsar_jobs.py`, the supervisor Python executable, and its `pyvenv.cfg` when
present (deduplicated by resolved path). The launch command preserves the
absolute venv interpreter path rather than replacing a symlink with the base
interpreter; the resolved executable is independently hash-bound.
Both submission and compute hosts must see the **same content at those exact
absolute paths**, including executables and local checkpoints. The worker
rehashes them on the compute host. This is shared-filesystem execution only:
there is no portable relocation, manifest rewriting, remote copying, or fake
bundle resolver. The `resources` object permits `partition`, `account`, `qos`,
`time`, `mem`, `cpus-per-task`, `nodes`, `gres`, and `constraint`. A shell-quoted
submission script invokes the same supervisor, not the scientific command via
an unbound shell string.

Slurm uses real `sbatch --parsable`, exact-name `sacct` allocation accounting,
and name-filtered `scancel`. Missing clients, scheduler errors, ambiguous records
and accounting lag are reported explicitly. A response lost after submission is
reconciled by its unique request-bound job name. No scheduler response is invented.
Scheduler `COMPLETED` alone cannot collect: a matching successful worker receipt
and unchanged outputs are mandatory. Preflight collection verifies completed
synthetic evidence and coverage. Search collection requires `checkpoint.json`,
`search_coverage.json`, `final_selection.json`, `trial_ledger.json`,
`fit_evidence.json`, `attempt_history.json`, `selection_procedure_oof.csv`,
`selection_procedure_oof_manifest.json`, all model artifacts and the
fit-index registry; it invokes the actual trainer validators without refitting
and cross-checks exact task/scope/cell/trial coverage. Final-fit collection checks
completed task/model/fit-row and selection/STOP-lock bindings, including persisted
model audit IDs, classification thresholds and domain training identities. Numerical validation
requires the trainer dependencies and matching recorded environment on the
collection host. None of these checks substitutes for an independent native PASS.
The ordinary producer manifest's `coverage` must equal the verified
`search_coverage.json` for search, or the completed `preflight.json`/`final_fit.json`
report for those modes; a caller's independently labelled coverage is rejected.

The same operations are exposed by `python qsar_jobs.py --run-dir RUN
prepare --dispatch-id ID --backend local`, then `submit`, `status`, `cancel`, or
`collect --job-id ID` (`collect` also needs `--manifest PATH`). Slurm preparation
accepts `--shared-filesystem PATH` and `--resources PATH` JSON files.

## Separate external evaluation exposure

Use `prepare_dispatch("terminal-evaluation", evaluation_mode="external", ...)`
and the same mode for its independent review. This requires accepted external
preprocessing plus reviewed STOP and final-fit. It uses a separate
`external_evaluation` entry and permanently reserves `external_exposed`/
`external_dispatch_id`; it does not reserve, reset or satisfy the terminal gate.
Only the authorized external bindings, not terminal/source/combined mapping,
are exposed in this mode. Default terminal mode exposes only its authorized
terminal bindings, not the external cohort. Each mode is single-attempt within
the run even if execution fails. Preserve the external cohort's declared prior
exposure; a new runtime reservation does not make previously seen data untouched.
External reservation freezes development immediately, even before terminal access;
it does not prevent the independent external review, terminal evaluation or release.
