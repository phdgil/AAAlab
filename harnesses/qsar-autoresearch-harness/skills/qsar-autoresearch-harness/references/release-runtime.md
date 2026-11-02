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

## Outputs

- `release_manifest.json` records the actual run/contract/execution identity,
  training and runtime code hashes, all accepted stage candidate/review hashes,
  commands, checks, summaries, coverage, limitations, input bindings, improvement
  decision/model lock, and an exact artifact inventory.
- `training_summary.html` renders actual stage coverage and recognized accepted
  JSON summary fields, including counts, metrics, environments, feature schemas
  and limitations when supplied. Every scientific string is HTML escaped.
  Missing values remain absent; no scores, quality claims or acceptance verdicts
  are invented. Links to copied summaries use package-relative paths.
- `summaries/STAGE/HASH-NAME.json` contains byte-identical copies of recognized
  accepted JSON summary artifacts up to 16 MiB. The size limit governs packaging
  only; it does not limit data or scientific training coverage.

Recognized names include `evaluation.json`, `terminal_evaluation.json`,
`final_fit.json`, `final_selection.json`, `search_coverage.json`, `preflight.json`,
`next_experiment_manifest.json`, `exposure_ledger.json`, `source_manifest.json`,
`curation_manifest.json`, `preprocessing_manifest.json`, `feature_manifest.json`,
`collection_manifest.json`, `training_manifest.json`, `training_summary.json`,
`exclusion_summary.json`, `source_registry.json`, and `collection_gaps.json`.

Other accepted outputs, including large/raw datasets, CSVs, NPZs and model
artifacts, remain external references with their exact absolute paths, hashes
and sizes. Preserve these artifacts and access rights for reproducibility; the
package is an evidence index, not a self-contained dataset archive. Unrecognized
JSON is referenced rather than parsed. Recognized summaries larger than the
copy limit remain fully referenced in the inventory.

The packager omits registered sealed inputs from its inventory and input-binding
listing and does not parse or copy sealed target rows. Runtime validation may
verify their existing hashes as part of the accepted terminal dispatch. The
terminal results shown are only the accepted terminal summary artifacts.

## Handoff and limitations

A completed package remains a producer output requiring a separate release
review. Submit `release_manifest.json`, `training_summary.html`, and all copied
summary files through the runtime; do not self-approve. The packager never calls
`submit` or `review` and never changes a stage to accepted. Existing packages
are not overwritten: corrections require a new runtime attempt.

The runtime stores leader-attested execution provenance; it cannot independently
authenticate native agents or sandbox arbitrary commands. Source and review
limitations are preserved verbatim in stage records and copied summaries.
Scientific interpretation still belongs to the independent reviewers.

Validation uses `python scripts/test_qsar_release.py -v` from the repository
root. Tests create explicitly synthetic runtime submissions/reviews, including
accepted final-fit and terminal stages, and exercise rendering, lineage,
staleness, missing prerequisites, dispatch ownership and overwrite rejection.
