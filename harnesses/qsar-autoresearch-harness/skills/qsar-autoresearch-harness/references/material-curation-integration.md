# Reviewed material curation integration

The material bridge is an explicit curation input. It does not classify a source,
write scientific approvals, or grant runtime acceptance. The existing isolated v1
material API and ordinary contracts without a material policy remain available.

Invoke `qsar_data.py curate --contract CONTRACT --registry REGISTRY --output-dir OUT
--material-config PACKET`, with `--adjudication-config` when the same external
narrative bindings are named by the packet. A contract declaring
`scientific_policy.verified_tested_material_v4` cannot omit the packet. The six-key
material execution policy is unchanged.

The v1 packet names the exact full contract, accepted contract envelope, registry,
optional external adjudication, classification JSONL and independent review,
material inventory/decisions and independent review, RDKit environment, and the
fixed `NativeReplayStore.replay_native` provider with the complete code dependency
set. Review descriptors bind actual returned review objects and receipt files.
This is provenance, not authentication. Synthetic fixtures cannot certify real
sources.

Construct artifacts in this order: original sources and native scope;
classifications and their separate review; affected material inventory and branch-specific integrated
decisions and their separate review; outer packet. Inner receipts do not hash the
outer packet. `classification_context()` and `NativeReplayStore.scope()` expose
the deterministic context/scope calculations used by fixture or review tooling;
they do not supply a classification or approval.

`pre_identity_observation()` extracts the existing native mapping, task, relation,
numeric-value, unit and material checks before any chemistry. The closed predicate
is `pre_identity_v1`. Identity failures cannot exempt rows from classification;
unexplained native reasons and unsupported native filters reject integration.
Missing or ambiguous OFT SUB joins cannot establish a disallowed material type.
The full iterator universe U is partitioned into eligible E and nonmaterial N;
classifications must cover E exactly. Affected inventory must cover affected E
exactly. Separate physical slot, candidate member and observation content hashes
prevent a missing sibling from falling back to ordinary chemistry. Complete
narrative ledgers T retain nonemitted reviewed candidates and source blockers.

Native records retain physical coordinates, literal fields and RFC6901 evidence
pointers, including the difference between absent, null and blank representations.
InChI-only input never gains an invented native SMILES field. Invalid supplied
SMILES plus valid InChI is invalid, without automatic fallback. Corrections account
for each supplied representation; support-only resolution requires absent native
representations and source-bound evidence. Raw molecular mass is never replaced by
support or verified-material mass. Inorganic formula-unit, predictor and grouping
columns use the reviewed complete graph. Optional source metadata can remain null;
known source-formula annotations and explicit contradictions cannot be discarded.

Raw identity-support callbacks run before normalization/filtering. Physical
duplicates, competing joins and orphan records remain distinguishable. Legacy
support ingestion failures still fail closed; no material decision can waive an
ingestion or chemical-join blocker by renaming it an identity conflict.

NPASS raw support also preserves its required NPID, both required representation
fields and native InChIKey checks. A well-shaped key is compared with the complete
agreeing literal graph, before any organic parent selection. Missing/malformed or
disagreeing native keys and missing required representations remain unwaivable;
invalid duplicate rows retain their own physical evidence and poison the matching
support set. Legacy `multiple_components` alone does not become such a blocker,
so a valid reviewed complete material graph remains usable.

The public `open_material_curation()` context owns a SQLite spool and has an
explicit collecting, sealed/ready, finished or aborted lifetime. After staging
supports, observations and narrative ledgers, `seal_native()` validates scope and
reviews. `iter_observations()` reuses stored native projections. Each E member
routes once; N cannot enter either identity resolver. `material_batch()` performs
material row lookups without per-row whole-file hashes or source replay; entry and
successful `finish()` recheck bindings. Clean exit without `finish()` fails. An
illegal entry/operation aborts a live batch even if its exception is caught;
finished/aborted batches cannot reopen. An
unresolved classification/material cannot publish success. Reviewed holds produce
quarantine without targets. Native identity/join restrictions and reviewed unit
basis checks still apply to verified material rows.

The guard creates a separate store from original inputs and reconstructs every
output. It decodes declared material CSV types, checks projections and compares
native-derived targets, identities, counts, ledgers and membership. Unaffected legacy organic rows
have blank material-only columns. Success manifests are published last; publication
failure removes only this invocation's success markers. Returned bindings preserve
material, classification, source/support, narrative-review, accepted-contract and
code dependencies through preparation and standalone feature generation.

Synthetic regression coverage is in `scripts/test_qsar_material_integration.py`:
mapped mixed curation and HSDB v2 sibling curation through the guard; native
SQL/OFT/HSDB/ChemID evidence; literal InChI, invalid/fallback and correction cases;
rebound membership/target/identity attacks; optional metadata; batch lifetime and
input mutation; publication failure; feature freshness; and equal whole-source
hash counts for one versus ten material rows. This is not a real-source scan,
scientific classification review, large-data performance benchmark or acceptance
receipt. Global testing and independent implementation review remain separate.


## Reviewed organic principal component (B)

The exact nested `verified_tested_material_v4.organic_salt_b_v1.execution`
selector opts into integrated decision/projection schema 3. Its six fields are
schema_version=1, adapter=reviewed_organic_principal_component_v1,
rdkit_version matching the pinned runtime, mass_algorithm=rdkit_descriptors_molwt,
predictor_policy=reviewed_principal_component, and
grouping_algorithm=sha256_utf8_rdkit_canonical_nonisomeric_smiles.
A declared malformed selector rejects even when the affected inventory is empty.
The existing inorganic six-key selector, schema 2, outer packet/container and
isolated inorganic API v1 remain unchanged.

A schema 3 decision adds exactly `principal_component`: canonical_smiles,
multiplicity, source_evidence, supplemental_evidence and selection_rationale.
The reviewed principal is one exact canonical isomeric component of the complete
verified graph. Its positive integer multiplicity equals the full component count;
Boolean counts reject. Source field pointers must resolve against native or
inventory support records. Supplemental entries bind an exact file descriptor and
nonempty locator. Neither graph membership nor a CAS/SMILES pointer scientifically
proves principal choice; the independent material review must establish that choice.

The complete graph and full formula-unit mass remain original_structure and
original_mw, including every principal copy, counterion and water component.
parent_smiles/predictor_graph and parent_mw describe one principal copy.
connectivity_id/group_id use the existing non-isomeric principal hash, without a
salt prefix. material_group_id remains the complete-graph audit signature.
Mass-based molar targets use the full mass, without multiplying by principal count;
explicit molar targets retain their reported formula-unit meaning.
Counterion/hydration effects are absent from predictors, so observations with equal
predictors and different targets remain distinct with their existing flags.

Classification still covers current E exactly and inventory covers affected E
exactly, including retained salts. Historical examples are not a whitelist.
Unresolved affected records cannot fall back to legacy chemistry; B failure alone
is not scientific ineligibility. Source identity, joins, origin, route, qualifiers
and units remain separate gates. Optional absent metadata alone is not exclusion.

The new principal_component CSV column is blank on schema 2 and unaffected rows.
Schema 3 requires the structured certificate; final CSV requires group_id.
Native and typed evidence distinguish absent, null and blank. The existing CSV
encoding conflates an empty source_dose_basis scalar with null; only guard CSV
comparison accounts for that empty cell. It does not strip whitespace or relax
nonempty values, typed producer projections, native replay or target checks.

Supplemental principal files enter transitive dependency bindings and are checked
at load, successful finish, SQL finalization and downstream reuse. Conflicting
normalized-path digests reject. No per-row whole-file hash or native replay is added.
Synthetic ordinary and SQL fixtures exercise these paths, plus legacy HSDB metadata
with both material branches; they grant no real source/material acceptance.
