"""Source-bound HSDB adjudication. Review receipts are local leader attestations.

Inventory export is diagnostic, not collection or scientific acceptance. JSONL
order is immaterial; IDs bind original archive bytes and exact native locators.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path


class AdjudicationError(ValueError):
    pass


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def archive_module():
    spec = importlib.util.spec_from_file_location("adjudication_archive", Path(__file__).with_name("qsar_archive_sources.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise AdjudicationError("Missing or unknown keys; expected " + str(sorted(required)))


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdjudicationError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def jsonl(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                raise AdjudicationError("Blank JSONL record")
            yield json.loads(line, object_pairs_hook=unique_pairs)


def native_fields(path, source_id, source_sha256):
    adapter = archive_module()
    for doc in adapter.iter_hsdb_documents(path):
        counts = Counter()
        fields = doc["native_record"]["fields"]
        if not fields:
            raise AdjudicationError("Empty native document cannot be evidence-accounted")
        document = []
        for position, field in enumerate(fields, 1):
            counts[field["tag"]] += 1
            locator = {k: doc["native_lineage"][k] for k in ("archive_member", "document_ordinal")}
            locator.update(docno=doc["record_id"], field_ordinal=position,
                           tag=field["tag"], tag_ordinal=counts[field["tag"]])
            text_hash = hashlib.sha256(field["text"].encode()).hexdigest()
            field_id = stable([source_id, source_sha256, locator, text_hash])
            document.append({"schema_version": 1, "field_id": field_id,
                             "source_id": source_id, "source_sha256": source_sha256,
                             "locator": locator, "text_sha256": text_hash,
                             "native_record": field,
                             "candidate_kind": "narrative_field" if field["tag"] == "ntxv" or adapter.LD50.search(field["text"]) else "context_only",
                             "parser": adapter.parse_hsdb_narrative(field["text"])})
        if document and not any(f["candidate_kind"] == "narrative_field" for f in document):
            next((f for f in document if f["locator"]["tag"] == "DOCNO"), document[0])["candidate_kind"] = "document_disposition"
        yield document


def export_inventory(source, source_id, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    before, initial_code = digest(source), code_hashes()
    count, documents, statuses, kinds = 0, 0, Counter(), Counter()
    with (destination / "inventory.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for document in native_fields(source, source_id, before):
            documents += 1
            for field in document:
                if field["candidate_kind"] == "context_only":
                    continue
                field = dict(field, document_context=[f for f in document if f["locator"]["tag"] in {"DOCNO", "NameOfSubstance", "CASRegistryNumber", "DateRevised"}])
                stream.write(json.dumps(field, ensure_ascii=False) + "\n")
                count += 1
                statuses[field["parser"]["status"]] += 1
                kinds[field["candidate_kind"]] += 1
    if digest(source) != before or code_hashes() != initial_code:
        raise AdjudicationError("Source or code changed during inventory export")
    queue_path = destination / "queue.jsonl"
    with queue_path.open("x", encoding="utf-8", newline="\n") as queue:
        for field in jsonl(destination / "inventory.jsonl"):
            compact = {key: field[key] for key in ("field_id", "source_id", "source_sha256", "locator", "text_sha256", "candidate_kind", "parser")}
            compact["text"] = field["native_record"]["text"]
            compact["identity_context"] = [{"field_id": f["field_id"], "locator": f["locator"],
                                            "text": f["native_record"]["text"]} for f in field["document_context"]
                                           if f["locator"]["tag"] in {"CASRegistryNumber", "NameOfSubstance"}]
            queue.write(json.dumps(compact, ensure_ascii=False) + "\n")
    manifest = {"schema_version": 1, "status": "diagnostic_unreviewed",
                "scope": "every ntxv and other LD50 field; no rat/route filter; no-candidate documents accounted",
                "source_id": source_id, "source_sha256": before, "fields": count,
                "documents": documents, "entry_kinds": dict(kinds),
                "parser_status_counts": dict(statuses),
                "inventory_sha256": digest(destination / "inventory.jsonl"),
                "queue_sha256": digest(queue_path),
                "code_hashes": code_hashes(),
                "limitation": "ElementTree serialization is not original XML bytes; archive hash binds originals"}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def code_hashes():
    return {name: digest(Path(__file__).with_name(name)) for name in
            ("qsar_adjudication.py", "qsar_archive_sources.py", "qsar_data.py")}


def policy_hash(policy):
    copied = json.loads(json.dumps(policy))
    for source in copied.get("sources", []):
        source.pop("narrative_adjudication", None)
    return stable(copied)


def bound(descriptor):
    keys(descriptor, ("path", "sha256"))
    path = Path(descriptor["path"])
    if not path.is_absolute() or digest(path) != descriptor["sha256"]:
        raise AdjudicationError("Stale or nonabsolute adjudication artifact: " + str(path))
    return path


def span(evidence, document):
    keys(evidence, ("field_id", "start", "end", "quote"))
    field = document.get(evidence["field_id"])
    start, end = evidence["start"], evidence["end"]
    if field is None or type(start) is not int or type(end) is not int:
        raise AdjudicationError("Unknown evidence field or invalid span")
    text = field["native_record"]["text"]
    if not 0 <= start < end <= len(text) or text[start:end] != evidence["quote"]:
        raise AdjudicationError("Invalid evidence span or quote mismatch")
    return evidence["quote"]


SCIENTIFIC = ("endpoint", "species", "route", "value", "unit", "relation", "sex",
              "strain", "reference", "test_material", "dose_basis", "study_id")


def bibliography_region(rule, field, document, values, interval):
    """Validate an explicitly reviewed terminal citation region, not its semantics."""
    import re
    keys(rule, ("evidence", "rationale"))
    evidence = rule["evidence"]
    quote = span(evidence, document)
    if (not isinstance(rule["rationale"], str) or not rule["rationale"].strip()
            or evidence != values["reference"] or evidence["field_id"] != field["field_id"]):
        raise AdjudicationError("Bibliographic region requires matching same-field reference evidence and rationale")
    text = field["native_record"]["text"]
    # A complete first outer bracket block, after the measured variables, with
    # no trailing prose. Brackets alone do not prove bibliographic meaning.
    if not quote.startswith("[") or not quote.endswith("]") or "[" in text[:evidence["start"]]:
        raise AdjudicationError("Bibliographic region requires whole outer bracket boundaries")
    depth = 0
    for index, char in enumerate(quote):
        depth += (char == "[") - (char == "]")
        if depth < 0 or (depth == 0 and index != len(quote) - 1):
            raise AdjudicationError("Bibliographic region has invalid bracket boundaries")
    if depth or not re.fullmatch(r"\s*(?:\*\*(?:PEER|QC) REVIEWED\*\*)?\s*", text[evidence["end"]:]):
        raise AdjudicationError("Bibliographic region must be terminal, apart from a source review marker")
    scientific = [value for name, value in values.items() if name not in {"reference", "study_id"} and value is not None]
    if interval is not None:
        scientific.extend(interval.values())
    for item in scientific:
        if item["field_id"] == field["field_id"] and item["end"] > evidence["start"]:
            raise AdjudicationError("Bibliographic region must follow all source measurement spans")
    return evidence


def reject_composite_point(text, evidence):
    """Reject components of negative, range and power expressions, not infer meaning."""
    import re
    number = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][+\-\u2212]?\d+)?"
    dash = r"[\-\u2010\u2011\u2012\u2013\u2014\u2212]"
    expressions = (
        rf"(?<![\w.]){dash}\s*{number}",
        rf"{number}\s*{dash}\s*{number}",
        rf"(?:{number}\s*[xX\u00d7\u00b7*]\s*)?10\s*(?:\^|\*\*)\s*[+\-\u2212]?\s*\d+",
    )
    for expression in expressions:
        for match in re.finditer(expression, text):
            if max(evidence["start"], match.start()) < min(evidence["end"], match.end()):
                raise AdjudicationError("Point overlaps a negative, range or composite-power expression")


def observation(observation, field, document, units, tasks=()):
    keys(observation, ("fields", "chemical_attribution", "confidence_interval", "dependence"),
         ("unit_normalization", "field_normalizations"))
    values = observation["fields"]
    keys(values, SCIENTIFIC)
    mapped = {}
    for name, evidence in values.items():
        mapped[name] = "" if evidence is None else span(evidence, document)
    if any(not mapped[name] for name in ("endpoint", "species", "route", "value", "unit")):
        raise AdjudicationError("Observation requires source-bound point, endpoint, species, route and unit")
    if values["value"]["field_id"] != field["field_id"]:
        raise AdjudicationError("Point evidence must belong to the decided field")
    # Only formatting normalization, never extracting a bound from a range.
    import math
    import re
    point = mapped["value"]
    point_span = values["value"]
    source_text = field["native_record"]["text"]
    # Same-length lexical normalization keeps evidence offsets/quotes intact.
    # It does not normalize the selected value or silently accept new spellings.
    lexical_text = source_text.translate(str.maketrans({char: "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2212"}))
    reject_composite_point(lexical_text, point_span)
    before, after = lexical_text[:point_span["start"]], lexical_text[point_span["end"]:]
    if (re.search(r"[\w.]$|\d,$|\d[eE][+-]$", before)
            or re.match(r"\d|[.,]\d|[eE][+-]?\d", after)
            or (values["endpoint"]["field_id"] == field["field_id"]
                and max(point_span["start"], values["endpoint"]["start"]) < min(point_span["end"], values["endpoint"]["end"]))):
        raise AdjudicationError("Point evidence is an embedded or partial numeric token")
    if not re.fullmatch(r"(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?(?:[eE][+-]?\d+)?", point):
        raise AdjudicationError("Point must be a scalar, not an interval")
    numeric = float(point.replace(",", ""))
    if not math.isfinite(numeric) or numeric <= 0:
        raise AdjudicationError("Invalid positive point")
    mapped["value"] = point.replace(",", "")
    attribution = observation["chemical_attribution"]
    keys(attribution, ("cas", "subject", "context", "rationale"), ("mode", "bibliographic_reference"))
    cas = span(attribution["cas"], document)
    span(attribution["subject"], document)
    span(attribution["context"], document)
    if not isinstance(attribution["rationale"], str) or not attribution["rationale"].strip():
        raise AdjudicationError("Chemical attribution requires an explicit review rationale")
    mode = attribution.get("mode", "explicit_narrative")
    if "bibliographic_reference" in attribution and mode != "document_subject":
        raise AdjudicationError("Bibliographic CAS region is only supported for explicit document-subject review")
    if mode == "explicit_narrative":
        if attribution["subject"]["field_id"] != field["field_id"]:
            raise AdjudicationError("Chemical subject must be explicitly evidenced in the narrative")
    elif mode == "document_subject":
        for key, tag in (("subject", "NameOfSubstance"), ("cas", "CASRegistryNumber")):
            evidence = attribution[key]
            native = document[evidence["field_id"]]
            if native["locator"]["tag"] != tag or evidence["quote"] != native["native_record"]["text"].strip():
                raise AdjudicationError("Document subject requires full native name and CAS evidence")
        context, point_span = attribution["context"], values["value"]
        if (context["field_id"] != field["field_id"]
                or not context["start"] <= point_span["start"] < point_span["end"] <= context["end"]):
            raise AdjudicationError("Document subject requires decided narrative context containing the point")
        document_cas = {f["native_record"]["text"].strip() for f in document.values()
                        if f["locator"]["tag"] == "CASRegistryNumber" and f["native_record"]["text"].strip()}
        measurement_text = source_text
        if "bibliographic_reference" in attribution:
            region = bibliography_region(attribution["bibliographic_reference"], field, document,
                                         values, observation["confidence_interval"])
            measurement_text = source_text[:region["start"]] + source_text[region["end"]:]
        narrative_cas = set(re.findall(r"\b\d{2,7}-\d{2}-\d\b", measurement_text))
        if document_cas != {cas} or narrative_cas - {cas}:
            raise AdjudicationError("Document-subject CAS conflict; explicit narrative attribution required")
        # These are structural checks of a reviewed assertion, not semantic proof
        # that a narrative concerns this chemical rather than a comparator.
    else:
        raise AdjudicationError("Unknown chemical attribution mode")
    # CAS itself must occur verbatim in this document. No replacement structures.
    mapped.update(cas=cas, smiles="", inchi="", dtxsid="", npid="", source_url="")
    interval = observation["confidence_interval"]
    if interval is not None:
        keys(interval, ("lower", "upper", "context"))
        for evidence in interval.values():
            span(evidence, document)
        lower, upper = (float(span(interval[k], document).replace(",", "")) for k in ("lower", "upper"))
        if not math.isfinite(lower) or not math.isfinite(upper) or not 0 < lower <= numeric <= upper:
            raise AdjudicationError("Invalid confidence bounds around point")
    dependence = observation["dependence"]
    keys(dependence, ("status", "evidence"))
    if dependence["status"] not in {"unknown", "shared_study", "independent_report"}:
        raise AdjudicationError("Unknown study dependence status")
    if dependence["evidence"] is not None:
        span(dependence["evidence"], document)
    elif dependence["status"] != "unknown":
        raise AdjudicationError("Study dependence claim requires evidence")
    normalization = observation.get("unit_normalization")
    if normalization is not None:
        keys(normalization, ("unit", "evidence", "rationale"))
        span(normalization["evidence"], document)
        if normalization["unit"] not in units or not normalization["rationale"]:
            raise AdjudicationError("Unsupported or unevidenced unit normalization")
        if mapped["unit"] in units and mapped["unit"] != normalization["unit"]:
            raise AdjudicationError("Cannot redefine supported native unit")
        mapped["adjudicated_unit"] = normalization["unit"]
    normalizations = observation.get("field_normalizations", {})
    keys(normalizations, (), ("species", "route", "endpoint"))
    projected = {}
    for name, normalization in normalizations.items():
        keys(normalization, ("value", "evidence", "rationale"))
        evidence, original = normalization["evidence"], values[name]
        span(evidence, document)
        target = normalization["value"]
        if (not isinstance(target, str) or target not in {task[name] for task in tasks}
                or not isinstance(normalization["rationale"], str) or not normalization["rationale"].strip()):
            raise AdjudicationError("Field normalization requires an accepted task value and review rationale")
        if (evidence["field_id"] != original["field_id"]
                or not evidence["start"] <= original["start"] < original["end"] <= evidence["end"]):
            raise AdjudicationError("Field normalization evidence must contain the original field span")
        projected[name] = target
    if projected:
        mapped["adjudicated_fields"] = projected
    # Shared written doses may describe distinct explicit populations. Only
    # scientific source spans distinguish them; cosmetic review metadata does not.
    identity = [field["field_id"], {key: values[key] for key in
                ("value", "endpoint", "species", "route", "sex", "strain", "study_id", "test_material", "dose_basis")},
                attribution["subject"], attribution["cas"]]
    mapped.update(record_id=stable(identity), status="candidate", why=[],
                  native_record=field["native_record"], native_lineage=field["locator"],
                  source_record_kind="adjudicated_observation", adjudication=observation,
                  native_value=point, field_id=field["field_id"])
    return mapped


class ObservationMetadata:
    """Finite, reviewed HSDB packet; provenance only, never a target transform."""

    FILES = ('source', 'decisions', 'scope_decisions', 'metadata', 'source_metadata',
             'links', 'source_review', 'integration_manifest', 'integration_review')
    REQUIRED = ('observation_id', 'field_id', 'population_anchor', 'value_anchor',
                'native_value', 'native_unit', 'native_modifier', 'modifier_semantics',
                'normalized_mg_kg', 'sex', 'strain', 'exposure_duration', 'study_identity',
                'study_independence', 'same_report_group', 'reported_observation_not_unique_study')

    def __init__(self, descriptor, config, entry, units, unit_aliases=None):
        import os
        import re
        self._refs = {}
        self._verified = set()

        def register(item):
            keys(item, ('path', 'sha256'))
            if (not isinstance(item['path'], str) or not Path(item['path']).is_absolute()
                    or not isinstance(item['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                raise AdjudicationError('Invalid metadata dependency binding')
            path = str(Path(item['path']).resolve())
            normalized = os.path.normcase(path)
            canonical = dict(path=path, sha256=item['sha256'])
            previous = self._refs.get(normalized)
            if previous and previous['sha256'] != canonical['sha256']:
                raise AdjudicationError('Conflicting metadata dependency digests')
            self._refs[normalized] = previous or canonical
            if normalized not in self._verified:
                if not Path(path).is_file() or digest(path) != item['sha256']:
                    raise AdjudicationError('Stale metadata dependency: ' + path)
                self._verified.add(normalized)
            return normalized

        self.binding = dict(descriptor)
        register(descriptor)
        manifest = read_json(descriptor['path'])
        keys(manifest, ('schema_version', 'kind', 'source_id', 'scope', *self.FILES))
        if (type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['kind'] != 'hsdb_observation_metadata'
                or manifest['source_id'] != entry['source_id']):
            raise AdjudicationError('Metadata source/schema mismatch')
        named = {name: register(manifest[name]) for name in self.FILES}
        def equal(left, right):
            return register(left) == register(right) and left['sha256'] == right['sha256']
        if not equal(manifest['source'], {k: entry[k] for k in ('path', 'sha256')}) or not equal(
                manifest['decisions'], config['decisions']):
            raise AdjudicationError('Metadata must bind live source and effective decisions')
        s, i, r = (read_json(manifest[k]['path']) for k in
                   ('source_review', 'integration_manifest', 'integration_review'))
        for receipt, stage in ((s, 'curation_adjudication'), (r, 'bounded_candidate_integration')):
            if (type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1
                    or receipt.get('stage') != stage or receipt.get('verdict') != 'PASS'
                    or receipt.get('bounded_decision') != 'APPROVE'
                    or any(not isinstance(receipt.get(k), str) or not receipt[k].strip() for k in
                           ('producer_execution_id', 'reviewer_execution_id', 'native_returned_review_locator',
                            'run_id', 'contract_version'))
                    or receipt['producer_execution_id'].strip() == receipt['reviewer_execution_id'].strip()):
                raise AdjudicationError('Metadata requires bounded independent review evidence')
        if any(s[k] != r[k] for k in ('run_id', 'contract_version')):
            raise AdjudicationError('Metadata review scope mismatch')

        def expand(obj, name):
            mapping = obj.get(name)
            if not isinstance(mapping, dict) or not mapping:
                raise AdjudicationError('Missing metadata dependency map: ' + name)
            result = set()
            for path, sha in mapping.items():
                normalized = register(dict(path=path, sha256=sha))
                if normalized in result:
                    raise AdjudicationError('Metadata dependency map path alias')
                result.add(normalized)
            return result
        si, sa = (expand(s, k) for k in ('input_output_bindings', 'review_artifact_bindings'))
        ii, io = (expand(i, k) for k in ('input_bindings', 'output_bindings'))
        rc, ri, ra = (expand(r, k) for k in ('candidate_bindings', 'input_bindings', 'review_artifact_bindings'))
        if 'append_only_erratum' in r:
            register(r['append_only_erratum'])
        requirements = [(si, ('source', 'scope_decisions', 'source_metadata')),
                        (ri, ('source', 'scope_decisions', 'source_metadata', 'source_review')),
                        (ii, ('scope_decisions', 'source_metadata', 'source_review')),
                        (rc, ('integration_manifest', 'decisions', 'metadata', 'links')),
                        (io, ('decisions', 'metadata', 'links'))]
        if (any(not {named[k] for k in names} <= mapping for mapping, names in requirements)
                or not sa <= ri or not ii <= ri or not io <= rc
                or manifest['metadata']['sha256'] != manifest['source_metadata']['sha256']):
            raise AdjudicationError('Metadata reviewed packet containment mismatch')
        self.references = sorted(self._refs.values(), key=lambda item: item['path'])
        self.entry, self.units = entry, units
        self.unit_aliases = unit_aliases or {}
        for alias, rule in self.unit_aliases.items():
            if (not isinstance(rule, dict) or rule.get('unit') not in units or not rule.get('evidence')
                    or (alias in units and alias != rule['unit'])):
                raise AdjudicationError('Metadata unit alias lacks accepted conversion evidence')
        keys(manifest['scope'], ('field_ids', 'observation_ids'))
        for values in manifest['scope'].values():
            if (not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values)
                    or values != sorted(set(values))):
                raise AdjudicationError('Metadata scope requires sorted unique IDs')
        self.scope = manifest['scope']
        def indexed(name, identifier):
            result = {}
            for row in jsonl(manifest[name]['path']):
                if not isinstance(row, dict) or not isinstance(row.get(identifier), str) or row[identifier] in result:
                    raise AdjudicationError('Duplicate or invalid metadata packet row: ' + name)
                result[row[identifier]] = row
            return result
        decisions = indexed('decisions', 'field_id')
        scoped = indexed('scope_decisions', 'field_id')
        self.metadata = indexed('source_metadata', 'observation_id')
        self.links = indexed('links', 'observation_id')
        if (self.metadata != indexed('metadata', 'observation_id')
                or sorted(scoped) != self.scope['field_ids']
                or sorted(self.metadata) != self.scope['observation_ids']
                or self.links.keys() != self.metadata.keys()):
            raise AdjudicationError('Metadata scope or original/copy membership mismatch')
        self.by_anchor = {}
        expected = set()
        for fid, decision in scoped.items():
            keys(decision, ('field_id', 'action', 'reason', 'evidence', 'observations'))
            if decision != decisions.get(fid):
                raise AdjudicationError('Scoped metadata decision differs from effective decision')
            for ordinal, proposal in enumerate(decision['observations'], 1):
                fields = proposal['fields']
                oid = stable([entry['source_id'], entry['sha256'], fid, fields['value'], fields['sex']])
                meta, link = self.metadata.get(oid), self.links.get(oid)
                if meta is None or link is None or oid in expected:
                    raise AdjudicationError('Missing or duplicate scoped metadata observation')
                keys(meta, self.REQUIRED, ('native_condition', 'condition_kind', 'confidence_interval'))
                keys(link, ('field_id', 'observation_ordinal', 'observation_id', 'decision_observation',
                            'observation_metadata', 'scope'))
                if (link['field_id'] != fid or type(link['observation_ordinal']) is not int
                        or link['observation_ordinal'] != ordinal or link['decision_observation'] != proposal
                        or link['observation_metadata'] != meta or not isinstance(link['scope'], str)
                        or not link['scope'].strip() or meta['field_id'] != fid
                        or meta['value_anchor'] != fields['value'] or meta['population_anchor'] != fields['sex']):
                    raise AdjudicationError('Metadata full source-span link mismatch')
                anchor = stable([fid, fields['value'], fields['sex']])
                if anchor in self.by_anchor:
                    raise AdjudicationError('Duplicate metadata source anchors')
                self.by_anchor[anchor] = oid
                expected.add(oid)
        if expected != self.metadata.keys():
            raise AdjudicationError('Orphan metadata observation')
        self.consumed = set()

    def attach(self, row, document):
        if row.get('field_id') not in self.scope['field_ids']:
            return row
        proposal = row.get('adjudication', {})
        fields = proposal.get('fields', {})
        oid = self.by_anchor.get(stable([row['field_id'], fields.get('value'), fields.get('sex')]))
        if oid is None or oid in self.consumed or proposal != self.links[oid]['decision_observation']:
            raise AdjudicationError('Unknown, duplicate or altered metadata observation emission')
        meta = self.metadata[oid]
        for name, field in (('native_value', 'value'), ('native_unit', 'unit'), ('native_modifier', 'relation'),
                            ('sex', 'sex'), ('strain', 'strain'), ('study_identity', 'study_id')):
            expected = span(fields[field], document) if fields[field] is not None else None
            if meta[name] != expected:
                raise AdjudicationError('Metadata native property mismatch: ' + name)
        for name in ('value_anchor', 'population_anchor'):
            if meta[name] is not None:
                span(meta[name], document)
        if (('native_condition' in meta) != ('condition_kind' in meta)
                or ('condition_kind' in meta and meta['condition_kind'] not in ('vehicle', 'observation_follow_up'))):
            raise AdjudicationError('Invalid metadata condition pair')
        if 'native_condition' in meta:
            span(meta['native_condition'], document)
        if meta.get('confidence_interval') != proposal['confidence_interval']:
            raise AdjudicationError('Metadata confidence interval mismatch')
        reference = span(fields['reference'], document) if fields['reference'] is not None else None
        expected_group = stable([self.entry['source_id'], document[row['field_id']]['locator']['docno'], reference])
        if (meta['same_report_group'] != expected_group or meta['study_independence'] != proposal['dependence']['status']
                or meta['reported_observation_not_unique_study'] is not True
                or meta['exposure_duration'] is not None
                or meta['modifier_semantics'] != 'reported_numeric_estimate_not_proven_exact'):
            raise AdjudicationError('Metadata reporting/independence semantics mismatch')
        # Same accepted conversion used by ordinary curation; no metadata target is substituted.
        unit = row.get('adjudicated_unit', row['unit'])
        if unit in self.unit_aliases:
            unit = self.unit_aliases[unit]['unit']
        conversion = self.units.get(unit)
        if (conversion is None or conversion[0] != 'mass' or isinstance(meta['normalized_mg_kg'], bool)
                or meta['normalized_mg_kg'] != float(row['value']) * conversion[1]):
            raise AdjudicationError('Metadata normalized value differs from native conversion')
        self.consumed.add(oid)
        row['observation_metadata'] = dict(schema_version=1, observation_id=oid,
                                          manifest=self.binding, metadata=meta)
        return row

    def finish(self):
        if self.consumed != self.metadata.keys():
            raise AdjudicationError('Metadata observation coverage incomplete')
        for item in self.references:
            bound(item)

    def accounting(self, dispositions):
        rows = {}
        for row in dispositions:
            if row['source_id'] != self.entry['source_id'] or not row.get('observation_metadata'):
                continue
            projection = row['observation_metadata']
            oid = projection['observation_id']
            if oid in rows or projection != dict(schema_version=1, observation_id=oid,
                    manifest=self.binding, metadata=self.metadata.get(oid)):
                raise AdjudicationError('Metadata disposition projection mismatch')
            rows[oid] = dict(row_id=row['row_id'], output_row_ids=sorted(row['output_row_ids']))
        if rows.keys() != self.consumed or self.consumed != self.metadata.keys():
            raise AdjudicationError('Metadata disposition accounting incomplete')
        return dict(manifest=self.binding, required_observation_ids=sorted(self.metadata),
                    consumed_observation_ids=sorted(rows), projections={k: rows[k] for k in sorted(rows)},
                    report=dict(reported_observations=len(rows),
                                report_groups=len({m['same_report_group'] for m in self.metadata.values()}),
                                unknown_independence=sum(m['study_independence'] == 'unknown' for m in self.metadata.values()),
                                output_rows=sum(len(p['output_row_ids']) for p in rows.values()),
                                scope='Reviewed packet only; report groups are not independent experiments'),
                    references=self.references)


class Adjudication:
    def __init__(self, config, policy, entry, units, ledger_path):
        keys(config, ("inventory", "decisions", "review_receipt"))
        self.config, self.entry, self.units, self.tasks = config, entry, units, policy["tasks"]
        self.paths = {name: bound(value) for name, value in config.items()}
        receipt = read_json(self.paths["review_receipt"])
        keys(receipt, ("schema_version", "status", "attestation", "producer_execution_id", "reviewer_execution_id",
                       "receipt_writer", "review_evidence", "source_sha256", "inventory_sha256", "decisions_sha256",
                       "config_sha256", "policy_sha256", "code_hashes", "review_scope", "findings"))
        if (receipt["schema_version"] != 1 or receipt["status"] != "PASS"
                or receipt["attestation"] != "local_leader_attested_not_authenticated"
                or any(not isinstance(receipt[k], str) or not receipt[k].strip()
                       for k in ("producer_execution_id", "reviewer_execution_id", "receipt_writer"))
                or str(receipt["producer_execution_id"]).strip() == str(receipt["reviewer_execution_id"]).strip()
                or receipt["findings"] != []
                or receipt["review_scope"] != "all_fields_and_observations_including_exclusions"):
            raise AdjudicationError("Independent review receipt is incomplete or not PASS")
        bound(receipt["review_evidence"])
        if not Path(receipt["review_evidence"]["path"]).stat().st_size:
            raise AdjudicationError("Review evidence cannot be empty")
        expected = {"source_sha256": entry["sha256"], "inventory_sha256": config["inventory"]["sha256"],
                    "decisions_sha256": config["decisions"]["sha256"],
                    "config_sha256": stable({k: v for k, v in config.items() if k != "review_receipt"}),
                    "policy_sha256": policy_hash(policy), "code_hashes": code_hashes()}
        if any(receipt[k] != v for k, v in expected.items()):
            raise AdjudicationError("Stale review data/code/policy/config binding")
        self.review_evidence = receipt["review_evidence"]
        self.reviewed_code = receipt["code_hashes"]
        self.decisions = {}
        for decision in jsonl(self.paths["decisions"]):
            keys(decision, ("field_id", "action", "reason", "evidence", "observations"))
            if decision["field_id"] in self.decisions:
                raise AdjudicationError("Duplicate decision field key")
            self.decisions[decision["field_id"]] = decision
        self.inventory = {}
        for field in jsonl(self.paths["inventory"]):
            if field["field_id"] in self.inventory:
                raise AdjudicationError("Duplicate inventory field key")
            # Store compact fingerprints, not full native text in memory.
            self.inventory[field["field_id"]] = stable(field)
        if self.inventory.keys() != self.decisions.keys():
            raise AdjudicationError("Missing residual decisions or unknown field keys")
        self.ledger_path = Path(ledger_path)
        self.summary = {"fields": 0, "documents": 0, "observations": 0, "actions": {}, "entry_kinds": {},
                        "complete_narrative_accounting": False}

    def rows(self):
        entry, actions, kinds, seen_observations = self.entry, Counter(), Counter(), set()
        if digest(entry["path"]) != entry["sha256"]:
            raise AdjudicationError("Stale source")
        with self.ledger_path.open("w", encoding="utf-8", newline="\n") as ledger:
            for fields in native_fields(entry["path"], entry["source_id"], entry["sha256"]):
                self.summary["documents"] += 1
                document = {field["field_id"]: field for field in fields}
                for field in fields:
                    if field["candidate_kind"] == "context_only":
                        continue
                    field = dict(field, document_context=[f for f in fields if f["locator"]["tag"] in {"DOCNO", "NameOfSubstance", "CASRegistryNumber", "DateRevised"}])
                    field_id = field["field_id"]
                    if self.inventory.pop(field_id, None) != stable(field):
                        raise AdjudicationError("Stale/mutated native field inventory or duplicate field")
                    decision = self.decisions.pop(field_id)
                    action = decision["action"]
                    if action not in {"parser", "observations", "excluded", "unresolved"} or not decision["reason"]:
                        raise AdjudicationError("Unknown action or missing decision reason")
                    for evidence in decision["evidence"]:
                        span(evidence, document)
                    if not decision["evidence"]:
                        raise AdjudicationError("Every decision requires source evidence")
                    emitted = []
                    if action == "observations":
                        if not decision["observations"]:
                            raise AdjudicationError("Observation expansion cannot be empty")
                        emitted = [observation(o, field, document, self.units, self.tasks) for o in decision["observations"]]
                    elif decision["observations"]:
                        raise AdjudicationError("Parser/manual overlap or unused observations")
                    elif action == "parser":
                        if field["parser"]["status"] != "candidate":
                            raise AdjudicationError("Parser route requires conservative scalar candidate")
                        # Explicit reviewed chemical attribution is mandatory for expansion;
                        # untouched parser rows preserve the established native identity route.
                        cas_values = {f["native_record"]["text"].strip() for f in fields
                                      if f["locator"]["tag"] == "CASRegistryNumber" and f["native_record"]["text"].strip()}
                        row = dict(field["parser"], record_id=stable([field_id, "parser"]), field_id=field_id,
                                   native_record=field["native_record"], native_lineage=field["locator"],
                                   cas=next(iter(cas_values)) if len(cas_values) == 1 else "",
                                   smiles="", inchi="", dtxsid="", npid="", test_material="", source_url="",
                                   source_record_kind="reviewed_parser_observation", adjudication=decision)
                        if len(cas_values) != 1:
                            row.update(status="quarantined", why=["ambiguous_or_missing_native_cas"])
                        emitted = [row]
                    ids = [row["record_id"] for row in emitted]
                    if len(ids) != len(set(ids)) or set(ids) & seen_observations:
                        raise AdjudicationError("Duplicate observation extraction")
                    seen_observations.update(ids)
                    actions[action] += 1
                    kinds[field["candidate_kind"]] += 1
                    self.summary["fields"] += 1
                    self.summary["observations"] += len(ids)
                    ledger.write(json.dumps(dict(decision, observation_ids=ids), ensure_ascii=False) + "\n")
                    for row in emitted:
                        if getattr(self, 'metadata', None) is not None:
                            self.metadata.attach(row, document)
                        yield row
        if self.inventory or self.decisions:
            raise AdjudicationError("Unused or unknown field keys")
        for descriptor in self.config.values():
            bound(descriptor)
        bound(self.review_evidence)
        if code_hashes() != self.reviewed_code:
            raise AdjudicationError("Code mutated during adjudication")
        if digest(entry["path"]) != entry["sha256"]:
            raise AdjudicationError("Source mutated during adjudication")
        self.summary.update(actions=dict(actions), entry_kinds=dict(kinds), complete_narrative_accounting=not actions["unresolved"])


QUARANTINE_REASONS_V2 = {
    "critical_original_tested_identity_ambiguity",
    "irreconcilable_same_measurement_numeric_conflict",
    "irreconcilable_same_measurement_censor_conflict",
    "irreconcilable_same_measurement_dose_denominator_conflict",
    "irreconcilable_same_measurement_dose_basis_conflict",
    "unavailable_critical_metadata",
}
EXCLUSION_REASONS_V2 = {"explicit_inequality", "range_only", "incompatible_task", "prohibited_conversion", "nonexperimental_target"}
POPULATION_FIELDS_V2 = ('species', 'sex', 'strain', 'study_id')


def population_semantics_v2(scope):
    """Canonical stated population properties; evidence selection is not identity."""
    keys(scope, POPULATION_FIELDS_V2)
    result = {}
    for name, evidence in scope.items():
        value = None if evidence is None else ' '.join(evidence['quote'].casefold().split())
        if value in {'', '-1', '-', 'unknown', 'unspecified', 'not reported', 'not available', 'n/a', 'n.a.', 'n.a', 'na'}:
            value = None
        result[name] = value
    return result


def populations_disjoint_v2(left, right):
    """Unknown/general claims cannot establish nonoverlap with a specific group."""
    # Different raw species/strain/study strings may be aliases or shared lineage.
    # This bounded adapter has no accepted population-partition crosswalk.
    return {left['sex'], right['sex']} == {'male', 'female'}


def candidate_ids_v2(field, candidate):
    """Native anchors, not adjudication wording/order, identify an estimate."""
    base = [field['source_id'], field['source_sha256']]
    measurement = stable(base + [candidate['measurement_evidence']])
    sex = population_semantics_v2(candidate['population_scope'])['sex']
    partition = sex if sex in {'male', 'female'} else 'unspecified'
    population = stable(base + [measurement, partition])
    return dict(measurement_id=measurement, population_id=population,
                candidate_id=stable([measurement, population]))


def nonempty_v2(value):
    if not isinstance(value, str) or not value.strip():
        raise AdjudicationError('Expected nonempty review text')


def strings_v2(value):
    if not isinstance(value, list):
        raise AdjudicationError('Expected a review list')
    for item in value:
        nonempty_v2(item)


def review_identity_v2(receipt):
    for name in ('producer_execution_id', 'reviewer_execution_id', 'receipt_writer'):
        nonempty_v2(receipt[name])
    if (receipt['schema_version'] != 2 or receipt['status'] != 'PASS'
            or receipt['attestation'] != 'local_leader_attested_not_authenticated'
            or receipt['producer_execution_id'].strip() == receipt['reviewer_execution_id'].strip()
            or receipt['findings'] != []):
        raise AdjudicationError('Independent v2 review is not PASS')
    if not bound(receipt['review_evidence']).stat().st_size:
        raise AdjudicationError('Empty review evidence')


class AdjudicationV2:
    """Explicit opt-in adapter; structural evidence checks are not semantic proof.

    This interface is intentionally separate from the historical v1 caller.
    rows() must be exhausted before summary or output can be accepted.
    """
    def __init__(self, config, entry, full_contract, accepted_contract_review, units, ledger_path):
        keys(config, ('schema_version', 'inventory', 'decisions', 'review_receipt'))
        if config['schema_version'] != 2:
            raise AdjudicationError('Explicit v2 configuration required')
        self.config, self.entry, self.units = config, entry, units
        self.contract_binding, self.acceptance_binding = full_contract, accepted_contract_review
        contract = read_json(bound(full_contract))
        policy = contract.get('scientific_policy', {}).get('narrative_adjudication_v2')
        keys(policy, ('schema_version', 'clauses'))
        if policy['schema_version'] != 2 or not isinstance(policy['clauses'], dict):
            raise AdjudicationError('Contract does not authorize v2')
        self.clauses, self.tasks = policy['clauses'], contract['data_curation']['tasks']
        if sum(s.get('source_id') == entry['source_id'] for s in contract['data_curation']['sources']) != 1:
            raise AdjudicationError('Source must be declared exactly once in the full contract')
        for clause_id, clause in self.clauses.items():
            nonempty_v2(clause_id)
            keys(clause, ('disposition', 'reason'))
            permitted = {'scientific_quarantine': QUARANTINE_REASONS_V2, 'excluded': EXCLUSION_REASONS_V2}
            if clause['reason'] not in permitted.get(clause['disposition'], set()):
                raise AdjudicationError('Unsupported contract policy clause')
        acceptance = read_json(bound(accepted_contract_review))
        common = ('schema_version', 'status', 'attestation', 'producer_execution_id',
                  'reviewer_execution_id', 'receipt_writer', 'review_evidence', 'findings')
        keys(acceptance, common + ('stage', 'contract_sha256', 'candidate'))
        review_identity_v2(acceptance)
        candidate = read_json(bound(acceptance['candidate']))
        if (acceptance['stage'] != 'contract' or acceptance['contract_sha256'] != full_contract['sha256']
                or candidate.get('contract') != full_contract):
            raise AdjudicationError('Accepted full-contract candidate mismatch')
        self.paths = {k: bound(config[k]) for k in ('inventory', 'decisions', 'review_receipt')}
        receipt = read_json(self.paths['review_receipt'])
        expected = dict(contract_sha256=full_contract['sha256'], accepted_contract_review_sha256=accepted_contract_review['sha256'],
            source_id=entry['source_id'], source_sha256=entry['sha256'],
            inventory_sha256=config['inventory']['sha256'], decisions_sha256=config['decisions']['sha256'],
            config_sha256=stable({k: v for k, v in config.items() if k != 'review_receipt'}),
            code_hashes=code_hashes(), accepted_policy_clauses=self.clauses)
        keys(receipt, common + tuple(expected) + ('review_scope',))
        review_identity_v2(receipt)
        if any(receipt[k] != v for k, v in expected.items()):
            raise AdjudicationError('Stale v2 contract/source/code/config/policy binding')
        self.bindings = [full_contract, accepted_contract_review, acceptance['candidate'], acceptance['review_evidence'],
                         receipt['review_evidence']] + [config[k] for k in self.paths]
        self.reviewed_code = receipt['code_hashes']
        self.inventory, self.decisions = {}, {}
        for field in jsonl(self.paths['inventory']):
            if field['field_id'] in self.inventory:
                raise AdjudicationError('Duplicate inventory field')
            self.inventory[field['field_id']] = stable(field)
        scope = []
        for decision in jsonl(self.paths['decisions']):
            keys(decision, ('schema_version', 'field_id', 'action', 'reason', 'evidence', 'coverage', 'candidates'))
            if decision['schema_version'] != 2 or decision['action'] != 'candidate_dispositions':
                raise AdjudicationError('Unknown v2 field decision')
            if decision['field_id'] in self.decisions or not isinstance(decision['candidates'], list):
                raise AdjudicationError('Duplicate field or malformed candidates')
            keys(decision['coverage'], ('candidate_enumeration', 'examined_field_ids', 'remaining_coverage_issues', 'remaining_policy_issues', 'no_candidate_review'))
            self.decisions[decision['field_id']] = decision
            scope.append(dict(field_id=decision['field_id'], candidate_ids=sorted(c['candidate_id'] for c in decision['candidates']),
                              examined_field_ids=decision['coverage']['examined_field_ids']))
        if self.decisions.keys() != self.inventory.keys():
            raise AdjudicationError('Incomplete field accounting')
        if not isinstance(receipt['review_scope'], list) or sorted(receipt['review_scope'], key=lambda s: s['field_id']) != sorted(scope, key=lambda s: s['field_id']):
            raise AdjudicationError('Exact candidate/context review scope mismatch')
        self.ledger_path = Path(ledger_path)
        self.summary = dict(schema_version=2, field_accounting_complete=False, candidate_disposition_accounting_complete=False,
            source_review_complete=False, required_source_coverage_complete=False)

    def _evidence(self, evidence, document):
        if not isinstance(evidence, list) or not evidence:
            raise AdjudicationError('Source evidence required')
        for item in evidence:
            span(item, document)

    def _terminal(self, candidate, document, reviewed):
        clause = self.clauses.get(candidate['policy_clause_id'])
        if clause != dict(disposition=candidate['disposition'], reason=candidate['reason']):
            raise AdjudicationError('Unauthorized disposition reason/clause')
        if not reviewed:
            raise AdjudicationError('Terminal decision cannot close incomplete source review')
        review = candidate['review']
        if candidate['reason'] == 'unavailable_critical_metadata':
            keys(review, ('missing_property', 'indispensable_property', 'evidence', 'reviewed_assets', 'attempts',
                'competing_interpretations', 'remaining_uncertainty', 'reopening_trigger',
                'extraction_complete', 'context_complete', 'support_complete', 'linkage_complete'))
            if (review['missing_property'] not in {'route', 'indispensable_experiment_linkage'}
                    or review['indispensable_property'] not in {'route', 'acute_context', 'tested_material', 'target'}
                    or (review['missing_property'] == 'route' and review['indispensable_property'] != 'route')
                    or any(review[k] is not True for k in ('extraction_complete', 'context_complete', 'support_complete', 'linkage_complete'))):
                raise AdjudicationError('Unavailable metadata is not an exhaustively reviewed indispensable property')
            if not isinstance(review['reviewed_assets'], list) or not review['reviewed_assets']:
                raise AdjudicationError('Reviewed source assets required')
            for asset in review['reviewed_assets']:
                keys(asset, ('path', 'sha256', 'source_id'))
                descriptor = {k: asset[k] for k in ('path', 'sha256')}
                bound(descriptor)
                self.bindings.append(descriptor)
            native_asset = {k: self.entry[k] for k in ('path', 'sha256', 'source_id')}
            if native_asset not in review['reviewed_assets']:
                raise AdjudicationError('Native source absent from metadata review')
            for k in ('attempts', 'competing_interpretations'):
                strings_v2(review[k])
                if not review[k]:
                    raise AdjudicationError('Review paths and alternatives required')
            nonempty_v2(review['remaining_uncertainty'])
        elif candidate['disposition'] == 'scientific_quarantine':
            keys(review, ('evidence', 'alternatives', 'resolution', 'same_measurement_population', 'reopening_trigger'))
            if not isinstance(review['alternatives'], list):
                raise AdjudicationError('Source-supported alternatives required')
            interpretations = []
            for alternative in review['alternatives']:
                keys(alternative, ('interpretation', 'evidence'))
                nonempty_v2(alternative['interpretation'])
                self._evidence(alternative['evidence'], document)
                interpretations.append(alternative['interpretation'])
            if len(set(interpretations)) < 2 or review['resolution'] != 'irreconcilable' or review['same_measurement_population'] is not True:
                raise AdjudicationError('Critical conflict requires same-measurement alternatives')
        else:
            keys(review, ('evidence', 'rationale', 'reopening_trigger'))
            nonempty_v2(review['rationale'])
        self._evidence(review['evidence'], document)
        nonempty_v2(review['reopening_trigger'])

    def _origin(self, candidate, document):
        """Validate reviewed origin assertions; never infer origin from keywords."""
        origin = candidate.get('target_origin')
        prediction_exclusion = candidate['disposition'] == 'excluded' and candidate['reason'] == 'nonexperimental_target'
        if origin is None:
            if prediction_exclusion or 'target_origin' in candidate:
                raise AdjudicationError('Typed target origin required')
            return
        classification = origin.get('classification') if isinstance(origin, dict) else None
        experimental = classification == 'experimental'
        extra = ('experimental_basis_evidence', 'estimation_method', 'transformation') if experimental else ()
        keys(origin, ('classification', 'subtype', 'evidence', 'binding') + extra)
        self._evidence(origin['evidence'], document)
        binding = origin['binding']
        keys(binding, ('chemical', 'row', 'column', 'endpoint', 'species', 'route', 'value', 'unit', 'modifier'))
        for name, evidence in binding.items():
            if name in {'chemical', 'row', 'column', 'value'} or evidence is not None:
                span(evidence, document)
        point, row = candidate['measurement_evidence'], binding['row']
        if (binding['value'] != point or row['field_id'] != point['field_id']
                or not row['start'] <= point['start'] < point['end'] <= row['end']):
            raise AdjudicationError('Origin classification must bind the exact candidate row/value')
        if classification == 'nonexperimental':
            if origin['subtype'] not in {'qsar_prediction', 'read_across', 'model_prediction', 'computed_toxicity_prediction'} or not prediction_exclusion:
                raise AdjudicationError('Affirmative nonexperimental origin requires its authorized exclusion')
        elif classification in {'origin_unknown', 'origin_conflict'}:
            if origin['subtype'] is not None or candidate['disposition'] != 'unresolved':
                raise AdjudicationError('Unknown/conflicting target origin remains unresolved')
            if candidate['reason'] != classification or classification not in candidate['remaining_issues']:
                raise AdjudicationError('Origin uncertainty must remain separately recorded')
        elif experimental:
            if prediction_exclusion or origin['subtype'] not in {'reported_measurement', 'same_substance_mortality_estimate', 'validated_experimental_transform'}:
                raise AdjudicationError('Experimental origin cannot authorize prediction exclusion')
            self._evidence(origin['experimental_basis_evidence'], document)
            method, transformation = origin['estimation_method'], origin['transformation']
            if method not in {'none', 'probit', 'maximum_likelihood', 'up_and_down', 'other_statistical_mortality'} or transformation not in {'none', 'unit', 'molar', 'logarithmic'}:
                raise AdjudicationError('Unsupported experimental estimation/transform assertion')
            if ((origin['subtype'] == 'same_substance_mortality_estimate' and method == 'none')
                    or (origin['subtype'] == 'validated_experimental_transform' and transformation == 'none')
                    or (origin['subtype'] == 'reported_measurement' and (method != 'none' or transformation != 'none'))):
                raise AdjudicationError('Experimental origin subtype lacks its required basis')
        else:
            raise AdjudicationError('Unknown target-origin classification')
        if candidate['disposition'] == 'curation_candidate':
            obs = candidate['observation']
            expected = {k: obs['fields'][k] for k in ('endpoint', 'species', 'route', 'value', 'unit')}
            expected.update(modifier=obs['fields']['relation'], chemical=obs['chemical_attribution']['subject'])
            if any(binding[k] != v for k, v in expected.items()):
                raise AdjudicationError('Target-origin binding differs from emitted observation')

    def rows(self):
        if digest(self.entry['path']) != self.entry['sha256']:
            raise AdjudicationError('Stale source')
        counts, issues, seen, seen_observations = Counter(), Counter(), set(), set()
        origin_counts, exclusion_reasons = Counter(), Counter()
        counts.update({name: 0 for name in ('documents', 'source_fields', 'context_only_fields', 'fields', 'candidates',
            'observations', 'curation_candidate', 'scientific_quarantine', 'excluded', 'unresolved',
            'unreviewed_fields', 'unresolved_coverage_fields', 'unresolved_policy_cases')})
        with self.ledger_path.open('w', encoding='utf-8', newline='\n') as ledger:
            for fields in native_fields(self.entry['path'], self.entry['source_id'], self.entry['sha256']):
                document = {f['field_id']: f for f in fields}
                counts['documents'] += 1
                counts['source_fields'] += len(fields)
                counts['context_only_fields'] += sum(f['candidate_kind'] == 'context_only' for f in fields)
                for native in fields:
                    if native['candidate_kind'] == 'context_only':
                        continue
                    field = dict(native, document_context=[f for f in fields if f['locator']['tag'] in {'DOCNO', 'NameOfSubstance', 'CASRegistryNumber', 'DateRevised'}])
                    fid = field['field_id']
                    if self.inventory.pop(fid, None) != stable(field):
                        raise AdjudicationError('Native inventory mismatch')
                    decision = self.decisions.pop(fid)
                    nonempty_v2(decision['reason'])
                    self._evidence(decision['evidence'], document)
                    coverage = decision['coverage']
                    for k in ('examined_field_ids', 'remaining_coverage_issues', 'remaining_policy_issues'):
                        strings_v2(coverage[k])
                    examined = coverage['examined_field_ids']
                    if len(examined) != len(set(examined)) or set(examined) - document.keys():
                        raise AdjudicationError('Unknown/duplicate or cross-document context scope')
                    if coverage['candidate_enumeration'] not in {'complete', 'unresolved'}:
                        raise AdjudicationError('Unknown enumeration state')
                    reviewed = (set(examined) == document.keys() and coverage['candidate_enumeration'] == 'complete'
                                and not coverage['remaining_coverage_issues'] and not coverage['remaining_policy_issues'])
                    counts['unreviewed_fields'] += not reviewed
                    counts['unresolved_policy_cases'] += len(coverage['remaining_policy_issues'])
                    blocked = not reviewed
                    if not decision['candidates'] and field['parser']['status'] == 'candidate' and reviewed:
                        raise AdjudicationError('Empty enumeration hides a conservative parser candidate')
                    empty_review = coverage['no_candidate_review']
                    if decision['candidates'] and empty_review is not None:
                        raise AdjudicationError('No-candidate review contradicts enumerated candidates')
                    if not decision['candidates'] and reviewed:
                        keys(empty_review, ('conclusion', 'evidence', 'rationale'))
                        if empty_review['conclusion'] != 'no_measurement_candidates':
                            raise AdjudicationError('Explicit no-measurement review required')
                        self._evidence(empty_review['evidence'], document)
                        nonempty_v2(empty_review['rationale'])
                    emitted = []
                    population_measurements = []
                    for candidate in decision['candidates']:
                        disposition = candidate['disposition']
                        extra = {'curation_candidate': ('observation',), 'scientific_quarantine': ('policy_clause_id', 'review'),
                                 'excluded': ('policy_clause_id', 'review'), 'unresolved': ('remaining_issues',)}
                        if disposition not in extra:
                            raise AdjudicationError('Unknown candidate disposition')
                        keys(candidate, ('candidate_id', 'measurement_id', 'population_id', 'measurement_evidence',
                                         'population_evidence', 'population_scope', 'disposition', 'reason') + extra[disposition], ('target_origin',))
                        nonempty_v2(candidate['reason'])
                        self._origin(candidate, document)
                        if 'target_origin' in candidate:
                            origin = candidate['target_origin']
                            origin_counts[origin['classification'] + ':' + (origin['subtype'] or 'unspecified')] += 1
                        if disposition == 'excluded':
                            exclusion_reasons[candidate['reason']] += 1
                        for k in ('measurement_evidence', 'population_evidence'):
                            span(candidate[k], document)
                        population = population_semantics_v2(candidate['population_scope'])
                        for evidence in candidate['population_scope'].values():
                            if evidence is not None:
                                span(evidence, document)
                        if candidate['measurement_evidence']['field_id'] != fid:
                            raise AdjudicationError('Measurement anchor belongs to another field')
                        if any(candidate[k] != v for k, v in candidate_ids_v2(field, candidate).items()) or candidate['candidate_id'] in seen:
                            raise AdjudicationError('Invalid or duplicate stable candidate identity')
                        measurement = candidate['measurement_evidence']
                        for previous, previous_population in population_measurements:
                            if (max(measurement['start'], previous['start']) < min(measurement['end'], previous['end'])
                                    and not populations_disjoint_v2(population, previous_population)):
                                raise AdjudicationError('Overlapping measurement/population dispositions')
                        population_measurements.append((measurement, population))
                        seen.add(candidate['candidate_id'])
                        counts['candidates'] += 1
                        counts[disposition] += 1
                        if disposition == 'curation_candidate':
                            obs = candidate['observation']
                            observation_population = {k: obs['fields'][k] for k in POPULATION_FIELDS_V2}
                            if population != population_semantics_v2(observation_population):
                                raise AdjudicationError('Population scope differs from observation population')
                            if obs['fields']['value'] != candidate['measurement_evidence']:
                                raise AdjudicationError('Observation value differs from measurement anchor')
                            if candidate['population_evidence'] not in [obs['fields'][k] for k in ('species', 'sex', 'strain')]:
                                raise AdjudicationError('Population anchor differs from observation population')
                            row = observation(obs, field, document, self.units, self.tasks)
                            if row['record_id'] in seen_observations:
                                raise AdjudicationError('Duplicate observation under different population anchors')
                            seen_observations.add(row['record_id'])
                            row.update(candidate_ids_v2(field, candidate))
                            row.update(record_id=candidate['candidate_id'], source_record_kind='reviewed_candidate_v2')
                            if 'target_origin' in candidate:
                                row['target_origin'] = candidate['target_origin']
                            emitted.append(row)
                        elif disposition == 'unresolved':
                            strings_v2(candidate['remaining_issues'])
                            if not candidate['remaining_issues']:
                                raise AdjudicationError('Unresolved candidate requires remaining issues')
                            blocked = True
                        else:
                            self._terminal(candidate, document, reviewed)
                            if disposition == 'scientific_quarantine':
                                issues[candidate['reason']] += 1
                    counts['unresolved_coverage_fields'] += blocked
                    counts['fields'] += 1
                    counts['observations'] += len(emitted)
                    ledger.write(json.dumps(dict(decision, observation_ids=[r['record_id'] for r in emitted],
                        native_record=field['native_record'], native_lineage=field['locator'],
                        document_context=fields), ensure_ascii=False) + '\n')
                    for row in emitted:
                        if getattr(self, 'metadata', None) is not None:
                            self.metadata.attach(row, document)
                        yield row
        if self.inventory or self.decisions:
            raise AdjudicationError('Unused field decisions')
        for descriptor in self.bindings:
            bound(descriptor)
        if code_hashes() != self.reviewed_code or digest(self.entry['path']) != self.entry['sha256']:
            raise AdjudicationError('Code/source mutated during adjudication')
        self.summary.update(dict(counts), field_accounting_complete=True, candidate_disposition_accounting_complete=True,
            target_origin_counts=dict(origin_counts), exclusion_reason_counts=dict(exclusion_reasons),
            source_review_complete=not counts['unreviewed_fields'], unresolved_coverage_fields=counts['unresolved_coverage_fields'],
            unresolved_policy_cases=counts['unresolved_policy_cases'], scientific_quarantine_candidates=counts['scientific_quarantine'],
            scientific_issue_counts=dict(issues), required_source_coverage_complete=not counts['unresolved_coverage_fields'] and not counts['unresolved_policy_cases'])


ORDINARY_ADAPTERS = frozenset({'npass', 'ice_oral', 'oecd', 'toxric'})
ORDINARY_KIND = 'ordinary_source_adjudication_v1'
ORDINARY_FILES = ('full_contract', 'accepted_contract_review', 'decisions',
                  'context_inventory', 'bounded_reviews', 'known_evidence_notices')


def ordinary_code_bindings():
    """Fixed dependency closure; callers cannot substitute a smaller code set."""
    from qsar_verified_material import file_binding
    return sorted((file_binding(Path(__file__).with_name(name)) for name in (
        'qsar_adjudication.py', 'qsar_archive_sources.py', 'qsar_curation_evidence.py',
        'qsar_data.py', 'qsar_material_curation.py', 'qsar_native_sources.py',
        'qsar_verified_material.py')), key=lambda item: item['path'])


def ordinary_environment():
    import platform
    from importlib.metadata import version, PackageNotFoundError
    from rdkit import rdBase
    try:
        excel = version('openpyxl')
    except PackageNotFoundError:
        excel = None
    return dict(python_version=platform.python_version(), rdkit_version=rdBase.rdkitVersion,
                openpyxl_version=excel)


class OrdinaryAdjudication:
    """Finite ordinary-source evidence validation, independently replayable over U.

    Paths in bounded reviews target native record IDs, context IDs, or registry
    absolute file paths. Acceptance is a local leader attestation, not authentication.
    This class alone does not activate curation, material or runtime acceptance.
    """
    def __init__(self, binding, entry, contract, registry, work_dir):
        import qsar_material_curation as native
        import qsar_verified_material as material
        import qsar_data as data
        self.n, self.m = native, material
        self.binding, self.entry = binding, entry
        self.references, self.seen, self.used_reviews = [], set(), set()
        self.results, self.observations = {}, {}
        self._finished = False
        keys(binding, ('schema_version', 'kind', *ORDINARY_FILES, 'review'))
        self._require(type(binding['schema_version']) is int and binding['schema_version'] == 1
                      and binding['kind'] == ORDINARY_KIND, 'Unknown ordinary binding')
        self._require(binding['full_contract'] == material.file_binding(contract), 'Invocation contract mismatch')
        self.contract = self._load(binding['full_contract'])
        self.registry_binding = material.file_binding(registry)
        self.registry = self._load(self.registry_binding)
        configs = [s for s in self.contract['data_curation']['sources'] if s['source_id'] == entry['source_id']]
        self._require(len(configs) == 1, 'Source must occur exactly once in full contract')
        self.config = configs[0]
        self._require(self.config['adapter'] in ORDINARY_ADAPTERS, 'Unsupported ordinary adapter')
        self._require(self.config.get('release') is None or type(self.config['release']) is str, 'Invalid source release')
        self.source_files = sorted((dict(source_id=e['source_id'], path=e['path'], sha256=e['sha256'])
                                    for e in self.registry['files']), key=lambda e: e['source_id'])
        self._require(len({e['source_id'] for e in self.source_files}) == len(self.source_files), 'Duplicate registry source')
        self._require(dict(source_id=entry['source_id'], path=entry['path'], sha256=entry['sha256']) in self.source_files,
                      'Invocation source absent from registry')
        for file in self.source_files:
            self._file({k: file[k] for k in ('path', 'sha256')})
        policy = self.contract.get('scientific_policy', {}).get('narrative_adjudication_v2')
        keys(policy, ('schema_version', 'clauses'))
        self._require(type(policy['schema_version']) is int and policy['schema_version'] == 2
                      and type(policy['clauses']) is dict, 'Typed accepted scientific clauses required')
        self.clauses = policy['clauses']
        acceptance = self._load(binding['accepted_contract_review'])
        common = ('schema_version', 'status', 'attestation', 'producer_execution_id',
                  'reviewer_execution_id', 'receipt_writer', 'review_evidence', 'findings')
        keys(acceptance, (*common, 'stage', 'contract_sha256', 'candidate'))
        self._identity(acceptance)
        candidate = self._load(acceptance['candidate'])
        self._require(acceptance['stage'] == 'contract' and acceptance['contract_sha256'] == binding['full_contract']['sha256']
                      and candidate.get('contract') == binding['full_contract'], 'Accepted full-contract candidate mismatch')
        self.contexts = self._records(binding['context_inventory'], 'context_id')
        for context in self.contexts.values():
            keys(context, ('context_id', 'artifact', 'content', 'content_format', 'source_role', 'release', 'locator', 'extraction_code'))
            self._require(context['content_format'] in {'utf8', 'json'} and context['source_role'] in
                          {'primary', 'secondary', 'native_support', 'retrieval_log'}, 'Invalid context type')
            nonempty_v2(context['locator'])
            self._require(context['release'] is None or type(context['release']) is str, 'Invalid context release')
            self._file(context['artifact'])
            self._file(context['content'])
            if context['extraction_code'] is not None:
                self._file(context['extraction_code'])
        self.reviews = self._records(binding['bounded_reviews'], 'review_id')
        self.decisions = self._records(binding['decisions'], 'decision_id')
        self.by_member = {}
        for decision in self.decisions.values():
            keys(decision, ('schema_version', 'member_key', 'native_observation_sha256', 'native_bindings',
                            'target_origin', 'route', 'remaining_issues', 'decision_id'))
            self._require(type(decision['schema_version']) is int and decision['schema_version'] == 1, 'Invalid decision version')
            native.sha(decision['member_key'])
            native.sha(decision['native_observation_sha256'])
            self._require(decision['member_key'] not in self.by_member, 'Duplicate ordinary decision member')
            self.by_member[decision['member_key']] = decision
        sheet = self.config.get('sheet', 'Data' if self.config['adapter'].startswith('ice_') else '')
        for number, raw in data.rows(entry['path'], sheet, self.config.get('header_row', 1), native_index=True):
            obs = native.observation(raw, self.config, entry, number, sheet)
            member = native.member_key(obs)
            self._require(member not in self.observations, 'Duplicate physical ordinary member')
            self.observations[member] = obs
        self._require(self.by_member.keys() == self.observations.keys(), 'Decisions must equal complete native U')
        self.scope = self._scope()
        for member, obs in self.observations.items():
            self.results[member] = self._decision(self.by_member[member], obs)
        self._require(self.used_reviews == self.reviews.keys(), 'Unconsumed bounded review')
        self._notices(self._load(binding['known_evidence_notices']))
        self.code, self.environment = ordinary_code_bindings(), ordinary_environment()
        for file in self.code:
            self._file(file)
        self.context = dict(full_contract=binding['full_contract'], accepted_contract_review=binding['accepted_contract_review'],
            source_registry=self.registry_binding, source_id=entry['source_id'], adapter=self.config['adapter'],
            source_release=self.config.get('release'), source_configuration_sha256=material.digest(self.config),
            source_files=self.source_files, binding_sha256=material.digest({k: v for k, v in binding.items() if k != 'review'}),
            **{k: binding[k] for k in ORDINARY_FILES[2:]}, code=self.code, environment=self.environment, scope=self.scope)
        descriptor = binding['review']
        keys(descriptor, ('receipt', 'actual_review'))
        receipt = self._load(descriptor['receipt'])
        keys(receipt, (*common, 'kind', 'context'))
        self._identity(receipt)
        self._require(receipt['kind'] == ORDINARY_KIND and material.canonical(receipt['context']) == material.canonical(self.context)
                      and material.canonical(descriptor['actual_review']) == material.canonical(dict(receipt, receipt=descriptor['receipt'])),
                      'Ordinary review context or actual return mismatch')
        self._fresh()

    @staticmethod
    def _require(condition, message):
        if not condition:
            raise AdjudicationError(message)

    def _file(self, file):
        self.n.validate_file(file)
        path = bound(file)
        if file not in self.references:
            self.references.append(dict(file))
        return path

    def _load(self, file):
        return self.n.decode(self._file(file).read_text(encoding='utf-8'))

    def _identity(self, receipt):
        self._require(type(receipt['schema_version']) is int, 'Invalid review version')
        review_identity_v2(receipt)
        self._file(receipt['review_evidence'])

    def _id(self, item, key):
        self.n.sha(item[key])
        self._require(item[key] == self.m.digest({k: v for k, v in item.items() if k != key}), 'Invalid ' + key)

    def _records(self, file, key):
        records = {}
        for line in self._file(file).read_text(encoding='utf-8').splitlines():
            self._require(bool(line.strip()), 'Blank ordinary JSONL record')
            item = self.n.decode(line)
            self._id(item, key)
            self._require(item[key] not in records, 'Duplicate ' + key)
            records[item[key]] = item
        return records

    def _scope(self):
        members = sorted([member, self.m.digest(obs)] for member, obs in self.observations.items())
        return dict(observation_count=len(members), member_keys=[m[0] for m in members], observations_sha256=self.m.digest(members))

    def _set(self, values, key=None, nonempty=False):
        self._require(type(values) is list and (bool(values) or not nonempty), 'Expected evidence array')
        ids = [key(v) if key else v for v in values]
        self._require(ids == sorted(set(ids)), 'Array must have sorted unique identities')

    def _evidence(self, evidence, obs, nonempty=True):
        self._set(evidence, self.m.digest, nonempty)
        for ref in evidence:
            self._ref(ref, obs)
        return evidence

    def _ref(self, ref, obs):
        self._require(type(ref) is dict, 'Typed evidence required')
        if ref.get('kind') == 'native':
            keys(ref, ('kind', 'field'))
            self.n.validate_field(ref['field'], {r['record_id']: r for r in obs['body']['records']})
            return ref['field']['value']
        keys(ref, ('kind', 'context_id', 'pointer', 'start', 'end', 'value'))
        self._require(ref['kind'] == 'context' and ref['context_id'] in self.contexts, 'Unknown context evidence')
        context = self.contexts[ref['context_id']]
        text = self._file(context['content']).read_text(encoding='utf-8')
        if context['content_format'] == 'json':
            value = self.n.pointer_value(self.n.decode(text), ref['pointer'])
        else:
            self._require(ref['pointer'] is None and ref['start'] is not None, 'UTF8 requires exact offsets')
            value = text
        start, end = ref['start'], ref['end']
        if start is not None or end is not None:
            self._require(type(start) is int and type(end) is int and type(value) is str
                          and 0 <= start < end <= len(value), 'Invalid context span')
            value = value[start:end]
        self._require(self.m.canonical(value) == self.m.canonical(ref['value']), 'Context value mismatch')
        return value

    def _native_bindings(self, obs):
        import qsar_data as data
        record = next(r for r in obs['body']['records'] if r['locator'] == obs['native_locator'])
        columns = dict(data.ADAPTERS[self.config['adapter']], **self.config.get('columns', {}))
        result = {key: self.n._field(record, columns.get('relation' if key == 'modifier' else key, ''))
                  for key in ('endpoint', 'species', 'route', 'value', 'unit', 'modifier', 'reference')}
        chemical = [self.n._field(record, columns.get(k, '')) for k in ('npid', 'cas', 'dtxsid', 'smiles', 'inchi')]
        result['chemical'] = sorted({self.m.digest(f): f for f in chemical if f is not None}.values(), key=self.m.digest)
        return result

    def _issues(self, issues, obs):
        self._set(issues, self.m.digest)
        for issue in issues:
            keys(issue, ('domain', 'reason', 'evidence'))
            self._require(issue['domain'] in {'target_origin', 'route', 'tested_identity', 'dose_basis',
                          'experiment_linkage', 'extraction', 'support', 'join', 'context', 'retrieval'}, 'Unknown scientific issue')
            nonempty_v2(issue['reason'])
            self._evidence(issue['evidence'], obs)

    def _treatment(self, treatment, clause, disposition, reason):
        expected = dict(clause_id=clause, disposition=disposition, reason=reason)
        self._require(treatment == expected and self.clauses.get(clause) == dict(disposition=disposition, reason=reason),
                      'Unauthorized ordinary treatment')

    def _timestamp(self, value):
        import datetime
        nonempty_v2(value)
        try:
            parsed = datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
        except ValueError as exc:
            raise AdjudicationError('Invalid UTC timestamp') from exc
        self._require(parsed.strftime('%Y-%m-%dT%H:%M:%SZ') == value, 'Noncanonical UTC timestamp')

    @staticmethod
    def _target_identity(target):
        """Normalize local absolute path aliases without treating URLs as files."""
        import os
        return os.path.normcase(str(Path(target).resolve())) if Path(target).is_absolute() else target

    def _accessible_targets(self, obs):
        return (set(self.contexts) | {r['record_id'] for r in obs['body']['records']}
                | {self._target_identity(e['path']) for e in self.source_files}
                | {self._target_identity(c[k]['path']) for c in self.contexts.values() for k in ('artifact', 'content')})

    def _bounded(self, review_id, decision, obs, property_name):
        self._require(review_id in self.reviews and review_id not in self.used_reviews, 'Missing or reused typed bounded review')
        review = self.reviews[review_id]
        keys(review, ('review_id', 'member_key', 'decision_native_sha256', 'property', 'indispensable_property',
                      'question_evidence', 'competing_interpretations', 'scope_justification', 'paths',
                      'remaining_issues', 'residual_uncertainty', 'reopening_triggers'))
        self._require(review['member_key'] == decision['member_key'] and review['decision_native_sha256'] == self.m.digest(obs)
                      and review['property'] == property_name, 'Bounded review candidate/property mismatch')
        for key in ('indispensable_property', 'scope_justification', 'residual_uncertainty'):
            nonempty_v2(review[key])
        self._set(review['reopening_triggers'], nonempty=True)
        for trigger in review['reopening_triggers']:
            nonempty_v2(trigger)
        self._evidence(review['question_evidence'], obs)
        if property_name == 'target_origin':
            self._require(review['question_evidence'] == decision['target_origin']['evidence'],
                          'Bounded origin review must address the specific decision question')
        self._set(review['competing_interpretations'], self.m.digest, True)
        self._require(len(review['competing_interpretations']) >= 2, 'Bounded review needs competing interpretations')
        for alternative in review['competing_interpretations']:
            keys(alternative, ('interpretation', 'evidence'))
            nonempty_v2(alternative['interpretation'])
            self._evidence(alternative['evidence'], obs)
        self._issues(review['remaining_issues'], obs)
        self._require(not review['remaining_issues'], 'Unfinished bounded review issues')
        self._set(review['paths'], lambda p: p['path_id'], True)
        targets, covered_contexts = set(), set()
        for path in review['paths']:
            keys(path, ('path_id', 'category', 'target', 'identified_by', 'in_scope', 'scope_rationale',
                        'state', 'attempts', 'reviewed_evidence', 'remaining_feasible_actions', 'findings'))
            self._id(path, 'path_id')
            self._require(path['category'] in {'native_extraction', 'context', 'support', 'linkage', 'retrieval'}
                          and type(path['in_scope']) is bool, 'Invalid review path')
            nonempty_v2(path['target'])
            nonempty_v2(path['scope_rationale'])
            self._evidence(path['identified_by'], obs)
            self._evidence(path['reviewed_evidence'], obs, False)
            # Discovery evidence locates a path; only reviewed evidence can
            # establish that its declared target was actually reviewed.
            refs = path['reviewed_evidence']
            target = self._target_identity(path['target'])
            native_targets = {r['record_id'] for r in obs['body']['records']}
            source_target = target == self._target_identity(self.entry['path'])
            if target in native_targets or source_target:
                self._require(any(e['kind'] == 'native' and (source_target
                                  or e['field']['record_id'] == target) for e in refs),
                              'Native inventory path needs reviewed native field evidence')
            context_targets = {cid for cid, c in self.contexts.items() if target == cid
                               or target in {self._target_identity(c[k]['path']) for k in ('artifact', 'content')}}
            if context_targets and not source_target:
                self._require(any(e['kind'] == 'context' and e['context_id'] in context_targets for e in refs),
                              'Context inventory path needs its own reviewed evidence')
            support_files = [e for e in self.source_files if self._target_identity(e['path']) == target and e['source_id'] != self.entry['source_id']]
            if support_files:
                self._require(any(e['kind'] == 'context' and any(self._target_identity(self.contexts[e['context_id']][k]['path']) == target
                                  for k in ('artifact', 'content')) for e in refs),
                              'Support inventory path needs reviewed source-bound support context')
            self._issues(path['findings'], obs)
            self._set(path['remaining_feasible_actions'])
            self._require(not path['remaining_feasible_actions'] and not path['findings'], 'Unfinished feasible path or findings')
            targets.add(path['target'])
            self._require(type(path['attempts']) is list, 'Typed access attempts required')
            for attempt in path['attempts']:
                keys(attempt, ('at', 'target', 'outcome', 'evidence', 'context_ids'))
                self._timestamp(attempt['at'])
                self._require(attempt['target'] == path['target'] and attempt['outcome'] in {'access_failure', 'retrieved'},
                              'Attempt target/outcome mismatch')
                self._file(attempt['evidence'])
                self._set(attempt['context_ids'])
                self._require(set(attempt['context_ids']) <= self.contexts.keys(), 'Unknown retrieved context')
                if attempt['outcome'] == 'retrieved':
                    self._require(bool(attempt['context_ids']), 'Retrieved content missing from context inventory')
                else:
                    self._require(not attempt['context_ids'], 'Failed access cannot supply retrieved context')
            self._require(path['state'] in {'unattempted', 'attempted_access_failure', 'retrieved_awaiting_review', 'reviewed'},
                          'Unknown bounded path state')
            if path['in_scope']:
                self._require(path['state'] in {'reviewed', 'attempted_access_failure'}, 'Accessible or feasible path not reviewed')
            if path['state'] == 'attempted_access_failure':
                self._require(bool(path['attempts']) and all(a['outcome'] == 'access_failure' for a in path['attempts'])
                              and target not in self._accessible_targets(obs),
                              'Access failure does not close accessible evidence')
            if path['state'] == 'reviewed' or not path['in_scope']:
                self._require(bool(path['reviewed_evidence']), 'Review or scope exclusion requires actual evidence')
            path_contexts = {e['context_id'] for e in path['reviewed_evidence'] if e['kind'] == 'context'}
            self._require(all(set(a['context_ids']) <= path_contexts for a in path['attempts'] if a['outcome'] == 'retrieved'),
                          'Retrieved content remains unread')
            covered_contexts.update(path_contexts)
        # Registry binding is global freshness, not scientific relevance. Only
        # configured identity adapters able to join this row enter its inventory.
        identifiers = obs['body']['native_identifiers']
        support_ids = set()
        for config in self.contract['data_curation'].get('identity_sources', []):
            adapter = config.get('adapter')
            applicable = (bool(identifiers['npid']) if adapter == 'npass_structure_v1' else
                          bool(identifiers['cas']) if adapter == 'chemid_identity' else
                          any(identifiers[k] for k in ('cas', 'dtxsid') if k in config.get('columns', {})))
            if applicable:
                support_ids.add(config['source_id'])
        self._require(support_ids <= {e['source_id'] for e in self.source_files}, 'Applicable support absent from registry')
        required_targets = (set(self.contexts) | {e['path'] for e in self.source_files
                            if e['source_id'] in support_ids | {self.entry['source_id']}}
                            | {r['record_id'] for r in obs['body']['records']})
        self._require(required_targets <= targets and self.contexts.keys() <= covered_contexts,
                      'Bounded inventory omitted native/source/support/context evidence')
        self.used_reviews.add(review_id)

    def _decision(self, decision, obs):
        self._require(decision['native_observation_sha256'] == self.m.digest(obs), 'Untouched native observation mismatch')
        self._require(self.m.canonical(decision['native_bindings']) == self.m.canonical(self._native_bindings(obs)),
                      'Mapped native bindings mismatch')
        self._issues(decision['remaining_issues'], obs)
        issues = decision['remaining_issues']
        origin, route = decision['target_origin'], decision['route']
        dispositions = []
        if origin is not None:
            keys(origin, ('classification', 'subtype', 'evidence', 'primary_attribution', 'experimental_basis', 'treatment', 'bounded_review_id'))
            self._evidence(origin['evidence'], obs)
            if any(e['kind'] == 'context' for e in origin['evidence']):
                self._require(any(e['kind'] == 'context' and any(self.m.canonical(e['value']) == self.m.canonical(f['value'])
                                  for f in decision['native_bindings']['chemical']) for e in origin['evidence']),
                              'Context origin assertion must identify the native chemical')
            keys(origin['primary_attribution'], ('chemical', 'row', 'column'))
            for evidence in origin['primary_attribution'].values():
                if evidence is not None:
                    self._ref(evidence, obs)
                    self._require(evidence['kind'] == 'context' and self.contexts[evidence['context_id']]['source_role'] == 'primary',
                                  'Native export cannot masquerade as primary attribution')
            attributed_chemical = origin['primary_attribution']['chemical']
            if attributed_chemical is not None:
                self._require(any(self.m.canonical(f['value']) == self.m.canonical(attributed_chemical['value'])
                                  for f in decision['native_bindings']['chemical']), 'Primary attribution chemical differs from native candidate')
            classification = origin['classification']
            self._require(classification in {'experimental', 'nonexperimental', 'origin_unknown', 'origin_conflict'}, 'Unknown target origin')
            if classification == 'experimental':
                self._require(origin['subtype'] in {'reported_measurement', 'same_substance_mortality_estimate', 'validated_experimental_transform'}
                              and origin['treatment'] is None and origin['bounded_review_id'] is None, 'Invalid experimental treatment')
                basis = origin['experimental_basis']
                keys(basis, ('method', 'transformation', 'evidence'))
                self._evidence(basis['evidence'], obs)
                method, transform = basis['method'], basis['transformation']
                self._require(method in {'none', 'probit', 'maximum_likelihood', 'up_and_down', 'other_statistical_mortality'}
                              and transform in {'none', 'unit', 'molar', 'logarithmic'}, 'Unsupported experimental basis')
                self._require(not ((origin['subtype'] == 'reported_measurement' and (method != 'none' or transform != 'none'))
                              or (origin['subtype'] == 'same_substance_mortality_estimate' and method == 'none')
                              or (origin['subtype'] == 'validated_experimental_transform' and transform == 'none')),
                              'Experimental subtype lacks required basis')
            else:
                self._require(origin['experimental_basis'] is None, 'Nonexperimental/unknown origin cannot assert experimental basis')
                if classification == 'nonexperimental':
                    self._require(origin['subtype'] in {'qsar_prediction', 'read_across', 'model_prediction', 'computed_toxicity_prediction'}
                                  and origin['bounded_review_id'] is None, 'Invalid nonexperimental assertion')
                    self._treatment(origin['treatment'], 'v4_nonexperimental_target', 'excluded', 'nonexperimental_target')
                else:
                    self._require(origin['subtype'] is None, 'Unknown origin subtype must remain null')
                    if classification == 'origin_unknown' and origin['treatment'] is not None:
                        self._treatment(origin['treatment'], 'v4_origin_unknown_bounded_review', 'scientific_quarantine',
                                        'source_anchored_origin_unknown_after_bounded_review')
                        self._bounded(origin['bounded_review_id'], decision, obs, 'target_origin')
                    else:
                        self._require(origin['treatment'] is None and origin['bounded_review_id'] is None
                                      and any(i['domain'] == 'target_origin' and i['reason'] == classification for i in issues),
                                      'Unresolved origin must remain a separate blocking issue')
            if origin['treatment'] is not None:
                dispositions.append(dict(property='target_origin', treatment=origin['treatment']))
        override = None
        if route is not None:
            keys(route, ('state', 'value', 'evidence', 'candidate_linkage', 'treatment', 'bounded_review_id'))
            self._evidence(route['evidence'], obs)
            self._evidence(route['candidate_linkage'], obs)
            if route['state'] == 'established':
                nonempty_v2(route['value'])
                self._require(route['treatment'] is None and route['bounded_review_id'] is None, 'Established route cannot have a hold')
                native_route = decision['native_bindings']['route']
                if native_route is not None and self.n.nonblank(native_route):
                    import qsar_data as data
                    self._require(data.text(native_route['value']).casefold() == data.text(route['value']).casefold(),
                                  'Reviewed route contradicts native route')
                constant_route = self.config.get('constants', {}).get('route')
                if constant_route:
                    self._require(str(constant_route).strip().casefold() == route['value'].strip().casefold(),
                                  'Reviewed route contradicts accepted source route')
                self._require(any(str(self._ref(e, obs)).strip().casefold() == route['value'].strip().casefold()
                                  for e in route['evidence']), 'Established route needs explicit route value evidence')
                self._require(any(e['kind'] == 'native' and e['field'] in decision['native_bindings']['chemical']
                                  for e in route['candidate_linkage']), 'Route linkage must bind native candidate chemical')
                self._require(any(e['kind'] == 'context' for e in route['candidate_linkage'])
                              or native_route is not None, 'Recovered route requires source crosswalk evidence')
                if native_route is None or not self.n.nonblank(native_route):
                    self._require(any(e['kind'] == 'context' and any(self.m.canonical(e['value']) == self.m.canonical(f['value'])
                                      for f in decision['native_bindings']['chemical']) for e in route['candidate_linkage']),
                                  'Recovered route crosswalk must identify the native chemical')
                override = route['value']
            elif route['state'] == 'unavailable':
                self._require(route['value'] is None, 'Unavailable route remains null')
                self._require(not self.n.nonblank(decision['native_bindings']['route'])
                              and not self.config.get('constants', {}).get('route'), 'Available native route cannot be held as unavailable')
                self._treatment(route['treatment'], 'v4_unavailable_critical_metadata', 'scientific_quarantine', 'unavailable_critical_metadata')
                self._bounded(route['bounded_review_id'], decision, obs, 'exposure_route')
            else:
                self._require(route['state'] == 'unresolved' and route['value'] is None and route['treatment'] is None
                              and route['bounded_review_id'] is None and any(i['domain'] == 'route' for i in issues),
                              'Unresolved route must remain separately blocking')
            if route['treatment'] is not None:
                dispositions.append(dict(property='exposure_route', treatment=route['treatment']))
        if origin is not None and origin['classification'] == 'origin_unknown' and origin['treatment'] is not None:
            native_route = decision['native_bindings']['route']
            if not self.n.nonblank(native_route) and not self.config.get('constants', {}).get('route'):
                self._require(route is not None or any(i['domain'] == 'route' for i in issues),
                              'Origin hold cannot hide missing route')
        return dict(schema_version=1, member_key=decision['member_key'], native_observation_sha256=decision['native_observation_sha256'],
                    decision_id=decision['decision_id'], route_override=override, target_origin=origin,
                    scientific_dispositions=sorted(dispositions, key=lambda d: d['property']), scientific_remaining_issues=issues)

    def _notices(self, notices):
        keys(notices, ('schema_version', 'source_id', 'source_sha256', 'notices'))
        self._require(type(notices['schema_version']) is int and notices['schema_version'] == 1
                      and notices['source_id'] == self.entry['source_id'] and notices['source_sha256'] == self.entry['sha256'],
                      'Known evidence source mismatch')
        self._set(notices['notices'], lambda n: n['notice_id'])
        for notice in notices['notices']:
            keys(notice, ('notice_id', 'member_keys', 'discovered_at', 'locator', 'artifact', 'applicability',
                          'applicability_evidence', 'addressed_decision_ids', 'addressed_bounded_review_ids'))
            self._id(notice, 'notice_id')
            self._timestamp(notice['discovered_at'])
            nonempty_v2(notice['locator'])
            self._set(notice['member_keys'], nonempty=True)
            self._require(set(notice['member_keys']) <= self.observations.keys(), 'Notice references unknown member')
            for key in ('addressed_decision_ids', 'addressed_bounded_review_ids'):
                self._set(notice[key])
            self._require(set(notice['addressed_decision_ids']) <= self.decisions.keys()
                          and set(notice['addressed_bounded_review_ids']) <= self.used_reviews, 'Notice references unknown decision/review')
            self._require(notice['applicability'] in {'applicable', 'not_applicable'}, 'Pending known evidence blocks acceptance')
            for member in notice['member_keys']:
                self._evidence(notice['applicability_evidence'], self.observations[member])
            if notice['artifact'] is not None:
                self._file(notice['artifact'])
            if notice['applicability'] == 'not_applicable':
                self._require(not notice['addressed_decision_ids'] and not notice['addressed_bounded_review_ids'],
                              'Nonapplicable notice cannot claim addressed decisions')
                continue
            required = {self.by_member[m]['decision_id'] for m in notice['member_keys']}
            self._require(set(notice['addressed_decision_ids']) == required, 'Applicable evidence must address every named member')
            for member in notice['member_keys']:
                decision = self.by_member[member]
                reviews = [self.reviews[r] for r in notice['addressed_bounded_review_ids'] if self.reviews[r]['member_key'] == member]
                refs = list(self._refs(decision)) + [e for r in reviews for p in r['paths'] for e in p['reviewed_evidence']]
                if notice['artifact'] is not None:
                    contexts = {c['context_id'] for c in self.contexts.values() if c['artifact'] == notice['artifact'] or c['content'] == notice['artifact']}
                    self._require(bool(contexts) and any(e['kind'] == 'context' and e['context_id'] in contexts for e in refs),
                                  'Applicable accessible notice remains unread')
                else:
                    self._require(self._target_identity(notice['locator']) not in self._accessible_targets(self.observations[member]),
                                  'Known accessible notice cannot use an inaccessible assessment')
                    self._require(any(p['target'] == notice['locator'] and p['state'] == 'attempted_access_failure'
                                      for r in reviews for p in r['paths']), 'Inaccessible notice needs current bounded-access assessment')

    def _refs(self, value):
        if type(value) is dict:
            if value.get('kind') in {'native', 'context'}:
                yield value
            else:
                for child in value.values():
                    yield from self._refs(child)
        elif type(value) is list:
            for child in value:
                yield from self._refs(child)

    def _fresh(self):
        for file in self.references:
            bound(file)
        self._require(ordinary_code_bindings() == self.code and ordinary_environment() == self.environment,
                      'Ordinary code/environment changed during replay')

    def resolve(self, native_observation):
        self._require(not self._finished, 'Ordinary adjudication already finished')
        self.n.validate_observation(native_observation)
        member = self.n.member_key(native_observation)
        self._require(member in self.observations and member not in self.seen
                      and self.m.canonical(native_observation) == self.m.canonical(self.observations[member]),
                      'Unknown, duplicate or modified native observation')
        self.seen.add(member)
        return json.loads(self.m.canonical(self.results[member]))

    def finish(self):
        self._require(not self._finished and self.seen == self.observations.keys(), 'Incomplete ordinary replay consumption')
        self._fresh()
        self._finished = True
        origins, treatments, issues = Counter(), Counter(), Counter()
        for result in self.results.values():
            if result['target_origin'] is not None:
                origins[result['target_origin']['classification']] += 1
            for disposition in result['scientific_dispositions']:
                treatments[disposition['treatment']['reason']] += 1
            issues.update(i['domain'] for i in result['scientific_remaining_issues'])
        return dict(summary=dict(schema_version=1, source_id=self.entry['source_id'], observation_count=len(self.seen),
                                 complete_u_accounting=True, target_origin_counts=dict(origins), scientific_treatments=dict(treatments),
                                 remaining_issue_counts=dict(issues)),
                    references=sorted(self.references, key=lambda f: (f['path'], f['sha256'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(export_inventory(args.source, args.source_id, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
