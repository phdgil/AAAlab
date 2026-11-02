# Source-bound narrative adjudication

Conservative parsing protects against invented measurements, but a rejected narrative can still contain eligible observations. Do not count a parser rejection as a scientific exclusion or promote incomplete extraction to complete source coverage.

## Freeze the evidence

`scripts/qsar_adjudication.py` exports a diagnostic HSDB inventory:

```text
python qsar_adjudication.py --source ORIGINAL_ARCHIVE --source-id EXACT_CONTRACT_SOURCE_ID --output-dir NEW_DIRECTORY
```

Use the exact source ID from the contract. Inventory identities bind original archive bytes, member, document and field locators, and exact extracted-text hashes. The inventory includes every `ntxv` field and other LD50-containing field, plus document-level accounting when no candidate field exists. It does not filter on rat/oral keywords. Its manifest is diagnostic evidence, not an accepted collection or curation receipt.

Preserve native text/XML and document context. ElementTree serialization is not the original XML byte sequence; the archive digest provides the original-byte anchor. A compact work queue is a viewing aid, not a substitute for the bound inventory or context.

## Produce decisions

Account for each inventoried field. A reviewed field can use its conservative scalar parse, yield zero/one/many evidence-backed observations, be scientifically excluded with a source-based reason, or remain explicitly unresolved. The loader's strict schema and synthetic examples in `test_qsar_adjudication.py` define executable field names; do not fabricate a receipt to make an incomplete decision file load.

Each extracted observation needs exact evidence spans and matching quotes for its scientific assertions. Context evidence must refer to the same bound source document. Identify the measured chemical before applying identity resolution: a document can discuss comparators, other substances, mixtures or metal-equivalent doses. A document CAS does not automatically identify every narrative measurement. Do not introduce ungrounded CAS or replacement structures.

Preserve native numeric spelling, qualifier, unit, species/route and source locator. Keep sex, strain, study/dependence links and confidence intervals separately. A point estimate of 2900 with a 2300–3500 confidence interval is one point estimate with uncertainty; a measurement reported only as a range is not an exact target. Multiple male/female/combined estimates remain separate reported observations with shared-study dependence, not independent experiments or an invented average.

Source-specific unit normalization needs explicit evidence and independent review. Preserve the original unit alongside the supported canonical unit. Do not globally fold capitalization, guess an unknown prefix, remove a time denominator, or infer mass from volume.

## Check study context before accepting a scalar

A successful parse or valid evidence span proves extraction, not scientific correctness. Review same-document study context for both parser rows and manual observations. Record the passages examined and the basis for linking them: a specific study reference and compatible population/material are stronger evidence than a shared handbook citation or an equal value.

Compare the point and qualifier, dose denominator, tested material, sex, strain and study identity across linked passages. A scalar may omit a formulation, hydrate, stabilizer or elemental-dose basis described in its narrative. Preserve that evidence; do not silently assign the document's pure-compound molecular weight. A dosing vehicle alone does not prove that the reported dose is preparation mass. Conflicting values or denominators require a source-supported resolution or explicit scientific quarantine, not an average or a preferred number.

Apply the accepted contract's same eligibility standard to ordinary parser rows and manually recovered scalars. Do not introduce a universal requirement for a second method narrative or complete optional metadata. Genuinely unreported sex, strain or study details remain unknown unless the contract makes them essential. A separate passage about a formulation is not itself proof that a clearly attributed scalar measured that formulation; assess linkage and contrary evidence. Conversely, do not ignore a concrete tested-material or measurement conflict merely because a scalar parsed successfully. Any new terminal quarantine rule requires support in the accepted scientific policy.

Carry supported population and material metadata into each observation. Distinguish repeated scalar/narrative representations of one estimate from different sex-specific estimates in the same study. Report observation counts separately from unique studies and compounds. A context screen based only on matching numbers or a small list of unit spellings cannot establish completeness: coordinated values, body-weight aliases and named-material units can evade it.

The independent reviewer states which measurement bodies and linked context were examined and which remain unreviewed. Do not turn a bounded review with no identified defect into full-corpus approval. A specific context conflict can invalidate an earlier parser decision; a generic unresolved screening result does not by itself replace a previously justified decision.

## Independent review and curation

The reviewer checks all residual decisions, including exclusions, observation multiplicity and attribution. Bind its actual returned evidence to inventory, decisions, source, scientific policy and code. Keep configuration/data bindings separate from the review receipt to avoid circular hashes. Execution IDs and local leader attestations document provenance; they are not authentication or scientific proof by themselves.

Declare inventory, decisions, receipt and relevant code as runtime dependencies. Do not accept an inline `reviewed: true` assertion. A producer cannot approve its own decisions. Revisions invalidate affected downstream results.

For a run with an already accepted scientific contract, pass an external artifact map using `qsar_data.py curate --adjudication-config MAP.json` alongside the usual contract, registry and output arguments. The map keys are exact declared source IDs and its values contain only adjudication artifact bindings. It cannot override task, chemistry or other scientific policy. The curation manifest separately binds this map; the original contract bytes and digest remain unchanged. Register the map and its referenced artifacts in the curation dispatch before execution.

Adjudicated observations still pass ordinary task, qualifier, unit, identity and material checks. The field ledger and observation ledger answer different questions: every field is accounted once; each observation has a stable source/evidence identity; each retained output traces to one observation. Never emit both the original parser row and its replacement observations. Equal values alone do not establish duplicate observations.

Unresolved extraction remains visible and blocks a full narrative-completeness claim. Scientific quarantine requires an affirmative evidence-based reason; parser inconvenience, missing decisions, or a desire to start training is not such a reason.
