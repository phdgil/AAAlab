---
name: classroom-slide-design-harness
description: Create, shorten, visually redesign, or iteratively improve project-based lecture slides. Use when explicitly requested for classroom slide design, especially English slides for learners with limited English. Coordinates teaching scope, evidence, visual production, rendering and independent review. Not for answer-only questions, spreadsheet-only edits, or publishing private course data.
---
# Classroom Slide Design Harness

## Mission
Help students see the key information quickly and begin meaningful work. This is a reusable design-and-review protocol, not a fixed PowerPoint theme or an automatic text-to-three-cards generator.

**Message before theme.** Vary color, composition, and visual form to emphasize meaning. Do not preserve a uniform theme at the expense of attention or comprehension.

## Defaults, not unchangeable course rules
- Audience: undergraduates with limited English proficiency; student-facing text in English.
- Format: a 15–20-minute explanation followed by a real project. Start with about 10 slides and a planned 17-minute speaking budget.
- One message and one student action per slide. Students should spot both in about five seconds; this is a review question, not a measured learning claim.
- Prefer 20–45 visible words; up to 65 when necessary, or 80 for a genuine evidence/table example. Exclude page numbers and source footers, not inconvenient body text.
- Prefer body text 24–30 pt, key words 32–48 pt, necessary labels at least 18 pt, captions/IDs at least 16 pt, source footers at least 11 pt. Cut content instead of shrinking it to fit.
- Use short verbs: Open, Match, Draw, Search, Read, Save, Count, Check. Define unavoidable specialist terms in a short phrase and speaker notes.
- User-specified audience, language, duration, or accessibility needs override defaults. Do not expand the lecture merely to cover all background knowledge.

## Phase 0: Context Check
1. Read the current deck, its generation source, latest user feedback, and intended course output directory. If the deck has manual edits, reconcile them before running an older generator.
2. Identify the practical question, required student deliverable, existing approved inputs, and time budget. Inspect context before asking; clarify only materially missing choices.
3. Record protected inputs and hashes. Do not overwrite the source workbook, source deck, or user's application session.
4. Create one brief from `templates/brief.example.json`, replacing its example fields with the current lesson. A blank source list means evidence must be collected, not permission to invent it. Keep a single active brief per objective.

## Phase 1: Teaching and evidence edit
Lead responsibilities are in `references/roles.md`.
- Begin with a concrete question, then show what students will do. End with the actual file/task and a simple submission checklist.
- Compress database/tool catalogs into one useful comparison or route when possible. Do not allocate one slide per tool by default.
- Show one traceable real example when data interpretation is required. Verify identity, endpoint, species/system, value, units, and source before drawing it.
- **No invented measurements, screenshots, citations, or claims of completed searches.** Mark illustrative diagrams as diagrams and synthetic teaching examples as synthetic. Never make a mock record look like a real database page.
- Separate observation, hypothesis, prediction, and conclusion. Preserve scientifically necessary qualifications on the slide; put supporting detail, sources, and speaking prompts in notes.
- Do not publish or upload private course data. Generalizing this harness never authorizes publishing decks, student information, product identifiers, unpublished findings, absolute local paths, or access tokens.

## Phase 2: Visual producer
Read `references/visual-playbook.md` and the brief. Use a producer with actual file-editing tools.
- Choose the visual form from the message: question/contrast, molecule or image, causal arrows, workflow, comparison table, annotated evidence, duplicate-count diagram, work map, action poster.
- No generic three-card layout. Never add filler to populate a template. For ten slides, aim for at least six meaningful layout families; no repeated adjacent layout without a teaching reason.
- Use message-specific color changes, including light/dark backgrounds. Navy, violet, blue, teal, coral and amber are options, not mandatory swatches on every slide.
- Preserve contrast. Color never carries the only meaning: label categories, directions and warnings. Do not use a red warning to imply that a merely hypothesized hazard is established.
- Give real structures, figures, and assay values more space than prose. Keep chemical identity and image provenance visible or in notes; flag unresolved isomers/mixtures.
- Create editable PPTX objects where feasible; do not flatten the whole deck into screenshots. Keep generator, brief and evidence ledger beside the project, not inside the installed skill.
- Deliver a slide manifest with title, intended message/action, layout, dominant colors, planned minutes, word count and source IDs. Total speaking time includes any live demonstration.

## Phase 3: Render and QA Review
The coordinator runs tools once across the completed draft; delegated producers/reviewers skip project-wide gates and formatters.

Install audit dependencies separately, with user/environment authorization:
```bash
python -m pip install -r <skill-dir>/scripts/requirements.txt
```
Structural-only audit (cross-platform):
```bash
python <skill-dir>/scripts/audit_pptx.py lesson.pptx --output-dir review-01
```
On Windows with installed desktop PowerPoint:
```powershell
python <skill-dir>/scripts/audit_pptx.py lesson.pptx --output-dir review-02 --render-office
```
Use a new review directory each run. `--overwrite` explicitly permits replacing generated outputs; never aim it at a source directory.

- Inspect all rendered slides, not just the PPTX ZIP or shape coordinates. Include table cells and nested groups in checks.
- Check clipped text, collisions, tiny labels, misleading arrows, and hyperlink contrast. PowerPoint theme colors may override intended hyperlink text colors; shape-action links can preserve readable labels.
- Review a contact sheet for visual repetition and reading load, and full-size images for dense evidence slides.
- Have an independent reviewer read `references/roles.md`, the brief, evidence and rendered artifacts. Obtain slide-numbered severity findings and an approve/revise verdict. Do not claim a reviewer ran without an actual result.
- When delegation is unavailable, use the same stages locally and explicitly label the review as self-review. No fictitious team or required model vendor.
- Missing renderer means visual verification is unavailable. Structural-only success must never be called visually approved. If another installed renderer is used, record its name and inspect that output; do not silently claim PowerPoint parity.

## Phase 4: Revision and release
- Fix material findings within the existing time budget. Do not create more slides merely to avoid editing.
- Rerender changes and recheck affected slides plus neighboring composition. Resolve scientific integrity issues before release.
- Verify protected-input hashes, editable PPTX, PDF/render status, hyperlinks, and notes. Report only checks actually run and the exact output files.
- Preserve the prior deck unless replacement was explicitly requested. If open or locked, create a named revision; never close or discard the user's unsaved PowerPoint work.

## Phase 5: Repeat and improve
Use `templates/feedback.example.json` for future feedback. Record the user's observation, generalized design rule, scope (this course or all courses), changed slides, and evidence of rechecking. Update the existing brief and relevant rule rather than spawning another competing harness.

A local course pointer may reference this installed skill when the user requests setup; do not install broad automatic triggers without authorization. Packaging/publication belongs in an isolated checkout/session, with only approved generalized material transferred out of the course context.

## Test Scenarios
See `references/trigger-tests.md` for invocation boundaries and failure scenarios. Run `scripts/validate_harness.py` for installed package structure and `scripts/test_audit_pptx.py` for behavioral tooling tests. These do not demonstrate improved student learning; user review and classroom evaluation remain separate.
