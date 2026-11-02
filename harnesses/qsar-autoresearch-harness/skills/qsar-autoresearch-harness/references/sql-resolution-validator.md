# Reviewed SQL resolution validation

`scripts/qsar_sql_resolution.py` provides a read-only validation function. It does not activate a contract, close a runtime gap, generate scientific approval, apply source fixes, or implement the COMPTOX exception. Existing v3 contracts fail because they lack the exact execution selector.

```python
result = qsar_sql_resolution.validate(
    contract_binding, accepted_contract_review, source_config, source_entry,
    original_gap_artifact, evidence, expected_scope, expected_consumption,
    actual_review, bind,
)
```

All descriptors use exactly `path` and lowercase `sha256`. `source_entry` is the caller's source inventory entry; its descriptor and `source_id` identify the immutable source. `source_config` must equal the required source entry in the bound full contract. `original_gap_artifact` supplies the original obligations through the existing scanner's obligation function; none may be omitted, duplicated or added.

The caller must supply independently established `expected_scope`, `expected_consumption` and `actual_review`. Copying these arguments from an unreviewed producer manifest defeats the trust boundary. Review envelopes explicitly declare `local_leader_attested_not_authenticated`: file hashes and distinct producer/reviewer IDs do not authenticate human or agent identity. The central integration must establish real accepted-contract and separate scientific-review provenance before calling this function.

The authoritative wire design consists of `sql-resolution-implementation/final-design.md`, `schema-revision-1/appendix.md` and `schema-revision-1/field-set-clarification.md` in the evaluation workspace. The exact selector is exposed as `SELECTOR`. There is no arbitrary query or policy language. The complete contract envelope and current scanner, native helper, candidate identity helper and resolver code are included in the scientific review context.

The validator freshly hashes consumed files and reuses the bound independently reviewed scan and support-audit facts. It recomputes table schema/count checks, full primary/exact-LD50/broad-scope membership digests, complete native-to-candidate bridge digests, native anchors and candidate IDs, decision IDs, applicable native fix IDs, and exact decision/consumption coverage. It compares the full reviewed native and candidate membership, including mapping-only rows and all siblings. Candidate identities reuse the v2 hash convention with explicit SQL anchors; this does not claim compatibility with the HSDB-only v2 execution adapter.

For both selectors, every supplied primary membership row is compared in native physical order against a complete independent stream from the bound SQLite index **before bridge filtering**. Native toxval ID, statement/tuple/line and row fingerprint must all match, including rows declaring themselves outside the diagnostic union. Missing, extra, duplicated or equal-count substituted primary entries fail even after every producer summary and receipt is consistently rebound. The index's decoded bodies and row fingerprints retain the separately reviewed scan trust boundary; this reconstruction does not reparse original SQL.

The fixed `toxval_native_membership_v1` algorithm reconstructs `exact_current_ld50` as literal native current `toxval_type == 'LD50'`. `task_relevant` is true when SQLite `upper(trim(...))` of either native current/original endpoint is `LD50`, or native original endpoint exactly matches an original term in a native `toxval_fix` row whose `field` is `toxval_type` and whose final term satisfies SQLite `upper(trim(term_final)) == 'LD50'`. Null does not match a mapping. Both supplied flags must be actual integers 0/1 and equal these computed facts; they cannot select which native rows receive validation. This is diagnostic membership, not scientific eligibility or application of a correction. Mapping terms are stored in the private bounded-cache SQLite work database, and primary rows are streamed without a full-source Python set. Native cursors are explicitly closed on failure as well as success.

Native membership, bridge, decisions and consumption use strict UTF-8 JSONL, one object per line. Array examples from the design constructor are illustrations and are rejected as production ledgers. Duplicate JSON keys, nonfinite numbers, blank JSONL records, extra schema keys and boolean counts fail. Structured metadata loads and individual JSONL records are limited to 16 MiB. Membership and decisions are projected into a private temporary SQLite database; native SQLite is opened read-only with `query_only`. Fixed queries are used throughout. Input files are hashed in 1 MiB chunks and checked for size/mtime changes through completion; callers must maintain immutable inputs during validation.

Each bridge candidate needs exactly one decision and consumption row. `preserve` uses native original/current values without unsupported correction or mixed numeric/unit pairs; explicit original inequalities cannot be erased. Reviewed scientific quarantine and exclusion require the exact bound v4 clause and no emitted output IDs or artifacts. Missing optional metadata remains a disclosure. Critical-metadata quarantine needs the complete typed, source-bound review evidence specified by the accepted clause. Incomplete extraction, unresolved obligations and correction attempts fail.

Empty mapping-only enumeration requires both native `toxval_numeric_original` and `toxval_numeric` to be absent (`null`, empty string or literal `-`). This is an absence check, not a regression-eligibility test: zero, negative values (including `-999` without a separately supported missing-value interpretation), censored text and unusable populated lexemes remain accounted candidates. Every declared no-candidate anchor is replayed; an anchor to either numeric field with a populated native value blocks the empty conclusion, even on another native row. This conservative predicate does not classify the measurement scientifically. Consumption and all critical-metadata locators require exact positive integer physical coordinates and existence in the bound native index; valid shared/support locators remain allowed. Strict JSON rejects nonfinite parsed numbers recursively, including positive/negative exponent overflow, before downstream field selection.

Scientific interpretation of anomaly membership, indispensable metadata, applicability and source conflicts remains with the separate bound review. The validator checks the reviewed consumption ledger and output bindings; it does not rerun a downstream consumer or interpret arbitrary output formats. The caller must supply the actual consumer code, actual ledger and actual output artifacts, with their scientific review context. Receipt references inside resolutions use a stable review key so the resolution hash does not depend on the later receipt hash.

Success returns only `obligation_ids` and `consumed_bindings`; `bind(descriptor)` receives every distinct consumed path binding for central guard integration. Failure raises `SQLResolutionError`; partial binding callbacks on failure do not indicate acceptance. There is no command-line activation interface.

## Opt-in v2 source-row terminal exclusion

`SELECTOR` and all v1 schemas/guards remain unchanged. The new exact `SELECTOR_V2` is:

```json
{"schema_version":2,"adapter":"toxval_reviewed_support_resolution_v2","scope_algorithm":"toxval_native_membership_v1","candidate_bridge":"sql_native_candidate_bridge_v2","candidate_identity":"adjudication_v2_identity_with_sql_anchors_v1","review_envelope":"sql_resolution_leader_attested_v1","corrections":"unsupported","source_row_terminal":"source_row_terminal_exclusion_v1"}
```

This is an explicit contract activation change. An existing v1 selector, draft6, factual bridge approval or old scientific receipt cannot activate it. Adopt only through a separately accepted full contract, caller integration supporting this exact selector, new exact scope/consumption inputs and actual independent scientific review. This implementation changes no live contract, receipt, runtime, source or consumer.

The finite v2 delta is mandatory, including zero-terminal packages; extra keys fail:

| Object | Exact change from v1 |
| --- | --- |
| Evidence | `schema_version:2`, v2 `adapter`; add descriptors `source_row_decisions`, `source_row_consumption` |
| Expected scope | `schema_version:2`; add `native_evidence` descriptor and `source_row_terminals:{count,members_sha256}` |
| Scope bridge summary | Add integer `candidate_native_count`; all four existing fields remain |
| Every bridge entry | Add `source_row_terminal`: null or decision SHA256; existing four fields remain |
| Expected consumption | Add `source_row_ledger` descriptor and `source_row_membership:{count,members_sha256}`; existing three fields remain |
| Scientific receipt context | `schema_version:2`, `selector:SELECTOR_V2`; add `source_row_decisions` descriptor; existing fields remain |

Native identity N and locator L retain their v1 exact fields. Source-row decisions are JSONL ordered strictly by N's `(statement_ordinal,tuple_ordinal)`, with exactly:

```text
kind native contract_clause review decision_id
```

`kind` is `source_row_terminal_exclusion_v1`; `native` is full N; `review` is a descriptor. `decision_id=H(the entire decision with decision_id removed)`. No candidate, measurement, population or measurement-evidence identity exists in this variant. The containing bridge and scientific receipt bind every decision to the exact source, full accepted contract, native index, native sidecar, support audit, code and complete enumeration without a circular bridge/decision hash.

The only implemented primary clause is `v4_explicit_inequality`, whose accepted contract clause must have `disposition:excluded` and `reason:explicit_inequality`. At least one original/current native qualifier must be exactly `<`, `>`, `<=`, `>=`, `≤` or `≥`. Both primary numeric field keys must exist and be null, empty or literal `-`; populated converted/human/standard alternatives also block. Zero, -0, -999, literal NULL, unusable strings and censored strings cannot enter this branch. Both original/current values and qualifiers remain in the full native snapshot. An incompatible species/route remains reviewed context; incompatible task alone is not a second primary-clause implementation. Blank/equality/unknown qualifiers, missing numeric, unknown route and missing optional metadata alone never suffice.

The bridge branch requires `candidates:[]`, `no_candidate_review:null` and a nonnull terminal decision ID. Candidate-bearing and ordinary mapping-only entries require a null terminal ID. Ordinary `no_measurement_candidates` remains restricted to its original mapping-only predicate. Full native coverage and exact-current-LD50 denominator stay unchanged. `candidate_native_count + no_candidate_count + source_row_terminals.count = bridge.native_count`; candidate count separately counts real candidates/siblings. Terminal membership digest projects `{native:N,decision_id}` in native order. No placeholder contributes to candidate count.

The bound review file has exactly:

```text
schema_version native source_sha256 contract native_evidence facts interpretations
conclusion contract_clause independent_return
```

`schema_version` is integer 1; `native` is N; `source_sha256`, `contract` and `native_evidence` exactly match the outer bindings; `conclusion` is `no_unenumerated_measurement`; clause matches the decision. `facts` is the complete canonical-locator-sorted list of `{native_locator:L,row_sha256,fields}` replayed by `terminal_facts` against native SQLite. `fields` is the entire native body, not a selected-value projection. This supplies field/locator/value anchors even for null fields, with no fabricated nonempty quote. It includes the primary row, all matching chemical, source_chemical, species, record_source, relationship and UF rows; all matching source_info alternatives; every dictionary and endpoint-dictionary row; and every fix matching a listed FIX_FIELDS original value. Multiple matches, unchanged fixes, empty/sentinel matches, current/original alternatives and full support bodies are retained. Joins use the fixed field pairs in `terminal_facts`; textual support comparisons are case-insensitive and null keys never match. Dictionary inclusion is deliberately conservative. No new publication retrieval is required by this schema.

`interpretations` has exactly one record per field of each fact, in fact order then sorted field-name order. Exact keys are `{native_locator,field,value,classification,rationale}`. Locator/field/value must match the complete native inventory without type coercion. Classification is only `not_measurement` or `administered_dose`, with a nonempty row-specific rationale. A measured target, unresolved interpretation, omitted field, cross-row anchor, omitted support/fix or stale snapshot blocks terminal accounting. Actual independently established narrative measurements must instead enter the candidate branch. The validator cannot infer scientific meaning from arbitrary prose: these classifications are assertions of the separately verified independent review, not a semantic text classifier or Boolean completeness shortcut.

`independent_return` is a descriptor for an exact object `{schema_version,review_key,producer_execution_id,reviewer_execution_id,review_payload}`. Version is integer 1; review key equals the scientific receipt key; execution IDs are nonempty and distinct; payload must exactly equal the complete review object minus `independent_return`. The leader must verify the actual independent return/provenance. These local envelopes are explicitly not authenticated by string IDs or hashes. Generic review evidence with a Boolean approval cannot substitute for this returned field-level payload.

Source-row consumption is a separate JSONL ledger in the same native order with exactly:

```text
kind native decision_id action clause output_observation_ids output_artifacts output_labels
```

Identity, kind, decision and clause must exactly join one source-row decision; `action` is `excluded`; all three output fields must be literal empty arrays. Every terminal requires exactly one decision and one consumption entry, including zero-terminal packages. `source_row_membership` hashes the entire consumption record per line; equal counts with wrong membership fail. The candidate consumption ledger is unchanged. The scientific receipt binds both actual consumption ledgers, their counts/digests, consumer code and outputs. The caller must supply actual consumer execution evidence; this read-only checker does not manufacture or execute a consumer.

For the reviewed real diagnostic universe, the intended future accounting is 142,768 candidate-bearing rows plus two terminal rows, zero ordinary no-candidate rows, and an unchanged exact-current-LD50 denominator of 142,764. This is an integration target, not a claim of actual consumption or scientific acceptance. Real source-row decisions, complete source-wide scientific review, independent return, newly accepted selector and actual zero-emission consumer accounting remain required.

The real source-wide SQL candidate bridge, reviewed scientific decisions, actual consumption package, runtime-accepted contract and central caller integration remain prerequisites. Synthetic test review envelopes are not real approvals. Run the focused suite from the repository root:

```text
python -B -X utf8 -m unittest discover -s scripts -p test_qsar_sql_resolution.py -v
```

The [SQL consumer integration](sql-consumer-integration.md) supplies the actual indexed scope and output effects. Its diagnostic path calls the exact extracted `candidate_decisions` and `terminal_decisions` helpers; final resolver validation calls the same helpers before the unchanged consumption checks.
