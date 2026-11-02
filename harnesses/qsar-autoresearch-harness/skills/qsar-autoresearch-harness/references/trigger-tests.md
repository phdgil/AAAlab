# QSAR harness routing scenarios

Should invoke: build a complete QSAR pipeline from public/raw sources; continue reviewed model improvement; repair and revalidate a changed curation policy; release an existing verified QSAR package without retraining.

Should not invoke: explain MAE; convert an Excel sheet without modeling; summarize a paper without research execution.

Stage routing: missing structures -> qsar-curate; source-query coverage -> qsar-collect; fixed split design -> qsar-preprocess; supported fingerprint inventory -> qsar-features; representation/model HPO -> qsar-train; locked prediction assessment -> qsar-evaluate; development error intervention -> qsar-improve; independent audit -> qsar-review. Do not force all stages for an isolated accepted task.
