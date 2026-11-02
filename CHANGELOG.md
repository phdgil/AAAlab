# Changelog

## Unreleased

- Added QSAR collection, curation, preprocessing, feature generation, training, evaluation, improvement and independent review skills, with 12 native agent roles and portable runtime tools.
- Added auditable source coverage, molecular identity and curation evidence, fold-local model search, sealed evaluation and release checks.
- Hardened interrupted training recovery: reconcile saved evidence before reuse, preserve failed attempts and reject duplicate search choices so trial counts and recorded costs remain accurate.
- Require Python 3.11+ in QSAR runtime preflight and report missing prerequisites with a nonzero exit status.

## 0.2.0 - 2026-09-26

- Added an installable OpenMRA harness for running supported RM workbooks through the tested Windows application and validating its native exports.
- Added input/report identity checks, preserved model warnings, isolated outputs, and recovery for interrupted batches.
- Added CLI discovery, installation and runtime checks, with automated provenance/resume tests in CI. OpenMRA and research datasets remain external.
