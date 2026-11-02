# Candidate v2 curation integration

This opt-in integration preserves the historical v1 external binding. It does not
activate a method draft, approve real source dispositions, close SQL coverage, or
authorize training. The isolated [v2 engine contract](narrative-adjudication-v2.md)
still defines all decision, review and source-span schemas.

The external map passed to `qsar_data.py curate --adjudication-config` maps each
declared HSDB source ID to exactly:

```json
{
  "schema_version": 2,
  "inventory": {"path": "absolute path", "sha256": "digest"},
  "decisions": {"path": "absolute path", "sha256": "digest"},
  "review_receipt": {"path": "absolute path", "sha256": "digest"},
  "full_contract": {"path": "absolute path", "sha256": "digest"},
  "accepted_contract_review": {"path": "absolute path", "sha256": "digest"}
}
```

`full_contract` must equal the actual curation contract descriptor. It must have
the v2 executable policy and strict source-coverage execution policy. The accepted
review uses the engine's exact contract adapter envelope, including bound candidate
and returned-review files. Distinct execution IDs alone are insufficient. Runtime
links these descriptors to its current accepted contract submission and review;
native execution provenance remains leader-attested, not cryptographically authenticated.

Only `curation_candidate` observations enter normal identity, task, unit, material
and qualifier processing. Three stable IDs and optional typed target origin survive
in all output ledgers. Excluded and scientifically quarantined candidates emit no
training rows. Eligible siblings survive. Unresolved candidates preserve diagnostic
rows from other candidates but block source completeness and strict handoff.
Field accounting, candidate accounting, source review and source coverage remain
separate v2 flags; no v1 completeness alias is synthesized.

The evidence guard replays native inventory, spans, dispositions and policy checks
into temporary scratch, then joins reconstructed candidate IDs and exact bodies to
disposition, identity and curated tables. It checks the entire field ledger, including
nonemitted candidates and same-document context. It returns bindings for all engine
dependencies, contract/review assets, curation code and output ledgers so preparation
and runtime reuse can reject mutation. Replay is a deterministic integrity check,
not an independent scientific review or proof of exhaustive semantic interpretation.

`qsar_curation_evidence.validate()` retains its list-of-bindings return contract.
After successful validation, runtime can call
`contract_acceptance_bindings(manifest_path)` for deduplicated adapter descriptors.
The accessor alone does not validate curation or grant acceptance. Applicable new
assets that have never been declared cannot be discovered by hash checking; such
changes require new source review and dependency declarations.

## Eligibility reconstruction repair

Independent review found that membership counts alone allowed an eligible sibling
to be relabeled excluded while the curated row and counts were removed coherently.
The same omission and target-value bypass affected ordinary OECD and ICE rows.
The initial engineering test pass did not establish protection against these attacks.

The guard now rebuilds a single identity index from raw support and reconstructs
each observation through the existing deterministic `curate_observation` calculation,
shared with production. Input rows come from fixed native adapters or source-bound
narrative reconstruction, never from submitted disposition or identity tables.
Expected native mappings, reasons, membership, identity evidence, structures and
numeric targets are checked exactly. Floating targets use the existing deterministic
CSV serialization, with no numerical tolerance. Post-row ToxVal metadata flags are
reconstructed from native metadata; replicate flags come from complete expected
task/group membership. Submitted flags do not determine eligibility or grouping.
Scratch SQLite keeps expected bodies on disk; source/support parsing is performed
for validation, not by runtime status refresh. Runtime can reuse accepted validation
only while its complete dependency hashes remain unchanged.

This integrity calculation shares the accepted producer policy implementation; it
does not constitute independent scientific judgment about that implementation.
Native SQL reconstruction tests retain the existing unresolved collection-table
gate and exercise semantic reconstruction separately, rather than manufacturing
SQL collection acceptance. No real SQL coverage obligation is closed.

Focused synthetic regression suite: `python -m unittest discover -s scripts -p
test_qsar_candidate_integration.py -v`. Fixtures are explicitly synthetic and cannot
serve as real contract, collection or curation acceptance.
