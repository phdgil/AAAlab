# SQL consumer integration

`qsar_sql_curation.py` connects the reviewed resolver-v2 source decisions to actual curation effects. It does not issue scientific receipts or authorize new exclusion rules. Native scanner and coverage modules remain unchanged.

## Staged workflow

1. Supply a declared required `toxval` source in the strict full contract with the exact `SELECTOR_V2` execution selector. The external adjudication map may contain SQL, ordinary, and HSDB entries with their existing distinct discriminators.
2. The SQL entry has exactly `schema_version:1`, `kind:"qsar_sql_curation_inputs_v2"`, and `full_contract`, `accepted_contract_review`, `review_key`, `profile`, `native_index`, `scan_review`, `support_audit_manifest`, `support_review`, `scope`, `candidate_decisions`, `source_row_decisions`. Descriptors contain exactly absolute `path` and `sha256`; `review_key` is a nonempty stable string. Scope is a descriptor of the existing v2 scope object. No final receipt or claimed consumption belongs in this entry.
3. Run `qsar_data.curate(contract, registry, destination, adjudication_config_path, material_config_path)`. SQL-enabled generation writes the fixed CSVs, actual candidate and source-row consumption, and blocked `curation_diagnostic.json`. It creates neither success manifest. The material packet binds the pre-consumption external map and uses the selected SQL projection before eligibility partitioning.
4. Obtain separate source review of actual outputs and ledgers. Table resolutions reference the diagnostic. The final scientific receipt uses the existing resolver-v2 context and the same review key. An earlier independent terminal field-review return is not this final scientific review.
5. Supply the finalization envelope `{schema_version:1, diagnostic:B, sources:{source_id:{resolutions:B, scientific_review:B, actual_review:existing_resolver_envelope}}}` to `finalize_curation(diagnostic_path, review_config_path)`. It replays all outputs from the bound inputs, validates every SQL resolution and every other coverage gate, and publishes `curation_manifest.json` and `manifest.json` last. Existing final markers reject; failed publication removes only markers created by that invocation.

```python
from qsar_sql_curation import finalize_curation
handoff = finalize_curation("/immutable/run/curation_diagnostic.json", "/reviews/sql-final-review.json")
```

The ordinary no-SQL publication path remains available. `qsar_data.build_curation(...)` is the shared generator and writes only data and ledgers, never success or diagnostic markers. The normal guard and material replay use it for reconstruction.

## Identity and effect accounting

Every indexed physical primary row is traversed. Bridge candidates, including siblings, each receive an actual disposition and identity record. Nonmeasurement terminals retain physical identity without synthetic candidate IDs. Candidate row IDs are `source_id:toxval:candidate:candidate_id`; emitted IDs append `:task_id`. Material candidate keys are `sql-v2:candidate_id`.

The separate SQL carrier binds the untouched adapter projection and the exact validated native/bridge/decision fields. Reviewed endpoint, species, route, qualifier, numeric/unit pair, DTXSID and chemical ID are applied before contract aliases and material eligibility. Selected chemical IDs cause a fresh indexed source-chemical join; original/current alternatives remain native evidence. Qualifier conflicts and selected identity join failures are not waived. No experimental origin is synthesized.

For `T = SHA256(source_id UTF8)`, consumption files are `sql_candidate_consumption_<T>.jsonl` and `sql_source_row_consumption_<T>.jsonl`; both exist, including an empty terminal file. Preserved candidates require actual emitted IDs and only the completed `curated.csv` descriptor. Nonemitting decisions have empty IDs/artifacts. The five output roles are `curated.csv`, `dispositions.csv`, `identity_ledger.csv`, `identity_support_ledger.csv`, and `conflict_ledger.csv`. Only exact configured narrative filenames and these two ledgers per SQL source are additional roles.

Preserve followed by a generic curation drop is a blocking discrepancy. An otherwise-eligible zero without a supported terminal clause remains blocked. Ordinary origin rules do not authorize SQL unknown-origin quarantine. Input membership, effects, and material joins use scratch SQLite; native inputs are opened read-only and source SQL is not reparsed per candidate.

## Final discovery and replay

Final SQL summaries contain `sql_finalization:{schema_version:1,diagnostic:B,review_config:B}` and `sql_integration.phase="final"`; diagnostics contain no finalization field. Final summaries record exact original obligation IDs in `sql_obligation_closures`. Historical collection gaps remain unchanged. Missing or extra source identities, altered counts/closures/discovery, and stale review dependencies reject.

`validate_sql_finalization(...)` performs read-only reconstruction. It compares complete serialized CSV fields and ledger records, including exact native, sibling and terminal membership. Scratch ledger artifact paths are compared logically against the original immutable output roles. The ordinary guard rediscovers final reviews only through the bound discovery object, reconstructs the complete final summary, and adds the final manifest binding to its returned references. Existing runtime acceptance, freshness caching, and preparation use this same guard without additional runtime approval state.

`test_qsar_sql_curation.py` uses explicitly synthetic receipts and actual collection, indexed generation, material routing, finalization, normal guard, runtime and preparation paths. It does not produce real source acceptance.

Organic B uses the same diagnostic, separate final source review, finalization and full replay flow. Its integrated schema 3 principal certificate and supplemental files remain transitive dependencies; material review cannot waive SQL source gates.
