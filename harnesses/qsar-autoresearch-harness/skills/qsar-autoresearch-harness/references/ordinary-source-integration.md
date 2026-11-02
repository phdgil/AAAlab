# Ordinary scientific adjudication in curation

The ordinary engine's [typed evidence contract](ordinary-adjudication-runtime.md)
is integrated into strict curation for NPASS, ICE oral, OECD and TOXRIC. This is
an execution mechanism, not scientific approval of any source or installation.
Actual full-contract, source-decision and material reviews remain required.

Pass `--adjudication-config` as a source-ID map. Ordinary entries carry
`kind: ordinary_source_adjudication_v1`; historical HSDB bindings retain their
existing schema. Unknown sources, duplicate inline/external bindings, unsupported
ordinary adapters and mismatched full contracts reject. Ordinary integration
requires strict source coverage. This does not enable SQL/HSDB unknown-origin
holds or change their source adapters.

Every physical row first becomes an untouched native ObservationV2 and is
resolved against the independently bound complete-U engine. A validated
`ScientificResult` carrier supplies only the reviewed route before task matching
and `pre_identity_v1`; arbitrary dictionaries cannot supply route overrides.
Native values, units, inequalities, raw row bodies and legacy row/output IDs are
preserved. ICE uses its existing `Data` default worksheet when no explicit sheet
is supplied. Explicit sheet names and other adapters retain their old defaults.

Material-enabled runs store the result separately from raw rows. Their native
body and member key remain unchanged, while the candidate projection becomes
`{kind: ordinary_reviewed_projection_v1, native_adapter_row, scientific_result}`.
Thus route recovery changes the observation/classification hash and can move a
missing-route row from N into E. An origin hold is not a material exemption.
Classification must cover every resulting E member, including held-origin oral
rows; pre-recovery classification is stale. Staged result bytes must still equal
the validated carrier at routing time. Existing material native-identity,
full-graph, support and lifecycle checks remain in force.

Final scientific gating runs after identity/material routing. Any typed remaining
scientific issue blocks curation even when another property has a valid hold.
Origin and route treatments remain separate in disposition and identity ledgers;
held rows emit no training/development/terminal outputs. Affirmative prediction
exclusion stays distinct from bounded unknown-origin and unavailable-route
quarantine. Ordinary secondary reports and supported statistical estimates or
deterministic transforms retain the accepted native numeric/qualifier policy.

`curation_manifest.json` records `ordinary_accounting` per configured source,
including complete-U summary and transitive evidence references. The guard
reconstructs a fresh curation from original inputs for both ordinary-only and
material-enabled runs, checks all ledgers/summary, and returns all evidence
bindings. It also exposes ordinary accepted-contract descriptors through the
existing runtime accessor. Runtime PASS verifies those descriptors against the
actual accepted contract stage; cached reuse checks the complete dependency set.
Preparation inherits the same bindings. Changed context, notice, receipt, source
or code bytes invalidate reuse before downstream output acceptance. No new
receipt framework or bypass selector is introduced.

Failed final gates, stale evidence and publication errors leave no success
manifest. Material abort closes the active observation cursor before deleting
its owned staging directory, including Windows failures during iteration. Partial
diagnostic CSVs may remain after publication failure; they are not accepted
outputs without valid manifests and independent review.

Validation entry point:
`python -m unittest scripts.test_qsar_ordinary_integration -v`.
Synthetic fixtures cover the eight-row NPASS source, duplicate support,
mixed equal-valued source columns, four adapter positives, route recovery,
separate holds, exact membership/IDs, original inequality, stale material
classification, forged outputs/results, runtime contract linkage and cached
freshness, preparation propagation, and rollback. Synthetic envelopes exercise
mechanics only. Independent review still establishes source semantics,
extraction fidelity and completeness; the engine cannot discover withheld facts.
No full-dataset scalability claim follows from finite fixtures.
