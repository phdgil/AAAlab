"""Read-only HSDB narrative and ChemID identity adapters.

Archive bytes are never extracted to disk or repaired. Hash inputs in the calling
stage. iter_hsdb_documents inventories every DOC, retaining every repeated native
field and its XML. iter_hsdb yields every ntxv field plus every other field with
an LD50 mention; DOCs with no such fields get an explicit document disposition.
Only an anchored, single rat-oral LD50 expression is parsed. A missing qualifier
remains unknown, inequalities stay inequalities, and ambiguous narratives remain
quarantined. This parser's candidate status is not scientific eligibility.

iter_chemid_identity joins native Chemical.id/CAS to SUPERID SMILES/InChI records.
It retains all candidates and their row locators; multiple native candidates or
CAS numbers are not arbitrarily collapsed. Chemistry validation remains required
downstream even for one candidate. The complete XML can be separately inventoried
with iter_chemid_documents. Notes mentioning measurements are retained for review;
this identity adapter does not establish toxicity-source irrelevance.
"""
from collections import Counter, defaultdict
import csv
import hashlib
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


class ArchiveSourceError(ValueError):
    pass


LD50 = re.compile(r"\bLD\s*(?:50\b|\(\s*50\s*\))", re.I)
# Entire native narrative must match. No substring dose extraction and no ranges.
RAT_ORAL = re.compile(
    r"\s*LD50\s+(?P<species>Rat|Rats)\s+(?P<route>oral|intragastric|gavage|ingestion|po)\b\s*"
    r"(?:\((?P<sex_note>[^)]+)\)\s*)?"
    r"(?P<relation>>=|<=|>|<|=|\u2264|\u2265)?\s*"
    r"(?P<value>(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(?P<unit>(?:mg|g|ug|\u00b5g|\u03bcg|mol|mmol)/kg(?: (?:bw|body weight))?)\s*\.?\s*"
    r"(?P<reference>\[[^\[\]]*\])\s*(?P<citation_review_marker>\*\*(?:PEER|QC) REVIEWED\*\*)?\s*"
    # Recognize the archive's PubMed link, never strip arbitrary HTML/prose.
    r'(?P<citation_tail><a href="https?://www\.ncbi\.nlm\.nih\.gov/pubmed/\d+\?dopt=Abstract"'
    r' target=new>PubMed Abstract</a>)?\s*\Z', re.I | re.S)


def archive(path):
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveSourceError("Archive integrity failure (no repair attempted): " + str(path)) from exc


def unique_member(book, predicate, label):
    names = [name for name in book.namelist() if predicate(name)]
    if len(names) != 1:
        raise ArchiveSourceError(f"Expected one {label} member, found {len(names)}")
    return names[0]


def documents(path, member_predicate, record_tag):
    """Streaming XML documents with bounded tree memory and native field order."""
    with archive(path) as book:
        member = unique_member(book, member_predicate, record_tag + " XML")
        with book.open(member) as stream:
            iterator = ET.iterparse(stream, events=("start", "end"))
            try:
                _, root = next(iterator)
            except StopIteration:
                raise ArchiveSourceError("Empty native XML")
            expected_root = "hsdb" if record_tag == "DOC" else "file"
            if root.tag != expected_root:
                raise ArchiveSourceError("Unexpected native XML root: " + root.tag)
            ordinal = 0
            for event, element in iterator:
                if event != "end" or element.tag != record_tag:
                    continue
                ordinal += 1
                xml = ET.tostring(element, encoding="unicode")
                fields = [{"tag": child.tag, "text": "".join(child.itertext()),
                           "xml": ET.tostring(child, encoding="unicode")} for child in element]
                yield element, {"path": str(Path(path).resolve()), "archive_member": member,
                                "document_ordinal": ordinal,
                                "serialized_document_sha256": hashlib.sha256(xml.encode()).hexdigest()}, fields, xml
                root.clear()


def iter_hsdb_documents(path):
    for element, lineage, fields, xml in documents(path, lambda n: ".xml" in n.lower(), "DOC"):
        docno = element.findtext("DOCNO", default="").strip()
        yield {"record_id": docno, "native_lineage": lineage,
               "native_record": {"fields": fields, "serialized_xml": xml}}


def identity_context(fields):
    wanted = {"DOCNO", "NameOfSubstance", "CASRegistryNumber", "DateRevised"}
    return [field for field in fields if field["tag"] in wanted]


def parse_hsdb_narrative(text):
    """Return a conservative scalar candidate or explicit quarantine/exclusion."""
    result = {"species": "", "route": "", "endpoint": "", "value": "", "unit": "", "relation": "",
              "sex": "", "sex_note": "", "reference": "", "status": "quarantined", "why": []}
    if not LD50.search(text):
        result.update(status="excluded", why=["no_ld50_mention"])
        return result
    if len(LD50.findall(text)) != 1:
        result["why"] = ["multiple_or_ambiguous_ld50_observations"]
        return result
    match = RAT_ORAL.fullmatch(text)
    if not match:
        result["why"] = ["narrative_not_verified_single_rat_oral_scalar"]
        return result
    note = match["sex_note"] or ""
    oral_gavage = match["route"].lower() == "oral" and note.lower() == "gavage"
    if note and note.lower() not in {"male", "female"} and not oral_gavage:
        result["why"] = ["unverified_parenthetical_context"]
        return result
    # A bracketed dose annotation or second observation is not a citation.
    if re.search(r"\b(?:mg|g|ug|\u00b5g|\u03bcg|mol|mmol)\s*/\s*kg\b", match["reference"], re.I):
        result["why"] = ["ambiguous_bracketed_dose_context"]
        return result
    result.update({key: value or "" for key, value in match.groupdict().items()})
    result["native_value"] = result["value"]
    result["value"] = result["value"].replace(",", "")
    result["route_note"] = note if oral_gavage else ""
    if oral_gavage:
        result["sex_note"] = ""
    result["sex"] = result["sex_note"]
    result.update(endpoint="LD50", status="candidate", why=[])
    result["normalized_relation"] = {"=": "=", "<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}.get(result["relation"], "unknown")
    return result


def iter_hsdb(path):
    """Account all DOCs/ntxv/other LD50 narratives, without guessed chemistry."""
    for doc in iter_hsdb_documents(path):
        fields = doc["native_record"]["fields"]
        context = identity_context(fields)
        cas_candidates = [f["text"].strip() for f in context if f["tag"] == "CASRegistryNumber" and f["text"].strip()]
        cas_values = set(cas_candidates)
        cas = next(iter(cas_values)) if len(cas_values) == 1 else ""
        counts, emitted = Counter(), 0
        for position, field in enumerate(fields, 1):
            counts[field["tag"]] += 1
            if field["tag"] != "ntxv" and not LD50.search(field["text"]):
                continue
            emitted += 1
            parsed = parse_hsdb_narrative(field["text"])
            if len(cas_values) != 1:
                parsed["status"] = "quarantined"
                parsed["why"].append("missing_native_cas" if not cas_values else "ambiguous_native_cas")
            yield dict(parsed, record_id=f'{doc["record_id"]}:{doc["native_lineage"]["document_ordinal"]}:{field["tag"]}:{counts[field["tag"]]}',
                       cas=cas, dtxsid="", smiles="", inchi="", npid="", test_material="", source_url="",
                       cas_candidates=cas_candidates, document_context=context,
                       native_record=field, native_lineage=dict(doc["native_lineage"], docno=doc["record_id"],
                       field_ordinal=position, tag=field["tag"], tag_ordinal=counts[field["tag"]]),
                       source_record_kind="narrative_field")
        if not emitted:
            yield {"record_id": f'{doc["record_id"]}:{doc["native_lineage"]["document_ordinal"]}:document',
                   **{k: "" for k in ("dtxsid", "smiles", "inchi", "npid", "species", "route", "endpoint", "value", "unit", "relation", "sex", "test_material", "reference", "source_url")},
                   "cas": cas, "cas_candidates": cas_candidates, "status": "excluded", "why": ["no_toxicity_candidate_fields"],
                   "native_record": {"document_context": context, "field_count": len(fields)},
                   "native_lineage": doc["native_lineage"], "document_context": context,
                   "source_record_kind": "document_disposition"}


def iter_chemid_documents(path):
    for element, lineage, fields, xml in documents(path, lambda n: n.lower().endswith(".xml"), "Chemical"):
        yield {"record_id": element.get("id", ""), "native_lineage": lineage,
               "native_record": {"attributes": dict(element.attrib), "fields": fields, "serialized_xml": xml}}


def structure_index(book, member, field):
    index = defaultdict(list)
    with book.open(member) as binary, io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not reader.fieldnames or not {"SUPERID", field}.issubset(reader.fieldnames):
            raise ArchiveSourceError("Missing native structure columns in " + member)
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ArchiveSourceError("Duplicate native structure columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ArchiveSourceError("Native structure field-count mismatch")
            superid = (row.get("SUPERID") or "").strip()
            if not superid:
                raise ArchiveSourceError("Structure record missing SUPERID")
            index[superid].append({"archive_member": member, "native_row": reader.line_num, "record": row})
    return index


def iter_chemid_identity(path):
    """Preserve one chemical's entire native candidate set; never choose first."""
    with archive(path) as book:
        smi_member = unique_member(book, lambda n: n.lower().endswith("-smiles.txt"), "SMILES")
        inchi_member = unique_member(book, lambda n: n.lower().endswith("-inchi.txt"), "InChI")
        smiles = structure_index(book, smi_member, "SMILES")
        inchis = structure_index(book, inchi_member, "InChI")
    matched = set()
    for element, lineage, fields, xml in documents(path, lambda n: n.lower().endswith(".xml"), "Chemical"):
        superid = element.get("id", "")
        matched.add(superid)
        cas_candidates = [(node.text or "").strip() for node in element.findall("./NumberList/CASRegistryNumber")]
        candidates = smiles.get(superid, []) + inchis.get(superid, [])
        smi_values = {c["record"]["SMILES"].strip() for c in smiles.get(superid, []) if c["record"].get("SMILES", "").strip()}
        inchi_values = {c["record"]["InChI"].strip() for c in inchis.get(superid, []) if c["record"].get("InChI", "").strip()}
        cas_values = {value for value in cas_candidates if value}
        why = []
        if len(cas_values) != 1:
            why.append("missing_cas" if not cas_values else "ambiguous_cas_candidates")
        if len(smi_values) > 1 or len(inchi_values) > 1:
            why.append("ambiguous_structure_candidates")
        if not smi_values and not inchi_values:
            why.append("missing_structure_candidates")
        notes = [field for field in fields if LD50.search(field["text"])]
        yield {"record_id": superid, "cas": next(iter(cas_values)) if len(cas_values) == 1 else "",
               "cas_candidates": cas_candidates, "smiles": next(iter(smi_values)) if len(smi_values) == 1 else "",
               "inchi": next(iter(inchi_values)) if len(inchi_values) == 1 else "", "dtxsid": "", "npid": "",
               "status": "quarantined" if why else "candidate", "why": why,
               "structure_validation": "chemistry_comparison_required_downstream",
               "candidate_structures": candidates, "measurement_notes": notes,
               "native_record": {"attributes": dict(element.attrib), "fields": fields},
               "native_lineage": lineage, "source_record_kind": "identity_support"}
    # Native structure tables can contain SUPERIDs absent from this XML snapshot.
    for superid in sorted((set(smiles) | set(inchis)) - matched):
        yield {"record_id": superid, "cas": "", "smiles": "", "inchi": "", "dtxsid": "", "npid": "",
               "status": "quarantined", "why": ["orphan_structure_superid"],
               "candidate_structures": smiles.get(superid, []) + inchis.get(superid, []),
               "native_record": {"SUPERID": superid}, "native_lineage": {"path": str(Path(path).resolve())},
               "source_record_kind": "identity_support"}
