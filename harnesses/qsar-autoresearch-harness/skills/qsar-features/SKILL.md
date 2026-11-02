---
name: qsar-features
description: "Generate and inventory declared QSAR molecular representation families with reproducible feature and row identities. Use after accepted curation and split specification; not target-driven selection, model tuning, or independent feature approval."
---

# QSAR Feature Generation

Generate the declared representations for all eligible development records and provide an exact replay recipe for terminal evaluation. This is the producer skill for stage `features`, normally assigned to an `executor`.

## Load and inputs

Read the shared [protocol](../qsar-autoresearch/SKILL.md) and [artifact contracts](../qsar-autoresearch/references/artifact-contracts.md). Load only the relevant representation specifications and toolkit documentation.

Require accepted curation and preprocessing artifacts/receipts, normalized molecular identities, development row axes, declared representation coverage, pinned environment and owned output paths. Use the current contract's families and deployment constraints, not a remembered recipe from another endpoint.

## Owned outputs

- Separate development feature matrices for each declared representation, with explicit row IDs, ordered feature names and dtype/shape/hash bindings.
- `feature_manifest.json`: toolkit/model versions, descriptor inventory, representation parameters, missing/nonfinite observations and coverage dispositions.
- Reproducible generator code/configuration and a target-independent inference/replay interface.
- Per-record/per-feature failure ledger and planned-versus-generated family accounting.
- Provenance for learned embeddings/checkpoints, including whether any fitting or task-label exposure occurred.

## Procedure

1. Enumerate available supported descriptors from the pinned toolkit and record exact names/versions. For a full 2D baseline, generate the full applicable inventory; neither a fixed count nor a handpicked small set establishes completeness.
2. Define each requested fingerprint family separately, including algorithm, radius/path settings, bit/count representation, length, chirality and collision handling where applicable. Keep optional combined families distinct from single-family baselines.
3. Check dependency and representation feasibility. Record unavailable or scientifically inapplicable families with evidence; an approximation is a different candidate and cannot silently replace a requested method.
4. Generate fixed row-wise features from the accepted structures using deterministic parameters. Preserve input row/identity order and reconcile failures against the full eligible development set; do not silently discard hard molecules.
5. Record missing/nonfinite outputs without selecting a data-dependent filter using all folds. Constant/variance/correlation filtering, imputation, scaling and target-dependent selection belong inside training folds, including when a filter is unsupervised.
6. For learned representations, distinguish externally frozen inference from fitting on project data. Verify checkpoint identity and available training provenance. Any project-fitted embedding or vocabulary must be fitted within the allowed training folds by `qsar-train`, not globally staged here.
7. Supply all-feature family baselines and the permitted feature-count/selection interface for later CV search. Do not choose the winning family or reduce dimensions based on targets at this stage.
8. Verify matrix ordering, finite/missingness reporting, generation repeatability and representative independent recomputations. Provide the generator and declared parameters to the reviewer; producer tests alone do not accept the stage.

## Decision boundaries

- No arbitrary descriptor subset, row cap or unreported family omission. If a user explicitly requests a subset, label it precisely and preserve the full research coverage requirement when applicable.
- No identifiers, target-derived labels, post-outcome metadata or prohibited source fields as predictive inputs. Approved condition features require the contract's intended-use and inference-availability rationale.
- Do not compute dataset-wide selection statistics over validation or terminal data. The terminal evaluator generates terminal features from the locked replay recipe; modeling agents receive no terminal labels.
- Do not call synthetic fractional vectors molecules or describe a proxy implementation as the original representation.
- A runtime or resource failure is an incomplete operational result, not evidence that a representation performs poorly.

## Completion and handoff

Return matrix/schema/generator hashes, full family accounting, generation failures and unresolved provenance. A distinct [qsar-review](../qsar-review/SKILL.md) execution independently checks feature values, row identity, forbidden inputs and inventory coverage before training consumes accepted matrices.

This stage's acceptance concerns fixed representation generation. Actual fold-local feature selection, fitted transforms and search coverage remain training-stage obligations. A changed molecular policy, toolkit, parameter set, checkpoint or feature order invalidates dependent matrices and models.

## Routing examples

Use: "Generate full supported RDKit 2D descriptors and each declared fingerprint family, with feature provenance."

Use: "Rebuild representation matrices after the accepted molecular standardization policy changes."

Near miss: "Find the optimal number of descriptors using nested CV" belongs to `qsar-train`.

Near miss: "Independently verify this feature matrix against the source structures" belongs to `qsar-review`.
