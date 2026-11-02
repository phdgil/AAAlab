---
name: qsar-evolve
description: Improve the reusable QSAR harness from run evidence and user feedback; capture lessons, repair code or instructions, validate changes and maintain history. Use for recurring workflow failures or requests to evolve the harness during continued use. Route model-development interventions to qsar-improve.
---

# Evolve the QSAR harness during use

Turn observed workflow problems into small, verified improvements that later runs
can reuse. This skill is bundled with the QSAR pack and needs no separately
installed general `evolve` skill. It is a maintenance workflow outside the
scientific stage sequence; it does not grant scientific acceptance.

Read the [shared scientific protocol](../qsar-autoresearch/SKILL.md), the
affected skill or runtime code, and relevant entries in the existing
[maintenance history](../qsar-autoresearch/maintenance/feedback.md). Load only
the run evidence needed for the issue. For development-error diagnosis and the
next model/data intervention, use [qsar-improve](../qsar-improve/SKILL.md).

## Capture useful observations without interrupting research

When a run reveals a recurring failure, user correction, missing capability or
costly workaround, record a compact observation at a stage checkpoint. Use the
run's existing maintenance log or `<run_dir>/maintenance/feedback.md` within its
owned output area. Reuse the same record when work resumes; group repeated
observations under the same issue instead of copying entire conversations.

Record the date, run identifier, harness commit (or relevant file hashes), stage,
expected versus observed behavior, feedback, and exact local evidence locators.
Distinguish a reproduced failure from a hypothesis, missing dependency, explicit
diagnostic limit or task-specific scientific choice. Record what was actually
executed and its coverage before suggesting a general rule.

Private transcripts, molecular records, predictions and local paths remain in
the run's private evidence area. Capture does not require publishing them or
adding a maintenance gate to scientific stage acceptance. Continue safe research
under the pinned harness version; block affected dependent work when an observed
defect makes its evidence unreliable.

## Improve a reproducible cause

1. Resolve the editable source checkout and compare the run's harness version
   with the current source and maintenance history. An installed skills directory
   may be a different snapshot. Locate these from available context; ask for a
   location only when the intended target cannot be determined.
2. Use a separate maintenance context, worktree or bounded native executor for
   editing, package validation and Git work. During active research, the
   coordinator sends only the issue, required evidence, source version, owned
   paths and success criteria. If isolation is unavailable, retain the observation
   and perform the edit after research has checkpointed. Never edit files still
   consumed by a running process or replace its hashed inputs in place.
3. Reproduce the problem with the smallest suitable synthetic fixture or a
   permitted local replay. State one falsifiable expected improvement. Compare
   against the same inputs, folds and budget when claiming an experiment gain;
   describe cohort differences when data policies change. Missing evidence stays
   a hypothesis, not a verified repair.
4. Change the responsible runtime, skill, role instruction or routing rule. Keep
   the reason beside the rule. Prefer a general cause over a dataset-specific
   threshold, preferred algorithm or extra instruction for every past incident.
   Check earlier decisions before reversing one; reconcile their scopes or ask
   about a genuine unresolved conflict. Preserve unrelated user work and defaults.
5. Validate the affected behavior. For code repairs, add or extend a regression
   that fails before the repair and passes after it. For skill changes, check
   frontmatter, relative links, at least three intended requests and three near
   misses. Run the package/isolated-install checks and relevant existing suites
   from a source checkout. Do not claim unrun checks when only an installed copy
   is available. Use an independent reviewer for changes to scientific decisions,
   evidence gates, resume behavior or evaluation boundaries.

## Record and adopt the change

Append the generalized lesson to the shared
[maintenance history](../qsar-autoresearch/maintenance/feedback.md); preserve its
existing entries. Record the observation, reproducible cause, changed files,
validation and review summaries or repository-relative test links, limitations,
and expected effect on the next run. Omit private evidence locators from this
distributable record.
Keep rejected or deferred proposals and their reasons in the private issue log.
Use statuses such as observed, reproduced, validated, adopted or deferred so
capturing a suggestion cannot be mistaken for applying it.

Default to adoption in the next run after validation. Record the new commit or
file hashes and installation target, then verify the installed skill files and
actual runtime discovery when installation is requested. Report source-only,
installed, published and merged states separately. Existing task authorization
governs installation and Git publication; repeated use of the harness alone is
not permission to overwrite an installation, push or merge.

If a repair must enter a checkpointed run, the coordinator versions the relevant
inputs/configuration, invalidates the earliest affected stage and its descendants,
and obtains fresh independent reviews under the
[runtime contract](../qsar-autoresearch-harness/scripts/RUNTIME.md). Preserve old
artifacts, failed attempts and receipts; a new maintenance note cannot renew an
old acceptance. Resume only after affected work is safe to consume the new version.

Terminal labels, predictions and outcome-driven tuning suggestions must not enter
the development or maintenance repair context for model selection. A failure
involving terminal evaluation may be repaired with sanitized execution evidence
or synthetic fixtures. If terminal outcomes already influenced redesign, record
exposure and require a new research version and appropriate fresh evaluation;
do not reset a terminal reservation or relabel the exposed test as untouched.

Finish with the verified change, evidence, rejected/deferred items, adoption state
and next-run expectation. Stop when the bounded issue is resolved or the missing
evidence is identified; do not start an endless self-editing or retraining loop.
Engineering correctness and predictive improvement are separate claims.

## Routing examples

- Use: "Repeated training resumes lose failure costs; improve the harness from these runs."
- Use: "Capture my curation-workflow feedback and make a reusable fix for the next run."
- Use: "Review the accumulated QSAR run lessons and update the stage instructions."
- Route to qsar-improve: "Use these reviewed OOF errors to choose the next model intervention."
- Route to qsar-train: "Run this accepted search configuration."
- Explain directly: "What does cross-validation mean?"
