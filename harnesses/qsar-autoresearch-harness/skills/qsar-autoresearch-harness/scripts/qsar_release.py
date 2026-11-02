"""Package accepted QSAR evidence from a live release producer dispatch; never refit."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import shutil
from urllib.parse import quote

from qsar_runtime import Runtime, GateError, STAGES, snapshot, verify


SUMMARY_NAMES = {"evaluation.json", "terminal_evaluation.json", "final_fit.json", "final_selection.json",
                 "search_coverage.json", "preflight.json", "next_experiment_manifest.json",
                 "exposure_ledger.json", "source_manifest.json", "curation_manifest.json",
                 "preprocessing_manifest.json", "feature_manifest.json", "collection_manifest.json",
                 "training_manifest.json", "training_summary.json", "exclusion_summary.json",
                 "source_registry.json", "collection_gaps.json"}


def dump(path, value):
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)


def release_context(runtime, dispatch_id):
    state = runtime.status()
    dispatch, entry = runtime._dispatch(state, dispatch_id, "producer")
    if dispatch["stage"] != "release" or dispatch["status"] not in {"bound", "running"}:
        raise GateError("Packaging requires the current bound/running release producer")
    if not dispatch.get("native_execution_id") or not dispatch.get("native_evidence"):
        raise GateError("Release dispatch lacks native execution evidence")
    verify([dispatch["native_evidence"]])
    tokens = runtime._prerequisites(state, "release")
    if dispatch["upstream_tokens"] != tokens:
        raise GateError("Release upstream acceptance differs from dispatch")
    # This is an ownership consistency check, not an authority grant from a path.
    expected = runtime.root / "stages" / "release" / f"attempt-{dispatch['attempt']}" / dispatch_id
    out = Path(dispatch["owned_output_dir"]).resolve()
    if out != expected.resolve() or not out.is_relative_to(runtime.root):
        raise GateError("Release output is outside current producer ownership")
    improvement = state["stages"]["improvement"]["submission"]
    if improvement.get("decision") != "STOP" or not improvement.get("model_lock"):
        raise GateError("Release requires the accepted STOP and model lock")
    verify(improvement["model_lock"])
    return state, dispatch, out


def text(value):
    return html.escape(str(value), quote=True)


def table(headers, rows):
    return "<table><thead><tr>" + "".join("<th>" + text(v) + "</th>" for v in headers) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join("<td>" + text(v) + "</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table>"


def scalar_facts(value, prefix=""):
    """Display declared scalar summaries without deriving new scientific judgments."""
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"input_hashes", "identity", "bindings", "fit_evidence", "row_ids", "source_records", "rows"}:
                continue
            path = prefix + "." + key if prefix else key
            if isinstance(child, (dict, list)):
                result.extend(scalar_facts(child, path))
            elif child is None or isinstance(child, (str, int, float, bool)):
                result.append((path, child))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            if isinstance(child, (dict, list)):
                result.extend(scalar_facts(child, f"{prefix}[{i}]"))
            elif child is None or isinstance(child, (str, int, float, bool)):
                result.append((f"{prefix}[{i}]", child))
    return result


def render_summary(manifest, summaries):
    parts = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">",
             "<title>QSAR training summary</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;line-height:1.5}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #bbb;text-align:left;padding:8px;overflow-wrap:anywhere}th{background:#eee}pre{white-space:pre-wrap}a{color:#164b9b}</style><body>",
             "<h1>QSAR training summary</h1>", "<p>Run: " + text(manifest["run_id"]) + "; contract: " + text(manifest["contract_version"]) + "</p>",
             "<p>This package assembles accepted stage evidence. It adds no scientific acceptance judgment and requires an independent release review.</p>",
             "<p><a href=\"release_manifest.json\">Reproducibility manifest</a></p>",
             "<h2>Stage evidence</h2>", table(["Stage", "Producer summary", "Coverage", "Review summary"],
             [(s["stage"], s["summary"], json.dumps(s["coverage"], ensure_ascii=False), s["review"]["summary"]) for s in manifest["stages"]])]
    for summary in summaries:
        parts.extend(["<h2>" + text(summary["stage"] + " / " + summary["name"]) + "</h2>",
                      "<p><a href=\"" + quote(summary["relative_path"], safe="/") + "\">Accepted summary artifact</a></p>",
                      table(["Recorded field", "Value"], scalar_facts(summary["content"]))])
    parts.extend(["<h2>Limitations</h2>", table(["Stage", "Recorded limitation"],
                  [(s["stage"], json.dumps(limit, ensure_ascii=False) if not isinstance(limit, str) else limit)
                   for s in manifest["stages"] for limit in s["limitations"] + s["review"].get("limitations", [])]),
                  "<h2>Artifact inventory</h2>", table(["Stage", "Artifact", "Storage", "SHA-256"],
                  [(a["stage"], Path(a["source"]["path"]).name, a["relative_path"] or "external hash reference", a["source"]["sha256"])
                   for a in manifest["artifacts"]]), "</body></html>"])
    return "\n".join(parts)


def package_release(run_dir, dispatch_id):
    runtime = Runtime(run_dir)
    state, dispatch, out = release_context(runtime, dispatch_id)
    identity = {"dispatch_id": dispatch_id, "upstream_tokens": dispatch["upstream_tokens"],
                "contract": state["contract"], "code": snapshot(__file__), "runtime_code": snapshot(Path(__file__).with_name("qsar_runtime.py"))}
    # Runtime.execute owns execution.log; never overwrite it or a previous package.
    package_names = ("release_manifest.json", "training_summary.html", "summaries")
    if any((out / name).exists() for name in package_names):
        raise GateError("Release output already contains a package; use a new reviewed attempt")
    sealed = {str(Path(a["path"]).resolve()) for a in state["sealed_inputs"]}
    manifest = {"schema_version": "1.0", "status": "incomplete", "run_id": state["run_id"],
                "contract_version": state["contract_version"], "producer_execution_id": dispatch["native_execution_id"],
                "identity": identity, "stages": [], "artifacts": [], "review_required": True,
                "storage_policy": "Small accepted JSON summaries copied; other artifacts referenced by exact hash. Sealed inputs omitted.",
                "limitations": ["External hash references require preserving the original artifacts and their access rights.",
                                "Runtime native identities are leader-attested provenance, not independent authentication."]}
    snapshots, summaries = [state["contract"], identity["code"], identity["runtime_code"]], []
    try:
        for stage in STAGES[:STAGES.index("release")]:
            entry = state["stages"][stage]
            candidate, review = entry["submission"], entry["review"]
            verify(candidate["outputs"] + review["evidence_bindings"])
            snapshots.extend(candidate["outputs"] + review["evidence_bindings"])
            stage_record = {"stage": stage, "candidate_hash": candidate["candidate_hash"],
                            "producer_execution_id": candidate["producer_execution_id"],
                            "summary": candidate["summary"], "coverage": candidate.get("coverage", {}),
                            "checks": candidate["checks"], "limitations": candidate.get("limitations", []),
                            "input_bindings": [b for b in candidate["bindings"] if str(Path(b["path"]).resolve()) not in sealed],
                            "command": candidate.get("command"), "execution": candidate.get("execution"),
                            "review": dict(review)}
            if stage == "improvement":
                stage_record.update(decision=candidate["decision"], reason=candidate["reason"], model_lock=candidate["model_lock"])
            manifest["stages"].append(stage_record)
            for artifact in candidate["outputs"]:
                source = Path(artifact["path"])
                if str(source.resolve()) in sealed:
                    continue
                record = {"stage": stage, "source": artifact, "relative_path": None, "storage": "external-reference"}
                # Never parse/copy arbitrary raw JSON or row-level prediction/target files.
                if source.name in SUMMARY_NAMES and source.suffix == ".json" and artifact["size"] <= 16 * 1024 * 1024:
                    value = json.loads(source.read_text(encoding="utf-8-sig"))
                    relative = Path("summaries") / stage / (artifact["sha256"][:12] + "-" + source.name)
                    target = out / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    copied = snapshot(target)
                    if copied["sha256"] != artifact["sha256"]:
                        raise GateError("Source changed while packaging")
                    record.update(relative_path=relative.as_posix(), storage="copied-summary", packaged=copied)
                    summaries.append({"stage": stage, "name": source.name, "relative_path": relative.as_posix(), "content": value})
                manifest["artifacts"].append(record)
        html_path = out / "training_summary.html"
        html_path.write_text(render_summary(manifest, summaries), encoding="utf-8")
        manifest["summary"] = snapshot(html_path)
        verify(snapshots)
        verify([a["packaged"] for a in manifest["artifacts"] if a.get("packaged")])
        fresh, fresh_dispatch, fresh_out = release_context(runtime, dispatch_id)
        if fresh_dispatch["upstream_tokens"] != identity["upstream_tokens"] or fresh_out != out or fresh["contract"] != identity["contract"]:
            raise GateError("Runtime acceptance changed while packaging")
        manifest["status"] = "complete"
        dump(out / "release_manifest.json", manifest)
    except BaseException as error:
        manifest.update(status="failed", failure_reason=f"{type(error).__name__}: {error}")
        dump(out / "release_manifest.json", manifest)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--dispatch-id", required=True)
    args = parser.parse_args()
    result = package_release(args.run_dir, args.dispatch_id)
    print(json.dumps({"status": result["status"], "dispatch_id": args.dispatch_id}))


if __name__ == "__main__":
    main()
