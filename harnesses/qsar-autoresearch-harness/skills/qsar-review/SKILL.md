---
name: qsar-review
description: Independently audit a QSAR stage against raw evidence and current artifact bindings, returning PASS, REVISE or BLOCK with reproducible checks. Use at scientific handoff gates; not as a producer self-check or candidate repair workflow.
---

# QSAR Independent Review

Review exactly one assigned stage: `contract`, `collection`, `curation`, `preprocessing`, `features`, `training`, `development-evaluation`, `improvement`, `terminal-evaluation` or `release`.
Use a distinct native `verifier` execution, or `critic` for an intervention decision, from every producer whose work is being accepted.
Evaluation is itself a produced artifact: the reviewer of evaluation must differ from its evaluator.

## Load only this stage's context

Read the [shared protocol](../qsar-autoresearch/SKILL.md), [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md), current research contract and the evidence needed for the assigned stage.
Read the producer skill only to establish its promised contract, not to adopt its reasoning as an answer key.
Do not load other reviews as substitutes for original evidence.

## Required inputs and independence

- Assigned stage/run, producer execution identity, candidate output paths/digests, source locators and upstream receipts.
- Relevant original data/documents, code/configuration, execution logs and actual output artifacts.
- Expected scientific scope, acceptance criteria, exposure constraints and declared checks not possible in this runtime.
- Your distinct reviewer execution identity and assigned review-output path, or a native return channel if read-only.

If source evidence or independent execution is unavailable, return BLOCK or explicitly limited findings; never relabel the producer's self-check as independent review.
Do not expose sealed terminal labels during upstream reviews. An authorized terminal evaluator checks any necessary sealed-label integrity without returning outcomes to model selection.

## Independent method by stage

| Stage | Reconstruct from evidence, rather than trusting the producer summary |
|---|---|
| contract | Compare the scope/candidate matrix to current user requirements and dated decisions; identify hidden sampling, pooled incompatible tasks or missing source/feature coverage. |
| collection | Reconcile native query/pagination/count evidence and raw bytes; inspect original assay definitions and source exclusions. A returned file does not prove query completeness or endpoint relevance. |
| curation | Recalculate full row-disposition accounting and deterministic unit/identity checks; trace salt/parent/tautomer/stereo transformations and conflicting replicates to source records. Examine every critical anomaly and state semantic sampling coverage. |
| preprocessing | Rebuild identity/group overlap and row-axis checks; inspect actual split creation and transform specifications. Verify development/terminal roles and prevent pre-fit use of sealed outcomes. |
| features | Independently calculate a declared coverage sample plus every discrepant/high-risk case; reconcile all generated columns and excluded families against the pinned implementation. Check target-derived fields and label-dependent feature construction. |
| training | Before broad HPO, audit actual pipeline fit indices and isolated leakage/holdout-perturbation fixtures. After execution, reconcile all candidate cells/trials, feature-count search, nested fold boundaries and model-selection provenance. |
| development-evaluation | Independently recompute all released metrics from row-bound OOF predictions, verify baselines/thresholds/uncertainty and matched comparison cohorts; inspect whether adaptive outer-CV use is disclosed. |
| improvement | Test whether development evidence supports the proposed cause/intervention, budget and comparison; verify invalidation and stop logic. Ensure terminal outcomes are not driving supposedly untouched evaluation. |
| terminal-evaluation | Verify reviewed development STOP and pre-exposure model/threshold lock; recompute all terminal metrics, denominators and uncertainty from predictions, and reconcile the access/exposure ledger. |
| release | Reconcile every current stage receipt, artifact digest, coverage/status claim and inference schema; reproduce a bounded inference/metric check. Confirm reports disclose missing sources, failed searches and exposed benchmarks. |

## Execute and decide

1. Establish candidate/source versions before checking them. A matching hash proves identity, not scientific correctness.
2. Write independent checks or calculations from the documented source contract. You may inspect producer code, but do not import its validation function as the sole oracle.
3. Apply deterministic integrity/accounting checks to the full relevant artifact. For semantic or expensive chemical inspection, declare the selection strategy, coverage and unexamined limits; known critical anomalies cannot be hidden by sampling.
4. Preserve exact discrepancy examples: source locator/value, candidate locator/value, reproducible command, severity and consequence. Distinguish verified defects from hypotheses and unavailable proof.
5. Use bounded independent refits or inference reproduction where needed to establish model execution. Do not repeat the entire expensive search merely to create the appearance of independence.
6. Return PASS only for the declared stage and inspected scope when its acceptance criteria are satisfied. REVISE identifies fixable candidate defects; BLOCK identifies unavailable prerequisites, unresolved scientific contracts or inability to verify a critical criterion.
7. After repair, verify the changed artifacts and their invalidated dependents. Preserve the initial verdict and failed version; a later recovery does not convert the first handoff into a pass.

For curation, matching counts and hashes are insufficient. Reconstruct which
native observations must be retained and their normalized targets, identities
and group assignments. Check that plausible but incorrect exclusion reasons,
removed eligible siblings, altered targets and forged replicate flags reject
even after submitted counts and hashes are updated. A shared producer/guard
calculation can enforce reproducibility, but it is not the independent scientific
oracle; verify source semantics and representative calculations separately.

## Owned output and boundaries

For ordinary origin/route adjudication, inspect the
[integrated curation and reuse checks](../qsar-autoresearch-harness/references/ordinary-source-integration.md)
alongside raw source evidence. Verify route recovery precedes material membership,
holds emit nothing, eligible siblings survive, and actual accepted-contract and
transitive evidence bindings reach runtime reuse and preparation.

Produce only a review receipt and independent evidence in assigned review paths. Do not modify source data, candidate artifacts, model choices or producer code while reviewing them.
The receipt includes producer/reviewer execution IDs, native returned-review locator, writer identity, input/output/code/environment digests, checks/commands/results, coverage, findings and verdict under the shared contract.
A leader persisting a read-only review does not become the scientific reviewer. Execution IDs locate evidence; they are not permission credentials.
Do not invent authority, numeric results, source contradictions or review completion when evidence is absent.

## Completion and routing examples

For inline HSDB observation metadata, independently reconstruct the source-span
join, packet-derived scope, native citations/report groups and complete projected
objects in all three curation ledgers. Inspect `observation_metadata_accounting`
and its bounded count report against actual dispositions/task outputs. Verify the
seven explicitly named source/integration receipt maps, original/copy equality,
prior integration review and optional erratum; leaves are hash-checked without
recursive context discovery. Rehashed output tampering and stale context must fail
curation validation, runtime reuse and preparation. Check nulls, same-dose siblings,
CI/follow-up/vehicle distinctions and unchanged targets/weights/connectivity groups.
The future consumer receipt remains outside its candidate dependency graph. Review
identities and hashes are evidence locators, not authentication of native returns.
Pass8 consumption does not imply pass9 metadata compatibility, complete HSDB source
acceptance, or authority to activate training.

Return the receipt and precise affected stages to the orchestrator; producer fixes require a new candidate version and independent re-review.
- Use: "Independently review the curated QSAR tables against the original source records."
- Use: "Audit the executed feature-count search and OOF metrics before accepting training."
- Do not use: "Generate and approve the model yourself"; separate production from review.
- Do not use: "Explain this metric"; answer directly without a scientific acceptance gate.

For reviewed SQL-v2 sources, use the [SQL consumer integration](../qsar-autoresearch-harness/references/sql-consumer-integration.md): generate immutable diagnostic outputs and actual consumption first, then obtain separate final source review and finalize. A diagnostic is not an accepted curation package.

The current metadata wire supports legacy HSDB field decisions. Synthetic end-to-end checks exercise ordinary identity routing and reviewed schema 2 inorganic/schema 3 organic-B material packets, preserving metadata, row IDs, targets and groups. These mechanics do not approve real sources or materials. Metadata-enabled v2 decisions and pass9 interval/context fields remain unsupported by this wire.

For organic B, independently reconstruct complete formula-unit mass, exact principal multiplicity, unprefixed parent grouping and full current-E/retained-salt coverage. Exercise actual ordinary/SQL consumers and supplemental freshness; synthetic fixture passes do not certify real materials or sources.
