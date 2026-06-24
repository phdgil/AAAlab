---
name: qsar-autoresearch
description: "Protocol source for reusable QSAR workbook audits, in-vivo table preparation, leakage-aware feature staging, per-table training, deterministic result packaging, and exactly one bounded next-experiment proposal when first-run performance is weak."
---

# QSAR Autoresearch Protocol

Use this skill as the protocol source for workbook-driven QSAR modeling tasks. The protocol is designed around reproducible artifact contracts rather than ad-hoc notebook exploration.

## Core Contract

Every run must be able to explain:

1. which workbook and sheet were used,
2. how in-vivo rows were selected,
3. how condition-specific subsets were formed,
4. how targets were mapped into task families,
5. which columns were eligible as chemistry, condition, metadata, or excluded fields,
6. how leakage controls were enforced, and
7. what the next bounded experiment should be when the first run is weak.

If any of those are unclear, stop and resolve the contract before training.

## Required Stages

### Stage 0: Dataset contract audit

Build or reuse an explicit dataset adapter contract containing at least:

- allowed workbook names or dataset ids,
- source sheet selection,
- in-vivo filtering rule,
- grouping lattice or subset strategy,
- enabled task families,
- column-role mapping,
- approved versus excluded feature columns,
- target modifier policy,
- gate policy ids, and
- required artifact contracts.

### Stage 1: Deterministic preparation

Prepare condition-specific tables in a reproducible way:

- normalize structures and identifiers when possible,
- preserve source row counts and exclusion reasons,
- emit subset manifests even for subsets that do not become trainable tables,
- remove empty output folders from the final share package and record why they were absent,
- do not silently merge incompatible conditions.

### Stage 2: Leakage-aware feature staging

Build feature variants from chemistry plus explicitly approved condition metadata.

Rules:

- fit selectors only inside CV folds,
- keep target or post-outcome fields out of features,
- default-exclude suspicious metadata such as response modifiers unless a policy explicitly allows controlled testing,
- emit manifest evidence for every accepted and rejected feature family.

### Stage 3: Task-family training

Map each prepared table into a task family such as:

- categorical classification,
- numeric regression,
- comparison-only parity or historical context branch.

For each trainable table:

- record total/train/test counts,
- for classification, record label balance per split,
- compare models using the declared gate policy,
- keep historical baselines separate when scales or metrics are not comparable,
- emit machine-readable summary plus human-readable HTML.

### Stage 4: Bounded next experiment

If first-run performance is weak, do not improvise a broad search. Emit exactly one deterministic `next_experiment_manifest` that:

- names the failure class,
- names the bounded proposed intervention,
- explains rejected alternatives,
- requires review,
- forbids auto-chain,
- preserves the same dataset contract unless an explicit contract issue was found.

### Stage 5: Final packaging

The final package must support both model review and thesis/report writing:

- final HTML summary,
- machine-readable training summary,
- prep manifest,
- feature manifest,
- dataset introspection manifest,
- next experiment manifest,
- share-ready README in the requested language when packaging is requested.

## Guardrails

- Never claim strong model quality from a table whose split sizes or label balance are opaque.
- Never treat a weak first run as permission to widen scope without a recorded proposal.
- Never reopen requirements discovery when the same dataset contract is still valid.
- Never hide leakage risk; preserve the exact columns tested or excluded.
- Never delete failed subsets from audit manifests just because they were not trainable.

## Trigger Tests

Should trigger:

- "Build a reusable QSAR pipeline for this workbook."
- "Train all in-vivo subsets and tell me what to try next if it fails."
- "Generalize this endocrine QSAR harness to another workbook family."
- "Produce a thesis-ready result package with prepared tables and leakage evidence."

Should not trigger:

- "Explain what QSAR means."
- "Summarize this toxicology paper without building a pipeline."
- "Convert this CSV to XLSX."
