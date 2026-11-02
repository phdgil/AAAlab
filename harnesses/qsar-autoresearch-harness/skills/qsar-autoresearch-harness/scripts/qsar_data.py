"""Read-only native source inventory and contract-driven acute-toxicity curation.

These producers do not grant independent acceptance. Run through the stage runtime.
See references/source-adapters.md for the JSON contract and supported boundaries.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, namedtuple
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import itertools
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import zipfile

VERSION = "1.0"
_NATIVE = None
_ARCHIVES = None


def native_sources():
    global _NATIVE
    if _NATIVE is None:
        spec = importlib.util.spec_from_file_location("qsar_native_sources", Path(__file__).with_name("qsar_native_sources.py"))
        _NATIVE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_NATIVE)
    return _NATIVE


def archive_sources():
    global _ARCHIVES
    if _ARCHIVES is None:
        spec = importlib.util.spec_from_file_location("qsar_archive_sources", Path(__file__).with_name("qsar_archive_sources.py"))
        _ARCHIVES = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_ARCHIVES)
    return _ARCHIVES


def adjudication_module():
    spec = importlib.util.spec_from_file_location("qsar_adjudication", Path(__file__).with_name("qsar_adjudication.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def curation_evidence_module():
    spec = importlib.util.spec_from_file_location("qsar_curation_evidence", Path(__file__).with_name("qsar_curation_evidence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DataError(ValueError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def text(value):
    return "" if value is None else str(value).strip()


def output_dir(path, protected):
    result = Path(path).resolve()
    for source in protected:
        source = Path(source).resolve()
        if result == source or result.is_relative_to(source) or source.is_relative_to(result):
            raise DataError("Output overlaps an input: " + str(source))
    result.mkdir(parents=True, exist_ok=True)
    if any(result.iterdir()):
        raise DataError("Output directory must be empty (version outputs instead of overwriting)")
    return result


def format_of(path):
    name = str(path).lower()
    if name.endswith((".csv", ".csv.gz")):
        return "csv"
    if name.endswith((".tsv", ".txt")):
        return "tsv"
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".sql", ".sql.gz")):
        return "sql"
    return "unsupported"


def tables(path):
    if format_of(path) == "xlsx":
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            return book.sheetnames
        finally:
            book.close()
    return [""]


def rows(path, sheet="", header_row=1, schema=None, native_index=False):
    """Yield (physical row number, native mapping); no row cap or blank-row drop."""
    kind = format_of(path)
    if kind == "xlsx":
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            tab = book[sheet] if sheet else book[book.sheetnames[0]]
            iterator = tab.iter_rows(min_row=header_row, values_only=True)
            header = [text(x) for x in next(iterator, ())]
            if native_index and header and not header[0]:
                header[0] = "__native_index"
            validate_header(header)
            if schema is not None:
                schema.extend(header)
            for number, values in enumerate(iterator, header_row + 1):
                yield number, dict(zip(header, values))
        finally:
            book.close()
    elif kind in {"csv", "tsv"}:
        opener = gzip.open if str(path).lower().endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t" if kind == "tsv" else ",")
            header = None
            for values in reader:
                if reader.line_num < header_row:
                    continue
                if header is None:
                    if [text(x) for x in values] == ["# Formula", "RT", "Mass", "Cpd", "Comments"]:
                        header = ["Formula", "RT", "Mass", "Cpd", "Comments"]
                        if schema is not None:
                            schema.extend(header)
                        continue
                    # Native DSSTox MSReady distribution has comment preamble.
                    if values and values[0].startswith("#"):
                        continue
                    header = [text(x) for x in values]
                    validate_header(header)
                    if schema is not None:
                        schema.extend(header)
                    continue
                if len(values) > len(header):
                    raise DataError(f"Extra fields at {path}:{reader.line_num}")
                yield reader.line_num, dict(zip(header, values + [""] * (len(header) - len(values))))
            if header is None:
                raise DataError("Missing table header")
    else:
        raise DataError("Unsupported native table: " + str(path))


def validate_header(header):
    if not header or any(not h for h in header) or len(set(header)) != len(header):
        raise DataError("Blank or duplicate native column names require an explicit adapter")


def handoff(out, stage, summary, checks, limitations, coverage):
    outputs = sorted(str(p.resolve()) for p in out.iterdir() if p.is_file() and p.name != "manifest.json")
    data = {"schema_version": 1, "adapter_version": VERSION, "stage": stage,
            "outputs": outputs, "output_hashes": {p: digest(p) for p in outputs},
            "summary": summary, "checks": checks, "limitations": limitations, "coverage": coverage}
    dump(out / "manifest.json", data)
    return data


def collect(raw_root, destination):
    root = Path(raw_root).resolve(strict=True)
    out = output_dir(destination, [root])
    entries, gaps = [], []
    inventory_records = 0
    inventory = out / "source_records.jsonl"
    with inventory.open("w", encoding="utf-8") as stream:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise DataError("Source link escapes or obscures inventory: " + str(path))
            entry = {"source_id": path.relative_to(root).as_posix(), "path": str(path.resolve()),
                     "sha256": digest(path), "bytes": path.stat().st_size, "format": format_of(path),
                     "status": "inventoried", "row_count": None, "tables": [],
                     "provenance": {"method": "local_read_only", "locator": str(path),
                                    "release": "unknown", "acquisition_time": "unknown"}}
            emitted = 0
            try:
                if entry["format"] in {"csv", "tsv", "xlsx"}:
                    total = 0
                    sheet_names = tables(path)
                    oft = {"SUB", "REF_SUB", "END_STUDY_REC.HumanHealth"}.issubset(sheet_names)
                    if oft:
                        entry["header_policy"] = "OFT unnamed first column preserved as __native_index"
                    for sheet in sheet_names:
                        count, columns = 0, []
                        for number, row in rows(path, sheet, schema=columns, native_index=oft):
                            count += 1
                            emitted += 1
                            stream.write(json.dumps({"source_id": entry["source_id"], "sha256": entry["sha256"],
                                "sheet": sheet, "native_row": number, "record": row}, default=str, ensure_ascii=False) + "\n")
                        entry["tables"].append({"sheet": sheet, "columns": columns, "row_count": count})
                        total += count
                    entry["row_count"] = total
                elif entry["format"] == "sql":
                    sql_inventory = {}
                    for row, lineage in native_sources().iter_sql_table(path, "toxval", inventory=sql_inventory):
                        emitted += 1
                        stream.write(json.dumps({"source_id": entry["source_id"], "sha256": entry["sha256"],
                            "sheet": "toxval", "native_row": emitted, "native_lineage": lineage,
                            "record": row}, ensure_ascii=False) + "\n")
                    entry["tables"] = [dict(sheet=table, **metadata) for table, metadata in sql_inventory.items()]
                    unresolved = [table for table, metadata in sql_inventory.items() if metadata["row_count"] is None]
                    if unresolved:
                        entry["status"] = "partial_native_parser"
                        gaps.append({"source_id": entry["source_id"], "reason": "SQL toxval table inventoried; other native tables not parsed",
                                     "unparsed_tables": unresolved})
                    else:
                        entry["row_count"] = emitted
                elif entry["format"] == "json":
                    value = load(path)
                    entry["json_type"] = type(value).__name__
                    entry["keys"] = list(value) if isinstance(value, dict) else []
                else:
                    entry["status"] = "unsupported_native_parser"
                    if path.suffix.lower() == ".zip" or zipfile.is_zipfile(path):
                        with zipfile.ZipFile(path) as archive:
                            entry["archive_members"] = [{"name": x.filename, "bytes": x.file_size,
                                                          "crc32": x.CRC} for x in archive.infolist()]
                            names = [x.filename for x in archive.infolist() if not x.is_dir()]
                            hsdb = len(names) == 1 and names[0].lower().startswith("hsdb.xml")
                            chemid = (sum(n.lower().endswith("-smiles.txt") for n in names) == 1
                                      and sum(n.lower().endswith("-inchi.txt") for n in names) == 1
                                      and sum(n.lower().endswith(".xml") for n in names) == 1)
                            parsed_members = set()
                            if hsdb or chemid:
                                reader = archive_sources().iter_hsdb_documents if hsdb else archive_sources().iter_chemid_documents
                                member, count = names[0] if hsdb else next(n for n in names if n.lower().endswith(".xml")), 0
                                for record in reader(path):
                                    count += 1
                                    emitted += 1
                                    stream.write(json.dumps({"source_id": entry["source_id"], "sha256": entry["sha256"],
                                        "sheet": member, "native_row": count, "record": record["native_record"],
                                        "native_lineage": record["native_lineage"]}, ensure_ascii=False) + "\n")
                                parsed_members.add(member)
                                entry["tables"].append({"sheet": member, "record_kind": "DOC" if hsdb else "Chemical", "row_count": count})
                            if chemid:
                                for member in names:
                                    if not member.lower().endswith(("-smiles.txt", "-inchi.txt")):
                                        continue
                                    count = 0
                                    with archive.open(member) as binary, io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as decoded:
                                        reader = csv.DictReader(decoded, delimiter="\t")
                                        validate_header(reader.fieldnames or [])
                                        for record in reader:
                                            if None in record or any(v is None for v in record.values()):
                                                raise DataError("Native archive structure field-count mismatch")
                                            count += 1
                                            emitted += 1
                                            stream.write(json.dumps({"source_id": entry["source_id"], "sha256": entry["sha256"],
                                                "sheet": member, "native_row": reader.line_num, "record": record}, ensure_ascii=False) + "\n")
                                        entry["tables"].append({"sheet": member, "columns": reader.fieldnames, "row_count": count})
                                    parsed_members.add(member)
                            if parsed_members:
                                remaining = sorted(set(names) - parsed_members)
                                entry["status"] = "partial_native_parser" if remaining else "inventoried"
                                entry["row_count"] = None if remaining else emitted
                                entry["unparsed_members"] = remaining
                                entry["record_scope"] = "Native document/identity inventory; not inferred toxicity observations"
                    if entry["status"] != "inventoried":
                        gaps.append({"source_id": entry["source_id"], "reason": "Native archive parser unavailable for some members; record coverage unresolved"})
            except (ValueError, OSError, ImportError, csv.Error, zipfile.BadZipFile) as exc:
                entry["status"] = "parse_failed"
                entry["error"] = str(exc)
                gaps.append({"source_id": entry["source_id"], "reason": str(exc)})
            entry["inventoried_record_count"] = emitted
            inventory_records += emitted
            if digest(path) != entry["sha256"]:
                raise DataError("Source changed while collecting: " + str(path))
            entries.append(entry)
    registry = {"schema_version": 1, "raw_root": str(root), "files": entries}
    dump(out / "source_registry.json", registry)
    dump(out / "collection_gaps.json", gaps)
    summary = {"schema_version": 1, "adapter_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
               "status": "limited" if gaps else "produced", "file_count": len(entries),
               "inventory_scope": "All files below supplied local root; no upstream completeness claim",
               "record_count": inventory_records, "gaps": gaps,
               "native_storage": "Original bytes referenced read-only and hash-verified, not copied"}
    dump(out / "collection_manifest.json", summary)
    return handoff(out, "collection", "Native inventory produced; independent acceptance required",
                   ["Every local file hashed before and after inventory"], gaps, summary)


ADAPTERS = {
    "ice_oral": {"record_id": "Record ID", "cas": "CASRN", "dtxsid": "DTXSID", "species": "Species",
                 "endpoint": "Endpoint", "value": "Response", "unit": "Response Unit", "relation": "Response Modifier",
                 "test_material": "Mixture", "reference": "Reference", "source_url": "URL", "sex": "Sex"},
    "ice_inhalation": {"record_id": "Record ID", "cas": "CASRN", "dtxsid": "DTXSID", "species": "Species",
                       "endpoint": "Endpoint", "value": "Response", "unit": "Response Unit", "relation": "Response Modifier",
                       "test_material": "Mixture", "reference": "Reference", "source_url": "URL", "sex": "Sex"},
    "oecd": {"record_id": "source_record_id", "cas": "casrn", "species": "species", "route": "route",
             "endpoint": "endpoint_type", "value": "numeric_value", "unit": "numeric_unit", "smiles": "smiles",
             "reference": "reference", "source_url": "source_url", "sex": "sex"},
    "toxric": {"record_id": "TAID", "smiles": "Canonical SMILES", "value": "Toxicity Value"},
    "npass": {"record_id": "np_id", "npid": "np_id", "species": "assay_organism", "endpoint": "activity_type",
              "value": "activity_value", "unit": "activity_units", "relation": "activity_relation", "reference": "ref_id"},
}
FIELDS = ("record_id", "cas", "dtxsid", "npid", "smiles", "inchi", "species", "route", "endpoint", "value", "unit",
          "relation", "test_material", "reference", "source_url", "sex")
NATIVE_ADAPTERS = {"toxval", "openfoodtox", "hsdb"}
for _adapter in NATIVE_ADAPTERS:
    ADAPTERS[_adapter] = {field: field for field in FIELDS}
UNITS = {"mg/kg": ("mass", 1.0), "ug/kg": ("mass", 0.001), "µg/kg": ("mass", 0.001),
         "μg/kg": ("mass", 0.001), "ng/kg": ("mass", 0.000001), "g/kg": ("mass", 1000.0),
         "mol/kg": ("molar", 1.0), "mmol/kg": ("molar", 0.001), "umol/kg": ("molar", 0.000001),
         "µmol/kg": ("molar", 0.000001), "μmol/kg": ("molar", 0.000001), "nmol/kg": ("molar", 1e-9)}


def valid_cas(value):
    if not re.fullmatch(r"\d{2,7}-\d{2}-\d", value):
        return False
    digits = value.replace("-", "")
    return sum(int(d) * i for i, d in enumerate(reversed(digits[:-1]), 1)) % 10 == int(digits[-1])


def chemistry(smiles, inchi, policy):
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    representations = [m for m in (Chem.MolFromSmiles(smiles) if smiles else None,
                                   Chem.MolFromInchi(inchi) if inchi else None)]
    if (smiles and representations[0] is None) or (inchi and representations[1] is None):
        raise DataError("missing_or_invalid_structure")
    if smiles and inchi and Chem.MolToSmiles(representations[0], isomericSmiles=True) != Chem.MolToSmiles(representations[1], isomericSmiles=True):
        raise DataError("conflicting_structure_representations")
    mol = next((m for m in representations if m is not None), None)
    if mol is None:
        raise DataError("missing_or_invalid_structure")
    if not mol.GetNumAtoms() or any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()) or not 0 < Descriptors.MolWt(mol) < float("inf"):
        raise DataError("unsupported_dummy_or_zero_mass_structure")
    original = Chem.MolToSmiles(mol, isomericSmiles=True)
    fragments = Chem.GetMolFrags(mol, asMols=True)
    parent = mol
    if len(fragments) > 1:
        if policy == "single_component":
            raise DataError("multiple_components")
        organic = [f for f in fragments if any(a.GetAtomicNum() == 6 for a in f.GetAtoms())]
        if len(organic) != 1:
            raise DataError("ambiguous_multiorganic_parent")
        parent = organic[0]
    canonical = Chem.MolToSmiles(parent, isomericSmiles=True)
    connectivity = Chem.MolToSmiles(parent, isomericSmiles=False)
    return {"original_structure": original, "original_mw": Descriptors.MolWt(mol),
            "parent_smiles": canonical, "parent_mw": Descriptors.MolWt(parent),
            "connectivity_id": hashlib.sha256(connectivity.encode()).hexdigest(),
            "normalization": "fragment_parent" if len(fragments) > 1 else "none"}


def bound_file(registry, source_id):
    matches = [x for x in registry["files"] if x["source_id"] == source_id]
    if len(matches) != 1:
        raise DataError("Missing or ambiguous source registry entry: " + source_id)
    entry = matches[0]
    if digest(entry["path"]) != entry["sha256"]:
        raise DataError("Source digest mismatch: " + source_id)
    return entry


def identity_index(policy, registry, support_ledger=None, blocked_keys=None, *, raw_support_observer=None):
    from rdkit import Chem
    index = defaultdict(dict)
    support_ledger = [] if support_ledger is None else support_ledger
    blocked_keys = {} if blocked_keys is None else blocked_keys
    for config in policy.get("identity_sources", []):
        entry = bound_file(registry, config["source_id"])
        if config.get("adapter") == "npass_structure_v1":
            if set(config) - {"source_id", "adapter"}:
                raise DataError("NPASS structure adapter does not accept column, sheet or row overrides")
            signatures = {}
            # Replay the small row ledger after all duplicates have been checked.
            # This keeps earlier clean rows quarantined when a later row poisons an NPID.
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as pending:
                with Path(entry["path"]).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream, delimiter="\t")
                    header = reader.fieldnames or []
                    validate_header(header)
                    if not {"np_id", "SMILES", "InChI", "InChIKey"}.issubset(header):
                        raise DataError("NPASS structure required header missing")
                    for row in reader:
                        if raw_support_observer is not None:
                            from qsar_material_curation import raw_table_support
                            raw_support_observer(raw_table_support(entry, config, reader.line_num, row))
                        if None in row or any(value is None for value in row.values()):
                            raise DataError("NPASS structure field-count mismatch")
                        number, npid = reader.line_num, text(row["np_id"])
                        evidence = {"source_id": config["source_id"], "sha256": entry["sha256"],
                                    "adapter": "npass_structure_v1", "record_id": npid,
                                    "native_row": number, "native_lineage": {"native_row": number},
                                    "native_record": row}
                        reasons, canonical, generated_key = [], "", ""
                        if not npid:
                            reasons.append("missing_npid")
                        smi, inchi, native_key = (text(row[k]) for k in ("SMILES", "InChI", "InChIKey"))
                        if not smi or not inchi:
                            reasons.append("missing_structure_representation")
                        else:
                            try:
                                result = chemistry(smi, inchi, policy["chemistry_policy"])
                                canonical = result["original_structure"]
                                generated_key = Chem.MolToInchiKey(Chem.MolFromSmiles(canonical))
                            except DataError as exc:
                                reasons.append(str(exc))
                        if not re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", native_key):
                            reasons.append("missing_or_invalid_native_inchikey")
                        elif generated_key and generated_key != native_key:
                            reasons.append("native_inchikey_disagreement")
                        if npid:
                            key = ("npid", npid)
                            signature = (canonical, native_key)
                            if canonical and npid in signatures and signatures[npid] != signature:
                                reasons.append("conflicting_structure_candidates")
                            if canonical:
                                signatures.setdefault(npid, signature)
                            if reasons:
                                prior = blocked_keys.get(key, {})
                                blocked_keys[key] = dict(evidence, reasons=sorted(set(reasons + prior.get("reasons", []))))
                            else:
                                index[key].setdefault(canonical, []).append(evidence)
                        pending.write(json.dumps(dict(evidence, npid=npid, cas="", reasons=reasons,
                                                      canonical_structure=canonical,
                                                      generated_inchikey=generated_key)) + "\n")
                if digest(entry["path"]) != entry["sha256"]:
                    raise DataError("NPASS identity support changed during parsing")
                pending.seek(0)
                for line in pending:
                    record = json.loads(line)
                    blocked = blocked_keys.get(("npid", record["npid"]))
                    if blocked:
                        record["reasons"] = sorted(set(record["reasons"] + blocked["reasons"]))
                    record["blocked_key"] = bool(blocked)
                    record["status"] = "quarantined" if record["reasons"] else "candidate"
                    support_ledger.append(record)
            continue
        if config.get("adapter") == "chemid_identity":
            for candidate in archive_sources().iter_chemid_identity(entry["path"]):
                if raw_support_observer is not None:
                    from qsar_material_curation import raw_chemid_support
                    raw_support_observer(raw_chemid_support(entry, config, candidate))
                cas = candidate.get("cas", "")
                evidence = {"source_id": config["source_id"], "sha256": entry["sha256"],
                            "record_id": candidate["record_id"], "native_lineage": candidate["native_lineage"]}
                cas_candidates = candidate.get("cas_candidates", [])
                if "ambiguous_cas_candidates" in candidate["why"]:
                    for ambiguous_cas in cas_candidates:
                        if valid_cas(ambiguous_cas):
                            blocked_keys[("cas", ambiguous_cas)] = dict(evidence,
                                reason="ambiguous_cas_candidates", cas_candidates=cas_candidates,
                                candidate_structures=candidate.get("candidate_structures", []))
                structures, invalid = set(), False
                for item in candidate.get("candidate_structures", []):
                    source = item["record"]
                    value = source.get("SMILES") or source.get("InChI")
                    if not value:
                        continue
                    mol = Chem.MolFromSmiles(value) if source.get("SMILES") else Chem.MolFromInchi(value)
                    if mol is None:
                        invalid = True
                    else:
                        structures.add(Chem.MolToSmiles(mol, isomericSmiles=True))
                reasons = list(candidate["why"])
                if len(structures) > 1:
                    reasons.append("conflicting_structure_candidates")
                if invalid:
                    reasons.append("unparseable_structure_candidate")
                support_ledger.append(dict(evidence, cas=cas, cas_candidates=cas_candidates, status="quarantined" if reasons else "candidate",
                                           reasons=reasons, candidate_count=len(candidate.get("candidate_structures", []))))
                if cas and valid_cas(cas):
                    if invalid:
                        blocked_keys[("cas", cas)] = evidence
                    for canonical in structures:
                        index[("cas", cas)].setdefault(canonical, []).append(evidence)
            if digest(entry["path"]) != entry["sha256"]:
                raise DataError("ChemID identity archive changed during parsing")
            continue
        columns = config["columns"]
        for number, row in rows(entry["path"], config.get("sheet", ""), config.get("header_row", 1)):
            if raw_support_observer is not None:
                from qsar_material_curation import raw_table_support
                raw_support_observer(raw_table_support(entry, config, number, row))
            if any(column not in row for column in columns.values()):
                raise DataError("Identity support column missing: " + config["source_id"])
            mapped = {key: text(row.get(column)) for key, column in columns.items()}
            smi, inchi = mapped.get("smiles", ""), mapped.get("inchi", "")
            if smi and inchi:
                left, right = Chem.MolFromSmiles(smi), Chem.MolFromInchi(inchi)
                if left is None or right is None or Chem.MolToSmiles(left, isomericSmiles=True) != Chem.MolToSmiles(right, isomericSmiles=True):
                    raise DataError("Conflicting identity support representations: " + config["source_id"] + ":" + str(number))
            mol = Chem.MolFromSmiles(smi) if smi else Chem.MolFromInchi(inchi) if inchi else None
            if mol is None:
                continue
            canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
            evidence = {"source_id": config["source_id"], "sha256": entry["sha256"], "native_row": number}
            for key in ("dtxsid", "cas", "npid"):
                value = mapped.get(key, "")
                if value and (key != "cas" or valid_cas(value)):
                    index[(key, value)].setdefault(canonical, []).append(evidence)
        if digest(entry["path"]) != entry["sha256"]:
            raise DataError("Identity support changed during parsing")
    return index


def resolve_identity(mapped, index, policy, blocked_keys=None):
    candidates, evidence = set(), []
    for key in ("dtxsid", "cas", "npid"):
        value = mapped[key]
        if not value or (key == "cas" and not valid_cas(value)):
            continue
        if blocked_keys and (key, value) in blocked_keys:
            return None, "unresolved_support_identity", [blocked_keys[(key, value)]]
        found = index.get((key, value), {})
        candidates.update(found)
        for canonical, records in found.items():
            evidence.append({"key": key, "value": value, "structure": canonical, "records": records})
    direct = mapped["smiles"] or mapped["inchi"]
    if direct:
        result = chemistry(mapped["smiles"], mapped["inchi"], policy)
        canonical = result["original_structure"]
        if candidates and candidates != {canonical}:
            return None, "identity_conflict", evidence
        return result, "direct_source", evidence
    if len(candidates) != 1:
        return None, "ambiguous_identity" if candidates else "unresolved_identity", evidence
    result = chemistry(next(iter(candidates)), "", policy)
    return result, "exact_identifier_mapping", evidence


def toxric_unit(config, registry, entry):
    evidence = config.get("unit_evidence", {})
    if evidence.get("unit") != "mg/kg" or not evidence.get("citation"):
        raise DataError("TOXRIC requires cited original-value mg/kg unit evidence")
    receipt_entry = bound_file(registry, evidence.get("receipt_source_id", ""))
    receipt = load(receipt_entry["path"])
    if "original-value" not in receipt.get("scope", "").lower():
        raise DataError("TOXRIC receipt does not establish original-value variant")
    matches = [f for f in receipt.get("files", []) if f.get("filename") == Path(entry["path"]).name
               and f.get("sha256") == entry["sha256"]]
    if len(matches) != 1 or not matches[0].get("url"):
        raise DataError("TOXRIC receipt does not bind exact native bytes and URL")
    return "mg/kg"


class DiskLedger:
    """Append-only stage-owned JSONL; replay one row at a time for CSV export.

    Only schema/counts and flags for retained rows stay resident. Final retained
    flags preserve the former shared-list updates from ToxVal metadata joins.
    """
    def __init__(self, path, stack):
        self.path = Path(path)
        self.stream = stack.enter_context(self.path.open("x", encoding="utf-8", newline="\n"))
        self.fields = set()
        self.count = 0
        self.final_flags = {}

    def append(self, row):
        self.stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.fields.update(row)
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        self.stream.flush()
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("row_id") in self.final_flags:
                    row["flags"] = self.final_flags[row["row_id"]]
                yield row


def write_csv(path, records, minimum, *, canonical_values=False):
    fields = list(minimum)
    keys = records.fields if isinstance(records, DiskLedger) else {k for r in records for k in r}
    fields += sorted(keys - set(fields))
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, default=str,
                **({'sort_keys': True, 'separators': (',', ':'), 'allow_nan': False} if canonical_values else {}))
                if isinstance(v, (dict, list)) else v for k, v in row.items()})


def narrative_binding(binding, contract_path):
    """Validate the discriminated integration envelope; v1 stays unchanged."""
    module = adjudication_module()
    names = ("inventory", "decisions", "review_receipt")
    if binding.get("schema_version") == 2:
        module.keys(binding, ("schema_version", *names, "full_contract", "accepted_contract_review"))
        if binding["full_contract"] != curation_evidence_module().binding(contract_path):
            raise DataError("V2 full contract must be the actual curation contract")
        if not curation_evidence_module().strict(load(contract_path)):
            raise DataError("V2 requires strict source coverage evidence checks")
        return {key: binding[key] for key in ("schema_version", *names)}
    module.keys(binding, names)
    return binding


def apply_adjudication_bindings(sources, mapping, contract_path):
    """One external envelope, finite ordinary discriminator, unchanged HSDB shapes."""
    declared = {s['source_id']: s for s in sources}
    if len(declared) != len(sources):
        raise DataError('Source IDs must be unique')
    if type(mapping) is not dict or not mapping or set(mapping) - declared.keys():
        raise DataError('External adjudication requires declared source IDs')
    module = adjudication_module()
    for source, binding in mapping.items():
        config = declared[source]
        if any(k in config for k in ('ordinary_adjudication', 'narrative_adjudication',
                                    'narrative_observation_metadata', 'sql_curation')):
            raise DataError('Duplicate binding: external adjudication requires no inline adjudication binding')
        if type(binding) is dict and binding.get('kind') == 'qsar_sql_curation_inputs_v2':
            if config['adapter'] != 'toxval':
                raise DataError('SQL curation requires toxval adapter')
            config['sql_curation'] = binding
        elif type(binding) is dict and binding.get('kind') == module.ORDINARY_KIND:
            if config['adapter'] not in module.ORDINARY_ADAPTERS:
                raise DataError('Unsupported ordinary adjudication adapter')
            if binding.get('full_contract') != dict(path=str(Path(contract_path).resolve()), sha256=digest(contract_path)):
                raise DataError('Ordinary adjudication full contract mismatch')
            config['ordinary_adjudication'] = binding
        else:
            if config['adapter'] != 'hsdb':
                raise DataError('Narrative adjudication requires HSDB')
            narrative_binding(binding, contract_path)
            config['narrative_adjudication'] = binding


def build_curation(contract_path, registry_path, destination, *, adjudication_config_path=None, material_config_path=None):
    evidence_module = curation_evidence_module()
    evidence = evidence_module.capture(contract_path, registry_path)
    contract_hash, registry_hash = digest(contract_path), digest(registry_path)
    contract, registry = load(contract_path), load(registry_path)
    material_enabled = 'verified_tested_material_v4' in contract.get('scientific_policy', {})
    if material_enabled and material_config_path is None:
        raise DataError("Material-enabled curation requires an explicit material packet")
    if material_config_path is not None and not material_enabled:
        raise DataError("Material packet requires an explicit material contract")
    policy = contract.get("data_curation")
    if not isinstance(policy, dict):
        raise DataError("Contract must declare data_curation")
    for key, allowed in (("chemistry_policy", {"single_component", "largest_organic"}),
                         ("relation_policy", {"exact_only", "reported_numeric"}),
                         ("replicate_policy", {"preserve_flag"})):
        if policy.get(key) not in allowed:
            raise DataError("Explicit supported policy required: " + key)
    tasks = policy.get("tasks", [])
    if not tasks or not policy.get("sources"):
        raise DataError("Explicit tasks and source scope required")
    if len({t["id"] for t in tasks}) != len(tasks):
        raise DataError("Task IDs must be unique")
    for task in tasks:
        if not all(text(task.get(k)) for k in ("id", "species", "route", "endpoint")):
            raise DataError("Task requires id/species/route/endpoint")
        if task["endpoint"].casefold() != "ld50":
            raise DataError("Only LD50 target conversion is implemented; other endpoints need a reviewed adapter")
    sources = policy["sources"]
    if len({s["source_id"] for s in sources}) != len(sources):
        raise DataError("Source IDs must be unique")
    external_adjudication = None
    if adjudication_config_path is not None:
        external_path = Path(adjudication_config_path).resolve()
        external_adjudication = {"path": str(external_path), "sha256": digest(external_path)}
        from qsar_material_curation import decode
        mapping = decode(external_path.read_text(encoding='utf-8'))
        apply_adjudication_bindings(sources, mapping, contract_path)
    protected = [contract_path, registry_path] + [e["path"] for e in registry["files"]]
    if external_adjudication:
        protected.append(external_adjudication["path"])
    if material_config_path is not None:
        protected.append(material_config_path)
    for source in sources:
        if 'narrative_observation_metadata' in source:
            if source.get('adapter') != 'hsdb' or not source.get('narrative_adjudication'):
                raise DataError('Observation metadata requires inline HSDB narrative adjudication')
            if not evidence_module.strict(contract):
                raise DataError('Observation metadata requires strict curation evidence checks')
            adjudication_module().keys(source['narrative_observation_metadata'], ('path', 'sha256'))
            protected.append(source['narrative_observation_metadata']['path'])
        if source.get('sql_curation'):
            if not evidence_module.strict(contract):
                raise DataError('SQL curation requires strict evidence checks')
            from qsar_material_curation import _files
            protected.extend(f['path'] for f in _files(source['sql_curation']))
        if source.get('ordinary_adjudication'):
            if not evidence_module.strict(contract):
                raise DataError('Ordinary adjudication requires strict source coverage evidence checks')
            from qsar_material_curation import _files
            protected.extend(f['path'] for f in _files(source['ordinary_adjudication']))
        if source.get("narrative_adjudication"):
            narrative_binding(source["narrative_adjudication"], contract_path)
            protected.extend(value["path"] for key, value in source["narrative_adjudication"].items()
                             if key != "schema_version")
    out = output_dir(destination, protected)
    # The unique spool directory is owned by this invocation; ExitStack closes
    # every file before TemporaryDirectory removes only that directory.
    with tempfile.TemporaryDirectory(prefix=".ledger-", dir=out) as spool_dir, ExitStack() as stack:
        material_batch = None
        if material_config_path is not None:
            from qsar_material_curation import open_material_curation
            material_batch = stack.enter_context(open_material_curation(material_config_path,
                contract_path=contract_path, registry_path=registry_path,
                adjudication_config_path=adjudication_config_path, work_dir=out))
        return _curate_into(policy, registry, out, contract_path, registry_path,
                            contract_hash, registry_hash, Path(spool_dir), stack, external_adjudication, evidence, material_batch)



def curate(contract_path, registry_path, destination, adjudication_config_path=None, material_config_path=None):
    built = build_curation(contract_path, registry_path, destination,
        adjudication_config_path=adjudication_config_path, material_config_path=material_config_path)
    summary = built['summary']
    out = Path(destination).resolve()
    if summary.get('sql_integration'):
        dump(out / 'curation_diagnostic.json', summary)
        return dict(output_dir=str(out), diagnostic=str(out / 'curation_diagnostic.json'), summary=summary)
    status_message = {'blocked': 'Curation produced; scope blocked',
        'limited': 'Curation produced with disclosed gaps; independent review required',
        'produced': 'Curation produced; independent review required'}[summary['status']]
    try:
        dump(out / 'curation_manifest.json', summary)
        return handoff(out, 'curation', status_message,
            ['Source hashes verified', 'Every parsed row has one terminal disposition', 'All eligible rows exported without a cap'],
            summary['limitations'], summary)
    except BaseException:
        for name in ('curation_manifest.json', 'manifest.json'):
            (out / name).unlink(missing_ok=True)
        raise


def _pre_identity_parts(raw, config, policy, entry, number, sheet,
                        inferred_unit=None, adjudication=False, scientific_result=None, sql_result=None):
    """Shared legacy mapping/checks; deliberately performs no chemistry."""
    adapter, tasks = config["adapter"], policy["tasks"]
    columns = dict(ADAPTERS[adapter], **config.get("columns", {}))
    constants = config.get("constants", {})
    if any(k in constants for k in ("value", "relation", "smiles", "inchi", "cas", "dtxsid", "npid")):
        raise DataError("Constants cannot invent measurements, qualifiers or identities")
    if adapter != "toxric" and "unit" in constants:
        raise DataError("Native unit fields must not be overridden")
    if adapter in NATIVE_ADAPTERS and (config.get("columns") or config.get("sheet") or config.get("header_row")):
        raise DataError("Native relational adapters use fixed reviewed schemas")
    retained = []
    if columns.get("value") not in raw:
        raise DataError("Required native value column missing: " + str(columns.get("value")))
    if adapter != "toxric" and columns.get("unit") not in raw:
        raise DataError("Required native unit column missing")
    if any(column not in raw for column in config.get("columns", {}).values()):
        raise DataError("Explicit column mapping missing from native source")
    mapped = {k: text(raw.get(columns.get(k, ""))) for k in FIELDS}
    for key, value in constants.items():
        if key not in FIELDS:
            raise DataError("Unknown constant field: " + key)
        if mapped[key] and mapped[key].casefold() != text(value).casefold():
            raise DataError("Constant conflicts with native field: " + key)
        mapped[key] = text(value)
    if inferred_unit:
        mapped["unit"] = inferred_unit
    adapter_reasons = list(raw.get("why", [])) if adapter in NATIVE_ADAPTERS else []
    if adapter in NATIVE_ADAPTERS and raw.get("status") == "quarantined" and not adapter_reasons:
        adapter_reasons.append("native_adapter_quarantine")
    original_species, original_route, original_endpoint = mapped["species"], mapped["route"], mapped["endpoint"]
    scientific = None
    if config.get('ordinary_adjudication') is not None and scientific_result is None:
        raise DataError('Configured ordinary row requires validated scientific result')
    if scientific_result is not None:
        from qsar_material_curation import scientific_value
        scientific = scientific_value(scientific_result, raw, config, entry, number, sheet)
        if adjudication:
            raise DataError('Ordinary scientific review cannot change narrative identity')
        if scientific['route_override'] is not None:
            mapped['route'] = scientific['route_override']
    sql = None
    if sql_result is not None:
        from qsar_sql_curation import SQLCandidateResult
        if not isinstance(sql_result, SQLCandidateResult) or scientific_result is not None or adjudication:
            raise DataError('Exclusive validated SQL carrier required')
        sql = sql_result.value(raw)
        projected = sql_result.projection(raw)
        mapped.update({k: text(projected.get(columns.get(k, '')))
                       for k in ('endpoint', 'species', 'route', 'relation', 'value', 'unit', 'dtxsid', 'cas')})
        adapter_reasons = list(projected['why'])
    if adjudication:
        mapped.update(raw.get("adjudicated_fields", {}))
    for field in ("species", "route"):
        aliases = config.get("exact_" + field + "_aliases", {})
        for alias, rule in aliases.items():
            if not isinstance(rule, dict) or not text(rule.get(field)) or not text(rule.get("evidence")):
                raise DataError("Each exact " + field + " alias requires target and evidence")
        if mapped[field] in aliases:
            mapped[field] = aliases[mapped[field]][field]
    # Exact source-specific aliases require contract evidence, never substrings.
    conversion_unit = mapped["unit"]
    if adjudication and raw.get("adjudicated_unit"):
        conversion_unit = raw["adjudicated_unit"]
    aliases = config.get("exact_unit_aliases", {})
    for alias, rule in aliases.items():
        if not isinstance(rule, dict) or rule.get("unit") not in UNITS or not text(rule.get("evidence")):
            raise DataError("Each exact unit alias requires a supported unit and evidence")
        if alias in UNITS and alias != rule["unit"]:
            raise DataError("Cannot redefine an already supported native unit")
    if conversion_unit in aliases:
        conversion_unit = aliases[conversion_unit]["unit"]
    route_mappings = config.get("administrative_endpoint_routes", {})
    for endpoint, rule in route_mappings.items():
        if not isinstance(rule, dict) or not text(rule.get("route")) or not text(rule.get("evidence")):
            raise DataError("Each administrative endpoint route mapping requires route and evidence")
    administrative_endpoint = raw.get("administrative_endpoint", "") if adapter == "openfoodtox" else ""
    if administrative_endpoint in route_mappings:
        route = route_mappings[administrative_endpoint]["route"]
        if mapped["route"] and mapped["route"].casefold() != route.casefold():
            adapter_reasons.append("administrative_route_conflict")
        else:
            mapped["route"] = route
    uid = f'{config["source_id"]}:{sheet}:{raw["record_id"] if adjudication else number}'
    if sql is not None:
        uid = config['source_id'] + ':toxval:candidate:' + sql['candidate_id']
    base = {"row_id": uid, "source_id": config["source_id"], "source_sha256": entry["sha256"],
            "native_path": entry["path"], "native_sheet": sheet, "native_row": number,
            "source_record_id": mapped["record_id"],
            "native_record": raw["native_record"] if adapter in NATIVE_ADAPTERS else raw,
            "raw_species": original_species, "raw_route": original_route, "raw_endpoint": original_endpoint}
    if adapter in NATIVE_ADAPTERS:
        base.update(native_lineage=raw["native_lineage"], effect_blocks=raw.get("effect_blocks", []),
                    relation_origin=raw.get("relation_origin", "native_effect_block"),
                    original_relation=raw.get("original_relation"), native_adapter_status=raw["status"])
        for field in ("native_unit_parent", "native_unit_other", "document_context", "cas_candidates", "source_record_kind", "sex_note", "metadata_warnings", "qc_status"):
            if field in raw:
                base[field] = raw[field]
        for field in ("sex", "strain", "study_id", "dose_basis", "adjudication", "field_id", "native_value",
                      "candidate_id", "measurement_id", "population_id", "target_origin", "observation_metadata"):
            if field in raw:
                base[field] = raw[field]
    if sql is not None:
        base.update(sql_result=sql, **{k: sql[k] for k in ('candidate_id', 'measurement_id', 'population_id')})
    disposition = dict(base, disposition="excluded", reasons=adapter_reasons.copy(), output_row_ids=[])
    if sql is not None and sql['disposition'] != 'preserve':
        disposition['reasons'].append(sql['contract_clause'])
    for field, rule in config.get("native_field_filters", {}).items():
        if not isinstance(rule, dict) or not isinstance(rule.get("allowed_values"), list) or not text(rule.get("evidence")):
            raise DataError("Native field filter requires allowed_values and evidence")
        if field not in base["native_record"]:
            raise DataError("Filtered native field missing: " + field)
        if text(base["native_record"][field]) not in rule["allowed_values"]:
            disposition["reasons"].append("native_field_not_allowed:" + field)
    applicable = [t for t in tasks if all(mapped[k].casefold() == text(t[k]).casefold() for k in ("species", "route", "endpoint"))]
    if not applicable:
        disposition["reasons"].append("outside_declared_task_or_missing_stratum")
    if sql is not None:
        clause = sql['contract_clause']
        if clause == 'v4_incompatible_task' and applicable:
            raise DataError('SQL incompatible-task decision contradicts selected native task')
        if clause == 'v4_explicit_inequality' and not any(
                text(raw['native_record'].get(f)) in {'<', '>', '<=', '>=', '≤', '≥'}
                for f in ('toxval_numeric_qualifier', 'toxval_numeric_qualifier_original')):
            raise DataError('SQL inequality decision lacks native inequality')
    if scientific is not None:
        fields = dict(target_origin=scientific['target_origin'], scientific_dispositions=scientific['scientific_dispositions'],
                      scientific_decision_id=scientific['decision_id'], scientific_remaining_issues=scientific['scientific_remaining_issues'])
        base.update(fields)
        disposition.update(fields)
    relation = {"=": "=", "==": "=", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "≤": "<=", "≥": ">="}.get(mapped["relation"], "unknown")
    unverified_original = (adapter == "toxval" and relation == "="
                           and text(raw.get("original_relation")) in {"", "-"})
    effective_modifier = mapped["relation"]
    if unverified_original:
        relation = "unknown"
        effective_modifier = ""
        base["qualifier_limitation"] = "Source-normalized equality lacks an original qualifier; not proven exact"
        disposition["qualifier_limitation"] = base["qualifier_limitation"]
    # Only absent qualifiers are eligible as reported numeric estimates;
    # nonempty unrecognized/censored qualifiers are never silently accepted.
    if relation != "=" and not (policy["relation_policy"] == "reported_numeric" and not effective_modifier):
        disposition["reasons"].append("nonexact_or_unknown_relation")
    try:
        value = float(mapped["value"])
        if not math.isfinite(value) or value <= 0:
            raise ValueError()
    except ValueError:
        value = None
        disposition["reasons"].append("invalid_positive_numeric_value")
    if conversion_unit not in UNITS:
        disposition["reasons"].append("unsupported_or_unknown_unit")
    material_policy = config.get("allowed_test_materials")
    if material_policy is not None and mapped["test_material"] not in material_policy:
        disposition["reasons"].append("test_material_not_allowed")
    elif (adapter.startswith("ice_") or adapter == "openfoodtox") and material_policy is None:
        raise DataError("ICE/OFT requires explicit allowed_test_materials (including blank only if justified)")
    flags = ["invalid_cas"] if mapped["cas"] and not valid_cas(mapped["cas"]) else []
    if adapter == "toxval" and text(raw.get("qc_status")) != "pass":
        flags.append("source_qc_not_pass_or_unspecified")
    return base, mapped, applicable, conversion_unit, relation, value, flags, adapter_reasons, disposition


PreIdentity = namedtuple("PreIdentity", "base mapped applicable_task_ids conversion_unit "
    "normalized_relation numeric_value flags native_reasons nonmaterial_reasons "
    "identity_reasons extraction_blockers requires_classification")

_NONMATERIAL_NATIVE = frozenset({
    "current_original_qualifier_conflict", "administrative_route_conflict",
    "missing_effect_block", "multiple_effect_blocks", "interval_not_point",
    "upper_bound_not_point", "missing_effect_value", "upper_qualifier_without_upper_value",
    "missing_native_unit_other_detail", "no_ld50_mention", "no_toxicity_candidate_fields"})
_IDENTITY_NATIVE = frozenset({
    "ambiguous_source_chemical_join", "source_chemical_dtxsid_conflict", "missing_sub_join",
    "ambiguous_sub_join", "missing_reference_join", "ambiguous_reference_join",
    "missing_native_cas", "ambiguous_native_cas", "ambiguous_or_missing_native_cas"})
_EXTRACTION_NATIVE = frozenset({
    "multiple_or_ambiguous_ld50_observations", "narrative_not_verified_single_rat_oral_scalar",
    "unverified_parenthetical_context", "ambiguous_bracketed_dose_context"})


def pre_identity_observation(raw, config, policy, entry, number, sheet,
                             inferred_unit=None, adjudication=False, scientific_result=None, sql_result=None):
    """Closed pre_identity_v1 membership; identity failures never exempt a row."""
    if any((config["adapter"], field) != ("toxval", "study_type")
           for field in config.get("native_field_filters", {})):
        raise DataError("Unsupported material integration native filter")
    base, mapped, applicable, unit, relation, value, flags, native, disposition = _pre_identity_parts(
        raw, config, policy, entry, number, sheet, inferred_unit, adjudication, scientific_result, sql_result)
    unknown = set(native) - _NONMATERIAL_NATIVE - _IDENTITY_NATIVE - _EXTRACTION_NATIVE
    if unknown:
        raise DataError("Unexplained material integration native reason: " + ",".join(sorted(unknown)))
    nonmaterial = [r for r in disposition["reasons"]
                   if r not in _IDENTITY_NATIVE and r not in _EXTRACTION_NATIVE]
    if config["adapter"] == "openfoodtox" and set(native) & {"missing_sub_join", "ambiguous_sub_join"}:
        nonmaterial = [r for r in nonmaterial if r != "test_material_not_allowed"]
    blockers = tuple(r for r in native if r in _EXTRACTION_NATIVE)
    return PreIdentity(base, mapped, tuple(t["id"] for t in applicable), unit, relation, value,
        tuple(flags), tuple(native), tuple(nonmaterial), tuple(r for r in native if r in _IDENTITY_NATIVE),
        blockers, not nonmaterial and not blockers)


def curate_observation(raw, config, policy, entry, number, sheet, index,
                       blocked_identity_keys, inferred_unit=None, adjudication=False, *, material_route=None, scientific_result=None, sql_result=None):
    """Deterministic legacy per-observation result, independent of output ledgers."""
    base, mapped, applicable, conversion_unit, relation, value, flags, adapter_reasons, disposition = _pre_identity_parts(
        raw, config, policy, entry, number, sheet, inferred_unit, adjudication, scientific_result, sql_result)
    retained = []
    uid = base["row_id"]
    material_fields = {}
    if material_route is not None:
        route = material_route['route']
        material_fields = dict(material_route=route, classification_key=material_route.get('member_key'),
                               material_result=material_route.get('material_result'))
    if material_route is None or material_route['route'] == 'legacy_organic':
        try:
            identity, identity_status, evidence = resolve_identity(mapped, index, policy["chemistry_policy"], blocked_identity_keys)
        except DataError as exc:
            identity, identity_status, evidence = None, str(exc), []
    elif material_route['route'] == 'nonmaterial_terminal':
        pre = pre_identity_observation(raw, config, policy, entry, number, sheet, inferred_unit, adjudication, scientific_result, sql_result)
        if pre.requires_classification:
            raise DataError('Eligible observation cannot use nonmaterial route')
        disposition['reasons'] = list(pre.nonmaterial_reasons + pre.identity_reasons + pre.extraction_blockers)
        identity, identity_status, evidence = None, 'not_evaluated_nonmaterial', []
    elif material_route['route'] == 'verified_material':
        from qsar_material_curation import NATIVE_SUPPORT_IDENTITY_REASONS
        for kind in ('cas', 'dtxsid', 'npid'):
            blocked = blocked_identity_keys.get((kind, mapped.get(kind, '')), {})
            reasons = set(blocked.get('reasons', [])) | {blocked.get('reason')}
            if reasons & (NATIVE_SUPPORT_IDENTITY_REASONS | {'ambiguous_cas_candidates'}):
                raise DataError('Verified material cannot waive native support identifier failure')
        result = material_route['material_result']
        identity = {k: v for k, v in result.items() if k not in ('status', 'observation_id')}
        identity_status, evidence = 'verified_material', [material_route['classification']]
        if conversion_unit in UNITS and ((UNITS[conversion_unit][0] == 'mass') !=
                (result['reviewed_dose_basis'] == 'reported_material_mass')):
            raise DataError('Native unit kind differs from reviewed material dose basis')
    else:
        identity, identity_status, evidence = None, material_route['route'], [material_route['classification']]
    identity_record = dict(base, original_smiles=mapped["smiles"], original_inchi=mapped["inchi"],
                           cas=mapped["cas"], dtxsid=mapped["dtxsid"], status=identity_status,
                           evidence=evidence, flags=flags, normalized_identity=identity)
    disposition.update(material_fields)
    identity_record.update(material_fields)
    if identity is None and identity_status != 'not_evaluated_nonmaterial':
        disposition["reasons"].append(identity_status)
    if scientific_result is not None:
        if base['scientific_remaining_issues']:
            raise DataError('Unresolved ordinary scientific issues prevent curation')
        treatments = base['scientific_dispositions']
        if treatments:
            disposition['reasons'].extend(d['treatment']['reason'] for d in treatments)
            disposition['disposition'] = ('quarantined' if any(d['treatment']['disposition'] == 'scientific_quarantine'
                                                             for d in treatments) else 'excluded')
            return disposition, identity_record, retained
    if sql_result is not None and base['sql_result']['disposition'] != 'preserve':
        disposition['disposition'] = ('quarantined' if base['sql_result']['disposition'] == 'scientific_quarantine' else 'excluded')
        return disposition, identity_record, retained
    if not disposition["reasons"]:
        kind, factor = UNITS[conversion_unit]
        mgkg = value * factor if kind == "mass" else value * factor * identity["original_mw"] * 1000
        molkg = mgkg / (1000 * identity["original_mw"])
        if not all(math.isfinite(x) and x > 0 for x in (mgkg, molkg)):
            disposition["reasons"].append("numeric_conversion_overflow_or_underflow")
            return disposition, identity_record, retained
        for task in applicable:
            row = dict(base, **identity, task_id=task["id"], output_row_id=uid + ":" + task["id"],
                       species=mapped["species"], route=mapped["route"], endpoint=mapped["endpoint"],
                       raw_value=mapped["value"], raw_unit=mapped["unit"], raw_relation=mapped["relation"], normalized_unit=conversion_unit,
                       normalized_relation=relation, target_kind="exact" if relation == "=" else "reported_numeric_estimate",
                       target_mg_kg=mgkg, target_mol_kg=molkg, target_pLD50=-math.log10(molkg),
                       reference=mapped["reference"], source_url=mapped["source_url"], test_material=mapped["test_material"],
                       cas=mapped["cas"], dtxsid=mapped["dtxsid"], flags=flags, replicate_flag=False)
            retained.append(row)
            disposition["output_row_ids"].append(row["output_row_id"])
        disposition["disposition"] = "retained"
    elif adapter_reasons or identity_status in {"reviewed_hold", "pending_review", "identity_conflict", "ambiguous_identity", "unresolved_identity", "conflicting_structure_representations", "unresolved_support_identity"}:
        disposition["disposition"] = "quarantined"
    return disposition, identity_record, retained


def _curate_into(policy, registry, out, contract_path, registry_path,
                 contract_hash, registry_hash, spool_dir, stack, external_adjudication=None, bound_evidence=None, material_batch=None):
    sources, tasks = policy["sources"], policy["tasks"]
    support_ledger = DiskLedger(spool_dir / "support.jsonl", stack)
    blocked_identity_keys = {}
    index = identity_index(policy, registry, support_ledger, blocked_identity_keys,
                           raw_support_observer=material_batch.observe_support if material_batch else None)
    dispositions = DiskLedger(spool_dir / "dispositions.jsonl", stack)
    identities = DiskLedger(spool_dir / "identities.jsonl", stack)
    retained, conflicts, blockers = [], [], []
    source_counts = {}
    metadata_coverage = {}
    narrative_accounting = {}
    metadata_consumers = {}
    ordinary_accounting = {}
    sql_inputs = {}
    sql_references = []
    sql_terminals = {}
    for config in sources:
        entry = bound_file(registry, config["source_id"])
        adapter = config.get("adapter")
        adjudication = None
        ordinary = None
        sql = None
        if config.get('sql_curation') is not None:
            from qsar_sql_curation import SQLCurationInputs
            sql = stack.enter_context(SQLCurationInputs(config['sql_curation'], contract_path=contract_path,
                registry_path=registry_path, adjudication_config_path=external_adjudication['path'],
                source_id=config['source_id'], work_dir=spool_dir, bind=sql_references.append))
            sql_inputs[config['source_id']] = sql
            if material_batch is not None:
                material_batch.sql_inputs[config['source_id']] = sql
        if config.get('ordinary_adjudication') is not None:
            from qsar_adjudication import OrdinaryAdjudication
            ordinary = OrdinaryAdjudication(config['ordinary_adjudication'], entry,
                contract_path, registry_path, spool_dir)
        if "narrative_adjudication" in config:
            if adapter != "hsdb":
                raise DataError("Narrative adjudication currently requires the HSDB adapter")
            ledger_name = "narrative_fields_" + hashlib.sha256(config["source_id"].encode()).hexdigest()[:16] + ".jsonl"
            binding = config["narrative_adjudication"]
            engine_config = narrative_binding(binding, contract_path)
            if binding.get("schema_version") == 2:
                adjudication = adjudication_module().AdjudicationV2(engine_config, entry,
                    binding["full_contract"], binding["accepted_contract_review"], UNITS, out / ledger_name)
            else:
                adjudication = adjudication_module().Adjudication(engine_config, policy,
                                                                 entry, UNITS, out / ledger_name)
            narrative_accounting[config["source_id"]] = adjudication.summary
            adjudication.summary["field_ledger"] = ledger_name
            if 'narrative_observation_metadata' in config:
                metadata = adjudication_module().ObservationMetadata(
                    config['narrative_observation_metadata'], engine_config, entry, UNITS,
                    config.get('exact_unit_aliases', {}))
                adjudication.metadata = metadata
                metadata_consumers[config['source_id']] = metadata
        elif adapter == "hsdb":
            narrative_accounting[config["source_id"]] = {"complete_narrative_accounting": False,
                "limitation": "Parser-only coverage does not prove exhaustive narrative recovery"}
        if adapter not in ADAPTERS:
            blockers.append({"source_id": config["source_id"], "reason": "unsupported_source_adapter", "adapter": adapter,
                             "required": config.get("required", True), "native_rows": entry.get("row_count")})
            continue
        columns = dict(ADAPTERS[adapter], **config.get("columns", {}))
        constants = config.get("constants", {})
        if any(k in constants for k in ("value", "relation", "smiles", "inchi", "cas", "dtxsid", "npid")):
            raise DataError("Constants cannot invent measurements, qualifiers or identities")
        inferred_unit = toxric_unit(config, registry, entry) if adapter == "toxric" else None
        if adapter != "toxric" and "unit" in constants:
            raise DataError("Native unit fields must not be overridden")
        count = 0
        sheet = config.get("sheet", "Data" if adapter.startswith("ice_") else "")
        schema = []
        if adapter in NATIVE_ADAPTERS:
            if config.get("columns") or config.get("sheet") or config.get("header_row"):
                raise DataError("Native relational adapters use fixed reviewed schemas")
            parser = archive_sources().iter_hsdb if adapter == "hsdb" else (lambda path: native_sources().iter_toxval(path, recover_identity=True)) if adapter == "toxval" else native_sources().iter_openfoodtox
            if sql is not None:
                def sql_rows(session):
                    for ordinal, n, raw, terminal in session.iter_primary():
                        candidates = list(session.candidates(n))
                        if candidates:
                            for carrier in candidates:
                                yield ordinal, (raw, carrier, n, None)
                        else:
                            yield ordinal, (raw, None, n, terminal)
                iterator = sql_rows(sql)
            else:
                iterator = enumerate(adjudication.rows() if adjudication else parser(entry["path"]), 1)
            schema.extend(FIELDS)
            sheet = "toxval" if adapter == "toxval" else "hsdb_narratives" if adapter == "hsdb" else "END_STUDY_REC.HumanHealth"
        else:
            iterator = rows(entry["path"], sheet, config.get("header_row", 1), schema=schema, native_index=ordinary is not None)
        first = next(iterator, None)
        if columns.get("value") not in schema or (adapter != "toxric" and columns.get("unit") not in schema):
            raise DataError("Required native value/unit column missing")
        if any(column not in schema for column in config.get("columns", {}).values()):
            raise DataError("Explicit column mapping missing from native source")
        for number, raw in itertools.chain(() if first is None else (first,), iterator):
            count += 1
            sql_result = terminal = native_n = None
            if sql is not None:
                raw, sql_result, native_n, terminal = raw
                if terminal is not None:
                    sql_terminals[(config['source_id'], number)] = native_n
            scientific = None
            if ordinary is not None:
                from qsar_material_curation import observation, ScientificResult
                scientific = ScientificResult(ordinary, observation(raw, config, entry, number, sheet))
            if material_batch is not None:
                material_batch.stage_observation(config['source_id'], number, sheet, raw, adjudicated=adjudication is not None,
                                                 scientific_result=scientific, sql_result=sql_result)
                continue
            disposition, identity_record, emitted = curate_observation(
                raw, config, policy, entry, number, sheet, index, blocked_identity_keys,
                inferred_unit, adjudication is not None, scientific_result=scientific, sql_result=sql_result)
            if sql is not None:
                if sql_result is not None:
                    sql.record(sql_result, disposition=disposition, identity_record=identity_record, emitted_rows=emitted)
                elif terminal is not None:
                    sql.record_terminal(native_n, disposition=disposition, identity_record=identity_record, emitted_rows=emitted)
            dispositions.append(disposition)
            identities.append(identity_record)
            if emitted:
                identities.final_flags[disposition["row_id"]] = emitted[0]["flags"]
            retained.extend(emitted)
        source_counts[config["source_id"]] = count
        if ordinary is not None:
            ordinary_accounting[config['source_id']] = ordinary.finish()
            if material_batch is not None:
                material_batch.references.extend(ordinary_accounting[config['source_id']]['references'])
        if material_batch is not None and adjudication is not None:
            with (out / ledger_name).open(encoding='utf-8') as stream:
                for line in stream:
                    material_batch.stage_narrative_field(config['source_id'], json.loads(line))
        if adjudication and not adjudication.summary.get(
                "required_source_coverage_complete" if adjudication.summary.get("schema_version") == 2
                else "complete_narrative_accounting", False):
            blockers.append({"source_id": config["source_id"], "reason": "unresolved_readable_narrative_extraction",
                             "required": config.get("required", True)})
        if adapter == "toxval" and material_batch is None:
            selected = [row for row in retained if row["source_id"] == config["source_id"]]
            references, relationships = (sql.selected_metadata({row["source_record_id"] for row in selected}) if sql is not None else native_sources().toxval_selected_metadata(entry["path"], {row["source_record_id"] for row in selected}))
            metadata_coverage[config["source_id"]] = {
                "selected_ids": len({row["source_record_id"] for row in selected}),
                "selected_ids_with_record_source": len(references),
                "record_source_rows": sum(len(records) for records in references.values()),
                "selected_relationship_memberships": sum(len(records) for records in relationships.values()),
                "source_chemical": "Exact chemical_id join attempted for all toxval rows; candidates retained in lineage",
                "scope": "Selected study references/relationships only; other SQL tables remain separately inventoried gaps"}
            for row in selected:
                record_id = row["source_record_id"]
                row["record_source_matches"] = references.get(record_id, [])
                row["native_relationships"] = relationships.get(record_id, [])
                row["study_references"] = [match["record"].get("long_ref") for match in references.get(record_id, [])]
                if not references.get(record_id):
                    row["flags"].append("missing_record_source_provenance")
                if relationships.get(record_id):
                    row["flags"].append("native_relationship_present")
        if digest(entry["path"]) != entry["sha256"]:
            raise DataError("Native source changed during curation")
    material_integration = None
    if material_batch is not None:
        if blockers:
            raise DataError('Unresolved source blockers prevent material integration')
        material_batch.seal_native()
        configs = {c['source_id']: c for c in sources}
        entries = {e['source_id']: e for e in registry['files']}
        for source, number, sheet, raw, adjudicated, member in material_batch.iter_observations():
            config, entry = configs[source], entries[source]
            inferred_unit = material_batch.inferred_units.get(source)
            scientific = material_batch.scientific_result(member)
            sql_result = material_batch.sql_result(member)
            pre = pre_identity_observation(raw, config, policy, entry, number, sheet, inferred_unit, adjudicated, scientific, sql_result)
            route = material_batch.route(member) if pre.requires_classification else dict(
                route='nonmaterial_terminal', member_key=None, material_result=None)
            disposition, identity_record, emitted = curate_observation(raw, config, policy, entry,
                number, sheet, index, blocked_identity_keys, inferred_unit, adjudicated, material_route=route, scientific_result=scientific, sql_result=sql_result)
            if source in sql_inputs:
                if sql_result is not None:
                    sql_inputs[source].record(sql_result, disposition=disposition, identity_record=identity_record, emitted_rows=emitted)
                elif (source, number) in sql_terminals:
                    sql_inputs[source].record_terminal(sql_terminals[(source, number)], disposition=disposition,
                        identity_record=identity_record, emitted_rows=emitted)
            for row in emitted:
                if route['route'] == 'verified_material':
                    material_batch.materials.validate_projection(row)
            dispositions.append(disposition)
            identities.append(identity_record)
            if emitted:
                identities.final_flags[disposition['row_id']] = emitted[0]['flags']
            retained.extend(emitted)
        # ToxVal selected-study metadata remains derived from the selected native IDs.
        for config in sources:
            if config['adapter'] != 'toxval':
                continue
            selected = [row for row in retained if row['source_id'] == config['source_id']]
            selected_ids = {row['source_record_id'] for row in selected}
            references, relationships = (sql_inputs[config['source_id']].selected_metadata(selected_ids)
                if config['source_id'] in sql_inputs else native_sources().toxval_selected_metadata(entries[config['source_id']]['path'], selected_ids))
            metadata_coverage[config['source_id']] = dict(selected_ids=len({row['source_record_id'] for row in selected}),
                selected_ids_with_record_source=len(references), record_source_rows=sum(map(len, references.values())),
                selected_relationship_memberships=sum(map(len, relationships.values())),
                source_chemical='Exact chemical_id join attempted for all toxval rows; candidates retained in lineage',
                scope='Selected study references/relationships only; other SQL tables remain separately inventoried gaps')
            for row in selected:
                record_id = row['source_record_id']
                row.update(record_source_matches=references.get(record_id, []), native_relationships=relationships.get(record_id, []),
                    study_references=[match['record'].get('long_ref') for match in references.get(record_id, [])])
                if not references.get(record_id): row['flags'].append('missing_record_source_provenance')
                if relationships.get(record_id): row['flags'].append('native_relationship_present')
    groups = defaultdict(list)
    for row in retained:
        row["group_id"] = row["connectivity_id"]
        groups[(row["task_id"], row["group_id"])].append(row)
    for (task, group), records in groups.items():
        if len(records) > 1:
            for row in records:
                row["replicate_flag"] = True
            conflicts.append({"task_id": task, "group_id": group, "row_ids": [r["output_row_id"] for r in records],
                              "values_mg_kg": [r["target_mg_kg"] for r in records], "policy": "preserve_flag",
                              "status": "potential_replicate_or_lineage_duplicate",
                              "distinct_values": len({r["target_mg_kg"] for r in records})})
    write_csv(out / "curated.csv", retained, ["output_row_id", "task_id", "group_id", "parent_smiles", "target_mg_kg", "target_pLD50"], canonical_values=material_batch is not None)
    write_csv(out / "dispositions.csv", dispositions, ["row_id", "disposition", "reasons", "output_row_ids"], canonical_values=material_batch is not None)
    write_csv(out / "identity_ledger.csv", identities, ["row_id", "status", "flags"], canonical_values=material_batch is not None)
    write_csv(out / "identity_support_ledger.csv", support_ledger, ["source_id", "record_id", "cas", "status", "reasons"])
    write_csv(out / "conflict_ledger.csv", conflicts, ["task_id", "group_id", "policy"])
    counts = dict(Counter(d["disposition"] for d in dispositions))
    observation_metadata_accounting = {source: consumer.accounting(dispositions)
                                      for source, consumer in metadata_consumers.items()}
    limitations = ["No independent acceptance; runtime and reviewer must verify collection receipt",
                   "No tautomer, charge or stereo normalization; connectivity grouping ignores stereochemistry for split safety",
                   "Potential replicates retained and flagged without aggregation; cross-source lineage may remain unresolved"]
    if policy["relation_policy"] == "reported_numeric":
        limitations.append("Unqualified numeric reports are estimates, not proven exact observations; unknown relations remain unknown")
    limitations.extend(blockers)
    summary = {"schema_version": 1, "adapter_version": VERSION,
               "status": "blocked" if any(b["required"] for b in blockers) else "limited" if blockers else "produced",
               "contract_sha256": contract_hash, "registry_sha256": registry_hash,
               "external_adjudication_config": external_adjudication,
               "policy": policy, "source_counts": source_counts, "input_rows": len(dispositions), "dispositions": counts,
               "metadata_coverage": metadata_coverage,
               "narrative_accounting": narrative_accounting,
               "ordinary_accounting": ordinary_accounting,
               "output_rows": len(retained), "groups": len({r["group_id"] for r in retained}),
               "task_counts": {t["id"]: sum(r["task_id"] == t["id"] for r in retained) for t in tasks},
               "blockers": blockers, "limitations": limitations}
    if metadata_consumers:
        summary['observation_metadata_accounting'] = observation_metadata_accounting
    selected = {s["source_id"] for s in sources}
    summary["unselected_inventory_sources"] = [e["source_id"] for e in registry["files"] if e["source_id"] not in selected]
    if digest(contract_path) != contract_hash or digest(registry_path) != registry_hash:
        raise DataError("Contract or registry changed during curation")
    if external_adjudication and digest(external_adjudication["path"]) != external_adjudication["sha256"]:
        raise DataError("External adjudication configuration changed during curation")
    for accounting in ordinary_accounting.values():
        for file in accounting['references']:
            adjudication_module().bound(file)
    sql_integration = None
    if sql_inputs:
        from qsar_sql_curation import BASE_OUTPUTS
        fixed = {name: curation_evidence_module().binding(out / name) for name in BASE_OUTPUTS}
        sql_sources = {}
        for source, session in sql_inputs.items():
            accounting = session.finish(output_dir=out, output_bindings=fixed)
            sql_references.extend(accounting.pop('references'))
            sql_sources[source] = accounting
        sql_integration = dict(schema_version=1, phase='diagnostic', sources=sql_sources)
        summary['status'] = 'blocked'
        summary['sql_integration'] = sql_integration
        summary['sql_references'] = sorted({b['path']: b for b in sql_references}.values(), key=lambda b:b['path'])
    if bound_evidence is not None:
        for consumer in metadata_consumers.values():
            consumer.finish()
        evidence_module = curation_evidence_module()
        evidence_module.verify_capture(bound_evidence)
        if sql_inputs:
            from qsar_sql_curation import BASE_OUTPUTS, ledger_names
            names = list(BASE_OUTPUTS)
            for source in sql_inputs: names.extend(ledger_names(source))
            names.extend(account['field_ledger'] for account in narrative_accounting.values() if account.get('field_ledger'))
            bound_evidence['outputs'] = {name: evidence_module.binding(out / name) for name in names}
        else:
            bound_evidence["outputs"] = {p.name: evidence_module.binding(p) for p in out.iterdir() if p.is_file()}
        summary["bound_evidence"] = bound_evidence
    if material_batch is not None:
        material_integration = material_batch.finish()
    summary['external_material_config'] = material_integration['packet'] if material_integration else None
    summary['material_integration'] = material_integration
    assert sum(counts.values()) == sum(source_counts.values())
    references = list(summary.get('sql_references', []))
    if bound_evidence is not None:
        references.extend([bound_evidence['contract'], bound_evidence['registry'], *bound_evidence['code'],
                           *[b for b in bound_evidence['collection'].values() if b is not None]])
    if external_adjudication: references.append(external_adjudication)
    if material_integration: references.extend(material_integration['references'])
    for accounting in ordinary_accounting.values(): references.extend(accounting['references'])
    return dict(summary=summary, outputs=summary.get('bound_evidence', {}).get('outputs', {}),
                references=sorted({b['path']: b for b in references}.values(), key=lambda b:b['path']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect")
    collection.add_argument("--raw-root", required=True)
    collection.add_argument("--output-dir", required=True)
    curation = sub.add_parser("curate")
    curation.add_argument("--contract", required=True)
    curation.add_argument("--registry", required=True)
    curation.add_argument("--output-dir", required=True)
    curation.add_argument("--adjudication-config", help="JSON map of declared source IDs to reviewed HSDB or typed ordinary bindings")
    curation.add_argument('--material-config', help='Reviewed material curation packet')
    args = parser.parse_args(argv)
    try:
        result = collect(args.raw_root, args.output_dir) if args.command == "collect" else curate(args.contract, args.registry, args.output_dir, args.adjudication_config, args.material_config)
        print(json.dumps({"summary": result["summary"], "coverage": result["coverage"]}, default=str))
        return 2 if result["coverage"].get("status") == "blocked" else 0
    except (ValueError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
