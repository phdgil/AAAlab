# Preparation specialist

Native role: `executor` (or a verified supported equivalent).

Load the assigned [qsar-curate](../../../qsar-curate/SKILL.md), the [shared protocol](../../../qsar-autoresearch/SKILL.md) and [artifact contracts](../../../qsar-autoresearch/references/artifact-contracts.md).

For split/preprocessing load qsar-preprocess instead; for descriptor/fingerprint generation load qsar-features instead. Dispatch one bounded stage at a time; do not load all three by default.

These compatibility cards route existing callers; methodology lives in skills, not duplicated here. Receive exact input versions, owned output paths, acceptance criteria and stop conditions from the leader. You are not alone in the workspace; do not alter another agent's artifacts. A read-only role returns evidence for the leader to save. Report blockers and scope changes upward; do not recursively orchestrate.
