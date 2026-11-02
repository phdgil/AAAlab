# Isolated reviewed material adapter

`scripts/qsar_verified_material.py` is an opt-in sidecar. It is not imported by
curation, preparation or runtime, does not activate contract v4, and supplies no
real material decisions or acceptance receipts. Tests are synthetic mechanics.
`chemistry()` remains unchanged. Full contract acceptance, actual source/material
review, independent code review and guarded integration remain required.

## API and input boundary

`load_reviewed_materials(binding, contract, registry, environment)` returns a
snapshot index. Pass the complete contract object, not an extracted chemistry
clause. It must contain `contract_version` and
`scientific_policy.verified_tested_material_v4.execution`. The execution object
must contain exactly these six fields, with the indicated types and values:

- `schema_version`: integer `1` (booleans and floats are rejected).
- `adapter`: `reviewed_material_full_graph_v1`.
- `rdkit_version`: the exact installed RDKit version string.
- `mass_algorithm`: `rdkit_descriptors_molwt`.
- `predictor_policy`: `complete_verified_material_graph`.
- `grouping_algorithm`: `sha256_utf8_rdkit_canonical_nonisomeric_smiles`.

Missing, empty, malformed, additional or unsupported execution settings fail
with `MaterialError` before review loading. The contract's RDKit version must
match both the live runtime and supplied environment. Scientific metadata outside
the execution object remains optional and is bound without adding eligibility
requirements. This compatibility check does not establish contract acceptance.
The complete JSON is hashed;
this module cannot detect a caller falsely presenting a fragment as a full contract.
The caller must provide the actual full accepted contract at integration.

Environment contains `rdkit_version`, equal to the installed RDKit version.
Additional environment fields are bound verbatim. Canonical JSON is UTF-8,
sorted keys, compact separators, non-ASCII preserved and nonfinite numbers refused.
File bindings are exact absolute `{path, sha256}` objects.

`binding` contains bindings for JSONL `inventory`, JSONL `decisions`, JSON
`receipt`, and an independently supplied `actual_review` descriptor. The latter
equals the exact receipt content plus its `receipt` file binding. It is the
leader's attestation of actual returned independent review, never producer
self-approval. Receipt fields are `schema_version: 1`, `result: PASS`, distinct
producer/reviewer execution IDs, `context`, sorted exact `scope_ids`, and a
nonempty `native_review` file binding. Synthetic contracts and receipts must
both carry `synthetic: true`. These labels do not authenticate an execution.

The context binds full contract SHA256, this implementation SHA256, environment,
inventory/decision file bindings, replay provider and source registry files.
Changing any of these invalidates the receipt. Loaded index resolution rechecks
code/environment and every bound file; no stale-cache bypass exists.

Registry contains `files` with source_id/path/sha256 and callable
`replay_native(source_entry, native_locator) -> native_body_dict`.
`replay_provider` contains an `implementation` file binding and exact callable
`__qualname__`; the callable's inspected source file must match. This checks
declared provider identity, not its correctness, closure variables, dependency
closure or independence. Provider implementations and dependencies need actual
source-adapter review and integration code bindings. No replay fallback exists.
Real integration must use existing source adapters and the curation guard's
exhaustive native membership checks. Synthetic tests replay temporary JSONL only.

Each inventory row contains:

- `observation`: source_id, source_sha256, native_locator object, candidate_key,
  exact native body object, raw_smiles and raw_smiles_field naming its body field.
  The observation ID hashes this complete object. Raw structure must match the
  exact replayed field. Native body membership compares canonical JSON bytes,
  preserving boolean/integer and integer/float distinctions recursively, including
  nested objects and arrays. Native schemas needing extraction must expose reviewed
  replay/adaptation without claiming invented native fields.
- Complete `support_candidates` and `blocked_support` arrays, including all
  conflicts; no duplicate entries. Their full content is bound and resolution
  requires an exact match. Caller omission before inventory creation cannot be
  discovered by this sidecar; exhaustive source guards must establish completeness.

Each decision names exactly one observation ID. Duplicate, missing or extra
decisions fail. Multiple observation decisions may share native records; changing
candidate key, source hash or body within an affected native row yields pending
review, never legacy fallback. Uninventoried native rows return NotApplicable:
coverage review must establish that the inventory includes every affected row.

## Reviewed decisions and graph mechanics

`status` is verified, reviewed_hold or unresolved. `material_class` is inorganic,
organic or unresolved, explicitly reviewed rather than inferred from carbon count.
Only verified inorganic decisions use the implemented graph path. Review evidence,
limitations, critical_conflicts and reopening_conditions are explicit fields.
Every blocked support entry has one exact-hash `support_resolutions` entry with
evidence and disposition unresolved or resolved_material_identity. Only an entry
whose reason is material_identity_conflict can use the latter. Ingestion and
nonmaterial conflicts cannot be globally waived by a material certificate.
The `support_resolutions` array is required even with no blocked support entries;
use an explicit empty array. Missing or non-array values fail during loading.

Verified decisions require fixed_composition=true, hydration_interpretation,
verified_smiles and a `formula_unit` object exactly matching `graph_facts()`.
That object retains canonical isomeric graph, nonisomeric grouping graph, formula,
atom/isotope inventory including explicit and implicit hydrogen, atom charge
inventory, total formal charge, full component multiplicities and positive finite
molar mass. Dummy atoms, variable composition, atom maps and malformed graphs
fail. No empirical reduction, water stripping or parent-fragment selection occurs.

When a source formula exists, source_formula_interpretation must bind that exact
source string, graph-consistent normalized formula and review evidence. This is
reviewed interpretation, not an automatic scientific formula parser. Optional
source formula, study ID and source dose-basis declaration can be null. Missing
these alone does not impose a new eligibility exclusion. Evidence must still
establish the tested formula unit and accepted reported-mass interpretation.

Changed raw-to-verified graph identity requires an exact `correction` containing
observation_id, raw_smiles, verified_smiles and evidence. It may implement a
source-reviewed material correction or equivalent-encoding representative; it
cannot modify dose, qualifier, range, route or experiment identity. Scientific
review must verify any formula-unit changes and encoding equivalence. Merely
filling these fields or matching hashes does not establish their truth.

Grouping is SHA256 of canonical nonisomeric full-graph SMILES UTF-8 bytes under
the pinned RDKit. Component order is invariant; multiplicity is retained. Distinct
formula-unit scales and hydrates remain distinct. Nonisomeric grouping deliberately
omits stereochemistry/isotope serialization per the proposed contract, while the
material atom ledger and mass retain isotopes; any desired isotope-sensitive
group policy requires a contract amendment. No family grouping is inferred.

## Resolution and projection

`resolve_material(observation, support_candidates, blocked_support, index)` returns
a dictionary with status NotApplicable, PendingMaterialReview, ReviewedHold or
VerifiedMaterial. Unresolved decisions remain pending. Reviewed holds, remaining
critical conflicts, unresolved support or unsupported dose bases remain held.
Supported bases are reported_material_mass and explicit_formula_unit_molar.
Elemental, active-ingredient and formulation conversions are not implemented.

Verified output separates raw_structure/raw_mw/source_formula,
verified_material_graph/formula_unit/formula_unit_mw, predictor_graph and
material_group_id. Compatibility original_structure/original_mw and
parent_smiles/parent_mw use the verified full material on this path only;
connectivity_id equals material_group_id and normalization is
verified_material_full_graph. Invalid corrected raw graphs retain raw_structure
and null raw_mw. No route, endpoint or target eligibility is supplied.

`validate_material_projection(curated_row, index, environment)` recomputes all
authoritative material columns and rejects missing/modified projections. Optional
target columns, if supplied, must be complete and satisfy
target_mol_kg = target_mg_kg / (1000 * formula_unit_mw) for material-mass reports
and target_pLD50 = -log10(target_mol_kg). Explicit formula-unit molar reports
require molar and log targets. Arithmetic checks cannot prove source dose values,
units, qualifiers or eligibility: source-native target replay remains mandatory.

## Remaining activation work

Draft-v4 precedence needs clarification: reviewed affected material routing must
cover carbon-containing inorganics and provisionally retained single fragments,
not merely old zero-carbon multifragment rejections. Material identity repair
must preserve all other eligibility/source/dose conflicts. This document does not
amend the draft or authorize promotion. Actual material/source review, scientific
encoding-equivalence review, complete affected inventory, contract acceptance,
integration bindings, downstream guard checks and fresh stage acceptance remain
the leader's next gates. No recovered-count claim is made.

Integrated organic schema 3 is described in [material curation integration](material-curation-integration.md). It requires the nested B selector and principal review; the isolated v1 API remains inorganic-only.
