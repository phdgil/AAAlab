# Release packaging

`scripts/qsar_release.py` assembles reproducibility evidence and an HTML training
summary from a live runtime run. It performs no fitting, model selection,
terminal inference, or independent review.

```text
python qsar_release.py --run-dir RUN --dispatch-id RELEASE_PRODUCER_DISPATCH
```

The Python API is `package_release(run_dir, dispatch_id)`. There is intentionally
no arbitrary destination argument. The destination is the current release
producer's `owned_output_dir`, checked against the runtime stage, attempt and
dispatch identity. A bound or currently running native producer is required.
When using `Runtime.execute`, register the CLI with `{run_dir}` and
`{dispatch_id}` substitutions. The runtime's `execution.log` remains untouched.

The packager obtains `Runtime.status()`, which revalidates bound artifacts and
marks stale stages. It then verifies the current dispatch, exact upstream
acceptance tokens, all accepted stages through terminal evaluation, native
binding evidence, accepted STOP/model lock, final-fit acceptance, artifact
hashes, and review evidence. It rechecks inputs and the live runtime before
declaring the package complete. A detached state JSON or a caller-supplied
`PASS`/`reviewed` flag is never accepted as authorization.

External evaluation is optional and independent of terminal evaluation. Only an
`accepted` entry in the runtime's separate `external_evaluation` state is
packaged as `external-evaluation` evidence. Its candidate must declare
`evaluation_mode: external`, with a matching independent PASS receipt. The
packager verifies its current upstream acceptance, rehashes its outputs and
review evidence, and records its exact candidate/review hashes in the release
identity. It rechecks that acceptance before completion. Pending, unreviewed,
failed, blocked or stale external work is not copied as accepted results; its
recorded status and whether accepted external evidence was included are explicit
in the manifest and HTML. External acceptance is not required when no external
result is packaged. The manifest's `packaged` flag means that accepted external
evidence is indexed; individual artifacts can still be external hash references,
not copied or accessible files.

## Reporting language

The accepted research contract may contain:

```json
{"reporting": {"language": "ko"}}
```

`reporting.language` accepts exactly `en` or `ko`. Omission of `reporting`, or
omission of its `language` field, selects English (`en`). A non-object
`reporting`, null language, unsupported language, different capitalization or
surrounding whitespace is rejected before package files are written. Language
is not inferred from the host locale, endpoint names or scientific data. There
is no CLI override: packaging reads the hash-bound contract and records the
resolved language in `release_manifest.json` under `reporting.language`.

Language changes report headings, column labels, field labels and unavailable
markers, not model selection, metrics, recorded scientific values or limitations.
Korean reports use explicit labels such as `개발 교차검증 지표`, `최종 평가`,
`외부 평가`, `선택된 분자 표현·모델`, and `탐색 수행 범위·기록된 상태`.
`render_summary(manifest, summaries)` retains its two-argument interface and
uses the same optional `manifest.reporting.language` configuration. It requires
publication-safe inputs and rejects row-bearing manifest or summary values
before producing HTML; `package_release` creates that publication view.

## Outputs

- `release_manifest.json` records the actual run/contract/execution identity,
  training and runtime code hashes, all accepted stage candidate/review hashes,
  commands, checks, summaries, coverage, limitations, input bindings, improvement
  decision/model lock, an exact artifact inventory, and `embedded_omissions`
  receipts in place of row-bearing embedded fields.
- `training_summary.html` starts with a task/endpoint table, followed by actual
  stage evidence, recognized accepted JSON summary fields, limitations and the
  artifact inventory, with a separate table of embedded omission receipts when
  needed. Every scientific string and omission field path is HTML escaped. Missing overview
  fields are explicitly `Unavailable` (`정보 없음`), never zero or a fabricated
  score. Detailed evidence still lists only recorded fields. Links to copied
  summaries use escaped package-relative paths.
- `summaries/STAGE/HASH-NAME.json` contains byte-identical copies of recognized
  accepted JSON summary artifacts up to 16 MiB that do not contain row records.
  The size limit governs packaging only; it does not limit data or scientific
  training coverage.

### Endpoint overview evidence

Only task-level records in these accepted stage/name pairs populate the overview:

| Overview field | Accepted evidence |
| --- | --- |
| Task/endpoint identities and cohort counts | `preprocessing/preprocessing_manifest.json`: `task_counts[TASK]`, with separate `development`, `terminal`, optional `external` and `excluded` counts |
| Chosen representation/model | `training/final_selection.json`: `tasks[TASK]`; `final-fit/final_fit.json`: `models[TASK]`; selection and final-fit records remain separately labeled |
| Development CV metrics | `development-evaluation/evaluation.json` with `mode: development`: `results[]` |
| Terminal metrics | `terminal-evaluation/terminal_evaluation.json` with `mode: terminal`: `results[]` |
| External metrics | Separately accepted `external-evaluation/external_evaluation.json` with `mode: external`: `results[]` |
| Search coverage/status | `training/search_coverage.json`: task-specific `cells[]` and explicitly run-level report `status` |

The task count is the number of distinct task IDs with such recorded evidence,
not a count of scientifically completed endpoints. Task names are not inferred
from a contract's intended work, producer/reviewer prose, run-level coverage or
arbitrary `training_summary.json` fields. A run-level metric or trial total is
not assigned to every endpoint. Evaluated `metrics.n` stays in its metric scope;
it does not substitute for prepared development, terminal or external row counts.

Metric dictionaries are displayed as recorded, including null/undefined scores
and their reasons. No scores, baselines, ranking, task types, positive classes or
decision thresholds are inferred. Classification results display recorded
`task_type`, `positive_class`, `threshold`, per-fold thresholds and threshold
policy beside the relevant metrics. Multiple candidate records remain distinct.
An external cohort count alone never creates an external metric, and a terminal
metric never becomes an external result. Exposure history and external-validation
claims remain available in the accepted summary details and linked artifacts.

Search counts sum the recorded task's cell fields over all recorded scopes,
separating expected search trials and anchors. A missing or invalid count in any
cell makes that total unavailable rather than reporting a partial sum. Completed,
failed and pending counts include anchors and search trials; they do not measure
distinct configurations. A recorded `complete`, `incomplete`, `failed` or
budget-related search status is not a declaration of scientific completion.
The overview lists its accepted source artifacts; the detailed sections retain
their links, original field paths and limitations.

Recognized names include `evaluation.json`, `terminal_evaluation.json`,
`external_evaluation.json`, `final_fit.json`, `final_selection.json`, `search_coverage.json`, `preflight.json`,
`next_experiment_manifest.json`, `exposure_ledger.json`, `source_manifest.json`,
`curation_manifest.json`, `preprocessing_manifest.json`, `feature_manifest.json`,
`collection_manifest.json`, `training_manifest.json`, `training_summary.json`,
`exclusion_summary.json`, `source_registry.json`, and `collection_gaps.json`.

Other accepted outputs, including large/raw datasets, CSVs, NPZs and model
artifacts, remain external references with their exact absolute paths, hashes
and sizes. Preserve these artifacts and access rights for reproducibility; the
package is an evidence index, not a self-contained dataset archive. Unrecognized
JSON is referenced rather than parsed. Recognized summaries larger than the
copy limit remain fully referenced in the inventory. A recognized filename is
not permission to publish row data: JSON containing row identity fields
(`output_row_id` or `row_id`) or nonempty `rows`/`source_records` containers is
kept as an external hash reference with a `copy_omitted` explanation, not copied
or rendered. Scalar row counts and aggregate metric dictionaries remain usable.

### Embedded evidence omission schema

The same row-data test applies to every embedded producer field and every field
of the independent review, including arbitrary structured review extensions.
Nested row records in coverage, checks, findings or limitations are not an
alternate publication route. A row-bearing field is omitted **in full**, rather
than partially rewriting accepted evidence; unaffected fields remain verbatim.
Aggregate-only structures such as `{"rows": 24, "metrics": {"n": 24}}` remain
usable, and scalar `rows`/`source_records` counts in safe summary artifacts are
rendered. Aggregates within a mixed row-bearing field are omitted with that
field, not inferred or reconstructed. Original accepted submissions, reviews,
artifacts, candidate/review hashes and acceptance tokens are never changed.

Each omitted field is replaced with `{"release_omission": "embedded-N"}`. The
manifest's `embedded_omissions` list (empty when none) contains exactly these
receipt fields:

| Field | Meaning |
| --- | --- |
| `id` | Package-local `embedded-N` identifier matching the replacement marker |
| `stage` | Published stage, including `external-evaluation` for separately accepted external work |
| `source` | `candidate` or `review`, identifying the original accepted record |
| `source_hash` | Unchanged accepted `candidate_hash` or `review_hash`, not a hash of the redacted view |
| `pointer` | RFC 6901 JSON pointer to the omitted field in that original record, with `~` and `/` escaped; empty for an entirely omitted row-shaped review |
| `reason` | Exactly `row-level-data` |
| `encoding` | Exactly `qsar-runtime-canonical-json-utf8` |
| `sha256` | SHA-256 of the complete omitted field encoded as below, never its contents |
| `size` | Byte length of that encoded field, not a row count |

The encoding uses the runtime's `canonical`: `json.dumps(value, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)`, then UTF-8. This is an inline-value
receipt, not a claim about the byte encoding of an artifact file. Receipt order
and IDs are package-local; the source hash, pointer and payload hash bind the
original evidence. The HTML lists receipt identifiers, source hashes, field
pointers and payload hashes without including row contents or links to them.
If a review itself has a top-level `row_id`/`output_row_id` or a nonempty
structured `rows`/`source_records`, its whole record is omitted except for its
candidate/review hashes, separate producer/reviewer IDs and verdict. These
retained fields establish the existing acceptance identity,
not permission to disclose its row-level details.

The packager omits paths registered in `sealed_inputs`, `external_inputs` or the
monotonic `custodian_only` registry from its inventory and input-binding listing,
before parsing or copying JSON. This also protects external source files and
combined grouping/exposure/exclusion ledgers even when their names resemble a
summary. It never parses or copies sealed target rows. Runtime validation may
verify current accepted dependencies as part of the accepted terminal dispatch;
historical custody hashes are not new scientific acceptance. Packaging rechecks
that the protected path registry has not changed before declaring completion.
Terminal and external results shown are only accepted aggregate summary evidence.
Byte-identical accepted manifests can still contain path/hash metadata referring
to protected artifacts. These are preserved, not redacted or rewritten. Such
metadata is provenance, not a claim that a file is packaged or accessible; it
does not create a clickable link to that underlying artifact. The HTML states
this distinction explicitly.

## Handoff and limitations

A completed package remains a producer output requiring a separate release
review. Submit `release_manifest.json`, `training_summary.html`, and all copied
summary files through the runtime; do not self-approve. The packager never calls
`submit` or `review` and never changes a stage to accepted. Existing packages
are not overwritten: corrections require a new runtime attempt. Package/job
completion is not scientific completion, model quality, or proof of independent
external validation.

The runtime stores leader-attested execution provenance; it cannot independently
authenticate native agents or sandbox arbitrary commands. Source and review
limitations are preserved verbatim in stage records and copied summaries unless
row-bearing, in which case the field or artifact omission policy above applies.
Scientific interpretation still belongs to the independent reviewers.

Validation uses `python scripts/test_qsar_release.py -v` from the repository
root. Tests create explicitly synthetic runtime submissions/reviews, including
accepted final-fit and terminal stages, and exercise rendering, lineage,
staleness, missing prerequisites, dispatch ownership and overwrite rejection,
language validation/localization, escaping, missing/undefined fields,
endpoint/report evidence separation, classification and external metric scopes,
separate external acceptance and stale/unreviewed external results, and
protected/row-level artifact exclusions. Focused package tests also cover
accepted terminal/external inline rows, nested producer/reviewer fields and
extensions, omission hashes and pointers, scalar aggregate counts, row-shaped
reviews, unchanged source bytes/acceptance and rejection of unsafe renderer
inputs.
