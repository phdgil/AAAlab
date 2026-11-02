---
name: qsar-collect
description: "Collect and reconcile source records for a defined QSAR endpoint and source scope. Use for new acquisition, coverage gaps, or task-driven recollection; not molecular curation, model training, or independent approval."
---

# QSAR Source Collection

Produce a source-complete acquisition package within the declared research scope. This is the producer skill for stage `collection`, normally assigned to an `executor`; it does not register a native agent or grant network permissions.

## Load and inputs

Read the shared [protocol](../qsar-autoresearch/SKILL.md) and [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md), then only this stage's source adapters and evidence.

Require the versioned research contract, source scope, endpoint/task definitions, approved acquisition methods, run identity and owned output paths. Read prior source receipts when resuming. If a contract decision is missing, return the precise question and source evidence to the leader; do not silently choose a narrower task.

## Owned outputs

Write only the dispatched collection paths:

- Immutable native downloads or API response snapshots with content hashes.
- `source_registry.json`: source/release, query, endpoint relevance, expected coverage and terminal status for each declared source.
- `collection_manifest.json`: request dates, URLs/locators, exact commands, pagination, byte/row counts, checksums, adapter versions and recovery details.
- Source-record inventory retaining native identifiers, units, qualifiers, assay metadata and lineage.
- Missing-source/query/error ledger with concrete evidence and proposed recovery.

Keep credential values out of commands, manifests and reports. Keep original files intact; a corrected acquisition creates a new version.

## Procedure

1. Translate the accepted endpoint into explicit source query criteria, including task-specific qualitative versus quantitative records and assay scope. Record what the query excludes.
2. Reconcile every required source against prior receipts. Reuse matching immutable acquisitions; reopen retrieval when source versions, endpoint scope or collection criteria change.
3. Use the available source API, bulk export or permitted local import. Consult its current official documentation when an adapter's behavior is unfamiliar. Record required dependency or access failures instead of substituting a different dataset.
4. Preserve native bytes before parsing. Verify pagination and duplicate-page behavior, release identity, expected versus received counts and response validity; an HTTP success alone is insufficient.
5. Inventory every acquired record before eligibility filtering. Preserve original endpoint, assay, species, route, units, qualifiers and label meaning even when the normalized schema is not yet settled.
6. Inspect source documentation and representative records for endpoint relevance. Separate direct target measurements from related functional or proxy assays; do not pool their labels during collection.
7. Reconcile source/query coverage and classify each source as acquired, scientifically out of scope with evidence, unavailable or operationally failed. A required unavailable source leaves full-scope collection incomplete.
8. Supply reproducible acquisition/reconciliation commands and evidence locators to independent review. Report completeness relative to the declared scope, never all data on the internet.

## Decision boundaries

- Use the complete declared acquisition scope; do not cap rows for convenience or call a sample a full acquisition.
- Do not convert units, infer missing labels, resolve conflicting measurements, normalize away source identities or select modeling rows here; hand these to `qsar-curate`.
- Do not treat source reputation, a large count or a successful download as evidence that records measure the intended endpoint.
- Do not replace an unavailable required source with another source without a reviewed contract revision.
- Do not expose an existing sealed terminal benchmark's labels to modeling agents. Preserve its separate access boundary when importing source assets.

## Completion and handoff

Return artifact paths/hashes, reconciled counts, unresolved gaps and the exact review scope to the leader. Mark the package produced, not accepted. A distinct agent using [qsar-review](../qsar-review/SKILL.md) must inspect native source evidence and coverage before curation consumes the collection as accepted input.

Stop with an explicit incomplete result when required evidence cannot be acquired. Other independent acquisition tasks may continue; a failed required source cannot be relabeled a scientific null or omitted from the registry.

## Routing examples

Use: "Collect the declared rat oral toxicity sources and show which queries are complete."

Use: "Our endpoint changed from IC50 regression to binding classification; reconcile the necessary new records."

Near miss: "Resolve duplicate molecular identities in the downloaded records" belongs to `qsar-curate`.

Near miss: "Check whether this collection is complete" belongs to an independent `qsar-review` execution.
