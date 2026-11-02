# Shared QSAR artifact and handoff contracts

Read with the assigned stage skill. Concrete paths are supplied by the coordinator; names below are semantic artifact types, not permission to search every research directory.

## Producer handoff

Every handoff contains schema_version, run_id, stage, iteration, producer_execution_id, contract_version, code/environment/config identifiers, exact input/output paths and SHA-256 hashes, performed checks, commands, row/feature/fold axes where applicable, limitations and unresolved findings. Include requested coverage versus completed coverage. A handoff is not approval.

Raw records retain source/release/record identity, native endpoint/unit/qualifier and structure provenance. Derived rows retain transformation and exclusion reasons. Aggregate counts reconcile to row-level records; no unexplained losses. Scientific-inapplicable and operational-failed are separate statuses.

## Independent review receipt

A receipt records:

- run_id, stage, iteration and contract_version;
- producer_execution_id and distinct reviewer_execution_id;
- native returned-review evidence locator and receipt_writer identity;
- reviewed input/output/code/config hashes, source locators and exact commands/check outputs;
- independent reconstruction scope, semantic sampling and untested limits;
- severity-ranked findings, required repairs and PASS, REVISE or BLOCK.

Execution IDs are audit locators, not proof of permissions or independence by themselves. The coordinator verifies the actual separate dispatch/returned review. A leader recording a review is not automatically its reviewer. Same native role may be used in separate executions; the producer may not approve its own stage. Read-only reviewers return receipts for the leader to persist and never claim nonexistent writes.

PASS applies only to the reviewed stage scope and exact artifacts, not overall model validity. A missing reviewer, unresolved critical finding, missing evidence or stale hash cannot satisfy PASS. No self-authored PASS placeholder.

## Dispatch envelope and minimal context

Provide the current objective/non-goals, stage/mode/iteration, installed native agent role, exact assigned SKILL.md path, shared contract paths, relevant versioned run inputs, producer/reviewer identity when reviewing, owned output paths, permitted operations, upstream accepted receipts, acceptance criteria and stop condition. Require acknowledgement of the skill and inputs actually read. Use a fresh native context (`fork_turns: "none"` where supported) populated with this explicit envelope. A short task prompt does not prevent inherited conversation leakage. If isolation is unavailable, report the boundary and do not claim blind independence or holdout secrecy. Do not paste the entire session or every skill. Historical transcripts are not current instructions.

Producer owns only its declared output/code paths; reviewer reads candidate outputs and owns separate review outputs (or returns evidence if read-only). State that other agents share the workspace. Source acquisition and project side effects remain within existing user authorization; role text does not grant additional permissions.

## Gate and invalidation rules

Stages: contract -> collection -> curation -> preprocessing -> features -> training-preflight -> training -> development-evaluation -> improvement. An accepted improvement returns to the earliest changed stage; STOP with a locked selection permits final-fit -> terminal-evaluation -> release. Final-fit uses only development data and requires its own independent PASS before terminal dispatch. Existing valid stages may be reused only with matching scope and dependency hashes.

The coordinator checks independent PASS before handing dependent work to the next agent. Training starts only after accepted data/split/feature contracts plus a reviewed execution preflight of the actual fold-local pipeline. This small preflight is not the scientific dataset used to claim completion.

Source/contract changes invalidate dependent curated rows, splits, features, searches/models, predictions and reports. Feature/model changes invalidate downstream results and reviews. Preserve superseded runs. Resume from the earliest invalid stage; never reuse a stale PASS merely because a filename matches.

An isolated data custodian may use labels before modeling solely to implement a prespecified stratified split, returning membership and permitted summaries rather than terminal labels. That execution must not later act as a model-selection agent.

No training/improvement agent receives terminal labels or terminal outcomes. An isolated evaluator may inspect sealed labels after STOP/model lock. Identity-only overlap checks are allowed earlier. If terminal evidence is used to redesign, record exposure and issue a new research version with appropriately limited claims.

## Completion states

Use complete, limited, budget_exhausted, blocked or failed with evidence. Scientific acceptance is a separate explicit judgment against the prespecified criteria. An exhausted search is not 'validated'; operational failure is not a scientific null. A reviewed STOP may reflect adequate results, no justified intervention, budget or unavailable evidence, with different status labels.

The routing validator checks packaged assignments only. The local qsar_runtime.py driver enforces these artifact/gate transitions with durable state, including an explicit reviewed training-preflight pipeline_identity. It cannot authenticate native tool results beyond leader-attested evidence, infer undeclared import dependencies, or sandbox arbitrary external commands. Scientific acceptance still requires an actual independent source-based review.
