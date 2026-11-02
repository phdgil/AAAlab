---
name: qsar-autoresearch
description: Define the scientific contract and shared artifact rules for end-to-end QSAR research, from collection and curation through model search and improvement. Use for QSAR pipeline design; delegate execution to the focused stage skills.
---

# QSAR research contract

This is the shared scientific contract, not an instruction to load every stage skill. For coordinated execution use [qsar-autoresearch-harness](../qsar-autoresearch-harness/SKILL.md). Each agent reads this file, [artifact contracts](references/artifact-contracts.md), its assigned skill and relevant run artifacts only.

## Establish the research contract

Before collection or fitting, record the current user objective and versioned decisions:

- Endpoint/task and intended use; species, route, subtype and assay strata; in-vivo or in-vitro selection and any allowed integration.
- Source/query scope, existing raw evidence, source versions, retrieval permissions and unresolved coverage.
- Structure/parent/tautomer/stereochemistry/mixture policy, units, qualifiers, duplicate/replicate conditions, conflicting labels and missing-data policy.
- Eligible features and representation families, model candidates, fold policy, primary metric and comparison baseline, resource/search budget, stopping rules and deployment constraints.
- Development versus terminal evaluation, existing benchmark exposure, required artifacts and independent reviewer assignments.

Use all scientifically eligible records by default. Any exclusion, sampling or narrowed feature/model coverage needs a reason in the contract. Explicit user limits are honored and disclosed; a runtime smoke test cannot satisfy full research completion. More data or features are not automatically better: preserve endpoint compatibility, usable chemistry and fold-local feature selection.

Current project instructions override older decisions. Keep superseded decisions with their dates and scope. Do not convert historical thresholds, metal exclusions, trial counts or preferred algorithms into universal defaults. Distinguish observed data conflict, suspected source error and prediction error.

## Scientific invariants

- Preserve raw sources and row lineage. Reconcile retained/excluded/unresolved records, including subsets that cannot be trained.
- Resolve endpoint meaning before pooling labels; source reputation and identical target names do not establish assay compatibility.
- Freeze grouping and development splits before fitting. All learned preprocessing, feature selection, resampling, calibration and thresholds are training-fold-only.
- The final holdout stays sealed throughout development/search/improvement. An exposed benchmark remains an internal/adaptation benchmark, never an untouched external test by renaming it.
- Search the declared representation/model matrix; account for every required cell and failed trial. No handcrafted descriptor subset may be labeled full RDKit 2D; no unverified substitute may be labeled an authentic embedding.
- Review each stage independently. Producer checks are useful evidence but cannot approve their own output.
- Continue a reviewed improvement within the declared scope/budget, or record a justified STOP. Never force an extra experiment or tune repeatedly against terminal-test results.

## Contract handoff

Output a versioned dataset contract, task/stratum matrix and decision log under the assigned run paths. Pass them to a separate contract reviewer using [qsar-review](../qsar-review/SKILL.md). Do not advance on unresolved critical scientific choices. A read-only specialist returns findings for the leader to persist; it does not fabricate file writes.

## Runtime and package

Check Python and relevant libraries before their stage: pandas/scikit-learn for tabular modeling, openpyxl for Excel, RDKit for chemistry. Validate requested optional models/representations individually. Missing dependencies are operational blockers, not zero-performing experiments; do not silently install or approximate outside the task's authority.

Release summaries include total/train/test counts, classification label balance, source/exclusion and feature coverage, model/search settings, per-row predictions, uncertainty, limitations and the next_experiment_manifest decision. Produce training_summary.html when a human-readable report is requested. Keep comparison-only historical branches distinct.

These skills define methods and handoffs. They do not themselves supply a universal scientific pipeline or a security boundary. Project-specific execution and source-based review remain required. See [maintenance feedback](maintenance/feedback.md) only when revising these methods.

For reusable harness changes from ongoing usage, use the bundled
[qsar-evolve](../qsar-evolve/SKILL.md). Capture private run observations separately
from the generalized maintenance history, and preserve versioned scientific
artifacts and independent review when adopting a repair.

## Routing examples

- Use: define an LD50 regression contract; review whether ER binding sources can be pooled; design complete-data QSAR research.
- Delegate: actual retrieval to qsar-collect; training to qsar-train; stage audit to qsar-review.
- Do not invoke for ordinary CSV conversion or a simple definition of QSAR.

Reviewed organic salts may use the explicitly contracted B principal-component variant: complete material mass for targets, one reviewed principal for predictors, and existing organic parent grouping without prefixes. Source/material acceptance and full current-E coverage remain separate gates; illustrative salts are never a whitelist.
