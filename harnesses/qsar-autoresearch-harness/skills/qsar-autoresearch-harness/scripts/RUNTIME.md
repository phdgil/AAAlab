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
artifacts must be in the runtime's reviewed `model_lock` path list. Register sealed holdout files at init or
using `seal_holdout(paths)` before training. They cannot be declared development
inputs. Preparing the terminal producer permanently reserves possible exposure, even if
evaluation fails; it cannot be reset or retried within this run.

`status()` revalidates transitive artifacts for resume. Changes invalidate the
earliest affected stage and descendants. Use `invalidate(stage, reason)` to
restart failed work; use `replace_contract(path, reason)` for explicit contract
revision before terminal exposure. After an interrupted running command,
confirm the old process has stopped before invalidating and retrying. The
runtime does not recover or terminate an unknown orphan process tree.

Opening an older state without final-fit inserts the pending gate and invalidates
terminal/release acceptance. Existing terminal exposure remains recorded; an
exposed older run cannot silently gain a fresh terminal attempt.

Execution uses frozen argv, `shell=False`, hidden Windows child processes and
recorded logs/exit codes. External producer execution is also supported by
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
