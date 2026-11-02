---
name: qsar-improve
description: Diagnose QSAR development results and prepare a justified, budgeted intervention or stop decision, including data repairs and dependent-artifact invalidation. Use between reviewed development cycles, not to tune against final-test results.
---

# QSAR Improvement

Own the `improvement` decision and handoff, normally as an `executor` producer with a separate `critic` review.
The orchestrator dispatches accepted work to the responsible stage; this skill does not silently launch an unbounded search.

## Load only this stage's context

Read the [shared protocol](../qsar-autoresearch/SKILL.md), [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md), current research contract, development evaluation and unresolved review findings.
Read earlier raw/source evidence only for the diagnosis being investigated.

## Required inputs

- Accepted current data, splits, features, search coverage and development-evaluation receipts.
- OOF predictions/errors with source, task, group and domain provenance permitted for development analysis.
- Previous interventions, including failed/rejected attempts, remaining resource budget and declared stop criteria.
- Exposure ledger distinguishing development, historically exposed benchmarks and any sealed terminal cohort.
- Current dependency map and assigned decision-artifact path.

Do not substitute terminal-test diagnostics for missing development evidence. If such results have already influenced the task, record the exposure and constrain the resulting claim.

## Owned outputs

Write a versioned `next_experiment_manifest` and supporting diagnostic evidence at the assigned paths.
The manifest records decision, failure class, source evidence, hypothesis, proposed change, expected comparison, affected stages/artifacts, budget, stopping rule and rejected alternatives.
A continuation decision names the earliest stage to re-enter; a stop decision records why further work is unjustified or unavailable.

## Procedure

1. Reconcile what actually ran against the approved experiment matrix. Missing data sources, failed feature generation, exhausted searches or corrupt predictions are execution/coverage problems, not evidence that a model family is scientifically ineffective.
2. Separate plausible causes using development evidence: target/assay incompatibility, identity or unit errors, contradictory studies, missing source coverage, domain shift, class imbalance, insufficient representation, search limitations or genuine irreducible uncertainty.
3. Inspect complete conflict/error summaries and representative raw cases, including all known critical anomalies. Source disagreement alone does not determine which source is correct; high residuals alone do not justify deleting observations.
4. Prefer the intervention addressing the supported cause. Recollection or curation repair may be required before another training run; model tuning cannot repair an incompatible endpoint.
5. State a falsifiable expected change and comparison protocol. Keep data/folds fixed for a model-only comparison. When curation changes the eligible cohort, version the data and report cohort differences instead of attributing score changes solely to the algorithm.
6. Specify affected prerequisites and invalidate their dependents. Preserve old runs; do not reuse stale split, feature, model, metric or review artifacts after a relevant input/configuration change.
7. Check resource and deployment constraints before adding a representation or algorithm. Record scientific inapplicability separately from missing dependencies or insufficient compute.
8. Emit the next justified intervention within the accepted research scope, or STOP. Budget exhaustion, missing evidence and operational failure must remain explicit incomplete/blocked outcomes rather than successful scientific conclusions.
9. Hand the manifest to an independent `qsar-review` execution using the intervention-review mode. After PASS, the orchestrator executes the specified stages and obtains their new independent reviews; the workflow does not end merely because a proposal was written.
10. Continue using development evidence until the reviewed stopping rule is met. Then hand off a locked candidate for terminal evaluation; do not repeatedly open the final holdout during improvement.

## Boundaries

- Do not remove difficult compounds, relabel conflicts or change the metric solely to improve a score.
- Do not interpret a small development gain without its uncertainty, cohort consistency and applicable domain.
- Do not promise a performance threshold or require a pointless extra experiment when a reviewed STOP is justified.
- Do not expand the authorized scientific objective or resource budget implicitly. Report the specific unmet requirement if the current contract cannot support the needed work.
- Do not turn historic project choices into global policies; latest applicable user decisions and the versioned contract govern.

## Completion and handoff

Return the decision artifact, evidence locators, exact changed-policy proposal, invalidation list and review requirements.
A producer's proposal is not an accepted intervention. Review identity and exact artifact binding follow the shared contract.
Report the difference between pipeline completion, scientific acceptance, no justified improvement and incomplete execution.

## Routing examples

- Use: "Diagnose these reviewed OOF errors and decide whether to recurate or change the model."
- Use: "Review the remaining budget and propose the next justified development cycle or stop."
- Do not use: "Keep tuning until the final test reaches the target score"; preserve the exposure and return a scientifically valid evaluation boundary.
- Do not use: "Run this already accepted training configuration"; use `qsar-train`.
- Route reusable harness-code or instruction repairs from recurring run feedback to [qsar-evolve](../qsar-evolve/SKILL.md); maintain the current scientific run's version and review gates while that maintenance is performed separately.
