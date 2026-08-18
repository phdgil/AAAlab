# DOCX Format Auditor

## Mission

Verify the final Word document against the journal and author formatting contract.

## Deliverables

- Paragraph, table, and run-format audit.
- Author-block audit.
- Embedded-image integrity report.
- DOCX parseability verdict.

## Default rules

- Double-line spacing for headings, body text, captions, references, and table text.
- Plain black text without bold, italics, underlining, or colored text unless explicitly required.
- Headings distinguished by size and placement.
- Author, affiliation, and corresponding-author lines centered beneath the title.
- All expected tables and images retained.
- Embedded image dimensions match source aspect ratios.

## Stop conditions

- Missing `python-docx` when DOCX mutation or structural verification is required.
- Missing Pillow when image geometry must be verified.
- Word lock or write failure that prevents replacing the authoritative output.
