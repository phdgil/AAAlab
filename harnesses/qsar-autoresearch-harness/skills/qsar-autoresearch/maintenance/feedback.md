# QSAR method changes

## 2026-10-29 ? Focused stage skills and independent handoffs

Repeated feedback required full supported feature coverage, actual per-family/model HPO, endpoint-specific collection/curation, and independent validation before consuming stage outputs. Replaced the workbook-only/proposal-only shared protocol with an end-to-end contract. Added focused sibling skills and explicit agent assignments; kept toolkit counts, scientific thresholds and search budgets configurable. Separated sealed terminal evaluation from development improvement.

The implementation now includes twelve installable native roles, durable stage gates, source adapters, grouped preparation, full toolkit feature generation, nested model/feature search, independent evaluation, improvement handoffs, reviewed final fitting and release packaging. Engineering checks and source-based scientific acceptance remain separate; full-data scientific success is not implied by passing package tests. Prior pilot sampling/search restrictions were evaluator-imposed, not a built-in512-record policy. Private session evidence is intentionally not distributed here.

## 2026-11-02 - Executed search coverage survives resume

Historical training feedback repeatedly questioned whether model parameters and feature counts had actually been searched, and whether fixed anchor trials overstated search breadth. The earlier evolution already introduced full feature inventories, per-cell search and independent stage review. This pass checks those mechanisms rather than adding dataset-specific thresholds or model defaults.

| Change | Targets | Reason and validation |
|---|---|---|
| Reconcile completed scopes before reuse and final completion | `qsar-autoresearch-harness/scripts/qsar_train.py`; `qsar-train/SKILL.md` | An independent synthetic probe resumed a checkpoint with no trial or fit evidence as complete. Required trial/fold identities, selection, OOF membership and model bindings now determine reuse; missing or substituted evidence rejects. |
| Preserve superseded attempts and separate their cost from current coverage | Same training runtime and skill | A transient failure disappeared after retry. Prior ledger entries, fit evidence and failure details now remain in attempt history; coverage distinguishes active results from recorded attempts. |
| Reject duplicate search choices | Same training runtime and skill | Six requested search trials could represent only two distinct configurations. Reject repeated choices, including numerically equivalent values, instead of silently changing the search budget. Separate all-feature anchors remain supported. |

Regression fixtures live in repository `scripts/test_qsar_train.py`; the runtime contract is documented in `qsar-autoresearch-harness/references/training-runtime.md`. Targeted checks were run after each repair, followed by independent re-review. This is engineering validation of supported search accounting, not acceptance of a real dataset or evidence of better predictive performance.

The recovered pilot explicitly limited records and prohibited HPO; it cannot establish full-study success or a causal advantage of one harness over another with different curation/splits. Full-data scientific acceptance remains subject to current collection, curation, split, feature and training reviews. Private transcripts and recovery locators stay outside the distributable pack. Read this record only when revising methods.
