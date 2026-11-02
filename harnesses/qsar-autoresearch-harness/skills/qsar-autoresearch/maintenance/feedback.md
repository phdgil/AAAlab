# QSAR method changes

## 2026-11-02 - Bind execution, model lineage and publication boundaries

Review of actual training histories distinguished executed fits from prepared
jobs and requests, and reusable workflow controls from endpoint-specific
preferences. The harness now has explicit binary classification, grouped
cross-fitted calibration, optional numeric SMOTE and Optuna, fitted chemical-rule
scoring, frozen local encoder heads, training-only applicability domains,
parent/scaffold grouping, count/feature-Morgan fingerprints, separate external
evaluation, advisory curation deltas, durable execution and localized reporting.
Metrics, thresholds, chemical exclusions, budgets and reporting language remain
contract choices. Frozen inference is not fine-tuning, and synthetic tests are
not scientific performance evidence.

Integration and independent review exposed several important failure modes:

- Hash-consistent artifacts can still belong to another training run. Outer and
  final models now carry the complete training-identity digest; evaluation also
  validates recipes and independently replays bound OOF predictions.
- Atom-map annotations are not chemical identity. Derived parent/scaffold keys
  discard maps while preserving original source bytes.
- An external-result reservation freezes development just as terminal exposure
  does. Neither invalidation nor a fresh dispatch erases exposure history.
- Custody checks and execution must resolve relative operands from the same
  owned working directory. Model-output ownership is checked before deserialization.
- Process completion requires owner-safe descendant teardown, not just a
  direct-child exit. Windows Job Objects and pinned POSIX process groups share
  the same supervision path for blocking and durable execution. A live Linux
  test also caught venv symlink resolution selecting the wrong interpreter;
  launch paths and interpreter/configuration bindings are now distinguished.
- Accepted structured coverage or review fields can contain raw rows.
  Publication omits such fields with hash/pointer receipts instead of treating
  scientific acceptance as authorization to publish their contents.
- Full Windows regressions exposed intermittent access denials while atomically
  replacing checkpoints. Unique flushed temporary files and bounded retries of
  that exact commit preserve old evidence on persistent failure; fits and
  scientific failures are never retried or suppressed by this policy.

Regression evidence includes real local process execution on Windows and Linux,
inference-only replay and stale-model rejection, actual optional-library
execution, and locally generated random encoder checkpoints. Slurm lifecycle
tests use controlled scheduler responses, not a live cluster. Private transcripts,
research data and scientific checkpoint weights are not distributed.

Final verification: 825 Python tests completed successfully on Windows with
four platform skips. All four skipped cases passed within a separate 16-test
Linux run. `npm test` and `git diff --check` also passed. Optional calibration,
sampling, search and random local encoder paths were exercised with installed
dependencies; upstream deprecation warnings were not suppressed.

## 2026-11-02 - Bundle evolution for continued use

The user wanted harness improvements from ongoing QSAR use to remain available
after installing the package. The earlier update applied a locally installed
general evolution skill, leaving only the change record in the distributable
pack. Add `qsar-evolve` as an eleventh sibling skill with no external skill
dependency; the namespaced name preserves an existing general `evolve` skill.

The coordinator now routes reusable harness feedback to this maintenance workflow
and model-development interventions to `qsar-improve`. Capture observations in a
private run log, reproduce the cause in a separate maintenance context, validate
a bounded repair, append a generalized history entry and adopt a recorded version
for the next run. Live dependencies, old receipts and terminal exposure remain
under their existing versioning and review rules. The skill does not add a
scientific stage, background watcher or automatic publication authority.

Package regressions in `scripts/test-qsar-skills.js` check eleven installed skills,
preservation of a general `evolve` skill, rejection of missing/malformed maintenance
instructions, and rejection of maintenance as a scientific dispatch. Routing
examples cover three intended requests and three near misses. These checks verify
packaging and guidance; improved scientific results require later run evidence.

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
