# QSAR harness routing scenarios

Should invoke: build a complete QSAR pipeline from public/raw sources; continue reviewed model improvement; repair and revalidate a changed curation policy; release an existing verified QSAR package without retraining.

Should not invoke: explain MAE; convert an Excel sheet without modeling; summarize a paper without research execution.

Stage routing: missing structures -> qsar-curate; source-query coverage -> qsar-collect; fixed split design -> qsar-preprocess; supported fingerprint inventory -> qsar-features; representation/model HPO -> qsar-train; locked prediction assessment -> qsar-evaluate; development error intervention -> qsar-improve; independent audit -> qsar-review. Do not force all stages for an isolated accepted task.

## Harness maintenance routing

Use the bundled `qsar-evolve` outside the scientific stage sequence for:

- "Repeated training resumes lose failed-attempt costs; improve the harness from these runs."
- "Capture my curation-workflow feedback and make a reusable fix for the next run."
- "Review our accumulated QSAR run lessons and update the stage instructions."

Near misses:

- "Choose the next model intervention from accepted OOF errors" -> `qsar-improve`.
- "Run this accepted search configuration" -> `qsar-train`.
- "Explain cross-validation" -> answer directly without harness execution.

Maintenance observations do not accept a scientific stage. Live-run dependencies
stay pinned; adopting a repair into a checkpointed run requires versioning and
dependent-stage invalidation. Terminal outcomes do not become tuning evidence
through maintenance routing.
