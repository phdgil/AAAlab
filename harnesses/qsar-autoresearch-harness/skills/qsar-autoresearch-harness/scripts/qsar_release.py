"""Package accepted QSAR evidence from a live release producer dispatch; never refit."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import quote

from qsar_runtime import Runtime, GateError, STAGES, canonical, snapshot, verify


SUMMARY_NAMES = {"evaluation.json", "terminal_evaluation.json", "external_evaluation.json", "final_fit.json", "final_selection.json",
                 "search_coverage.json", "preflight.json", "next_experiment_manifest.json",
                 "exposure_ledger.json", "source_manifest.json", "curation_manifest.json",
                 "preprocessing_manifest.json", "feature_manifest.json", "collection_manifest.json",
                 "training_manifest.json", "training_summary.json", "exclusion_summary.json",
                 "source_registry.json", "collection_gaps.json"}

LABELS = {
    "title": ("QSAR training summary", "QSAR 학습 요약"),
    "run": ("Run", "실행"),
    "contract": ("Contract", "연구 계약"),
    "intro": ("This package assembles accepted stage evidence. Packaging and search statuses describe recorded work, not scientific completion, model quality, or independent external validation. A separate release review is required.",
              "이 패키지는 승인된 단계별 근거를 모읍니다. 패키징·탐색 상태는 기록된 작업 상태이며 과학적 완료, 모델 품질 또는 독립 외부 검증을 뜻하지 않습니다. 별도의 릴리스 검토가 필요합니다."),
    "manifest": ("Reproducibility manifest", "재현성 명세"),
    "provenance_note": ("Paths and hashes recorded inside accepted summaries are provenance references, not promises that the underlying files are packaged or accessible. Custodian-only file contents and links are excluded.",
                        "승인된 요약 안의 경로와 해시는 출처를 추적하기 위한 참조이며, 해당 파일이 패키지에 포함되거나 접근 가능하다는 뜻이 아닙니다. 관리 전용 파일의 내용과 링크는 제외됩니다."),
    "endpoints": ("Task / endpoint summary", "태스크·평가변수별 요약"),
    "endpoint_count": ("Tasks / endpoints with recorded evidence", "근거가 기록된 태스크·평가변수 수"),
    "endpoint_note": ("Cohort counts come only from accepted preparation summaries. CV, terminal and external metrics retain their own evaluation scope and candidate identity; unavailable means not recorded here, never zero. Search counts include anchors and search trials across recorded scopes, not distinct configurations or scientific completion.",
                      "코호트 행 수는 승인된 전처리 요약만 사용합니다. 교차검증·최종 평가·외부 평가 지표는 각각의 평가 범위와 후보 식별자를 유지합니다. ‘정보 없음’은 여기 기록되지 않았다는 뜻이며 0이 아닙니다. 탐색 횟수는 기록된 범위의 앵커와 탐색 시행을 포함하며 고유 설정 수나 과학적 완료를 뜻하지 않습니다."),
    "no_endpoints": ("No task / endpoint-level evidence was recorded in recognized accepted summaries.", "승인된 요약에 인식 가능한 태스크·평가변수별 근거가 기록되지 않았습니다."),
    "task": ("Task / endpoint", "태스크·평가변수"),
    "rows": ("Recorded cohort rows", "기록된 코호트별 행 수"),
    "chosen": ("Chosen representation / model", "선택된 분자 표현·모델"),
    "cv": ("Development CV metrics", "개발 교차검증 지표"),
    "terminal": ("Terminal evaluation", "최종 평가"),
    "external": ("External evaluation", "외부 평가"),
    "external_status": ("External evaluation evidence status (run-level)", "외부 평가 근거 상태 (실행 전체)"),
    "external_packaged": ("Accepted external evidence indexed", "승인된 외부 평가 근거의 목록 포함 여부"),
    "search": ("Search coverage / recorded status", "탐색 수행 범위·기록된 상태"),
    "evidence": ("Accepted summary sources", "승인된 요약 근거"),
    "development": ("Development", "개발"),
    "excluded": ("Excluded", "제외"),
    "unavailable": ("Unavailable", "정보 없음"),
    "selection": ("Selected recipe", "선택된 모델 설정"),
    "models": ("Final fitted model", "최종 학습 모델"),
    "family": ("Representation", "분자 표현"),
    "algorithm": ("Algorithm", "알고리즘"),
    "candidate_id": ("Candidate", "후보 식별자"),
    "task_type": ("Task type", "태스크 유형"),
    "target_column": ("Target column", "목표값 열"),
    "positive_class": ("Positive class", "양성 클래스"),
    "threshold": ("Decision threshold", "판정 임계값"),
    "fold_thresholds": ("Per-fold thresholds", "폴드별 판정 임계값"),
    "threshold_policy": ("Threshold policy", "임계값 정책"),
    "metrics": ("Metrics", "평가 지표"),
    "n": ("Evaluated rows (n)", "평가 행 수 (n)"),
    "rmse": ("RMSE", "RMSE"),
    "mae": ("MAE", "MAE"),
    "r2": ("R²", "R²"),
    "roc_auc": ("ROC AUC", "ROC AUC"),
    "pr_auc": ("PR AUC", "PR AUC"),
    "accuracy": ("Accuracy", "정확도"),
    "balanced_accuracy": ("Balanced accuracy", "균형 정확도"),
    "precision": ("Precision", "정밀도"),
    "recall": ("Recall", "재현율"),
    "f1": ("F1", "F1"),
    "status": ("Search report status (run-level)", "탐색 보고서 상태 (실행 전체)"),
    "cells": ("Recorded cells across scopes", "범위별로 기록된 탐색 셀 수"),
    "expected_search_trials": ("Expected search trials", "계획된 탐색 시행 수"),
    "expected_anchor_trials": ("Expected anchor trials", "계획된 앵커 시행 수"),
    "complete": ("Completed trials (anchor + search)", "완료된 시행 수 (앵커+탐색)"),
    "failed": ("Failed trials (anchor + search)", "실패한 시행 수 (앵커+탐색)"),
    "pending": ("Pending trials (anchor + search)", "대기 중인 시행 수 (앵커+탐색)"),
    "stages": ("Stage evidence", "단계별 근거"),
    "stage": ("Stage", "단계"),
    "producer": ("Producer summary", "생성자 요약"),
    "coverage": ("Coverage", "수행 범위"),
    "review": ("Review summary", "검토자 요약"),
    "artifact_link": ("Accepted summary artifact", "승인된 요약 산출물"),
    "field": ("Recorded field", "기록된 필드"),
    "value": ("Value", "값"),
    "limitations": ("Limitations", "한계"),
    "limitation": ("Recorded limitation", "기록된 한계"),
    "package": ("Package", "패키지"),
    "inventory": ("Artifact inventory", "산출물 목록"),
    "artifact": ("Artifact", "산출물"),
    "storage": ("Storage", "저장 위치"),
    "external_reference": ("External hash reference", "외부 파일 해시 참조"),
    "omissions": ("Embedded row-data omissions", "내장된 행 수준 데이터의 제외 기록"),
    "omission_note": ("Row-bearing embedded fields are omitted in full. Receipts bind the omitted payload to its original accepted record; acceptance does not authorize publication of row data.",
                      "행 수준 데이터가 포함된 내장 필드는 전체를 제외합니다. 제외 기록은 원래 승인된 기록과 제외된 내용의 해시를 연결합니다. 근거의 승인이 행 수준 데이터의 공개를 허용하는 것은 아닙니다."),
    "omitted": ("Row-level details omitted; see omission receipt", "행 수준 세부 내용 제외: 제외 기록 참조"),
    "record": ("Accepted record", "승인된 기록"),
    "omission_id": ("Omission receipt", "제외 기록 식별자"),
}


def reporting_language(contract):
    if not isinstance(contract, dict):
        raise GateError("Reporting configuration requires a contract object")
    reporting = contract.get("reporting", {})
    if not isinstance(reporting, dict):
        raise GateError("reporting must be an object")
    language = reporting.get("language", "en")
    if not isinstance(language, str) or language not in {"en", "ko"}:
        raise GateError("reporting.language must be en or ko")
    return language


def protected_paths(state):
    return {str(Path(item["path"]).resolve())
            for registry in ("sealed_inputs", "external_inputs", "custodian_only")
            for item in state.get(registry, [])}


def has_direct_row_data(value):
    return ("output_row_id" in value or "row_id" in value
            or any(isinstance(value.get(key), (dict, list)) and value[key] for key in ("rows", "source_records")))


def contains_row_data(value):
    """A recognized filename does not authorize publishing row-level records."""
    if isinstance(value, dict):
        return has_direct_row_data(value) or any(contains_row_data(child) for child in value.values())
    return isinstance(value, list) and any(contains_row_data(child) for child in value)


def publication_value(value, stage, source, source_hash, pointer, omissions):
    """Omit an entire row-bearing field without changing its accepted source."""
    if not contains_row_data(value):
        return value
    payload = canonical(value).encode("utf-8")
    receipt = {"id": f"embedded-{len(omissions) + 1}", "stage": stage, "source": source,
               "source_hash": source_hash, "pointer": pointer, "reason": "row-level-data",
               "encoding": "qsar-runtime-canonical-json-utf8", "sha256": hashlib.sha256(payload).hexdigest(),
               "size": len(payload)}
    omissions.append(receipt)
    return {"release_omission": receipt["id"]}


def publication_review(review, stage, omissions):
    if has_direct_row_data(review):
        # A row-shaped receipt cannot safely expose any of its free-form details.
        result = publication_value(review, stage, "review", review["review_hash"], "", omissions)
        result.update({key: review[key] for key in ("candidate_hash", "producer_execution_id",
                                                   "reviewer_execution_id", "verdict", "review_hash")})
        return result
    return {key: publication_value(value, stage, "review", review["review_hash"],
                                   "/" + key.replace("~", "~0").replace("/", "~1"), omissions)
            for key, value in review.items()}


def external_acceptance(state):
    entry = state.get("external_evaluation", {})
    if entry.get("status") != "accepted":
        return None
    candidate, review = entry.get("submission"), entry.get("review")
    if (not isinstance(candidate, dict) or not isinstance(review, dict)
            or candidate.get("stage") != "terminal-evaluation" or candidate.get("evaluation_mode") != "external"
            or review.get("stage") != "terminal-evaluation" or review.get("verdict") != "PASS"
            or not candidate.get("candidate_hash") or not review.get("review_hash")
            or not candidate.get("producer_execution_id")
            or review.get("candidate_hash") != candidate["candidate_hash"]
            or review.get("producer_execution_id") != candidate.get("producer_execution_id")
            or not review.get("reviewer_execution_id")
            or review["reviewer_execution_id"] == candidate.get("producer_execution_id")):
        raise GateError("External evaluation requires its current independent acceptance")
    return {"submission": candidate["candidate_hash"], "review": review["review_hash"]}


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
            if key in {"input_hashes", "identity", "bindings", "fit_evidence", "row_ids"}:
                continue
            if key in {"source_records", "rows"} and isinstance(child, (dict, list)):
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


def endpoint_evidence(summaries):
    """Keep task-level facts separate from report-level status and other stages."""
    endpoints = {}

    def add(task, kind, record, summary):
        if isinstance(task, str) and task.strip() and isinstance(record, dict):
            endpoints.setdefault(task, {}).setdefault(kind, []).append((record, summary))

    for summary in summaries:
        content = summary["content"]
        if not isinstance(content, dict):
            continue
        source = (summary["stage"], summary["name"])
        task_maps = {("preprocessing", "preprocessing_manifest.json"): ("task_counts", "counts"),
                     ("training", "final_selection.json"): ("tasks", "selection"),
                     ("final-fit", "final_fit.json"): ("models", "models")}
        if source in task_maps:
            field, kind = task_maps[source]
            records = content.get(field)
            if isinstance(records, dict):
                for task, record in records.items():
                    add(task, kind, record, summary)
        elif source == ("training", "search_coverage.json"):
            cells = content.get("cells")
            if isinstance(cells, list):
                by_task = {}
                for cell in cells:
                    if isinstance(cell, dict) and isinstance(cell.get("task_id"), str) and cell["task_id"].strip():
                        by_task.setdefault(cell["task_id"], []).append(cell)
                for task, records in by_task.items():
                    add(task, "search", {"status": content.get("status"), "cells": records}, summary)
        else:
            scope = {("development-evaluation", "evaluation.json"): "development",
                     ("terminal-evaluation", "terminal_evaluation.json"): "terminal",
                     ("external-evaluation", "external_evaluation.json"): "external"}.get(source)
            if scope is None or content.get("mode") != scope:
                continue
            results = content.get("results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict):
                        add(result.get("task_id"), scope, result, summary)
    return endpoints


def endpoint_table(summaries, labels):
    endpoints = endpoint_evidence(summaries)

    def value(item):
        if item is None:
            return labels["unavailable"]
        return json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)

    def fields(record, keys):
        return "; ".join(labels.get(key, key) + ": " + value(record.get(key)) for key in keys)

    def recorded(entries, kind):
        blocks = []
        for record, _ in entries:
            if kind == "counts":
                block = fields(record, ("development", "terminal", "external", "excluded"))
            elif kind in {"selection", "models"}:
                keys = ["family", "algorithm", "candidate_id"]
                keys.extend(k for k in ("task_type", "positive_class", "threshold") if k in record)
                block = labels[kind] + ": " + fields(record, keys)
            elif kind == "search":
                totals = {"status": record["status"], "cells": len(record["cells"])}
                for key in ("expected_search_trials", "expected_anchor_trials", "complete", "failed", "pending"):
                    counts = [cell.get(key) for cell in record["cells"]]
                    totals[key] = sum(counts) if all(type(n) is int and n >= 0 for n in counts) else None
                block = fields(totals, totals)
            else:
                keys = ["candidate_id", "target_column", "task_type"]
                keys.extend(k for k in ("positive_class", "threshold", "fold_thresholds", "threshold_policy") if k in record)
                block = fields(record, keys)
                metrics = record.get("metrics")
                block += "\n" + labels["metrics"] + ": " + (fields(metrics, metrics) if isinstance(metrics, dict) and metrics else labels["unavailable"])
            blocks.append(block)
        return "\n\n".join(blocks) if blocks else labels["unavailable"]

    rows = []
    for task, evidence in sorted(endpoints.items()):
        sources = sorted({s["stage"] + " / " + s["name"] + " (" + s["relative_path"] + ")"
                          for entries in evidence.values() for _, s in entries})
        chosen = [recorded(evidence[kind], kind) for kind in ("selection", "models") if kind in evidence]
        rows.append((task, recorded(evidence.get("counts", []), "counts"),
                     "\n\n".join(chosen) if chosen else labels["unavailable"],
                     recorded(evidence.get("development", []), "development"),
                     recorded(evidence.get("terminal", []), "terminal"),
                     recorded(evidence.get("external", []), "external"),
                     recorded(evidence.get("search", []), "search"), "\n".join(sources)))
    parts = ["<h2>" + text(labels["endpoints"]) + "</h2>",
             "<p>" + text(labels["endpoint_count"]) + ": " + str(len(endpoints)) + "</p>",
             "<p>" + text(labels["endpoint_note"]) + "</p>"]
    parts.append(table([labels[key] for key in ("task", "rows", "chosen", "cv", "terminal", "external", "search", "evidence")], rows)
                 if rows else "<p>" + text(labels["no_endpoints"]) + "</p>")
    return "\n".join(parts)


def render_summary(manifest, summaries):
    language = reporting_language(manifest)
    if contains_row_data(manifest) or any(contains_row_data(summary["content"]) for summary in summaries):
        raise GateError("Release rendering requires row-data exclusion before publication")
    labels = {key: variants[0 if language == "en" else 1] for key, variants in LABELS.items()}
    parts = ["<!doctype html><html lang=\"" + language + "\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">",
             "<title>" + text(labels["title"]) + "</title><style>body{font:16px system-ui;max-width:1500px;margin:32px auto;padding:0 20px;line-height:1.5}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #bbb;text-align:left;padding:8px;overflow-wrap:anywhere;white-space:pre-line;vertical-align:top}th{background:#eee}pre{white-space:pre-wrap}a{color:#164b9b}</style><body>",
             "<h1>" + text(labels["title"]) + "</h1>", "<p>" + text(labels["run"]) + ": " + text(manifest["run_id"]) + "; " + text(labels["contract"]) + ": " + text(manifest["contract_version"]) + "</p>",
             "<p>" + text(labels["intro"]) + "</p>",
             "<p>" + text(labels["provenance_note"]) + "</p>",
             "<p><a href=\"release_manifest.json\">" + text(labels["manifest"]) + "</a></p>"]
    external = manifest.get("external_evaluation")
    if isinstance(external, dict):
        parts.append("<p>" + text(labels["external_status"]) + ": " + text(external.get("status", labels["unavailable"]))
                     + "; " + text(labels["external_packaged"]) + ": " + text(external.get("packaged", labels["unavailable"])) + "</p>")
    parts.extend([endpoint_table(summaries, labels),
             "<h2>" + text(labels["stages"]) + "</h2>", table([labels[key] for key in ("stage", "producer", "coverage", "review")],
             [(s["stage"], s["summary"], json.dumps(s["coverage"], ensure_ascii=False),
               s["review"].get("summary", labels["omitted"])) for s in manifest["stages"]])])
    for summary in summaries:
        parts.extend(["<h2>" + text(summary["stage"] + " / " + summary["name"]) + "</h2>",
                      "<p><a href=\"./" + quote(summary["relative_path"], safe="/") + "\">" + text(labels["artifact_link"]) + "</a></p>",
                      table([labels["field"], labels["value"]], scalar_facts(summary["content"]))])
    def limitation_items(value):
        return value if isinstance(value, list) else [value]

    limits = [(labels["package"], limit) for limit in limitation_items(manifest.get("limitations", []))]
    limits.extend((s["stage"], limit) for s in manifest["stages"]
                  for limit in limitation_items(s["limitations"]) + limitation_items(s["review"].get("limitations", [])))
    parts.extend(["<h2>" + text(labels["limitations"]) + "</h2>", table([labels["stage"], labels["limitation"]],
                  [(stage, json.dumps(limit, ensure_ascii=False) if not isinstance(limit, str) else limit) for stage, limit in limits])])
    if manifest.get("embedded_omissions"):
        parts.extend(["<h2>" + text(labels["omissions"]) + "</h2>", "<p>" + text(labels["omission_note"]) + "</p>",
                      table([labels["omission_id"], labels["stage"], labels["record"], labels["field"], "SHA-256"],
                            [(o["id"], o["stage"], o["source"] + " / " + o["source_hash"], o["pointer"], o["sha256"])
                             for o in manifest["embedded_omissions"]])])
    parts.extend(["<h2>" + text(labels["inventory"]) + "</h2>", table([labels["stage"], labels["artifact"], labels["storage"], "SHA-256"],
                  [(a["stage"], Path(a["source"]["path"]).name, a["relative_path"] or labels["external_reference"], a["source"]["sha256"])
                   for a in manifest["artifacts"]]), "</body></html>"])
    return "\n".join(parts)


def package_release(run_dir, dispatch_id):
    runtime = Runtime(run_dir)
    state, dispatch, out = release_context(runtime, dispatch_id)
    contract = state["contract"]
    with Path(contract["path"]).open("rb") as stream:
        contract_bytes = stream.read(contract["size"] + 1)
    if len(contract_bytes) != contract["size"] or hashlib.sha256(contract_bytes).hexdigest() != contract["sha256"]:
        raise GateError("Contract changed while packaging")
    language = reporting_language(json.loads(contract_bytes.decode("utf-8-sig")))
    external_token = external_acceptance(state)
    identity = {"dispatch_id": dispatch_id, "upstream_tokens": dispatch["upstream_tokens"],
                "contract": state["contract"], "external_acceptance": external_token,
                "code": snapshot(__file__), "runtime_code": snapshot(Path(__file__).with_name("qsar_runtime.py"))}
    # Runtime.execute owns execution.log; never overwrite it or a previous package.
    package_names = ("release_manifest.json", "training_summary.html", "summaries")
    if any((out / name).exists() for name in package_names):
        raise GateError("Release output already contains a package; use a new reviewed attempt")
    protected = protected_paths(state)
    manifest = {"schema_version": "1.0", "status": "incomplete", "run_id": state["run_id"],
                "contract_version": state["contract_version"], "producer_execution_id": dispatch["native_execution_id"],
                "reporting": {"language": language},
                "external_evaluation": {"status": state.get("external_evaluation", {}).get("status", "unavailable"),
                                        "packaged": external_token is not None, "acceptance": external_token},
                "identity": identity, "stages": [], "artifacts": [], "embedded_omissions": [], "review_required": True,
                "storage_policy": "Small accepted JSON summaries without row records copied; other artifacts referenced by exact hash. Custodian-only inputs and row-bearing embedded fields omitted.",
                "limitations": ["External hash references require preserving the original artifacts and their access rights.",
                                "Runtime native identities are leader-attested provenance, not independent authentication."]}
    snapshots, summaries = [state["contract"], identity["code"], identity["runtime_code"]], []
    try:
        entries = [(stage, state["stages"][stage]) for stage in STAGES[:STAGES.index("release")]]
        if external_token is not None:
            entry = state["external_evaluation"]
            tokens = runtime._prerequisites(state, "terminal-evaluation", terminal_review=True, evaluation_mode="external")
            if entry["submission"]["upstream_tokens"] != tokens:
                raise GateError("External evaluation has stale upstream acceptance")
            entries.append(("external-evaluation", entry))
        for stage, entry in entries:
            candidate, review = entry["submission"], entry["review"]
            verify(candidate["outputs"] + review["evidence_bindings"])
            snapshots.extend(candidate["outputs"] + review["evidence_bindings"])
            stage_record = {"stage": stage, "candidate_hash": candidate["candidate_hash"],
                            "producer_execution_id": candidate["producer_execution_id"],
                            "summary": candidate["summary"], "coverage": candidate.get("coverage", {}),
                            "checks": candidate["checks"], "limitations": candidate.get("limitations", []),
                            "input_bindings": [b for b in candidate["bindings"] if str(Path(b["path"]).resolve()) not in protected],
                            "command": candidate.get("command"), "execution": candidate.get("execution")}
            if stage == "improvement":
                stage_record.update(decision=candidate["decision"], reason=candidate["reason"], model_lock=candidate["model_lock"])
            if stage == "external-evaluation":
                stage_record["evaluation_mode"] = candidate["evaluation_mode"]
            stage_record = {key: publication_value(value, stage, "candidate", candidate["candidate_hash"],
                                                    "/" + ("bindings" if key == "input_bindings" else key),
                                                    manifest["embedded_omissions"])
                            for key, value in stage_record.items()}
            stage_record["review"] = publication_review(review, stage, manifest["embedded_omissions"])
            manifest["stages"].append(stage_record)
            for artifact in candidate["outputs"]:
                source = Path(artifact["path"])
                if str(source.resolve()) in protected:
                    continue
                record = {"stage": stage, "source": artifact, "relative_path": None, "storage": "external-reference"}
                # Never parse/copy arbitrary raw JSON or row-level prediction/target files.
                if source.name in SUMMARY_NAMES and source.suffix == ".json" and artifact["size"] <= 16 * 1024 * 1024:
                    with source.open("rb") as stream:
                        payload = stream.read(artifact["size"] + 1)
                    if len(payload) != artifact["size"] or hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                        raise GateError("Source changed while packaging")
                    value = json.loads(payload.decode("utf-8-sig"))
                    if contains_row_data(value):
                        record["copy_omitted"] = "Row-level JSON remains an external hash reference"
                    else:
                        relative = Path("summaries") / stage / (artifact["sha256"][:12] + "-" + source.name)
                        target = out / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
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
        if protected_paths(fresh) != protected:
            raise GateError("Protected artifact registry changed while packaging")
        if external_acceptance(fresh) != external_token:
            raise GateError("External evaluation acceptance changed while packaging")
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
