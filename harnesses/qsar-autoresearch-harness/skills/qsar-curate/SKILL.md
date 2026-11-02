---
name: qsar-curate
description: "Curate acquired QSAR measurements into traceable endpoint-specific tables, resolving structures, units, qualifiers and replicate policies. Use before splitting; not source acquisition, model tuning, or independent acceptance."
---

# QSAR Data Curation

Produce all scientifically eligible records under the accepted research contract, with an auditable reason for every disposition. This is the producer skill for stage `curation`, normally assigned to an `executor`.

## Load and inputs

Read the shared [protocol](../qsar-autoresearch/SKILL.md) and [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md). Load only relevant source adapters, chemistry rules and endpoint policies.

Require an accepted collection package and its current independent receipt, the versioned research contract, original source records, identity support and owned output paths. Check their hashes before reuse. Missing or stale collection approval cannot be replaced by this producer's self-check.

## Owned outputs

- Curated tables for every declared task/species/route/subtype stratum, including explicit dispositions for untrainable strata.
- `curation_manifest.json`: policy/tool versions, source bindings, row/compound counts and unresolved issues.
- Row-disposition ledger linking every original record to retained, excluded, quarantined or aggregated output membership.
- Identity ledger retaining original identifiers/structures, normalization steps, match evidence and ambiguity.
- Replicate/conflict ledger with conditions, conflicting values or labels, applied policy and source locators.
- Runnable curation code/configuration and inspection evidence; write only dispatched paths.

## Procedure

1. Verify source schemas and native endpoint meaning against documentation. Map species, route, subtype, assay and measurement conditions before combining records; unknowns remain explicit.
2. Inventory missing or conflicting structures. Resolve through the available identifiers under the accepted matching policy, preserving query/response evidence and ambiguous candidates; a name or CAS match alone is not automatic identity proof.
3. Apply versioned parsing, salt/parent, tautomer, charge, stereochemistry, mixture and metal rules. Retain original structures and each transformed identity so collisions introduced by normalization can be examined.
4. Normalize units only with a defensible conversion and required molecular identity/context. Preserve original values and modifiers; censored limits do not become exact targets merely because a numeric value exists.
5. Build task-specific target definitions. Direct binding, agonism, antagonism and proxy activities are separate unless the contract justifies their relationship. Do not interpret an assay's negative label as universal inactivity.
6. Detect exact records, cross-source lineage duplicates, normalized-identity matches and replicate measurements. Apply condition-aware aggregation or conflict rules from the contract; preserve all supporting rows and every rejected alternative.
7. Reconcile each stratum's record counts, distinct identities, target missingness and classification balance. Explain large losses or label shifts by named dispositions, not only an aggregate final count.
8. Export every eligible record. Prove raw-record accounting with mutually exclusive terminal dispositions and separate many-to-one aggregation links; report all critical unresolved conflicts for review.

For narrative sources, parsing failure is not an eligibility decision. Use the [source-bound adjudication procedure](../qsar-autoresearch-harness/references/narrative-adjudication.md) when a field contains multiple observations, contextual chemical attribution, or a point estimate with a confidence interval. Reconcile source fields separately from extracted observations and obtain independent review of residual decisions, including exclusions.


For reviewed SQL-v2 sources, use the [SQL consumer integration](../qsar-autoresearch-harness/references/sql-consumer-integration.md): generate immutable diagnostic outputs and actual consumption first, then obtain separate final source review and finalize. A diagnostic is not an accepted curation package.

## Decision boundaries

For reviewed NPASS, ICE oral, OECD or TOXRIC origin/route questions, follow the
[ordinary-source integration](../qsar-autoresearch-harness/references/ordinary-source-integration.md).
Resolve complete native membership before task/material filtering; preserve
separate holds and blocking issues, and obtain independent source review.

- No arbitrary row cap, label-balancing relabeling or performance-driven exclusion. Later data repairs require a new version and downstream invalidation.
- Do not invent universal salt, metal, majority-label, assay-equivalence or replicate-variance policies from a historical project example.
- Preserve uncertain units, identities, qualifiers and disagreements. Quarantine unresolved critical cases under the declared policy; do not guess which source is correct.
- If the scientific policy is underspecified, return concrete alternatives/evidence to the leader before applying it. Routine implementation choices within an accepted policy remain the producer's responsibility.
- Do not fit models, choose splits using model outcomes, or expose an already sealed terminal benchmark's labels. Stage output roles must preserve that benchmark's custody.

## Completion and handoff

Reviewed HSDB observation metadata uses an inline source-scoped
`narrative_observation_metadata: {path, sha256}` beside `narrative_adjudication`,
with strict curation evidence enabled. The version-1 manifest binds original and
integrated metadata, exact source-span links, scoped decisions, original archive,
and prior source/integration reviews. Its finite seven receipt maps are freshness
dependencies; context pointers and external metadata envelopes are unsupported.
Preserve the complete `observation_metadata` object in curated, disposition and
identity rows, including nulls and legitimate nonemissions. The source-scoped
`observation_metadata_accounting` includes actual membership, output IDs,
references and a bounded count report; report groups are not independent studies.
Metadata never changes targets, record IDs, weights or connectivity split groups.
This wire supports the reviewed pass8 properties only: untyped intervals and other
new metadata fields require a separately reviewed extension, not CI coercion.
Prior source approval does not approve this consumer or unresolved source fields;
obtain fresh independent consumer acceptance before downstream use.

Return versioned artifacts/hashes, full disposition accounting, policy deviations and source-linked anomalies. A distinct [qsar-review](../qsar-review/SKILL.md) agent must reconstruct accounting/identity checks and review scientific semantics from original records before preprocessing proceeds.

The producer may run reproducibility and accounting checks but cannot issue its own independent PASS. A changed target/identity/eligibility policy invalidates dependent splits, features and results; report the earliest affected stage.

## Routing examples

Use: "Recover missing structures, resolve replicate conflicts and prepare separate eligible species/route tables."

Use: "Audit why the binding-negative count collapsed after source harmonization and produce corrected versioned tables."

Near miss: "Download additional assay records" belongs to `qsar-collect`.

Near miss: "Select features that improve CV performance" belongs to `qsar-train`, after curation review.

The current metadata wire supports legacy HSDB field decisions. Synthetic end-to-end checks exercise ordinary identity routing and reviewed schema 2 inorganic/schema 3 organic-B material packets, preserving metadata, row IDs, targets and groups. These mechanics do not approve real sources or materials. Metadata-enabled v2 decisions and pass9 interval/context fields remain unsupported by this wire.

For explicitly activated organic B, follow [material integration](../qsar-autoresearch-harness/references/material-curation-integration.md). Preserve complete mass/multiplicity and native evidence; require source-bound principal review, consistent retained-salt coverage and fresh supplemental bindings.
