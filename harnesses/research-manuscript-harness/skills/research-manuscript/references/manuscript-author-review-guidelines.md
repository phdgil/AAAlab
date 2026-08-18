# Manuscript Author Review Guidelines

## Scientific writing

- Write for readers encountering the work for the first time and reading from beginning to end.
- Use publication-level scientific prose, not classroom explanations, lab notes, or an early graduate-student draft style.
- Make literature paragraphs understandable without requiring readers to retrieve every citation.
- Explain what prior studies did, what they established, and how the present work differs.
- Remove duplicated reasoning, vague claims, and sentences that do not advance the argument.
- Use the narrowest claim supported by current evidence.

## Author block template

When this harness is used for the Duksung Women’s University project, place this block beneath the title:

1. Yebin Choi
2. Seoyoon Lee
3. Seeum Han
4. Hyun Kil Shin, corresponding author

Affiliation:

Department of Artificial Intelligence (AI)-based drug development, Duksung Women’s University, Seoul 01369, Republic of Korea

Corresponding author:

Hyun Kil Shin; phdgil@duksung.ac.kr

For other projects, obtain author order, affiliations, corresponding-author details, funding, and competing-interest statements from the user; never infer them.

## Abbreviations

- Treat the abstract and main body as independent scopes.
- Define an abbreviation at first use in the abstract.
- Define it again at first use from the Introduction onward.
- Write the full term first and the abbreviation in parentheses.
- Expand table abbreviations if they were not defined in preceding main text.
- Do not invent expansions for proper names such as PubChem, RDKit, PageRank, DrugCentral, or GitHub.

## Introduction

- Explain cited research in enough detail for readers who have not read it.
- Describe established approaches, inputs, outputs, and limitations.
- Avoid a novelty paragraph that duplicates the objective paragraph.
- End with explicit research questions and a bounded contribution.
- Number references by first citation order.

## Materials and Methods

- Provide reproducible detail without website-navigation instructions.
- Name authoritative data sources and category hierarchies.
- State structure acquisition, parsing, standardization, canonicalization, and deduplication rules.
- Define fingerprint type, radius, bit length, similarity metric, and cutoff.
- Explain positive sets, comparison sets, exact-overlap exclusions, thresholds, and folds.
- Keep scoring-function construction coherent while identifying genuine component differences.
- Describe structural-pattern discovery with graph nodes, edges, restart/damping, ranking, fold design, and leakage controls.
- State when a method was tested for every category; reserve category-specific adoption for Results.
- Split distinct analyses into subsections.

## Results

- Present evidence in the order that determines later decisions.
- Begin with overlap and specificity when they determine model selection.
- Explain why every function was retained, merged, or rejected.
- Present comparison-set construction before benchmark interpretation.
- Separate primary benchmarking, external comparison, specificity, uncertainty, rebuilding, and augmentation.
- Report failed candidates and failed promotion experiments.

## Tables and figures

- Do not duplicate the same result in both a figure and a table without added value.
- Put figures next to their legends and near first discussion.
- Combine closely related plots when a multi-panel figure improves the narrative.
- Label panels `(A)`, `(B)`, `(C)`, etc., without boxes.
- Put labels consistently near each panel’s upper-left corner.
- Describe every panel separately in the legend.
- Explain denominators, thresholds, symbols, outlines, colors, and schematic elements.
- Preserve embedded-image aspect ratios.
- Number figures and tables in order of first appearance.

## Discussion and Conclusions

- Synthesize rather than repeat Results.
- Distinguish ontological or industrial overlap from analytical specificity.
- Interpret performance only within the documented comparison regime.
- Separate inspectability from mechanistic or causal explanation.
- State what positive-only external data can and cannot establish.
- Discuss annotation dependence, constructed negatives, unresolved structures, operational thresholds, and untested alternatives.
- End Conclusions with the narrowest defensible claim.

## Formatting

- Use double-line spacing throughout headings, body text, captions, references, and tables.
- Use plain black text without bold, italics, underlining, or colored type unless the journal requires otherwise.
- Distinguish headings by size and placement.
- Center author, affiliation, and corresponding-author lines under the title.
- Maintain consistent terminology, capitalization, hyphenation, symbols, and decimal precision.

## Final checklist

- [ ] Abstract and main body define abbreviations independently.
- [ ] References follow first-citation order.
- [ ] Introduction explains rather than lists cited work.
- [ ] Data construction, thresholds, fingerprints, similarity rules, and folds are explicit.
- [ ] Results begin with model-selection evidence.
- [ ] Retained, merged, and rejected candidates have reasons.
- [ ] Figures and tables are not redundant.
- [ ] Multi-panel figures use unboxed parenthesized labels.
- [ ] Legends describe every panel.
- [ ] Figures preserve aspect ratios and are placed with legends.
- [ ] External positive-only evidence is not called full external validation.
- [ ] Discussion synthesizes rather than repeats.
- [ ] Claims remain bounded.
- [ ] Text is plain black and double-spaced.
- [ ] Author details, tables, figures, and Supporting Information references are correct.
