# Trigger Tests

## Should use the full harness

1. “Discover the authoritative author-edited package, preserve its preimage, reconcile every claim with current evidence, and prepare the complete journal submission bundle.”
2. “Revise the main manuscript and reader-focused SI, remove redundant old summary tables without losing raw data, repair the workbook, and deliver a concise cover letter.”
3. “Renumber changed figures and sheets everywhere, export matching RGB TIFFs for all main, SI, and TOC images, render the DOCX files to PDF, and produce a current manifest and independent QA receipt.”
4. “Apply all author comments globally, audit citation ranges and abbreviations, run actual requested Codex imagegen with an AI disclosure, and inspect every source and rendered visual at final size.”
5. “Continue this active bundle run while I send several additional corrections; preserve the existing todo state and running conversion or image jobs, then revalidate only the affected downstream artifacts.”

## Should use only the protocol, a focused tool, or a direct answer

1. “What is the difference between Results and Discussion?”
2. “Rewrite this one sentence.”
3. “How should this abbreviation be introduced?”
4. “Check whether this one TIFF is RGB.” Use a focused image check unless it is part of bundle readiness.
5. “Has the paper been submitted to the journal?” Answer only from an actual journal receipt; preparing or publishing a bundle is not submission.

## Must stop, clarify, or downgrade the claim

1. Multiple candidate packages exist and the authoritative author-edited package cannot be identified.
2. A safe preimage cannot be made, the only author copy would be overwritten, or an unexplained preimage difference appears.
3. A numerical claim cannot be mapped to authoritative evidence or competing artifacts disagree.
4. The target-journal contract is unavailable: useful author-review work may continue, but the result cannot be called `submission-ready`.
5. Required DOCX parsing, image decoding, rendering, or workbook inspection cannot run: record the blocker rather than marking that gate passed.
6. Requested Codex imagegen is unavailable or its API fails: retain the failed execution evidence, never fabricate an image, and block the dependent asset/readiness claim.
7. External journal upload, credential use, payment, or legal attestation is requested without the required explicit authorization and human-supplied information.
8. Only an archive of documents exists: do not claim scientific reproducibility without the necessary data, code, environment, parameters, and executable steps.

## Practical pass/fail scenarios

| Scenario | PASS behavior | FAIL behavior |
|---|---|---|
| Many mid-run user steers | Merge every steer into the existing ordered plan and todo state, preserve running job handles and completed evidence, invalidate only affected descendants, and resolve all steers before final QA. | Restart from an older package, drop or duplicate todo items, abandon or relaunch jobs without accounting for their results, or finish while a steer is unresolved. |
| Authoritative package and preimage | Identify one authoritative working package, record the immutable author-edited preimage outside the bundle, and account for every approved difference. | Create several competing “final” packages, modify the preimage, include the recovery archive in the deliverable, or silently change author-controlled content. |
| Concise cover and reader-focused SI | Keep the cover letter journal-specific and shorter than a repeated abstract; use SI for necessary detail with scoped main-text pointers and no duplicated narrative. | Copy the abstract into the cover, inflate claims, or repeat main-text prose and tables throughout SI. |
| Legacy “all-old” summary-table redundancy | Remove or reorder the reader-facing table only after proving it adds no current reader value; preserve exact unrounded values, formulas, SMARTS, units, and provenance in evidence or the authoritative workbook. | Delete it by age or appearance, lose raw precision or provenance, or leave a stale table that contradicts current evidence. |
| Deleted or renumbered figures and sheets | Update all main/SI callouts, captions, legends, panel labels, worksheet names/title cells, formulas, named ranges, chart sources, TIFF files, rendered PDFs, and manifest hashes; remove obsolete exports. | Renumber only captions, leave stale links or `#REF!`, retain an extra TIFF, or update a display value while its formula still targets the old sheet. |
| First-citation order with ranges | Expand numeric lists and ranges during the audit: a first citation such as `[1–3]` introduces each resolvable entry in order; semantic support is checked, and bracketed SMARTS is excluded. | Let `[1, 3–4]` precede the first citation of `[2]`, accept a range containing a missing entry, parse SMARTS as a citation, or retain a stale bibliography target. |
| Matched TIFF inventory | Supply exactly one actual RGB TIFF for each expected main, SI, and TOC image and verify decoded RGB pixels, native dimensions, orientation, and aspect ratio against the authoritative embedded image. | Pass on filenames or dimensions alone, tolerate a missing/extraneous/stale TIFF, or accept a TIFF whose pixel dimensions match but decoded pixels do not. |
| Requested Codex imagegen | Run the actual configured imagegen/API operation, retain a non-secret receipt and selected-output hash, verify scientific content plus every icon and text element, and add the required AI disclosure. | Draw a placeholder and call it generated, claim success after an API error, use unverified icons/text, or omit disclosure. |
| Correct numbers but unreadable graphic | Open source/TIFF and current DOCX/PDF renderings at final publication size; regenerate until labels, annotations, legends, and primary values are readable, unclipped, and high contrast. | Approve because source dimensions, hashes, or plotted numbers are correct while final-size text is clipped, crowded, too small, or low contrast. |
| Metric scope | Preserve exact authoritative values and precision and label full-fit, held-out, positive-only recovery/concordance, and consistency results distinctly. | Mix scopes, silently round or copy an old value, call positive-only recovery full external validation, or use a software timeline as evidence. |
| Current document and manifest set | Parse the expected main/SI DOCX, inspect current rendered PDFs and workbook dependencies, and bind the complete safe bundle plus structural and external-QA receipts to current hashes. | Accept missing/stale DOCX, PDF, workbook, manifest, or receipt; trust an Office lock file; or treat an old render receipt as current. |
| Archived-document reproducibility | State exactly what the archived package permits a reader to inspect and disclose missing executable inputs. | Claim that archived DOCX/PDF files alone reproduce the analyses. |
| No work needed | When discovery and hash-bound checks show the current artifact already satisfies its contract, record a no-work-needed disposition and stop with it unchanged. | Force cosmetic rewrites, regenerate identical assets, or touch timestamps merely to show activity. |

## Readiness assertions

- `review-ready` passes only when the current authoritative package can be evaluated coherently by the authors and every remaining decision or limitation is explicit.
- `submission-ready` passes only when the target-journal contract and complete bundle are satisfied, the automated structural audit passed, and external scientific, semantic-citation, and final-size visual QA evidence is current and bound to final hashes.
- `journal-submitted` passes only with an actual external journal confirmation or receipt. Neither `review-ready` nor `submission-ready` implies submission.
