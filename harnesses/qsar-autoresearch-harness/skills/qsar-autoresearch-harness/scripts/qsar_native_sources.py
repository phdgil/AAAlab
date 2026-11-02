"""Native ToxVal SQL and OpenFoodTox readers. Never execute SQL or alter inputs.

API: iter_toxval(path), iter_openfoodtox(path) yield canonical dictionaries with
status ('candidate' or 'quarantined'), why, native_record and native_lineage.
'candidate' means parsed, not scientifically eligible or independently accepted.
Consumers MUST honor status/why before numeric curation, retain native records,
and bind input hashes externally. No filters, record cap, unit conversion, identity
standardization, or scientific acceptance are performed here.

ToxVal maps the source-normalized numeric/unit pair together and preserves its
original pair and qualifiers. Missing SQL values do not inherit DDL defaults.
OFT joins study.Parent UUID -> SUB.Document UUID -> reference UUID -> REF_SUB;
duplicate keys (even identical rows) are ambiguous and never arbitrarily collapsed.
OFT ranges and upper-only limits have no point value. A lone lowerValue is exported
with its native qualifier (blank remains unknown), not asserted to be exact.
"""
from collections import defaultdict
import gzip
import hashlib
from pathlib import Path
import re


class NativeSourceError(ValueError):
    pass


SQL_STRING = re.compile(r"'(?:\\.|''|[^'\\])*'", re.S)
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
RELATIONS = {"=": "=", "==": "=", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "≤": "<=", "≥": ">="}


def txt(value):
    return "" if value is None else str(value).strip()


def source_text(value):
    # ToxVal's literal '-' missing sentinel is retained in native_record.
    value = txt(value)
    return "" if value == "-" else value


def split_sql(text):
    """Split SQL comma lists outside strings and nested type parentheses."""
    start, pos, depth = 0, 0, 0
    while pos < len(text):
        char = text[pos]
        if char == "'":
            match = SQL_STRING.match(text, pos)
            if not match:
                raise NativeSourceError("Unterminated SQL string")
            pos = match.end()
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise NativeSourceError("Unbalanced SQL parentheses")
        elif char == "," and depth == 0:
            yield text[start:pos].strip()
            start = pos + 1
        pos += 1
    if depth:
        raise NativeSourceError("Unbalanced SQL parentheses")
    yield text[start:].strip()


def sql_value(token):
    if token.upper() == "NULL":
        return None
    if SQL_STRING.fullmatch(token):
        body = token[1:-1]
        escapes = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a",
                   "\\": "\\", "'": "'", '"': '"', "%": "\\%", "_": "\\_"}
        def unescape(match):
            if match[0] == "''":
                return "'"
            return escapes.get(match[0][1], match[0][1])
        return re.sub(r"\\.|''", unescape, body, flags=re.S)
    if NUMBER.fullmatch(token):
        # Keep numeric lexemes; downstream parsing decides validity/scale.
        return token
    raise NativeSourceError("Unsupported SQL literal/expression: " + token[:80])


def complete_sql_statements(stream):
    """Split every statement before filtering, respecting quotes and comments.

One statement and at most one unfinished string/comment are buffered. Comments
between statements are not part of the next statement's native start location.
Executable MySQL data-operation comments are unsupported and fail closed.
"""
    quoted = {"'": SQL_STRING,
              '"': re.compile(r'"(?:\\.|""|[^"\\])*"', re.S),
              "`": re.compile(r"`(?:``|[^`])*`", re.S)}
    plain = re.compile(r"[^'\"`;/#-]+")
    pending, pieces, classified = "", [], []
    line_number, start = 1, 1
    for line in stream:
        pending += line
        position = 0
        while position < len(pending):
            char = pending[position]
            comment = False
            if char in quoted:
                match = quoted[char].match(pending, position)
                if not match:
                    break
                end = match.end()
            elif pending.startswith("/*", position):
                close = pending.find("*/", position + 2)
                if close < 0:
                    break
                end, comment = close + 2, True
            elif char == "#" or (pending.startswith("--", position) and
                                  (position + 2 == len(pending) or pending[position + 2].isspace())):
                end = pending.find("\n", position)
                end, comment = len(pending) if end < 0 else end + 1, True
            else:
                match = plain.match(pending, position)
                end = match.end() if match else position + 1
            token = pending[position:end]
            if comment and re.match(r"/\*!\d*\s*(?:INSERT|REPLACE|UPDATE|DELETE|TRUNCATE|LOAD)\b", token, re.I):
                raise NativeSourceError(f"Unsupported executable SQL data-operation comment at line {line_number}")
            if char == ";" and not comment:
                if pieces:
                    yield start, "".join(pieces) + ";", "".join(classified) + ";"
                    pieces, classified = [], []
            elif pieces or (not comment and token.strip()):
                if not pieces:
                    start = line_number + token[:len(token) - len(token.lstrip())].count("\n")
                pieces.append(token)
                classified.append(" " if comment else token)
            line_number += token.count("\n")
            position = end
        pending = pending[position:]
    if pending.strip() or pieces:
        raise NativeSourceError(f"Unterminated native SQL statement/string/comment at line {start}")


def sql_statements(path, table, inventory=None):
    """Read only the selected CREATE/INSERT statements, at most one in memory."""
    target = re.compile(r"\s*(CREATE TABLE(?: IF NOT EXISTS)?|INSERT INTO)\s+`" + re.escape(table) + r"`(?:\s|\()", re.I)
    operation_start = re.compile(r"\s*(?:CREATE\s+TABLE|INSERT\b|REPLACE\b|UPDATE\b|DELETE\b|TRUNCATE\b|LOAD\b)", re.I)
    identifier = r"(?:`[^`]+`|[A-Za-z_][\w$]*)"
    operation_table = re.compile(
        r"\s*(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT(?:\s+\w+)*?\s+INTO|"
        r"REPLACE(?:\s+\w+)*?\s+INTO|UPDATE(?:\s+(?:LOW_PRIORITY|IGNORE))*|"
        r"DELETE(?:\s+\w+)*?\s+FROM|TRUNCATE(?:\s+TABLE)?|LOAD\b.*?\bINTO\s+TABLE)\s+"
        r"(?P<name>" + identifier + r"(?:\s*\.\s*" + identifier + r")?)(?=\s|\(|;)", re.I | re.S)
    opener = gzip.open if str(path).lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="strict", newline="") as stream:
        for start, statement, classified in complete_sql_statements(stream):
            if not operation_start.match(classified):
                continue
            matched = operation_table.match(classified)
            if not matched:
                raise NativeSourceError(f"Cannot classify native SQL operation at line {start}")
            name = matched["name"].split(".")[-1].strip().strip("`")
            if inventory is not None and re.match(r"\s*CREATE\s+TABLE", classified, re.I):
                inventory.setdefault(name, {"schema_line": start, "row_count": None})
            if name != table:
                continue
            if not target.match(statement):
                raise NativeSourceError(f"Unsupported selected-table SQL operation at line {start}")
            yield start, statement


def iter_sql_table(path, table="toxval", required_columns=(), inventory=None, filter_column=None, filter_values=None):
    """Yield all native rows with CREATE-derived names and exact tuple lineage.

Supports MySQL dump CREATE and INSERT VALUES, explicit INSERT column lists,
NULL, numeric lexemes, backslash escapes, doubled quotes and extended INSERTs.
Other SQL operations are never executed. Unsupported literals fail closed.
"""
    schema = None
    for line, statement in sql_statements(path, table, inventory):
        if re.match(r"\s*CREATE", statement, re.I):
            begin, end = statement.find("("), statement.rfind(")")
            if begin < 0 or end < begin:
                raise NativeSourceError("Malformed CREATE TABLE")
            schema = []
            for definition in split_sql(statement[begin + 1:end]):
                match = re.match(r"`([^`]+)`\s+\w+", definition)
                if match:
                    schema.append(match[1])
            if not schema or len(schema) != len(set(schema)):
                raise NativeSourceError("Missing/duplicate CREATE column schema")
            if not set(required_columns).issubset(schema):
                raise NativeSourceError("Required SQL columns missing: " + ", ".join(sorted(set(required_columns) - set(schema))))
            if inventory is not None:
                inventory[table].update(columns=list(schema), row_count=0)
            if filter_column is not None and filter_column not in schema:
                raise NativeSourceError("Metadata filter column missing from CREATE schema")
            continue
        if schema is None:
            raise NativeSourceError("INSERT precedes required CREATE schema")
        match = re.match(r"\s*INSERT INTO\s+`[^`]+`\s*(\([^)]*\))?\s*VALUES\s*(.*);\s*\Z", statement, re.I | re.S)
        if not match:
            raise NativeSourceError(f"Unsupported INSERT syntax at line {line}")
        columns = schema
        if match[1]:
            columns = []
            for token in split_sql(match[1][1:-1]):
                name = re.fullmatch(r"`([^`]+)`", token)
                if not name:
                    raise NativeSourceError("Unsupported INSERT column identifier")
                columns.append(name[1])
            if len(columns) != len(set(columns)) or not set(columns).issubset(schema):
                raise NativeSourceError("INSERT columns disagree with CREATE schema")
        statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        for ordinal, tuple_sql in enumerate(split_sql(match[2]), 1):
            if not tuple_sql.startswith("(") or not tuple_sql.endswith(")"):
                raise NativeSourceError(f"Malformed value tuple at line {line}")
            tokens = list(split_sql(tuple_sql[1:-1]))
            if len(tokens) != len(columns):
                raise NativeSourceError(f"Value/column count mismatch at line {line}, tuple {ordinal}")
            if inventory is not None:
                inventory[table]["row_count"] += 1
            if filter_column is not None:
                selected = sql_value(tokens[columns.index(filter_column)]) if filter_column in columns else None
                if selected not in filter_values:
                    continue
            values = [sql_value(token) for token in tokens]
            row = {column: None for column in schema}
            row.update(zip(columns, values))
            yield row, {"path": str(Path(path).resolve()), "table": table, "line": line,
                        "tuple": ordinal, "columns": list(columns), "sql_tuple": tuple_sql,
                        "statement_sha256": statement_hash}
    if schema is None:
        raise NativeSourceError("Requested CREATE TABLE not found: " + table)


def iter_toxval(path, recover_identity=False):
    """All toxval rows; no endpoint or route filtering and no unit guessing."""
    required = {"toxval_id", "dtxsid", "toxval_type", "toxval_numeric", "toxval_units", "toxval_numeric_qualifier",
                "species_original", "exposure_route"}
    chemical_index = defaultdict(list)
    if recover_identity:
        required.add("chemical_id")
        for chemical, locator in iter_sql_table(path, "source_chemical", ("chemical_id", "casrn", "dtxsid")):
            key = source_text(chemical["chemical_id"])
            if not key:
                raise NativeSourceError("source_chemical row has no native chemical_id")
            chemical_index[key].append({"record": chemical, "lineage": locator})
    for raw, lineage in iter_sql_table(path, "toxval", required):
        missing = required - set(raw)
        if missing:
            raise NativeSourceError("ToxVal required columns missing: " + ", ".join(sorted(missing)))
        relation = source_text(raw.get("toxval_numeric_qualifier"))
        original_relation = source_text(raw.get("toxval_numeric_qualifier_original"))
        why = []
        dtxsid, cas = source_text(raw["dtxsid"]), ""
        metadata_warnings = []
        if recover_identity:
            matches = chemical_index.get(source_text(raw.get("chemical_id")), [])
            lineage["source_chemical_matches"] = matches
            if len(matches) > 1:
                why.append("ambiguous_source_chemical_join")
            elif not matches:
                metadata_warnings.append("missing_source_chemical_join")
            else:
                chemical = matches[0]["record"]
                linked_dtxsid = source_text(chemical.get("dtxsid"))
                if dtxsid and linked_dtxsid and dtxsid != linked_dtxsid:
                    why.append("source_chemical_dtxsid_conflict")
                else:
                    dtxsid = dtxsid or linked_dtxsid
                    cas = source_text(chemical.get("casrn"))
        # The native normalized qualifier must not erase a contradictory original.
        if original_relation and RELATIONS.get(original_relation, original_relation) != RELATIONS.get(relation, relation):
            why.append("current_original_qualifier_conflict")
        row = {"record_id": source_text(raw["toxval_id"]), "dtxsid": dtxsid,
               "cas": cas, "smiles": "", "inchi": "", "npid": "",
               "species": source_text(raw["species_original"]), "route": source_text(raw["exposure_route"]),
               "endpoint": source_text(raw["toxval_type"]), "value": source_text(raw["toxval_numeric"]),
               "unit": source_text(raw["toxval_units"]), "relation": relation,
               "normalized_relation": RELATIONS.get(relation, "unknown"),
               "relation_origin": "source_normalized_column_not_verified_original_qualifier",
               "original_value": raw.get("toxval_numeric_original"), "original_unit": raw.get("toxval_units_original"),
               "original_relation": raw.get("toxval_numeric_qualifier_original"),
               "sex": source_text(raw.get("sex")), "test_material": source_text(raw.get("exposure_form")),
               "reference": " | ".join(source_text(raw.get(k)) for k in ("source", "subsource", "year") if source_text(raw.get(k))),
               "source_url": source_text(raw.get("source_url")), "native_record": raw, "native_lineage": lineage,
               "metadata_warnings": metadata_warnings, "qc_status": raw.get("qc_status"),
               "status": "quarantined" if why else "candidate", "why": why}
        yield row


def toxval_selected_metadata(path, selected_ids):
    """Join exact native study and relationship evidence for selected IDs only.

Quality/qa_status remain source labels, never automatic exclusion rules. Multiple
references are all retained. Relationships involving a selected record are flags,
not permission to aggregate or discard measurements.
"""
    selected_ids = set(selected_ids)
    references, relationships = defaultdict(list), defaultdict(list)
    if not selected_ids:
        return references, relationships
    for row, lineage in iter_sql_table(path, "record_source", ("toxval_id",),
                                       filter_column="toxval_id", filter_values=selected_ids):
        references[source_text(row["toxval_id"])].append({"record": row, "lineage": lineage})
    for row, lineage in iter_sql_table(path, "toxval_relationship", ("toxval_id_1", "toxval_id_2", "relationship")):
        for key in {source_text(row["toxval_id_1"]), source_text(row["toxval_id_2"])} & selected_ids:
            relationships[key].append({"record": row, "lineage": lineage})
    return references, relationships


def workbook_rows(book, name, required=()):
    if name not in book.sheetnames:
        raise NativeSourceError("Required OFT sheet missing: " + name)
    iterator = book[name].iter_rows(values_only=True)
    header = list(next(iterator, ()))
    # This native repository export has an unlabeled pandas row index in column A.
    if header and header[0] is None:
        header[0] = "__native_index"
    if not header or any(not isinstance(h, str) or not h for h in header) or len(header) != len(set(header)):
        raise NativeSourceError("Unsupported OFT blank/duplicate column names: " + name)
    if not set(required).issubset(header):
        raise NativeSourceError("Required OFT columns missing: " + name)
    for number, values in enumerate(iterator, 2):
        yield number, dict(zip(header, values))


def keyed_rows(book, name, required=()):
    index = defaultdict(list)
    for number, row in workbook_rows(book, name, ("Document UUID", *required)):
        key = txt(row.get("Document UUID"))
        if key:
            index[key].append({"sheet": name, "native_row": number, "record": row})
    return index


def effect_blocks(raw):
    prefixes = sorted({k.rsplit(".", 1)[0] for k in raw if k.endswith((".lowerValue", ".upperValue"))
                       and k.startswith("ResultsAndDiscussion.") and ".EffectLevel." in k})
    blocks = []
    for prefix in prefixes:
        parent = prefix.rsplit(".", 1)[0]
        block = {"prefix": prefix, "lower_value": raw.get(prefix + ".lowerValue"),
                 "upper_value": raw.get(prefix + ".upperValue"), "lower_qualifier": raw.get(prefix + ".lowerQualifier"),
                 "upper_qualifier": raw.get(prefix + ".upperQualifier"), "unit": raw.get(prefix + ".Unit"),
                 "unit_other": raw.get(prefix + ".Unit.Other"), "endpoint": raw.get(parent + ".Endpoint"),
                 "endpoint_other": raw.get(parent + ".Endpoint.Other"), "uuid": raw.get(parent + ".UUID")}
        if any(txt(block[k]) for k in ("lower_value", "upper_value", "lower_qualifier", "upper_qualifier", "endpoint")):
            blocks.append(block)
    return blocks


def oft_point(block):
    """Return native point value/relation or an explicit bound/interval blocker."""
    lower, upper = txt(block["lower_value"]), txt(block["upper_value"])
    lq, uq = txt(block["lower_qualifier"]), txt(block["upper_qualifier"])
    if lower and upper:
        return "", "", ["interval_not_point"]
    if upper:
        return "", "", ["upper_bound_not_point"]
    if not lower:
        return "", "", ["missing_effect_value"]
    if uq:
        return "", "", ["upper_qualifier_without_upper_value"]
    return lower, lq, []


def iter_openfoodtox(path):
    """Yield one accounted record per HumanHealth native row with full join lineage.

All effect blocks are preserved; multiple blocks quarantine the row rather than
arbitrarily choosing one. Exact route inference is intentionally left to a reviewed
contract mapping. Administrative endpoint and test-material composition remain
available to that mapping. Non-HumanHealth sheets are inventory, not LD50 rows.
"""
    import openpyxl
    book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        subs = keyed_rows(book, "SUB", ("ReferenceSubstance.ReferenceSubstance",))
        refs = keyed_rows(book, "REF_SUB", ("Inventory.CASNumber",))
        name = "END_STUDY_REC.HumanHealth"
        required = ("Document UUID", "Parent UUID", "ResultsAndDiscussion.EffectLevels.Endpoint",
                    "ResultsAndDiscussion.EffectLevels.EffectLevel.lowerValue",
                    "ResultsAndDiscussion.EffectLevels.EffectLevel.upperValue")
        for number, raw in workbook_rows(book, name, required):
            why, join_lineage = [], []
            sub_matches = subs.get(txt(raw.get("Parent UUID")), [])
            sub, ref = {}, {}
            join_lineage.extend(sub_matches)
            if len(sub_matches) != 1:
                why.append("missing_sub_join" if not sub_matches else "ambiguous_sub_join")
            else:
                sub = sub_matches[0]["record"]
                ref_matches = refs.get(txt(sub.get("ReferenceSubstance.ReferenceSubstance")), [])
                join_lineage.extend(ref_matches)
                if len(ref_matches) != 1:
                    why.append("missing_reference_join" if not ref_matches else "ambiguous_reference_join")
                else:
                    ref = ref_matches[0]["record"]
            blocks = effect_blocks(raw)
            value, relation, unit, endpoint = "", "", "", ""
            if len(blocks) != 1:
                why.append("missing_effect_block" if not blocks else "multiple_effect_blocks")
            else:
                value, relation, reasons = oft_point(blocks[0])
                why.extend(reasons)
                unit, endpoint = txt(blocks[0]["unit"]), txt(blocks[0]["endpoint"])
                if unit == "other:":
                    unit = txt(blocks[0]["unit_other"])
                    if not unit:
                        why.append("missing_native_unit_other_detail")
            row = {"record_id": txt(raw.get("Document UUID")), "species": txt(raw.get("MaterialsAndMethods.TestAnimals.Species")),
                   "species_other": txt(raw.get("MaterialsAndMethods.TestAnimals.Species.Other")),
                   "route": txt(raw.get("MaterialsAndMethods.AdministrationExposure.RouteOfAdministration")),
                   "administrative_endpoint": txt(raw.get("AdministrativeData.Endpoint")),
                   "endpoint": endpoint, "value": value, "unit": unit, "relation": relation,
                   "native_unit_parent": txt(blocks[0]["unit"]) if len(blocks) == 1 else "",
                   "native_unit_other": txt(blocks[0]["unit_other"]) if len(blocks) == 1 else "",
                   "normalized_relation": RELATIONS.get(relation, "unknown"), "effect_blocks": blocks,
                   "cas": txt(ref.get("Inventory.CASNumber")), "dtxsid": txt(ref.get("US EPA DTXSID")), "npid": "",
                   "smiles": txt(ref.get("MolecularStructuralInfo.SmilesNotation")),
                   "inchi": txt(ref.get("MolecularStructuralInfo.InChl")),
                   "sex": txt(raw.get("MaterialsAndMethods.TestAnimals.Sex")),
                   "test_material": txt(sub.get("TypeOfSubstance.Composition")),
                   "reference": txt(raw.get("DataSource.Reference")), "source_url": "",
                   "native_record": raw, "native_lineage": {"path": str(Path(path).resolve()),
                   "sheet": name, "native_row": number, "joins": join_lineage},
                   "status": "quarantined" if why else "candidate", "why": why}
            yield row
    finally:
        book.close()
