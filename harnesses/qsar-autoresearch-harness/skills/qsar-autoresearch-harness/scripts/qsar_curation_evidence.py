"""Read-only, fail-closed evidence checks; never grants scientific acceptance."""
from collections import Counter
from contextlib import closing, ExitStack
import copy
import csv
import hashlib
import importlib.util
import itertools
import json
import re
from pathlib import Path
import sqlite3
import tempfile


class EvidenceError(ValueError):
    pass


def fail(message):
    raise EvidenceError("Invalid curation evidence: " + message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate JSON key: " + key)
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def binding(path):
    path = Path(path).resolve(strict=True)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path), "sha256": h.hexdigest()}


def checked(item):
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        fail("missing file binding")
    current = binding(item["path"])
    if current["sha256"] != item.get("sha256"):
        fail("stale file: " + item["path"])
    return current


def strict(contract):
    return contract.get("execution_policy", {}).get(
        "allow_training_with_unresolved_required_source_coverage") is False


def csv_rows(path, required=()):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or not set(required) <= set(fields):
            fail("malformed CSV header")
        for row in reader:
            if None in row or None in row.values():
                fail("malformed CSV row")
            yield row


def json_rows(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            yield json.loads(line, object_pairs_hook=unique)


def code_bindings():
    return [binding(Path(__file__).with_name(name)) for name in (
        "qsar_data.py", "qsar_curation_evidence.py", "qsar_adjudication.py",
        "qsar_archive_sources.py", "qsar_native_sources.py", "qsar_sql_curation.py",
        "qsar_sql_resolution.py", "qsar_sql_coverage.py", "qsar_material_curation.py", "qsar_verified_material.py")]


def capture(contract_path, registry_path):
    """Capture inputs before generation; absent collection files remain diagnostic."""
    parent = Path(registry_path).resolve().parent
    return {"schema_version": 1, "contract": binding(contract_path), "registry": binding(registry_path),
            "collection": {key: binding(parent / name) if (parent / name).is_file() else None
                           for key, name in (("manifest", "collection_manifest.json"),
                                             ("gaps", "collection_gaps.json"),
                                             ("records", "source_records.jsonl"))},
            "code": code_bindings()}


def verify_capture(evidence):
    for item in [evidence["contract"], evidence["registry"], *evidence["code"],
                 *evidence["collection"].values()]:
        if item is not None:
            checked(item)


def contract_acceptance_bindings(manifest_path):
    """Runtime linkage accessor; call only after validate, not as acceptance proof."""
    result = []
    manifest = load(manifest_path)
    material = manifest.get('material_integration')
    if material:
        result.append(checked(material['classification_context']['accepted_contract_review']))
    for source in manifest.get("policy", {}).get("sources", []):
        sql = source.get('sql_curation')
        if sql is not None:
            item = checked(sql['accepted_contract_review'])
            if item not in result: result.append(item)
        ordinary = source.get('ordinary_adjudication')
        if ordinary is not None:
            item = checked(ordinary['accepted_contract_review'])
            if item not in result:
                result.append(item)
        config = source.get("narrative_adjudication", {})
        if config.get("schema_version") == 2:
            item = checked(config["accepted_contract_review"])
            if item not in result:
                result.append(item)
    return result


def validate(contract_path, manifest_path=None, curated_csv=None, candidate_outputs=None,
             collection_outputs=None):
    """Validate strict evidence and return exact bindings for subsequent freshness checks."""
    try:
        contract = load(contract_path)
        if not strict(contract):
            if any('narrative_observation_metadata' in s for s in contract.get('data_curation', {}).get('sources', [])):
                fail('observation metadata requires strict curation evidence checks')
            if manifest_path is not None and contract_acceptance_bindings(manifest_path):
                fail("v2 requires strict source coverage evidence checks")
            return []  # Historical non-strict contracts retain their existing workflow.
        if manifest_path is None:
            fail("strict contracts require a bound curation manifest")
        return _validate(contract_path, contract, manifest_path, curated_csv,
                         candidate_outputs, collection_outputs)
    except EvidenceError:
        raise
    except sqlite3.Error:
        fail("duplicate or malformed ledger membership")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        # Parsing errors can embed target values or complete malformed records.
        fail("missing or malformed evidence (" + type(error).__name__ + ")")


def _validate(contract_path, contract, manifest_path, curated_csv, candidate_outputs, collection_outputs):
    manifest_binding = binding(manifest_path)
    manifest = load(manifest_path)
    if any(c.get('sql_curation') is not None for c in manifest.get('policy', {}).get('sources', [])):
        from qsar_sql_curation import validate_sql_finalization
        discovery = manifest.get('sql_finalization')
        if type(discovery) is not dict or set(discovery) != {'schema_version', 'diagnostic', 'review_config'} or type(discovery['schema_version']) is not int or discovery['schema_version'] != 1:
            fail('missing or malformed SQL final review discovery')
        for item in (discovery['diagnostic'], discovery['review_config']): checked(item)
        if Path(discovery['diagnostic']['path']).resolve().parent != Path(manifest_path).resolve().parent:
            fail('SQL final manifest moved from immutable diagnostic directory')
        validated = validate_sql_finalization(discovery['diagnostic']['path'], discovery['review_config']['path'],
            candidate_outputs=candidate_outputs, collection_outputs=collection_outputs)
        if manifest != validated['summary'] or checked(manifest['bound_evidence']['contract']) != binding(contract_path):
            fail('SQL final summary differs from independent reconstruction')
        if curated_csv is not None and Path(curated_csv).resolve() != Path(manifest['bound_evidence']['outputs']['curated.csv']['path']).resolve():
            fail('SQL curated table mismatch')
        if candidate_outputs is not None and (manifest_binding['path'], manifest_binding['sha256']) not in {
                (str(Path(b['path']).resolve()), b['sha256']) for b in candidate_outputs}:
            fail('SQL final manifest not in candidate outputs')
        return [*validated['references'], manifest_binding]
    return _validate_summary(contract_path, contract, manifest, manifest_binding, curated_csv, collection_outputs,
                             candidate_outputs=candidate_outputs)


def _validate_summary(contract_path, contract, manifest, manifest_binding, curated_csv, collection_outputs,
                      *, candidate_outputs=None, sql_closures=None):
    if manifest.get("status") not in {"produced", "limited"}:
        fail("blocked or unknown curation status")
    evidence = manifest["bound_evidence"]
    if evidence["schema_version"] != 1:
        fail("unknown evidence schema")
    refs = [manifest_binding] if manifest_binding is not None else []
    def bound(item):
        current = checked(item)
        refs.append(current)
        return Path(current["path"])
    if bound(evidence["contract"]) != Path(contract_path).resolve() or manifest["contract_sha256"] != refs[-1]["sha256"]:
        fail("contract mismatch")
    registry = load(bound(evidence["registry"]))
    if manifest["registry_sha256"] != refs[-1]["sha256"]:
        fail("registry mismatch")
    if evidence["code"] != code_bindings():
        fail("stale curation code")
    for item in evidence["code"]:
        bound(item)
    collection = {key: bound(evidence["collection"][key]) for key in ("manifest", "gaps", "records")}
    if collection_outputs is not None:
        accepted = {(str(Path(x["path"]).resolve()), x["sha256"]) for x in collection_outputs}
        for item in [evidence["registry"], *evidence["collection"].values()]:
            if (str(Path(item["path"]).resolve()), item["sha256"]) not in accepted:
                fail("collection evidence is not an accepted collection output")
    upstream = load(collection["manifest"])
    gaps = load(collection["gaps"])
    if upstream["gaps"] != gaps or upstream["status"] not in {"produced", "limited"}:
        fail("contradictory or blocked collection evidence")
    configs = contract["data_curation"]["sources"]
    expected_policy = copy.deepcopy(contract["data_curation"])
    external = manifest.get("external_adjudication_config")
    if external:
        mapping = load(bound(external))
        import qsar_data
        qsar_data.apply_adjudication_bindings(expected_policy['sources'], mapping, contract_path)
    if manifest["policy"] != expected_policy:
        fail("curation policy differs from actual contract")
    ordinary_sources = {s['source_id'] for s in expected_policy['sources'] if s.get('ordinary_adjudication') is not None}
    if set(manifest.get('ordinary_accounting', {})) != ordinary_sources:
        fail('Ordinary adjudication accounting differs from configured source scope')
    sources = {s["source_id"]: s for s in configs}
    if not sources or len(sources) != len(configs):
        fail("missing or duplicate contract sources")
    entries = {s["source_id"]: s for s in registry["files"]}
    if len(entries) != len(registry["files"]) or not set(sources) <= entries.keys():
        fail("missing or duplicate registry sources")
    if upstream["file_count"] != len(entries):
        fail("collection file count mismatch")
    for entry in entries.values():
        bound(entry)
    required = {key for key, value in sources.items() if value.get("required", True) is not False}
    exceptions = set()
    for key, config in sources.items():
        entry = entries[key]
        if key == "toxvaldb/COMPTOX_Public.zip" and (
                config.get("required") is False and config.get("adapter") == "unresolved_truncated_archive"
                and config.get("scope_disposition") == "operationally_unreadable; user authorized proceeding with all readable sources, not scientific irrelevance"
                and contract.get("execution_policy", {}).get("allow_disclosed_user_accepted_unreadable_bundle_gap") is True
                and contract.get("user_decisions", {}).get("unreadable_comptox_bundle") ==
                "Use all readable sources and disclose truncated COMPTOX_Public.zip coverage gap; user explicitly confirmed."
                and entry.get("status") == "parse_failed" and entry.get("error")
                and entry.get("inventoried_record_count") == 0):
            exceptions.add(key)
        if key in required and entry.get("status") != "inventoried" and not (
                sql_closures and key in sql_closures and entry.get("status") == "partial_native_parser"):
            fail("unresolved required collection source: " + key)
    closed = set()
    if sql_closures:
        import qsar_sql_resolution as resolver
        for source, ids in sql_closures.items():
            obligations = resolver.scan.obligations(gaps, source)
            if sorted(ids) != sorted(o['obligation_id'] for o in obligations):
                fail('SQL closure differs from original obligation IDs')
            closed.update(ids)
    for gap in gaps:
        key = gap["source_id"]
        if sql_closures and key in sql_closures:
            obligations = resolver.scan.obligations([gap], key)
            if obligations and all(o['obligation_id'] in closed for o in obligations):
                continue
        if key not in entries or key in required or (key in sources and key not in exceptions):
            fail("unresolved collection gap: " + key)
    for blocker in manifest["blockers"]:
        if blocker["source_id"] not in exceptions:
            fail("recorded source blocker: " + blocker["source_id"])
    for name in ("unresolved_coverage_fields", "unresolved_policy_cases", "remaining_coverage_issues"):
        if manifest.get(name):
            fail("unresolved " + name)
    outputs = {name: bound(item) for name, item in evidence["outputs"].items()}
    for name in ("curated.csv", "dispositions.csv", "identity_ledger.csv", "identity_support_ledger.csv", "conflict_ledger.csv"):
        if name not in outputs:
            fail("missing " + name)
    if curated_csv is not None and outputs["curated.csv"] != Path(curated_csv).resolve():
        fail("curated table mismatch")
    if candidate_outputs is not None:
        current = {(str(Path(x["path"]).resolve()), x["sha256"]) for x in candidate_outputs}
        for item in [manifest_binding, *evidence["outputs"].values()]:
            if (str(Path(item["path"]).resolve()), item["sha256"]) not in current:
                fail("evidence is not a current candidate output")
    # Stream large tables; retain only identifiers needed for exact membership joins.
    inventory_counts = _ledger_integrity(collection["records"], outputs, sources, entries, manifest["policy"])
    if set(inventory_counts) - entries.keys() or sum(inventory_counts.values()) != upstream["record_count"]:
        fail("collection record accounting mismatch")
    for key, entry in entries.items():
        if inventory_counts[key] != entry["inventoried_record_count"]:
            fail("collection source count mismatch: " + key)
    counts, dispositions, row_ids, output_ids, observation_ids = Counter(), Counter(), set(), set(), {}
    memberships, native_rows = {}, set()
    for row in csv_rows(outputs["dispositions.csv"]):
        key = row["source_id"]
        if key not in sources or row["source_sha256"] != entries[key]["sha256"] or row["row_id"] in row_ids:
            fail("unknown, duplicate or mismatched disposition source")
        row_ids.add(row["row_id"])
        locator = (key, row["native_sheet"], row["native_row"])
        if locator in native_rows and (not sql_closures or key not in sql_closures):
            fail("duplicate native disposition locator")
        native_rows.add(locator)
        counts[key] += 1
        dispositions[row["disposition"]] += 1
        ids = json.loads(row["output_row_ids"])
        if row["disposition"] not in {"retained", "excluded", "quarantined"} or bool(ids) != (row["disposition"] == "retained"):
            fail("invalid disposition membership")
        if len(ids) != len(set(ids)) or output_ids.intersection(ids):
            fail("duplicate output membership")
        output_ids.update(ids)
        memberships.update({identity: (row["row_id"], key, row["source_sha256"]) for identity in ids})
        if sources[key]["adapter"] == "hsdb":
            observation_ids.setdefault(key, set()).add(row["source_record_id"])
    expected_sources = set(sources) - exceptions
    if set(manifest["source_counts"]) != expected_sources:
        fail("missing or unknown source coverage")
    if any(manifest["source_counts"][key] != counts[key] for key in expected_sources):
        fail("source count mismatch")
    if manifest["input_rows"] != sum(counts.values()) or manifest["dispositions"] != dict(dispositions):
        fail("disposition counts mismatch")
    for key in required:
        entry, config = entries[key], sources[key]
        if config["adapter"] != "hsdb":
            sheet = {"toxval": "toxval", "openfoodtox": "END_STUDY_REC.HumanHealth"}.get(
                config["adapter"], config.get("sheet", "Data" if config["adapter"].startswith("ice_") else ""))
            tables = [t for t in entry["tables"] if t["sheet"] == sheet]
            native_count = sum(n[0] == key for n in native_rows) if sql_closures and key in sql_closures else counts[key]
            if len(tables) != 1 or native_count != tables[0]["row_count"]:
                fail("native row count mismatch: " + key)
    actual_ids, groups, tasks = set(), set(), Counter()
    for row in csv_rows(outputs["curated.csv"]):
        if (row["output_row_id"] in actual_ids or memberships.get(row["output_row_id"]) !=
                (row["row_id"], row["source_id"], row["source_sha256"])):
            fail("duplicate or unaccounted curated row")
        actual_ids.add(row["output_row_id"])
        groups.add(row["group_id"])
        tasks[row["task_id"]] += 1
    if actual_ids != output_ids or len(actual_ids) != manifest["output_rows"] or len(groups) != manifest["groups"]:
        fail("curated output accounting mismatch")
    if (set(tasks) - {t["id"] for t in contract["data_curation"]["tasks"]}
            or {t["id"]: tasks[t["id"]] for t in contract["data_curation"]["tasks"]} != manifest["task_counts"]):
        fail("task count mismatch")
    identity_ids = set()
    for row in csv_rows(outputs["identity_ledger.csv"]):
        if row["row_id"] in identity_ids:
            fail("duplicate identity row")
        identity_ids.add(row["row_id"])
    if identity_ids != row_ids:
        fail("identity ledger membership mismatch")
    narrative = manifest["narrative_accounting"]
    if set(narrative) != {key for key in sources if sources[key]["adapter"] == "hsdb"}:
        fail("missing or unknown narrative coverage")
    if not sql_closures:
        _curation_semantics(manifest, outputs, entries, observation_ids, bound)
    for item in refs:
        checked(item)
    return refs


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).digest()


def lineage_fingerprint(row):
    # These native fields must remain identical across all three row ledgers.
    value = {key: row.get(key, "") for key in ("row_id", "source_id", "source_sha256", "source_record_id",
             "native_path", "native_sheet", "native_row", "field_id")}
    for key in ("native_record", "native_lineage"):
        value[key] = json.loads(row[key], object_pairs_hook=unique) if row.get(key) else None
    return stable(value)


def _ledger_integrity(records, outputs, sources, entries, policy):
    """Stream bodies once; indexed scratch stores only locators, fingerprints and IDs.

    The database is temporary and never writes into an evidence or runtime directory.
    SQL/OFT use their native primary-table locators; HSDB observations are checked
    separately against the reviewed field inventory, not against document row counts.
    """
    with tempfile.TemporaryDirectory(prefix="qsar-evidence-index-") as scratch:
        with closing(sqlite3.connect(str(Path(scratch) / "locators.sqlite3"))) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.executescript("""
                CREATE TABLE native(source TEXT, sheet TEXT, row TEXT, fingerprint BLOB, lineage BLOB, used INTEGER DEFAULT 0,
                                    PRIMARY KEY(source,sheet,row)) WITHOUT ROWID;
                CREATE TABLE disposition(id TEXT PRIMARY KEY, fingerprint BLOB) WITHOUT ROWID;
                CREATE TABLE curated(id TEXT PRIMARY KEY, task TEXT, grp TEXT, value REAL) WITHOUT ROWID;
                CREATE INDEX curated_group ON curated(task,grp);
                CREATE TABLE conflict(task TEXT, grp TEXT, PRIMARY KEY(task,grp)) WITHOUT ROWID;
                CREATE TABLE support(source TEXT, id TEXT, lineage TEXT, candidates INTEGER DEFAULT 0, used INTEGER DEFAULT 0,
                                     PRIMARY KEY(source,id)) WITHOUT ROWID;
                CREATE TABLE npass(source TEXT, row TEXT, id TEXT, body BLOB, canonical TEXT, generated TEXT, reasons TEXT,
                                   used INTEGER DEFAULT 0, PRIMARY KEY(source,row)) WITHOUT ROWID;
                CREATE TABLE npass_key(id TEXT PRIMARY KEY, signature TEXT, reasons TEXT) WITHOUT ROWID;
            """)
            selected = {key: {"toxval": "toxval", "openfoodtox": "END_STUDY_REC.HumanHealth"}.get(
                config["adapter"], config.get("sheet", "Data" if config["adapter"].startswith("ice_") else ""))
                for key, config in sources.items() if config["adapter"] not in {"hsdb", "unresolved_truncated_archive"}}
            supports = {s["source_id"] for s in policy.get("identity_sources", []) if s.get("adapter") == "chemid_identity"}
            npass_configs = [s for s in policy.get("identity_sources", []) if s.get("adapter") == "npass_structure_v1"]
            npass_sources = {s["source_id"] for s in npass_configs}
            if any(s.get("adapter") not in {None, "chemid_identity", "npass_structure_v1"}
                   for s in policy.get("identity_sources", [])):
                fail("unknown identity support adapter")
            schemas = {}
            if npass_configs:
                spec = importlib.util.spec_from_file_location("coverage_npass_chemistry", Path(__file__).with_name("qsar_data.py"))
                data_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(data_module)
                if len(npass_configs) != len(npass_sources):
                    fail("duplicate NPASS identity source configuration")
                for config in npass_configs:
                    source = config["source_id"]
                    if set(config) != {"source_id", "adapter"} or source not in entries or entries[source]["status"] != "inventoried":
                        fail("invalid NPASS support source or configuration")
                    with Path(entries[source]["path"]).open(encoding="utf-8-sig", newline="") as stream:
                        header = next(csv.reader(stream, delimiter="\t"), [])
                    if (not header or len(header) != len(set(header)) or any(not key for key in header)
                            or not {"np_id", "SMILES", "InChI", "InChIKey"} <= set(header)
                            or len(entries[source]["tables"]) != 1 or entries[source]["tables"][0]["sheet"] != ""
                            or entries[source]["tables"][0]["columns"] != header):
                        fail("NPASS native schema disagrees with collection")
                    schemas[source] = set(header)
            counts = Counter()
            for row in json_rows(records):
                source = row["source_id"]
                if source not in entries or row["sha256"] != entries[source]["sha256"]:
                    fail("collection record source binding mismatch")
                counts[source] += 1
                if source in npass_sources:
                    native = row["record"]
                    if (row["sheet"] != "" or type(row["native_row"]) is not int or row["native_row"] < 2
                            or set(native) != schemas[source] or any(not isinstance(value, str) for value in native.values())):
                        fail("malformed NPASS collection row")
                    npid, canonical, generated, native_key, reasons = _npass_row(native, policy["chemistry_policy"], data_module)
                    if npid:
                        prior = db.execute("SELECT signature,reasons FROM npass_key WHERE id=?", (npid,)).fetchone()
                        signature = json.dumps([canonical, native_key]) if canonical else None
                        if prior and signature and prior[0] is not None and signature != prior[0]:
                            reasons.append("conflicting_structure_candidates")
                        union = sorted(set(reasons + (json.loads(prior[1]) if prior else [])))
                        db.execute("INSERT INTO npass_key VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET signature=COALESCE(signature,excluded.signature),reasons=excluded.reasons",
                                   (npid, signature, json.dumps(union)))
                    db.execute("INSERT INTO npass(source,row,id,body,canonical,generated,reasons) VALUES(?,?,?,?,?,?,?)",
                               (source, str(row["native_row"]), npid, stable(native), canonical, generated, json.dumps(sorted(set(reasons)))))
                if source in selected and row["sheet"] == selected[source]:
                    db.execute("INSERT INTO native(source,sheet,row,fingerprint,lineage) VALUES(?,?,?,?,?)",
                               (source, row["sheet"], str(row["native_row"]), stable(row["record"]),
                                stable(row.get("native_lineage", {}))))
                if source in supports:
                    native = row["record"]
                    if "attributes" in native:
                        record_id = native["attributes"].get("id", "")
                        if db.execute("SELECT 1 FROM support WHERE source=? AND id=? AND lineage IS NOT NULL", (source, record_id)).fetchone():
                            fail("duplicate identity support document")
                        db.execute("INSERT INTO support(source,id,lineage) VALUES(?,?,?) ON CONFLICT(source,id) DO UPDATE SET lineage=excluded.lineage",
                                   (source, record_id, json.dumps(row["native_lineage"], sort_keys=True)))
                    elif "SUPERID" in native:
                        record_id = native["SUPERID"].strip()
                        db.execute("INSERT INTO support(source,id,candidates) VALUES(?,?,1) ON CONFLICT(source,id) DO UPDATE SET candidates=candidates+1",
                                   (source, record_id))
                    else:
                        fail("unknown identity support collection record")
            emitted = Counter()
            for row in csv_rows(outputs["dispositions.csv"], ("row_id", "disposition", "reasons", "output_row_ids")):
                source = row["source_id"]
                if source not in sources or Path(row["native_path"]).resolve() != Path(entries[source]["path"]).resolve():
                    fail("unknown disposition source path")
                db.execute("INSERT INTO disposition VALUES(?,?)", (row["row_id"], lineage_fingerprint(row)))
                if sources[source]["adapter"] == "hsdb":
                    continue
                lineage = json.loads(row.get("native_lineage") or "{}", object_pairs_hook=unique)
                sheet, number = row["native_sheet"], row["native_row"]
                if sources[source]["adapter"] == "openfoodtox":
                    emitted[source] += 1
                    if row["native_row"] != str(emitted[source]):
                        fail("inconsistent native emission ordinal")
                    sheet, number = lineage["sheet"], str(lineage["native_row"])
                    if row["native_sheet"] != sheet or lineage["path"] != entries[source]["path"]:
                        fail("inconsistent native sheet")
                locator = (source, sheet, number)
                found = db.execute("SELECT fingerprint,lineage,used FROM native WHERE source=? AND sheet=? AND row=?", locator).fetchone()
                sql_source = any(c['source_id'] == source and c.get('sql_curation') is not None for c in policy['sources'])
                if found is None or (found[2] and not sql_source) or found[0] != stable(json.loads(row["native_record"], object_pairs_hook=unique)):
                    fail("disposition locator is missing, duplicate or contradicts collection")
                # Collection stores SQL tuple lineage; curation adds the separate
                # identity join. OFT collection stores the physical sheet/row only.
                original_lineage = ({k: v for k, v in lineage.items() if k != "source_chemical_matches"}
                                    if sources[source]["adapter"] == "toxval" else {})
                if stable(original_lineage) != found[1]:
                    fail("disposition native lineage contradicts collection")
                db.execute("UPDATE native SET used=1 WHERE source=? AND sheet=? AND row=?", locator)
            if db.execute("SELECT 1 FROM native WHERE used=0 LIMIT 1").fetchone():
                fail("unaccounted collection locator")
            for name in ("curated.csv", "identity_ledger.csv"):
                # Empty curated output uses the producer's documented minimal header.
                minimum = ("output_row_id", "task_id", "group_id", "parent_smiles", "target_mg_kg", "target_pLD50") if name == "curated.csv" else ("row_id", "status", "flags")
                for row in csv_rows(outputs[name], minimum):
                    expected = db.execute("SELECT fingerprint FROM disposition WHERE id=?", (row["row_id"],)).fetchone()
                    if expected is None or expected[0] != lineage_fingerprint(row):
                        fail("native lineage differs across row ledgers")
                    if name == "curated.csv":
                        db.execute("INSERT INTO curated VALUES(?,?,?,?)", (row["output_row_id"], row["task_id"], row["group_id"], float(row["target_mg_kg"])))
            for row in csv_rows(outputs["identity_support_ledger.csv"], ("source_id", "record_id", "cas", "status", "reasons")):
                source = row["source_id"]
                reasons = json.loads(row["reasons"], object_pairs_hook=unique)
                if source in npass_sources:
                    _npass_support_row(db, row, entries[source], reasons)
                    continue
                if (source not in supports or row.get("sha256") != entries[source]["sha256"]
                        or not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason for reason in reasons)
                        or row["status"] != ("quarantined" if reasons else "candidate")):
                    fail("invalid support source or disposition")
                found = db.execute("SELECT lineage,candidates,used FROM support WHERE source=? AND id=?", (source, row["record_id"])).fetchone()
                if found is None or found[2] or int(row["candidate_count"]) != found[1]:
                    fail("unknown, duplicate or contradictory support membership")
                lineage = json.loads(row["native_lineage"], object_pairs_hook=unique)
                if found[0] is not None:
                    if lineage != json.loads(found[0]):
                        fail("support document lineage mismatch")
                elif lineage != {"path": entries[source]["path"]} or "orphan_structure_superid" not in reasons:
                    fail("unaccounted orphan support membership")
                db.execute("UPDATE support SET used=1 WHERE source=? AND id=?", (source, row["record_id"]))
            if db.execute("SELECT 1 FROM support WHERE used=0 LIMIT 1").fetchone():
                fail("missing identity support membership")
            if db.execute("SELECT 1 FROM npass WHERE used=0 LIMIT 1").fetchone():
                fail("missing NPASS physical support row")
            if npass_sources:
                _npass_identity_membership(db, outputs, sources, entries, npass_sources)
            for row in csv_rows(outputs["conflict_ledger.csv"], ("task_id", "group_id", "policy")):
                key = (row["task_id"], row["group_id"])
                ids, values = json.loads(row["row_ids"]), json.loads(row["values_mg_kg"])
                if (row["policy"] != "preserve_flag" or row["status"] != "potential_replicate_or_lineage_duplicate"
                        or not isinstance(ids, list) or not isinstance(values, list) or len(ids) != len(values)
                        or len(ids) < 2 or len(ids) != len(set(ids))):
                    fail("malformed conflict disposition")
                expected_count = db.execute("SELECT COUNT(*) FROM curated WHERE task=? AND grp=?", key).fetchone()[0]
                if len(ids) != expected_count or int(row["distinct_values"]) != len(set(values)):
                    fail("conflict group accounting mismatch")
                for identity, value in zip(ids, values):
                    found = db.execute("SELECT task,grp,value FROM curated WHERE id=?", (identity,)).fetchone()
                    if found != (*key, value):
                        fail("unknown or contradictory conflict row membership")
                db.execute("INSERT INTO conflict VALUES(?,?)", key)
            if db.execute("SELECT task,grp FROM curated GROUP BY task,grp HAVING COUNT(*)>1 EXCEPT SELECT task,grp FROM conflict LIMIT 1").fetchone():
                fail("missing conflict group")
            return counts


def _npass_row(native, chemistry_policy, data_module):
    """Derive adapter-v1 structural evidence using the unchanged pure chemistry rule."""
    from rdkit import Chem
    npid, smiles, inchi, native_key = (native[key].strip() for key in ("np_id", "SMILES", "InChI", "InChIKey"))
    reasons = [] if npid else ["missing_npid"]
    canonical, generated = "", ""
    if not smiles or not inchi:
        reasons.append("missing_structure_representation")
    else:
        try:
            canonical = data_module.chemistry(smiles, inchi, chemistry_policy)["original_structure"]
            generated = Chem.MolToInchiKey(Chem.MolFromSmiles(canonical))
        except data_module.DataError as error:
            reasons.append(str(error))
    if not re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", native_key):
        reasons.append("missing_or_invalid_native_inchikey")
    elif generated and generated != native_key:
        reasons.append("native_inchikey_disagreement")
    return npid, canonical, generated, native_key, reasons


def _npass_support_row(db, row, entry, reasons):
    found = db.execute("SELECT id,body,canonical,generated,reasons,used FROM npass WHERE source=? AND row=?",
                       (row["source_id"], row["native_row"])).fetchone()
    if found is None or found[5]:
        fail("unknown or duplicate NPASS physical support row")
    npid, body, canonical, generated, local_reasons, _ = found
    union = db.execute("SELECT reasons FROM npass_key WHERE id=?", (npid,)).fetchone() if npid else None
    expected_reasons = sorted(set(json.loads(local_reasons) + (json.loads(union[0]) if union else [])))
    blocked = bool(npid and expected_reasons)
    if (row.get("adapter") != "npass_structure_v1" or row.get("sha256") != entry["sha256"]
            or row["record_id"] != npid or row["npid"] != npid or row["cas"] != ""
            or stable(json.loads(row["native_record"], object_pairs_hook=unique)) != body
            or json.loads(row["native_lineage"], object_pairs_hook=unique) != {"native_row": int(row["native_row"])}
            or row["canonical_structure"] != canonical or row["generated_inchikey"] != generated
            or reasons != expected_reasons or row["blocked_key"] != str(blocked)
            or row["status"] != ("quarantined" if expected_reasons else "candidate")):
        fail("NPASS support source, structure or blocked-key evidence mismatch")
    db.execute("UPDATE npass SET used=1 WHERE source=? AND row=?", (row["source_id"], row["native_row"]))


def _npass_identity_membership(db, outputs, sources, entries, npass_sources):
    db.execute("CREATE TEMP TABLE blocked_npass_row(id TEXT PRIMARY KEY) WITHOUT ROWID")
    for row in csv_rows(outputs["identity_ledger.csv"]):
        config = sources[row["source_id"]]
        native = json.loads(row["native_record"], object_pairs_hook=unique)
        npid, known, matched_npass = None, None, False
        if config["adapter"] == "npass":
            npid = native.get(config.get("columns", {}).get("npid", "np_id"), "").strip()
            known = db.execute("SELECT reasons FROM npass_key WHERE id=?", (npid,)).fetchone()
            if known and json.loads(known[0]):
                if row["status"] != "unresolved_support_identity":
                    fail("blocked NPASS key cannot resolve an identity")
                db.execute("INSERT INTO blocked_npass_row VALUES(?)", (row["row_id"],))
        claims = json.loads(row["evidence"], object_pairs_hook=unique)
        if config["adapter"] == "npass" and row["status"] == "exact_identifier_mapping":
            if not any(claim.get("records") for claim in claims):
                fail("NPASS exact identity lacks candidate support evidence")
        for claim in claims:
            records = claim.get("records", [claim])
            for record in records:
                source = record.get("source_id")
                if source not in npass_sources:
                    continue
                found = db.execute("SELECT id,body,canonical FROM npass WHERE source=? AND row=?",
                                   (source, str(record["native_row"]))).fetchone()
                if (found is None or record["sha256"] != entries[source]["sha256"]
                        or record.get("adapter") != "npass_structure_v1" or record["record_id"] != found[0]
                        or stable(record["native_record"]) != found[1]
                        or record["native_lineage"] != {"native_row": record["native_row"]}):
                    fail("identity references unknown or contradictory NPASS support evidence")
                if npid is not None and found[0] != npid:
                    fail("NPASS identity support does not match native toxicity identifier")
                if "records" in claim:
                    blocked = db.execute("SELECT reasons FROM npass_key WHERE id=?", (found[0],)).fetchone()
                    if (claim.get("key") != "npid" or claim.get("value") != found[0] or claim.get("structure") != found[2]
                            or not found[2] or (blocked and json.loads(blocked[0]))):
                        fail("identity claims an invalid NPASS candidate mapping")
                    matched_npass = True
        if known and row["status"] == "exact_identifier_mapping" and not matched_npass:
            fail("NPASS exact identity lacks native candidate support evidence")
    for row in csv_rows(outputs["dispositions.csv"]):
        if db.execute("SELECT 1 FROM blocked_npass_row WHERE id=?", (row["row_id"],)).fetchone():
            if row["disposition"] != "quarantined" or json.loads(row["output_row_ids"]):
                fail("blocked NPASS key cannot produce training rows")


def _curation_semantics(manifest, outputs, entries, observation_ids, bound):
    """One raw identity index and one adapter pass; no submitted eligibility inputs."""
    spec = importlib.util.spec_from_file_location("coverage_semantics", Path(__file__).with_name("qsar_data.py"))
    data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(data)
    if manifest.get('material_integration') is not None:
        return _material_semantics(data, manifest, outputs, bound)
    contract = load(bound(manifest['bound_evidence']['contract']))
    if 'verified_tested_material_v4' in contract.get('scientific_policy', {}) or manifest.get('external_material_config'):
        fail('material-enabled curation omitted its bound packet')
    if any(s.get('ordinary_adjudication') is not None for s in manifest['policy']['sources']):
        return _material_semantics(data, manifest, outputs, bound)
    policy = manifest["policy"]
    sources = {s["source_id"]: s for s in policy["sources"]}
    metadata_sources = {s for s, c in sources.items() if 'narrative_observation_metadata' in c}
    if set(manifest.get('observation_metadata_accounting', {})) != metadata_sources:
        fail('metadata accounting source scope mismatch')
    registry = load(bound(manifest["bound_evidence"]["registry"]))
    with tempfile.TemporaryDirectory(prefix="qsar-eligibility-") as scratch, ExitStack() as stack:
        db = stack.enter_context(closing(sqlite3.connect(str(Path(scratch) / "expected.sqlite3"))))
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("CREATE TABLE expected(kind TEXT,id TEXT,source TEXT,row_id TEXT,task TEXT,grp TEXT,body TEXT,used INTEGER DEFAULT 0,PRIMARY KEY(kind,id)) WITHOUT ROWID")
        db.execute("CREATE INDEX expected_group ON expected(kind,task,grp)")
        db.execute("CREATE INDEX expected_row ON expected(kind,row_id)")
        blocked_keys = {}
        support = data.DiskLedger(Path(scratch) / "support.jsonl", stack)
        index = data.identity_index(policy, registry, support, blocked_keys)
        inferred_units = {}

        def reconstruct(source, number, raw, sheet, adjudicated=False):
            disposition, identity, emitted = data.curate_observation(raw, sources[source], policy,
                entries[source], number, sheet, index, blocked_keys, inferred_units.get(source), adjudicated)
            for kind, identifier, row in [("dispositions.csv", "row_id", disposition),
                                          ("identity_ledger.csv", "row_id", identity)]:
                db.execute("INSERT INTO expected(kind,id,source,row_id,body) VALUES(?,?,?,?,?)",
                           (kind, row[identifier], source, row["row_id"], json.dumps(row, ensure_ascii=False, default=str)))
            for row in emitted:
                row["group_id"] = row["connectivity_id"]
                db.execute("INSERT INTO expected(kind,id,source,row_id,task,grp,body) VALUES(?,?,?,?,?,?,?)",
                           ("curated.csv", row["output_row_id"], source, row["row_id"], row["task_id"], row["group_id"], json.dumps(row, ensure_ascii=False, default=str)))
            return emitted

        for source, config in sources.items():
            adapter, entry = config["adapter"], entries[source]
            if adapter == "hsdb":
                _narrative(source, config, entry, manifest, outputs, observation_ids.get(source, set()), bound, reconstruct)
                continue
            if adapter not in data.ADAPTERS:
                continue  # Existing strict collection/blocker checks already gate exceptions.
            if adapter == "toxric":
                inferred_units[source] = data.toxric_unit(config, registry, entry)
            sheet = config.get("sheet", "Data" if adapter.startswith("ice_") else "")
            if adapter in data.NATIVE_ADAPTERS:
                sheet = "toxval" if adapter == "toxval" else "END_STUDY_REC.HumanHealth"
                parser = data.native_sources().iter_toxval(entry["path"], recover_identity=True) if adapter == "toxval" else data.native_sources().iter_openfoodtox(entry["path"])
                iterator = enumerate(parser, 1)
            else:
                iterator = data.rows(entry["path"], sheet, config.get("header_row", 1))
            for number, raw in iterator:
                reconstruct(source, number, raw, sheet)

        # Reconstruct the producer's only post-row mutations from expected native
        # membership. Shared flags accumulate once per emitted task for ToxVal.
        for source, config in sources.items():
            if config["adapter"] != "toxval":
                continue
            selected = {json.loads(body)["source_record_id"] for (body,) in
                        db.execute("SELECT body FROM expected WHERE kind='curated.csv' AND source=?", (source,))}
            references, relationships = data.native_sources().toxval_selected_metadata(entries[source]["path"], selected)
            for identifier, row_id, body in db.execute("SELECT id,row_id,body FROM expected WHERE kind='curated.csv' AND source=?", (source,)):
                row = json.loads(body)
                record_id = row["source_record_id"]
                row["record_source_matches"] = references.get(record_id, [])
                row["native_relationships"] = relationships.get(record_id, [])
                row["study_references"] = [match["record"].get("long_ref") for match in references.get(record_id, [])]
                additions = ([] if references.get(record_id) else ["missing_record_source_provenance"])
                if relationships.get(record_id):
                    additions.append("native_relationship_present")
                multiplicity = db.execute("SELECT COUNT(*) FROM expected WHERE kind='curated.csv' AND row_id=?", (row_id,)).fetchone()[0]
                row["flags"].extend(additions * multiplicity)
                db.execute("UPDATE expected SET body=? WHERE kind='curated.csv' AND id=?", (json.dumps(row), identifier))
                identity = json.loads(db.execute("SELECT body FROM expected WHERE kind='identity_ledger.csv' AND id=?", (row_id,)).fetchone()[0])
                identity["flags"] = row["flags"]
                db.execute("UPDATE expected SET body=? WHERE kind='identity_ledger.csv' AND id=?", (json.dumps(identity), row_id))
        for identifier, task, group, body in db.execute("SELECT id,task,grp,body FROM expected WHERE kind='curated.csv'"):
            row = json.loads(body)
            row["replicate_flag"] = db.execute("SELECT COUNT(*) FROM expected WHERE kind='curated.csv' AND task=? AND grp=?", (task, group)).fetchone()[0] > 1
            db.execute("UPDATE expected SET body=? WHERE kind='curated.csv' AND id=?", (json.dumps(row), identifier))

        for name, identifier in (("dispositions.csv", "row_id"), ("identity_ledger.csv", "row_id"),
                                 ("curated.csv", "output_row_id")):
            for row in csv_rows(outputs[name]):
                found = db.execute("SELECT body,used FROM expected WHERE kind=? AND id=?", (name, row[identifier])).fetchone()
                if found is None or found[1]:
                    fail("unknown or duplicate reconstructed curation membership")
                expected = json.loads(found[0])
                if bool(row.get('observation_metadata')) != bool(expected.get('observation_metadata')):
                    fail('unexpected or omitted observation metadata projection')
                for key, value in expected.items():
                    actual = row.get(key, "")
                    if isinstance(value, (dict, list)):
                        equal = bool(actual) and json.loads(actual, object_pairs_hook=unique) == value
                    else:
                        equal = actual == ("" if value is None else str(value))
                    if not equal:
                        fail("curation differs from native policy reconstruction: " + name + ":" + key)
                db.execute("UPDATE expected SET used=1 WHERE kind=? AND id=?", (name, row[identifier]))
        if db.execute("SELECT 1 FROM expected WHERE used!=1 LIMIT 1").fetchone():
            fail("missing reconstructed curation membership")


def _material_semantics(data, manifest, outputs, bound):
    """Reconstruct a distinct native store from original inputs, then check typed rows.

    Sharing deterministic algorithms is an integrity check, not an independent
    scientific classification. The separately attested source reviews remain required.
    """
    import qsar_material_curation as material
    integration = manifest.get('material_integration')
    packet_path = None
    if integration is not None:
        material.keys(integration, ('schema_version', 'packet', 'classification_context', 'material_context_sha256', 'references'))
        if manifest.get('external_material_config') != integration['packet']:
            fail('material packet must be discovered through the bound manifest')
        packet_path = bound(integration['packet'])
        for item in integration['references']:
            bound(item)
    for accounting in manifest.get('ordinary_accounting', {}).values():
        material.keys(accounting, ('summary', 'references'))
        for item in accounting['references']:
            bound(item)
    with tempfile.TemporaryDirectory(prefix='qsar-material-reconstruction-') as scratch:
        out = Path(scratch) / 'curation'
        built = data.build_curation(manifest['bound_evidence']['contract']['path'], manifest['bound_evidence']['registry']['path'], out,
            adjudication_config_path=manifest['external_adjudication_config']['path'] if manifest.get('external_adjudication_config') else None,
            material_config_path=packet_path)
        expected_manifest = built['summary']
        if expected_manifest['material_integration'] != integration:
            fail('material integration differs from current native reconstruction')
        for key in ('source_counts', 'input_rows', 'dispositions', 'output_rows', 'groups', 'task_counts',
                    'metadata_coverage', 'narrative_accounting', 'ordinary_accounting', 'blockers'):
            if expected_manifest[key] != manifest[key]:
                fail('material reconstructed summary mismatch: ' + key)
        if expected_manifest.get('observation_metadata_accounting') != manifest.get('observation_metadata_accounting'):
            fail('material reconstructed metadata accounting mismatch')
        for accounting in expected_manifest.get('observation_metadata_accounting', {}).values():
            for item in accounting['references']:
                bound(item)
        packet = material.decode(packet_path.read_text(encoding='utf-8')) if packet_path else None
        # The separate reconstruction just checked these exact source-bound files.
        reviewed = None
        if packet is not None:
            inventory = {material.digest(item['observation']): item for item in json_rows(packet['materials']['inventory']['path'])}
            decisions = {item['observation_id']: item for item in json_rows(packet['materials']['decisions']['path'])}
            context = load(packet['materials']['receipt']['path'])['context']
            reviewed = dict(inventory=inventory, decisions=decisions, context=context)
        names = set(expected_manifest['bound_evidence']['outputs'])
        if names != set(outputs):
            fail('material reconstructed output inventory mismatch')
        for name in sorted(names):
            if name.endswith('.csv'):
                sentinel = object()
                for actual, expected in itertools.zip_longest(csv_rows(outputs[name]), csv_rows(out / name), fillvalue=sentinel):
                    if actual is sentinel or expected is sentinel or set(actual) != set(expected):
                        fail('material output membership/header mismatch: ' + name)
                    if name == 'curated.csv':
                        if actual.get('material_schema_version'):
                            typed = material.decode_material_csv(actual)
                            material.validate_projection_v2(typed, reviewed, csv_projection=True)
                        elif any(actual.get(k) for k in material.MATERIAL_COLUMNS):
                            fail('organic output contains material-only columns')
                    for key, value in expected.items():
                        if value.startswith(('{', '[')):
                            try:
                                expected_value = material.decode(value)
                            except ValueError:
                                equal = actual[key] == value  # Literal bracketed SMILES.
                            else:
                                equal = material.decode(actual[key]) == expected_value
                        else:
                            equal = actual[key] == value
                        if not equal:
                            fail('material output differs from native reconstruction: ' + name + ':' + key)
            elif list(json_rows(outputs[name])) != list(json_rows(out / name)):
                fail('material narrative field ledger differs from reconstruction')


def _narrative(key, config, entry, manifest, outputs, expected_ids, bound, reconstruct):
    summary = manifest["narrative_accounting"][key]
    effective = next(s for s in manifest["policy"]["sources"] if s["source_id"] == key)
    if effective.get("narrative_adjudication", {}).get("schema_version") == 2:
        return _narrative_v2(key, effective, entry, manifest, outputs, expected_ids, bound, reconstruct)
    if summary.get("complete_narrative_accounting") is not True:
        fail("incomplete narrative review: " + key)
    spec = importlib.util.spec_from_file_location("coverage_adjudication", Path(__file__).with_name("qsar_adjudication.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Constructor validates exact receipt/code/policy/source/config and independent identities.
    # Do not call rows(): it is a producer and writes a ledger.
    config = manifest["policy"]["sources"][[s["source_id"] for s in manifest["policy"]["sources"]].index(key)]
    for item in config["narrative_adjudication"].values():
        bound(item)
    adjudication = module.Adjudication(config["narrative_adjudication"], manifest["policy"], entry, {}, Path("unused-read-only-ledger"))
    metadata = None
    if 'narrative_observation_metadata' in config:
        data = __import__('qsar_data')
        metadata = module.ObservationMetadata(config['narrative_observation_metadata'],
                                              config['narrative_adjudication'], entry, data.UNITS,
                                              config.get('exact_unit_aliases', {}))
        adjudication.metadata = metadata
    bound(adjudication.review_evidence)
    native, kinds, documents = _narrative_membership(module, adjudication, key, entry, outputs, summary, reconstruct)
    if metadata is not None:
        _metadata_accounting(metadata, key, manifest, outputs, bound)
    if native != adjudication.inventory:
        fail("narrative inventory differs from native source")
    actions, ids, fields_seen = Counter(), set(), set()
    for row in json_rows(outputs[summary["field_ledger"]]):
        field_id = row["field_id"]
        if field_id in fields_seen or field_id not in adjudication.decisions:
            fail("duplicate or unknown narrative field")
        fields_seen.add(field_id)
        if {k: v for k, v in row.items() if k != "observation_ids"} != adjudication.decisions[field_id]:
            fail("field ledger disagrees with reviewed decisions")
        action = row["action"]
        if action not in {"parser", "observations", "excluded"}:
            fail("unresolved narrative field")
        current = row["observation_ids"]
        if len(current) != len(set(current)) or ids.intersection(current):
            fail("duplicate narrative observation")
        if len(current) != (1 if action == "parser" else len(row["observations"]) if action == "observations" else 0):
            fail("narrative observation count mismatch")
        ids.update(current)
        actions[action] += 1
    if (fields_seen != native.keys() or ids != expected_ids or dict(actions) != summary["actions"]
            or len(fields_seen) != summary["fields"] or len(ids) != summary["observations"]
            or len(ids) != manifest["source_counts"][key] or documents != summary["documents"]
            or dict(kinds) != summary["entry_kinds"]):
        fail("narrative accounting mismatch")


def _narrative_v2(source, config, entry, manifest, outputs, expected_ids, bound, reconstruct):
    """Replay span/policy checks in scratch; independently join all emitted bodies.

    This is an integrity guard, not an independent scientific review. The engine
    never writes to the submitted evidence. Native fields, all candidate holds,
    exact receipt scope and context are reconstructed on every validation.
    """
    spec = importlib.util.spec_from_file_location("coverage_v2_data", Path(__file__).with_name("qsar_data.py"))
    data_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(data_module)
    binding_config = config["narrative_adjudication"]
    engine_config = data_module.narrative_binding(binding_config, manifest["bound_evidence"]["contract"]["path"])
    summary = manifest["narrative_accounting"][source]
    with tempfile.TemporaryDirectory(prefix="qsar-candidate-reconstruction-") as scratch:
        ledger = Path(scratch) / "fields.jsonl"
        engine = data_module.adjudication_module().AdjudicationV2(engine_config, entry,
            binding_config["full_contract"], binding_config["accepted_contract_review"], data_module.UNITS, ledger)
        metadata = None
        if 'narrative_observation_metadata' in config:
            metadata = data_module.adjudication_module().ObservationMetadata(
                config['narrative_observation_metadata'], engine_config, entry, data_module.UNITS,
                config.get('exact_unit_aliases', {}))
            engine.metadata = metadata
        with closing(sqlite3.connect(str(Path(scratch) / "membership.sqlite3"))) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("CREATE TABLE candidate(id TEXT PRIMARY KEY, body TEXT, dispositions INTEGER DEFAULT 0, "
                       "identities INTEGER DEFAULT 0, outputs INTEGER DEFAULT 0, expected_outputs INTEGER DEFAULT 0)")
            # A candidate can enter ordinary curation only through an emitted row.
            # Holds never enter this index, so any held/unknown-origin emission fails.
            for number, row in enumerate(engine.rows(), 1):
                row.update(native_row=number, native_sheet="hsdb_narratives",
                           row_id=f"{source}:hsdb_narratives:{row['record_id']}")
                expected_outputs = reconstruct(source, number, row, "hsdb_narratives", True)
                db.execute("INSERT INTO candidate(id,body,expected_outputs) VALUES(?,?,?)",
                           (row["candidate_id"], json.dumps(row, ensure_ascii=False), len(expected_outputs)))
            reconstructed = engine.summary
            if not all(reconstructed.get(k) is True for k in (
                    "field_accounting_complete", "candidate_disposition_accounting_complete",
                    "source_review_complete", "required_source_coverage_complete")):
                fail("incomplete candidate source review: " + source)
            if {k: v for k, v in summary.items() if k != "field_ledger"} != reconstructed:
                fail("candidate summary differs from native reconstruction")
            sentinel = object()
            for actual, expected in itertools.zip_longest(json_rows(outputs[summary["field_ledger"]]),
                                                          json_rows(ledger), fillvalue=sentinel):
                if actual != expected:
                    fail("candidate field ledger differs from native reconstruction")
            ids = {row[0] for row in db.execute("SELECT id FROM candidate")}
            if ids != expected_ids or len(ids) != manifest["source_counts"][source]:
                fail("candidate observation membership mismatch")
            scalars = ("candidate_id", "measurement_id", "population_id", "field_id", "source_record_kind",
                       "sex", "strain", "study_id", "dose_basis", "native_value", "original_relation",
                       "row_id", "native_row", "native_sheet")
            structured = ("native_record", "native_lineage", "adjudication", "target_origin", "observation_metadata")
            for name, counter in (("dispositions.csv", "dispositions"), ("identity_ledger.csv", "identities"),
                                  ("curated.csv", "outputs")):
                for row in csv_rows(outputs[name]):
                    if row.get("source_id") != source:
                        continue
                    found = db.execute("SELECT body FROM candidate WHERE id=?", (row["source_record_id"],)).fetchone()
                    if found is None:
                        fail("held or unknown candidate emitted")
                    projected = json.loads(found[0])
                    for key in scalars:
                        expected = projected.get(key)
                        if row.get(key, "") != ("" if expected is None else str(expected)):
                            fail("candidate scalar body mismatch: " + key)
                    for key in structured:
                        actual = json.loads(row[key], object_pairs_hook=unique) if row.get(key) else None
                        if actual != projected.get(key):
                            fail("candidate native/evidence body mismatch: " + key)
                    if counter == "outputs":
                        for key in ("value", "unit", "relation"):
                            if row["raw_" + key] != data_module.text(projected.get(key)):
                                fail("candidate original measurement mismatch: " + key)
                    db.execute(f"UPDATE candidate SET {counter}={counter}+1 WHERE id=?", (row["source_record_id"],))
            if db.execute("SELECT 1 FROM candidate WHERE dispositions!=1 OR identities!=1 OR outputs!=expected_outputs LIMIT 1").fetchone():
                fail("missing or duplicate candidate membership")
        for descriptor in engine.bindings:
            bound(descriptor)
        if metadata is not None:
            _metadata_accounting(metadata, source, manifest, outputs, bound)


def observation_fingerprint(record_id, field_id, record, locator, kind, proposal):
    return stable([record_id, field_id, record, locator, kind, proposal])


def _metadata_accounting(metadata, source, manifest, outputs, bound):
    metadata.finish()
    def dispositions():
        for row in csv_rows(outputs['dispositions.csv']):
            if row['source_id'] == source:
                row['observation_metadata'] = json.loads(row['observation_metadata'], object_pairs_hook=unique) if row.get('observation_metadata') else None
                row['output_row_ids'] = json.loads(row['output_row_ids'], object_pairs_hook=unique)
                yield row
    if metadata.accounting(dispositions()) != manifest.get('observation_metadata_accounting', {}).get(source):
        fail('metadata consumption accounting differs from native replay')
    for item in metadata.references:
        bound(item)


def _narrative_membership(module, adjudication, source, entry, outputs, summary, reconstruct):
    """Reconstruct v1 IDs with pure span validation, never Adjudication.rows()."""
    spec = importlib.util.spec_from_file_location("coverage_units", Path(__file__).with_name("qsar_data.py"))
    data_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(data_module)
    native, kinds, documents = {}, Counter(), 0
    ordinal = 0
    with tempfile.TemporaryDirectory(prefix="qsar-narrative-index-") as scratch:
        with closing(sqlite3.connect(str(Path(scratch) / "observations.sqlite3"))) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.executescript("""
                CREATE TABLE observation(id TEXT PRIMARY KEY, fingerprint BLOB,
                    dispositions INTEGER DEFAULT 0, identities INTEGER DEFAULT 0,
                    expected_outputs INTEGER DEFAULT 0, outputs INTEGER DEFAULT 0) WITHOUT ROWID;
                CREATE TABLE field(id TEXT PRIMARY KEY, observations TEXT) WITHOUT ROWID;
            """)
            for fields in module.native_fields(entry["path"], source, entry["sha256"]):
                documents += 1
                document = {field["field_id"]: field for field in fields}
                for field in fields:
                    if field["candidate_kind"] == "context_only":
                        continue
                    kinds[field["candidate_kind"]] += 1
                    field = dict(field, document_context=[f for f in fields if f["locator"]["tag"] in
                                 {"DOCNO", "NameOfSubstance", "CASRegistryNumber", "DateRevised"}])
                    field_id = field["field_id"]
                    native[field_id] = module.stable(field)
                    if adjudication.inventory.get(field_id) != native[field_id]:
                        fail("narrative inventory differs from native source")
                    decision = adjudication.decisions[field_id]
                    if not decision["reason"] or not decision["evidence"]:
                        fail("narrative decision lacks evidence")
                    for span in decision["evidence"]:
                        module.span(span, document)
                    action = decision["action"]
                    ids = []
                    if action == "observations":
                        if not decision["observations"]:
                            fail("empty narrative observation proposal")
                        for proposal in decision["observations"]:
                            # Existing v1 pure function validates same-document spans
                            # and computes the source-derived observation identity.
                            projected = module.observation(proposal, field, document, data_module.UNITS, adjudication.tasks)
                            if getattr(adjudication, 'metadata', None) is not None:
                                adjudication.metadata.attach(projected, document)
                            ordinal += 1
                            reconstruct(source, ordinal, projected, "hsdb_narratives", True)
                            identity = projected["record_id"]
                            ids.append(identity)
                            fingerprint = observation_fingerprint(identity, field_id, field["native_record"], field["locator"],
                                                                  "adjudicated_observation", proposal)
                            db.execute("INSERT INTO observation(id,fingerprint) VALUES(?,?)", (identity, fingerprint))
                    elif decision["observations"]:
                        fail("unused narrative observation proposal")
                    elif action == "parser":
                        if field["parser"]["status"] != "candidate":
                            fail("narrative parser field is not a scalar candidate")
                        identity = module.stable([field_id, "parser"])
                        cas_values = {f["native_record"]["text"].strip() for f in fields
                                      if f["locator"]["tag"] == "CASRegistryNumber" and f["native_record"]["text"].strip()}
                        projected = dict(field["parser"], record_id=identity, field_id=field_id,
                            native_record=field["native_record"], native_lineage=field["locator"],
                            cas=next(iter(cas_values)) if len(cas_values) == 1 else "",
                            smiles="", inchi="", dtxsid="", npid="", test_material="", source_url="",
                            source_record_kind="reviewed_parser_observation", adjudication=decision)
                        if len(cas_values) != 1:
                            projected.update(status="quarantined", why=["ambiguous_or_missing_native_cas"])
                        ordinal += 1
                        reconstruct(source, ordinal, projected, "hsdb_narratives", True)
                        ids.append(identity)
                        fingerprint = observation_fingerprint(identity, field_id, field["native_record"], field["locator"],
                                                              "reviewed_parser_observation", decision)
                        db.execute("INSERT INTO observation(id,fingerprint) VALUES(?,?)", (identity, fingerprint))
                    elif action != "excluded":
                        fail("unresolved narrative field")
                    db.execute("INSERT INTO field VALUES(?,?)", (field_id, json.dumps(ids)))
            for field in json_rows(outputs[summary["field_ledger"]]):
                expected = db.execute("SELECT observations FROM field WHERE id=?", (field["field_id"],)).fetchone()
                if expected is None or json.loads(expected[0]) != field["observation_ids"]:
                    fail("field ledger observation IDs differ from reviewed native proposals")
            for name, counter in (("dispositions.csv", "dispositions"), ("identity_ledger.csv", "identities"), ("curated.csv", "outputs")):
                for row in csv_rows(outputs[name]):
                    if row.get("source_id") != source:
                        continue
                    fingerprint = observation_fingerprint(row["source_record_id"], row["field_id"],
                        json.loads(row["native_record"], object_pairs_hook=unique), json.loads(row["native_lineage"], object_pairs_hook=unique),
                        row["source_record_kind"], json.loads(row["adjudication"], object_pairs_hook=unique))
                    expected = db.execute("SELECT fingerprint FROM observation WHERE id=?", (row["source_record_id"],)).fetchone()
                    if expected is None or expected[0] != fingerprint:
                        fail("HSDB observation does not match its reviewed native field and proposal")
                    # Column names are fixed internal constants, never evidence text.
                    db.execute(f"UPDATE observation SET {counter}={counter}+1 WHERE id=?", (row["source_record_id"],))
                    if counter == "dispositions":
                        db.execute("UPDATE observation SET expected_outputs=? WHERE id=?",
                                   (len(json.loads(row["output_row_ids"])), row["source_record_id"]))
            if db.execute("SELECT 1 FROM observation WHERE dispositions!=1 OR identities!=1 OR outputs!=expected_outputs LIMIT 1").fetchone():
                fail("missing or duplicate HSDB observation membership")
    return native, kinds, documents
