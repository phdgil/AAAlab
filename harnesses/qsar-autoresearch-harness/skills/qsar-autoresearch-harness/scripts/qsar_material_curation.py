"""Native evidence primitives for the reviewed material curation bridge.

These mechanics neither classify materials nor issue scientific review receipts.
Native JSON is preserved before text normalization; derived graph diagnostics are
kept separate from the literal representations that supplied them.
"""
import json
import math
import os
import re
from pathlib import Path
import inspect
import sqlite3
import tempfile
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors

from qsar_verified_material import MaterialError, canonical, digest


def require(condition, message):
    if not condition:
        raise MaterialError(message)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'Unexpected material schema keys')


def string(value, nonempty=False):
    require(type(value) is str and (not nonempty or bool(value.strip())), 'Invalid material string')


def sha(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'Invalid material digest')


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate material JSON key: ' + key)
        result[key] = value
    return result


def decode(value):
    def reject(value):
        raise MaterialError('Nonfinite material JSON number: ' + value)
    result = json.loads(value, object_pairs_hook=unique, parse_constant=reject)
    canonical(result)  # Also reject overflowing finite-looking JSON numbers.
    return result


def native_json(value):
    result = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    canonical(result)
    return result


_LOCATORS = {
    'table': ('sheet', 'row'),
    'sql': ('table', 'line', 'tuple'),
    'xml_field': ('archive_member', 'document_ordinal', 'record_id', 'field_ordinal', 'tag', 'tag_ordinal'),
    'xml_document': ('archive_member', 'document_ordinal', 'record_id'),
    'archive_table': ('archive_member', 'row'),
}
_ORDINALS = {'row', 'line', 'tuple', 'document_ordinal', 'field_ordinal', 'tag_ordinal'}


def validate_locator(locator):
    require(type(locator) is dict and locator.get('kind') in _LOCATORS, 'Unknown native locator')
    keys(locator, ('kind', *_LOCATORS[locator['kind']]))
    for field, value in locator.items():
        if field in _ORDINALS:
            require(type(value) is int and value >= 1, 'Invalid physical native ordinal')
        else:
            string(value)
    return locator


def native_record(source_id, source_sha256, locator, record, lineage):
    string(source_id, True)
    sha(source_sha256)
    validate_locator(locator)
    body = dict(source_id=source_id, source_sha256=source_sha256, locator=locator,
                native_record=native_json(record), native_lineage=native_json(lineage))
    return dict(record_id=digest(body), **body)


def validate_record(record):
    keys(record, ('record_id', 'source_id', 'source_sha256', 'locator', 'native_record', 'native_lineage'))
    expected = native_record(**{k: record[k] for k in ('source_id', 'source_sha256', 'locator')},
                             record=record['native_record'], lineage=record['native_lineage'])
    require(record == expected, 'Native record digest mismatch')


def pointer_value(document, pointer):
    string(pointer)
    require(pointer == '' or pointer.startswith('/'), 'Invalid RFC6901 pointer')
    value = document
    for token in pointer.split('/')[1:]:
        require(re.search(r'~(?![01])', token) is None, 'Invalid RFC6901 escape')
        token = token.replace('~1', '/').replace('~0', '~')
        if type(value) is list:
            require(re.fullmatch('0|[1-9][0-9]*', token) is not None, 'Invalid pointer array index')
            require(int(token) < len(value), 'Pointer index out of bounds')
            value = value[int(token)]
        else:
            require(type(value) is dict and token in value, 'Pointer missing from native record')
            value = value[token]
    return value


def field_evidence(record, pointer, start=None, end=None):
    value = pointer_value(record['native_record'], pointer)
    if start is not None or end is not None:
        require(type(start) is int and type(end) is int and type(value) is str and
                0 <= start < end <= len(value), 'Invalid native evidence span')
        value = value[start:end]
    return dict(record_id=record['record_id'], pointer=pointer, start=start, end=end, value=value)


def validate_field(field, records):
    keys(field, ('record_id', 'pointer', 'start', 'end', 'value'))
    require(field['record_id'] in records, 'Evidence references an unknown native record')
    expected = field_evidence(records[field['record_id']], field['pointer'], field['start'], field['end'])
    require(canonical(field) == canonical(expected), 'Native field evidence mismatch')


def _field(record, name):
    if name not in record['native_record']:
        return None
    return field_evidence(record, '/' + name.replace('~', '~0').replace('/', '~1'))


def _records(records):
    require(type(records) is list and bool(records), 'Nonempty native records required')
    for record in records:
        validate_record(record)
    ids = [r['record_id'] for r in records]
    require(ids == sorted(set(ids)), 'Native records must have sorted unique identities')
    return {r['record_id']: r for r in records}


def _field_identity(field):
    return (field['record_id'], field['pointer'], -1 if field['start'] is None else field['start'],
            -1 if field['end'] is None else field['end'])


def validate_bundle(bundle):
    keys(bundle, ('schema_version', 'kind', 'records', 'representations', 'native_identifiers'))
    require(type(bundle['schema_version']) is int and bundle['schema_version'] == 1, 'Unsupported native bundle')
    require(bundle['kind'] in ('direct', 'joined', 'support_only'), 'Unknown native bundle kind')
    records = _records(bundle['records'])
    keys(bundle['representations'], ('smiles', 'inchi'))
    for field in bundle['representations'].values():
        if field is not None:
            validate_field(field, records)
    keys(bundle['native_identifiers'], ('cas', 'dtxsid', 'npid'))
    for fields in bundle['native_identifiers'].values():
        require(type(fields) is list, 'Native identifiers must be arrays')
        identities = []
        for field in fields:
            validate_field(field, records)
            identities.append((field['record_id'], field['pointer'], field['start'], field['end']))
        require(len(identities) == len(set(identities)), 'Duplicate native identifier evidence')
        require(fields == sorted(fields, key=_field_identity), 'Native identifier evidence must be sorted')
    if bundle['kind'] == 'support_only':
        require(not any(nonblank(f) for f in bundle['representations'].values()),
                'Support-only bundle cannot have a nonblank native representation')
    return records


def nonblank(field):
    if field is None or field['value'] is None:
        return False
    return bool(str(field['value']).strip())


def graph_diagnostics(representations):
    """No fallback: any supplied malformed representation invalidates the pair."""
    keys(representations, ('smiles', 'inchi'))
    result = {}
    for kind, field in representations.items():
        diagnostic = dict(state='absent', canonical_smiles=None, mw=None)
        if nonblank(field):
            value = str(field['value']).strip()
            try:
                mol = Chem.MolFromSmiles(value) if kind == 'smiles' else Chem.MolFromInchi(value)
                mass = Descriptors.MolWt(mol) if mol is not None else None
                valid = mol is not None and mol.GetNumAtoms() > 0 and math.isfinite(mass) and mass > 0
            except (ValueError, RuntimeError):
                valid = False
            if valid:
                diagnostic.update(state='valid', canonical_smiles=Chem.MolToSmiles(mol, canonical=True,
                    isomericSmiles=True), mw=mass)
            else:
                diagnostic['state'] = 'invalid'
        result[kind] = diagnostic
    supplied = [d for d in result.values() if d['state'] != 'absent']
    if any(d['state'] == 'invalid' for d in supplied):
        agreement = 'invalid'
    elif not supplied:
        agreement = 'absent'
    elif len(supplied) == 1:
        agreement = 'single'
    else:
        agreement = 'agree' if supplied[0]['canonical_smiles'] == supplied[1]['canonical_smiles'] else 'conflict'
    return dict(result, agreement=agreement,
                input_graph_smiles=supplied[0]['canonical_smiles'] if agreement in ('single', 'agree') else None)


def physical_locator(raw, adapter, number, sheet):
    lineage = raw.get('native_lineage', {})
    if adapter == 'toxval':
        result = dict(kind='sql', **{k: lineage[k] for k in ('table', 'line', 'tuple')})
    elif adapter == 'openfoodtox':
        result = dict(kind='table', sheet=lineage['sheet'], row=lineage['native_row'])
    elif adapter == 'hsdb':
        result = dict(kind='xml_field' if 'field_ordinal' in lineage else 'xml_document',
            archive_member=lineage['archive_member'], document_ordinal=lineage['document_ordinal'],
            record_id=str(lineage.get('docno', raw.get('record_id', '').split(':')[0])))
        if result['kind'] == 'xml_field':
            result.update({k: lineage[k] for k in ('field_ordinal', 'tag', 'tag_ordinal')})
    else:
        result = dict(kind='table', sheet=sheet, row=number)
    return validate_locator(result)


class ScientificResult:
    """In-process carrier constructed from a validated engine resolution only."""
    __slots__ = ('__encoded',)

    def __init__(self, engine, native_observation):
        from qsar_adjudication import OrdinaryAdjudication
        require(type(engine) is OrdinaryAdjudication, 'Actual ordinary adjudication engine required')
        self.__encoded = canonical(engine.resolve(native_observation))

    @property
    def _encoded(self):
        return self.__encoded

    def value(self, native_observation):
        result = decode(self._encoded)
        require(result['member_key'] == member_key(native_observation)
                and result['native_observation_sha256'] == digest(native_observation),
                'Scientific result differs from untouched native observation')
        return result


def scientific_value(result, raw, config, entry, number, sheet):
    require(isinstance(result, ScientificResult), 'Validated scientific result carrier required')
    return result.value(observation(raw, config, entry, number, sheet))


def observation(raw, config, entry, number, sheet, *, adjudicated=False, document_record=None, scientific_result=None, sql_result=None):
    """Build literal v2 evidence from a freshly emitted source adapter record."""
    import qsar_data as data
    adapter = config['adapter']
    locator = physical_locator(raw, adapter, number, sheet)
    if adapter == 'hsdb' and document_record is not None:
        locator['record_id'] = document_record['locator']['record_id']
    primary = native_record(config['source_id'], entry['sha256'], locator,
        raw['native_record'] if adapter in data.NATIVE_ADAPTERS else raw,
        raw.get('native_lineage', {}))
    records, owner = [primary], primary
    columns = dict(data.ADAPTERS[adapter], **config.get('columns', {}))
    kind = 'direct'
    if adapter == 'openfoodtox':
        kind, owner = 'joined', None
        references = []
        for joined in raw['native_lineage']['joins']:
            record = native_record(config['source_id'], entry['sha256'],
                dict(kind='table', sheet=joined['sheet'], row=joined['native_row']),
                joined['record'], {k: v for k, v in joined.items() if k != 'record'})
            records.append(record)
            if joined['sheet'] == 'REF_SUB':
                references.append(record)
        if len(references) == 1 and not set(raw.get('why', [])) & data._IDENTITY_NATIVE:
            owner = references[0]
        columns = dict(smiles='MolecularStructuralInfo.SmilesNotation', inchi='MolecularStructuralInfo.InChl',
                       cas='Inventory.CASNumber', dtxsid='US EPA DTXSID')
    representations = {k: _field(owner, columns.get(k, '')) if owner else None for k in ('smiles', 'inchi')}
    identifiers = {k: [] for k in ('cas', 'dtxsid', 'npid')}
    if owner:
        for key in identifiers:
            field = _field(owner, columns.get(key, ''))
            if field is not None:
                identifiers[key].append(field)
    # A native relational projection is not a literal native field. SQL identifiers
    # are preserved from the primary record; external structures remain supports.
    if adapter == 'toxval':
        identifiers['cas'] = [f for f in [_field(primary, 'casrn')] if f is not None]
        for joined in raw['native_lineage'].get('source_chemical_matches', []):
            lineage = joined['lineage']
            record = native_record(config['source_id'], entry['sha256'],
                dict(kind='sql', **{k: lineage[k] for k in ('table', 'line', 'tuple')}), joined['record'], lineage)
            records.append(record)
            for key, column in (('cas', 'casrn'), ('dtxsid', 'dtxsid')):
                field = _field(record, column)
                if field is not None:
                    identifiers[key].append(field)
    if adapter == 'hsdb':
        require(document_record is not None, 'HSDB material evidence requires independently preserved native document')
        if locator['kind'] == 'xml_document':
            records = [document_record]
        else:
            records.append(document_record)
        for index, field in enumerate(document_record['native_record']['fields']):
            if field['tag'] == 'CASRegistryNumber':
                identifiers['cas'].append(field_evidence(document_record, f'/fields/{index}/text'))
    if not any(nonblank(f) for f in representations.values()):
        kind = 'support_only'
    for fields in identifiers.values():
        fields.sort(key=_field_identity)
    bundle = dict(schema_version=1, kind=kind, records=sorted(records, key=lambda r: r['record_id']),
                  representations=representations, native_identifiers=identifiers)
    validate_bundle(bundle)
    candidate = 'row'
    if adapter == 'hsdb':
        candidate = ('v2:' + raw['candidate_id']) if 'candidate_id' in raw else (
            ('v1:' if adjudicated else 'parser:') + raw['record_id'])
    if sql_result is not None:
        require(adapter == 'toxval' and scientific_result is None and not adjudicated, 'Exclusive SQL projection required')
        from qsar_sql_curation import SQLCandidateResult
        require(isinstance(sql_result, SQLCandidateResult), 'Validated SQL carrier required')
        sql_value = sql_result.value(raw)
        candidate = 'sql-v2:' + sql_value['candidate_id']
    result = dict(schema_version=2, source_id=config['source_id'], source_sha256=entry['sha256'],
        native_locator=locator, candidate_key=candidate, body=bundle, candidate_projection=native_json(raw))
    validate_observation(result)
    if sql_result is not None:
        result['candidate_projection'] = dict(kind='sql_reviewed_projection_v2',
            native_adapter_row=result['candidate_projection'], sql_result=sql_value)
        validate_observation(result)
    if scientific_result is not None:
        require(not adjudicated and config['adapter'] in {'npass', 'ice_oral', 'oecd', 'toxric'},
                'Scientific projection requires an ordinary native row')
        require(isinstance(scientific_result, ScientificResult), 'Validated scientific result carrier required')
        value = scientific_result.value(result)
        result['candidate_projection'] = dict(kind='ordinary_reviewed_projection_v1',
            native_adapter_row=result['candidate_projection'], scientific_result=value)
        validate_observation(result)
    return result


def validate_observation(obs):
    keys(obs, ('schema_version', 'source_id', 'source_sha256', 'native_locator', 'candidate_key', 'body', 'candidate_projection'))
    require(type(obs['schema_version']) is int and obs['schema_version'] == 2, 'Integrated observations require v2')
    string(obs['source_id'], True)
    string(obs['candidate_key'], True)
    sha(obs['source_sha256'])
    validate_locator(obs['native_locator'])
    records = validate_bundle(obs['body'])
    require(all(r['source_id'] == obs['source_id'] and r['source_sha256'] == obs['source_sha256']
                for r in records.values()), 'Native observation source mismatch')
    require(any(r['locator'] == obs['native_locator'] for r in records.values()), 'Missing primary native record')
    canonical(obs['candidate_projection'])


def member_key(obs):
    return digest([obs['source_id'], obs['native_locator'], obs['candidate_key']])


def slot_id(obs):
    return digest([obs['source_id'], obs['native_locator']])


def raw_table_support(entry, config, number, row):
    """Capture mapped/NPASS native support before normalization or rejection."""
    columns = config.get('columns', {})
    if config.get('adapter') == 'npass_structure_v1':
        columns = dict(smiles='SMILES', inchi='InChI', npid='np_id')
    record = native_record(config['source_id'], entry['sha256'],
        dict(kind='table', sheet=config.get('sheet', ''), row=number), row, {})
    representations = {k: _field(record, columns.get(k, '')) for k in ('smiles', 'inchi')}
    reasons = npass_native_reasons(row, representations) if config.get('adapter') == 'npass_structure_v1' else []
    return dict(source_id=config['source_id'], source_sha256=entry['sha256'], primary_record_id=record['record_id'],
        records=[record], representations=representations,
        identifiers={k: [f] if (f := _field(record, columns.get(k, ''))) is not None else []
                     for k in ('cas', 'dtxsid', 'npid')}, native_reasons=reasons)


NATIVE_SUPPORT_IDENTITY_REASONS = frozenset({
    'missing_npid', 'missing_structure_representation',
    'missing_or_invalid_native_inchikey', 'native_inchikey_disagreement'})


def npass_native_reasons(row, representations):
    """NPASS native requirements, independent of legacy organic parent policy.

    Use the complete agreeing literal graph for key validation. Graph parsing or
    representation disagreements remain separate reviewable structure diagnostics;
    a malformed/missing/mismatched native identifier is never a graph correction.
    """
    import qsar_data as data
    reasons = ['missing_required_native_field:' + key for key in ('np_id', 'SMILES', 'InChI', 'InChIKey') if key not in row]
    if None in row or any(value is None for value in row.values()):
        reasons.append('native_support_field_count_mismatch')
    if not data.text(row.get('np_id')):
        reasons.append('missing_npid')
    if not all(nonblank(representations[k]) for k in ('smiles', 'inchi')):
        reasons.append('missing_structure_representation')
    key = data.text(row.get('InChIKey'))
    if re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]', key) is None:
        reasons.append('missing_or_invalid_native_inchikey')
    else:
        diagnostic = graph_diagnostics(representations)
        if diagnostic['agreement'] == 'agree':
            generated = Chem.MolToInchiKey(Chem.MolFromSmiles(diagnostic['input_graph_smiles']))
            if generated != key:
                reasons.append('native_inchikey_disagreement')
    return reasons


def raw_chemid_support(entry, config, candidate):
    """Preserve all physical structure candidates, including orphan records."""
    records = []
    for item in candidate['candidate_structures']:
        records.append(native_record(config['source_id'], entry['sha256'],
            dict(kind='archive_table', archive_member=item['archive_member'], row=item['native_row']),
            item['record'], {k: v for k, v in item.items() if k != 'record'}))
    lineage = candidate['native_lineage']
    primary = None
    if 'document_ordinal' in lineage:
        primary = native_record(config['source_id'], entry['sha256'],
            dict(kind='xml_document', archive_member=lineage['archive_member'],
                 document_ordinal=lineage['document_ordinal'], record_id=candidate['record_id']),
            candidate['native_record'], lineage)
        records.append(primary)
    require(records, 'ChemID support has no physical evidence')
    primary = primary or records[0]
    identifiers = dict(cas=[], dtxsid=[], npid=[])
    for index, field in enumerate(primary['native_record'].get('fields', [])):
        if field.get('tag') == 'CASRegistryNumber':
            identifiers['cas'].append(field_evidence(primary, f'/fields/{index}/text'))
        elif field.get('tag') == 'NumberList':
            for match in re.finditer(r'<CASRegistryNumber(?:\s[^>]*)?>([^<]*)', field['xml']):
                if match.start(1) != match.end(1):
                    identifiers['cas'].append(field_evidence(primary, f'/fields/{index}/xml', *match.span(1)))
    representations = {}
    for key, column in (('smiles', 'SMILES'), ('inchi', 'InChI')):
        fields = [f for record in records if (f := _field(record, column)) is not None]
        # Never arbitrarily select one competing physical record. The complete
        # candidates remain in records even when selection is ambiguous.
        representations[key] = fields[0] if len(fields) == 1 else None
    return dict(source_id=config['source_id'], source_sha256=entry['sha256'], primary_record_id=primary['record_id'],
        records=sorted(records, key=lambda r: r['record_id']), representations=representations,
        identifiers={k: sorted(v, key=_field_identity) for k, v in identifiers.items()}, native_reasons=list(candidate['why']))


def validate_raw_support(raw):
    keys(raw, ('source_id', 'source_sha256', 'primary_record_id', 'records', 'representations', 'identifiers', 'native_reasons'))
    string(raw['source_id'], True)
    sha(raw['source_sha256'])
    records = validate_bundle(dict(schema_version=1, kind='joined', records=raw['records'],
        representations=raw['representations'], native_identifiers=raw['identifiers']))
    require(raw['primary_record_id'] in records, 'Missing primary support record')
    require(all(r['source_id'] == raw['source_id'] and r['source_sha256'] == raw['source_sha256']
                for r in records.values()), 'Support source mismatch')
    require(type(raw['native_reasons']) is list and all(type(v) is str for v in raw['native_reasons']),
            'Invalid support native reasons')
    return records


def _evidence(value):
    require(type(value) is list and bool(value) and all(type(v) is str and v.strip() for v in value),
            'Nonempty review evidence required')


def validate_inventory(item):
    keys(item, ('observation', 'support_candidates', 'blocked_support', 'source_metadata'))
    validate_observation(item['observation'])
    records = {r['record_id']: r for r in item['observation']['body']['records']}
    keys(item['source_metadata'], ('source_formula', 'study_id', 'dose_basis_declaration'))
    for field in item['source_metadata'].values():
        if field is not None:
            validate_field(field, records)
    require(type(item['support_candidates']) is list and type(item['blocked_support']) is list,
            'Explicit support arrays required')
    ids = []
    for support in item['support_candidates']:
        keys(support, ('support_id', 'raw', 'matched_keys'))
        validate_raw_support(support['raw'])
        require(support['support_id'] == digest(support['raw']), 'Support digest mismatch')
        ids.append(support['support_id'])
        require(type(support['matched_keys']) is list and support['matched_keys'], 'Matched support keys required')
        matched = []
        for key in support['matched_keys']:
            keys(key, ('kind', 'value'))
            require(key['kind'] in ('cas', 'dtxsid', 'npid'), 'Unknown support key kind')
            string(key['value'], True)
            matched.append((key['kind'], key['value']))
        require(matched == sorted(set(matched)), 'Support keys must be sorted and unique')
    require(ids == sorted(set(ids)), 'Support inventory must be sorted and unique')
    ids = []
    evidence_records = set(records)
    for support in item['support_candidates']:
        evidence_records.update(r['record_id'] for r in support['raw']['records'])
    for blocker in item['blocked_support']:
        keys(blocker, ('blocker_id', 'category', 'reason', 'evidence_record_ids', 'original_reasons'))
        require(blocker['category'] in ('ingestion', 'identity', 'chemical_join'), 'Unknown blocker category')
        string(blocker['reason'], True)
        require(type(blocker['evidence_record_ids']) is list and blocker['evidence_record_ids'] ==
                sorted(set(blocker['evidence_record_ids'])) and set(blocker['evidence_record_ids']) <= evidence_records,
                'Unknown or duplicate blocker evidence')
        require(type(blocker['original_reasons']) is list and all(type(v) is str for v in blocker['original_reasons']),
                'Invalid original blocker reasons')
        require(blocker['blocker_id'] == digest({k: v for k, v in blocker.items() if k != 'blocker_id'}),
                'Blocker digest mismatch')
        ids.append(blocker['blocker_id'])
    require(ids == sorted(set(ids)), 'Blockers must be sorted and unique')


def organic_execution(contract):
    policy = contract.get('scientific_policy', {}).get('verified_tested_material_v4', {})
    if 'organic_salt_b_v1' not in policy:
        return False
    branch = policy['organic_salt_b_v1']
    require(type(branch) is dict, 'Invalid organic salt policy')
    expected = dict(schema_version=1, adapter='reviewed_organic_principal_component_v1',
        rdkit_version=rdBase.rdkitVersion, mass_algorithm='rdkit_descriptors_molwt',
        predictor_policy='reviewed_principal_component',
        grouping_algorithm='sha256_utf8_rdkit_canonical_nonisomeric_smiles')
    require(canonical(branch.get('execution')) == canonical(expected), 'Unsupported organic salt execution policy')
    return True


def validate_principal(principal, item, facts):
    keys(principal, ('canonical_smiles', 'multiplicity', 'source_evidence', 'supplemental_evidence', 'selection_rationale'))
    string(principal['canonical_smiles'], True)
    string(principal['selection_rationale'], True)
    mol = Chem.MolFromSmiles(principal['canonical_smiles'])
    require(mol is not None and len(Chem.GetMolFrags(mol)) == 1 and
            any(atom.GetAtomicNum() == 6 for atom in mol.GetAtoms()), 'Principal must be one organic component')
    canonical_smiles = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    require(principal['canonical_smiles'] == canonical_smiles and facts is not None and
            canonical_smiles in facts['components'], 'Principal is not an exact canonical material component')
    require(type(principal['multiplicity']) is int and principal['multiplicity'] > 0 and
            principal['multiplicity'] == facts['components'][canonical_smiles], 'Principal multiplicity mismatch')
    records = {r['record_id']: r for r in item['observation']['body']['records']}
    for support in item['support_candidates']:
        records.update((r['record_id'], r) for r in support['raw']['records'])
    require(type(principal['source_evidence']) is list and principal['source_evidence'], 'Principal source evidence required')
    for field in principal['source_evidence']:
        validate_field(field, records)
    require(type(principal['supplemental_evidence']) is list, 'Principal supplemental evidence array required')
    for evidence in principal['supplemental_evidence']:
        keys(evidence, ('file', 'locator'))
        validate_file(evidence['file'])
        string(evidence['locator'], True)
    # File bytes are checked once at load and again at batch finish, not per row.


def validate_decision(decision, item, *, organic_enabled=False):
    from qsar_verified_material import graph_facts
    version = decision.get('schema_version')
    require(type(version) is int and version in (2, 3), 'Integrated decisions require v2 or v3')
    require(version != 3 or organic_enabled, 'Organic schema3 requires explicit execution policy')
    keys(decision, ('schema_version', 'observation_id', 'status', 'material_class', 'review_evidence',
        'reopening_conditions', 'limitations', 'critical_conflicts', 'support_resolutions', 'dose_basis',
        'fixed_composition', 'hydration_interpretation', 'verified_smiles', 'formula_unit',
        'source_formula_interpretation', 'resolution') + (('principal_component',) if version == 3 else ()))
    obs = item['observation']
    require(decision['observation_id'] == digest(obs), 'Wrong observation decision')
    require(decision['status'] in ('verified', 'reviewed_hold', 'unresolved'), 'Unknown material status')
    require(decision['material_class'] in ('inorganic', 'organic', 'unresolved'), 'Unknown material class')
    for key in ('review_evidence', 'reopening_conditions'):
        _evidence(decision[key])
    for key in ('limitations', 'critical_conflicts'):
        require(type(decision[key]) is list and all(type(v) is str for v in decision[key]), 'Invalid material ' + key)
    require(decision['dose_basis'] in ('reported_material_mass', 'explicit_formula_unit_molar', None), 'Unknown dose basis')
    require(decision['fixed_composition'] is None or type(decision['fixed_composition']) is bool, 'Invalid composition flag')
    for key in ('hydration_interpretation', 'verified_smiles'):
        if decision[key] is not None:
            string(decision[key], True)
    require(type(decision['support_resolutions']) is list, 'Support resolutions required')
    resolved = {}
    blockers = {digest(b): b for b in item['blocked_support']}
    for resolution in decision['support_resolutions']:
        keys(resolution, ('support_sha256', 'disposition', 'evidence'))
        key = resolution['support_sha256']
        require(key in blockers and key not in resolved, 'Unknown or duplicate support resolution')
        require(resolution['disposition'] in ('resolved_material_identity', 'unresolved'), 'Unknown support disposition')
        _evidence(resolution['evidence'])
        if resolution['disposition'] == 'resolved_material_identity':
            require(blockers[key]['category'] == 'identity' and blockers[key]['reason'] == 'material_identity_conflict',
                    'Material review cannot waive ingestion or chemical join blockers')
        resolved[key] = resolution
    require(set(resolved) == set(blockers), 'Every support blocker requires an exact disposition')
    facts = None
    if decision['verified_smiles'] is not None:
        facts = graph_facts(decision['verified_smiles'])
        require(canonical(facts) == canonical(decision['formula_unit']), 'Formula-unit facts mismatch')
    else:
        require(decision['formula_unit'] is None, 'Formula unit requires a verified graph')
    interpretation = decision['source_formula_interpretation']
    formula = item['source_metadata']['source_formula']
    if interpretation is not None:
        keys(interpretation, ('source_formula', 'formula', 'evidence'))
        _evidence(interpretation['evidence'])
        string(interpretation['formula'], True)
        require(formula is not None and canonical(interpretation['source_formula']) == canonical(formula['value']), 'Formula interpretation is not source-bound')
        require(facts is not None and interpretation['formula'] == facts['formula'], 'Source formula contradicts graph')
    resolution = decision['resolution']
    if resolution is not None:
        require(facts is not None, 'Resolution requires a verified graph')
        validate_resolution(resolution, item, facts)
    if version == 3 and decision['principal_component'] is not None:
        validate_principal(decision['principal_component'], item, facts)
    if decision['status'] == 'verified':
        require(version != 3 or decision['principal_component'] is not None, 'Verified organic material requires principal component')
        require(decision['material_class'] == ('organic' if version == 3 else 'inorganic') and decision['fixed_composition'] is True,
                'Verified material requires reviewed branch classification and fixed composition')
        require(decision['hydration_interpretation'] is not None and facts is not None and resolution is not None,
                'Verified material requires hydration, graph and resolution')
        require(decision['dose_basis'] is not None and not decision['critical_conflicts'] and
                all(r['disposition'] != 'unresolved' for r in resolved.values()), 'Unresolved verified material')
        if formula is not None and formula['value'] not in (None, ''):
            require(interpretation is not None, 'Source formula requires reviewed interpretation')


def validate_resolution(resolution, item, facts):
    keys(resolution, ('kind', 'native_representations_sha256', 'selected_support_ids', 'source_evidence',
                      'representation_dispositions', 'evidence'))
    require(resolution['kind'] in ('unchanged_native', 'native_correction', 'support_resolution'), 'Unknown resolution kind')
    reps = item['observation']['body']['representations']
    require(resolution['native_representations_sha256'] == digest(reps), 'Stale native representation resolution')
    _evidence(resolution['evidence'])
    selected = resolution['selected_support_ids']
    require(type(selected) is list and selected == sorted(set(selected)) and
            set(selected) <= {s['support_id'] for s in item['support_candidates']}, 'Unknown selected supports')
    records = {r['record_id']: r for r in item['observation']['body']['records']}
    require(type(resolution['source_evidence']) is list, 'Source evidence array required')
    for field in resolution['source_evidence']:
        validate_field(field, records)
    require(type(resolution['representation_dispositions']) is list, 'Representation dispositions required')
    dispositions = {}
    for disposition in resolution['representation_dispositions']:
        keys(disposition, ('representation', 'disposition', 'evidence'))
        key = disposition['representation']
        require(key in reps and key not in dispositions and nonblank(reps[key]), 'Unknown or duplicate representation disposition')
        require(disposition['disposition'] in ('agrees_with_verified', 'source_reviewed_correction'), 'Unknown representation disposition')
        _evidence(disposition['evidence'])
        dispositions[key] = disposition['disposition']
    supplied = {k for k, v in reps.items() if nonblank(v)}
    require(set(dispositions) == supplied, 'Every supplied representation needs a disposition')
    diagnostics = graph_diagnostics(reps)
    for key, disposition in dispositions.items():
        agrees = diagnostics[key]['state'] == 'valid' and diagnostics[key]['canonical_smiles'] == facts['canonical_smiles']
        require(disposition != 'agrees_with_verified' or agrees, 'Representation does not agree with verified graph')
        require(agrees or disposition == 'source_reviewed_correction', 'Invalid representation requires explicit correction')
    if resolution['kind'] == 'unchanged_native':
        require(diagnostics['agreement'] in ('single', 'agree') and diagnostics['input_graph_smiles'] == facts['canonical_smiles']
                and all(v == 'agrees_with_verified' for v in dispositions.values()) and not selected,
                'Unchanged native resolution requires agreeing native graph')
    elif resolution['kind'] == 'native_correction':
        require(supplied and resolution['source_evidence'], 'Correction requires source-bound native representation evidence')
    else:
        require(not supplied and resolution['source_evidence'], 'Support resolution requires absent direct representations and source evidence')


MATERIAL_COLUMNS = ('material_schema_version', 'material_observation_id', 'material_binding_sha256', 'material_class',
    'raw_representations', 'raw_graph_diagnostics', 'raw_structure', 'raw_structure_format', 'raw_mw', 'source_formula',
    'source_dose_basis', 'verified_material_graph', 'formula_unit', 'formula_unit_mw', 'predictor_graph', 'material_group_id',
    'material_resolution', 'reviewed_dose_basis', 'material_limitations', 'principal_component')


def resolve_v2(observation, support_candidates, blocked_support, data):
    import hashlib
    validate_observation(observation)
    key = digest(observation)
    require(key in data['inventory'], 'Material observation outside exact reviewed inventory')
    item, decision = data['inventory'][key], data['decisions'][key]
    require(canonical(observation) == canonical(item['observation']) and
            canonical(support_candidates) == canonical(item['support_candidates']) and
            canonical(blocked_support) == canonical(item['blocked_support']), 'Material native/support membership mismatch')
    if decision['status'] != 'verified':
        return dict(status='ReviewedHold' if decision['status'] == 'reviewed_hold' else 'PendingMaterialReview',
                    observation_id=key, reasons=decision['critical_conflicts'], review_evidence=decision['review_evidence'])
    reps = observation['body']['representations']
    diagnostics = graph_diagnostics(reps)
    supplied = [(k, v['value']) for k, v in reps.items() if nonblank(v)]
    facts = decision['formula_unit']
    group = hashlib.sha256(facts['group_smiles'].encode('utf-8')).hexdigest()
    formula = item['source_metadata']['source_formula']
    principal = decision.get('principal_component')
    predictor = Chem.MolFromSmiles(principal['canonical_smiles']) if principal is not None else None
    parent = Chem.MolToSmiles(predictor, canonical=True, isomericSmiles=True) if predictor is not None else facts['canonical_smiles']
    parent_mw = Descriptors.MolWt(predictor) if predictor is not None else facts['mw']
    parent_group = hashlib.sha256(Chem.MolToSmiles(predictor, canonical=True, isomericSmiles=False).encode('utf-8')).hexdigest() if predictor is not None else group
    return dict(status='VerifiedMaterial', observation_id=key, material_schema_version=decision['schema_version'], material_observation_id=key,
        material_binding_sha256=digest(data['context']), material_class=decision['material_class'],
        raw_representations=reps, raw_graph_diagnostics=diagnostics,
        raw_structure=supplied[0][1] if len(supplied) == 1 else None,
        raw_structure_format=supplied[0][0] if len(supplied) == 1 else None,
        raw_mw=next(diagnostics[k]['mw'] for k in ('smiles', 'inchi') if diagnostics[k]['state'] == 'valid')
            if diagnostics['agreement'] in ('single', 'agree') else None,
        source_formula=formula['value'] if formula is not None else None,
        source_dose_basis=observation['candidate_projection'].get('dose_basis'),
        verified_material_graph=facts['canonical_smiles'], formula_unit=facts, formula_unit_mw=facts['mw'],
        predictor_graph=parent, principal_component=principal, material_group_id=group, material_resolution=decision['resolution'],
        reviewed_dose_basis=decision['dose_basis'], material_limitations=decision['limitations'],
        original_structure=facts['canonical_smiles'], original_mw=facts['mw'], parent_smiles=parent,
        parent_mw=parent_mw, connectivity_id=parent_group,
        normalization='verified_organic_principal_component' if principal is not None else 'verified_material_full_graph')


def load_materials_v2(binding, contract, registry, environment, *, integration):
    import qsar_verified_material as material
    keys(binding, ('inventory', 'decisions', 'receipt', 'actual_review'))
    keys(integration, ('schema_version', 'classification_context', 'classification_review'))
    require(type(integration['schema_version']) is int and integration['schema_version'] == 1, 'Unsupported integration version')
    validate_classification_context(integration['classification_context'])
    classification_binding = integration['classification_context']
    require(classification_binding['canonical_contract_sha256'] == digest(contract) and
            decode(material._file(classification_binding['full_contract']).read_text(encoding='utf-8')) == contract,
            'Integrated material full contract mismatch')
    require(decode(material._file(classification_binding['source_registry']).read_text(encoding='utf-8'))['files'] == registry['files'],
            'Integrated material registry mismatch')
    string(contract.get('contract_version'), True)
    expected_execution = dict(schema_version=1, adapter='reviewed_material_full_graph_v1',
        rdkit_version=rdBase.rdkitVersion, mass_algorithm='rdkit_descriptors_molwt',
        predictor_policy='complete_verified_material_graph', grouping_algorithm='sha256_utf8_rdkit_canonical_nonisomeric_smiles')
    execution = contract.get('scientific_policy', {}).get('verified_tested_material_v4', {}).get('execution')
    require(canonical(execution) == canonical(expected_execution), 'Unsupported material execution policy')
    organic_enabled = organic_execution(contract)
    require(environment == {'rdkit_version': rdBase.rdkitVersion}, 'Material environment mismatch')
    provider, replay = registry.get('replay_provider'), registry.get('replay_native')
    keys(provider, ('implementation', 'callable', 'dependencies'))
    require(callable(replay) and provider['callable'] == replay.__qualname__ and
            Path(inspect.getsourcefile(replay)).resolve() == material._file(provider['implementation']),
            'Native replay provider mismatch')
    files = [binding[k] for k in ('inventory', 'decisions', 'receipt')] + [provider['implementation']]
    require(type(provider['dependencies']) is list, 'Replay dependencies required')
    files.extend(provider['dependencies'])
    sources = {}
    for entry in registry['files']:
        require(entry['source_id'] not in sources, 'Duplicate source identity')
        files.append({k: entry[k] for k in ('path', 'sha256')})
        sources[entry['source_id']] = entry
    files.extend(_files(integration))
    for file in files:
        material._file(file)
    context = dict(contract_sha256=digest(contract), implementation_sha256=material.file_binding(material.__file__)['sha256'],
        environment=environment, inventory=binding['inventory'], decisions=binding['decisions'],
        replay_provider=provider, sources=registry['files'], integration=integration)
    receipt = decode(Path(binding['receipt']['path']).read_text(encoding='utf-8'))
    keys(receipt, ('schema_version', 'result', 'synthetic', 'producer_execution_id', 'reviewer_execution_id',
                   'context', 'scope_ids', 'native_review'))
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and receipt['result'] == 'PASS',
            'Material review not accepted')
    require(type(receipt['synthetic']) is bool and receipt['synthetic'] == bool(contract.get('synthetic', False)),
            'Synthetic material review mismatch')
    for field in ('producer_execution_id', 'reviewer_execution_id'):
        string(receipt[field], True)
    require(receipt['producer_execution_id'] != receipt['reviewer_execution_id'], 'Independent material reviewer required')
    require(canonical(receipt['context']) == canonical(context) and canonical(binding['actual_review']) == canonical(dict(receipt, receipt=binding['receipt'])),
            'Stale material review context or actual descriptor')
    require(material._file(receipt['native_review']).stat().st_size > 0, 'Empty native material review')
    files.append(receipt['native_review'])
    inventory, members = {}, set()
    with Path(binding['inventory']['path']).open(encoding='utf-8') as stream:
        for line in stream:
            require(line.strip(), 'Blank material inventory record')
            item = decode(line)
            validate_inventory(item)
            obs = item['observation']
            key, member = digest(obs), member_key(obs)
            require(key not in inventory and member not in members, 'Duplicate material membership')
            require(obs['source_id'] in sources and sources[obs['source_id']]['sha256'] == obs['source_sha256'],
                    'Material source binding mismatch')
            require(canonical(replay(sources[obs['source_id']], obs['native_locator'])) == canonical(obs['body']),
                    'Material native replay mismatch')
            inventory[key] = item
            members.add(member)
    require(receipt['scope_ids'] == sorted(inventory), 'Material review exact scope mismatch')
    decisions = {}
    with Path(binding['decisions']['path']).open(encoding='utf-8') as stream:
        for line in stream:
            require(line.strip(), 'Blank material decision record')
            decision = decode(line)
            key = decision.get('observation_id')
            require(key in inventory and key not in decisions, 'Unknown or duplicate material decision')
            validate_decision(decision, inventory[key], organic_enabled=organic_enabled)
            files.extend(_files(decision.get('principal_component')))
            decisions[key] = decision
    require(set(decisions) == set(inventory), 'Missing material decisions')
    normalized = {}
    for file in files:
        path = os.path.normcase(str(Path(file['path']).resolve()))
        require(path not in normalized or normalized[path]['sha256'] == file['sha256'],
                'Conflicting evidence digests for normalized path')
        normalized[path] = file
    files = list(normalized.values())
    for file in files:
        material._file(file)
    return material.ReviewedMaterials(canonical(dict(schema_version=2, inventory=inventory, decisions=decisions,
        slots=sorted({slot_id(i['observation']) for i in inventory.values()}), context=context, files=files)))


def validate_projection_v2(row, data, *, csv_projection=False):
    key = row.get('material_observation_id')
    require(key in data['inventory'], 'Projection outside material inventory')
    item = data['inventory'][key]
    expected = resolve_v2(item['observation'], item['support_candidates'], item['blocked_support'], data)
    require(expected['status'] == 'VerifiedMaterial', 'Held material cannot have an output projection')
    # CSV already represents both null and an empty scalar as an empty cell.
    # Native evidence and typed producer validation retain the distinction.
    if csv_projection and expected['source_dose_basis'] == '':
        expected['source_dose_basis'] = None
    fields = (*MATERIAL_COLUMNS, 'original_structure', 'original_mw', 'parent_smiles', 'parent_mw', 'connectivity_id', 'normalization')
    require(all(k in row and canonical(row[k]) == canonical(expected[k]) for k in fields), 'Material projection mismatch')
    if 'group_id' in row:
        require(row['group_id'] == expected['connectivity_id'], 'Material partition group mismatch')
    targets = ('target_mg_kg', 'target_mol_kg', 'target_pLD50')
    if any(k in row for k in targets):
        require(all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) for k in targets), 'Invalid typed material target')
        require(row['target_mg_kg'] > 0 and row['target_mol_kg'] > 0 and
            math.isclose(row['target_mol_kg'], row['target_mg_kg'] / (1000 * expected['formula_unit_mw']), rel_tol=1e-12)
            and math.isclose(row['target_pLD50'], -math.log10(row['target_mol_kg']), rel_tol=1e-12, abs_tol=1e-12),
            'Material mass/target mismatch')
    return True


def decode_material_csv(row):
    """Typed declared material projection; reject JSON duplicate/nonfinite values."""
    result = dict(row)
    structured = {'raw_representations', 'raw_graph_diagnostics', 'formula_unit', 'material_resolution', 'material_limitations', 'principal_component'}
    numeric = {'raw_mw', 'formula_unit_mw', 'original_mw', 'parent_mw', 'target_mg_kg', 'target_mol_kg', 'target_pLD50'}
    for key in MATERIAL_COLUMNS:
        require(key in row, 'Missing declared material CSV column')
        if row[key] == '':
            result[key] = None
        elif key in structured:
            result[key] = decode(row[key])
    for key in numeric:
        if key in row:
            if row[key] == '':
                result[key] = None
            else:
                result[key] = float(row[key])
                require(math.isfinite(result[key]), 'Nonfinite material CSV numeric value')
    require(row['material_schema_version'] in ('2', '3'), 'Invalid material CSV schema version')
    result['material_schema_version'] = int(row['material_schema_version'])
    require('group_id' in row and bool(row['group_id']), 'Final material CSV requires partition group')
    require((result['principal_component'] is None) == (result['material_schema_version'] == 2),
            'Material schema and principal component mismatch')
    return result


class MaterialBatch:
    """Explicit finite freshness lifetime; no source replay/hash on row lookup."""
    def __init__(self, binding, contract, registry, environment, *, integration=None):
        self.state = 'NEW'
        self._args = binding, contract, registry, environment
        self._integration = integration
        self._index = self._data = None
        self._used = set()

    def _state(self, expected):
        if self.state != expected:
            previous = self.state
            if self.state in ('NEW', 'COLLECTING', 'READY'):
                self.abort()
            raise MaterialError('Invalid material batch lifecycle: ' + previous)

    def __enter__(self):
        self._state('NEW')
        self.state = 'COLLECTING'
        try:
            import qsar_verified_material as material
            index = material.load_reviewed_materials(*self._args, integration=self._integration)
            self._state('COLLECTING')
            self._index = index
            self._data = material._current(self._index)
            self.state = 'READY'
            return self
        except BaseException:
            self.abort()
            raise

    def resolve(self, observation, support_candidates, blocked_support):
        self._state('READY')
        try:
            import qsar_verified_material as material
            key = digest(observation)
            require(key not in self._used, 'Material observation consumed twice')
            resolver = resolve_v2 if self._data.get('schema_version') == 2 else material._resolve
            result = resolver(observation, support_candidates, blocked_support, self._data)
            require(result['status'] != 'NotApplicable', 'Batch observation outside material inventory')
            self._used.add(key)
            return decode(canonical(result))
        except BaseException:
            self.abort()
            raise

    def validate_projection(self, typed_row):
        self._state('READY')
        try:
            require(self._data.get('schema_version') == 2, 'Batch projection validation requires v2')
            require(typed_row.get('material_observation_id') in self._used, 'Projection precedes material routing')
            return validate_projection_v2(typed_row, self._data)
        except BaseException:
            self.abort()
            raise

    def finish(self):
        self._state('READY')
        try:
            import qsar_verified_material as material
            require(self._used == set(self._data['inventory']), 'Unconsumed material observations')
            require(all(d['status'] != 'unresolved' for d in self._data['decisions'].values()), 'Unresolved material review')
            material._current(self._index)
            result = dict(context_sha256=digest(self._data['context']), references=self._data['files'])
            self.state = 'FINISHED'
            self._data = self._index = None
            return result
        except BaseException:
            self.abort()
            raise

    def abort(self):
        if self.state == 'FINISHED':
            raise MaterialError('Finished material batch cannot be aborted')
        self.state = 'ABORTED'
        self._data = self._index = None

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            if self.state != 'FINISHED':
                self.abort()
            return False
        if self.state != 'FINISHED':
            self.abort()
            raise MaterialError('Material batch exited without successful finish')
        return False


def validate_file(file):
    keys(file, ('path', 'sha256'))
    string(file['path'], True)
    require(Path(file['path']).is_absolute(), 'Material file path must be absolute')
    sha(file['sha256'])


def code_bindings():
    from qsar_verified_material import file_binding
    return sorted((file_binding(Path(__file__).with_name(name)) for name in (
        'qsar_material_curation.py', 'qsar_verified_material.py', 'qsar_data.py', 'qsar_curation_evidence.py',
        'qsar_native_sources.py', 'qsar_archive_sources.py', 'qsar_adjudication.py',
        'qsar_sql_curation.py', 'qsar_sql_resolution.py', 'qsar_sql_coverage.py')), key=lambda f: f['path'])


def validate_classification_context(context):
    keys(context, ('full_contract', 'canonical_contract_sha256', 'accepted_contract_review', 'source_registry',
        'adjudication_config', 'source_files', 'classification', 'environment', 'code', 'predicate_id', 'scope'))
    for field in ('full_contract', 'accepted_contract_review', 'source_registry', 'classification'):
        validate_file(context[field])
    if context['adjudication_config'] is not None:
        validate_file(context['adjudication_config'])
    sha(context['canonical_contract_sha256'])
    require(context['environment'] == {'rdkit_version': rdBase.rdkitVersion}, 'Classification environment mismatch')
    require(context['predicate_id'] == 'pre_identity_v1', 'Unknown preidentity predicate')
    require(context['code'] == code_bindings(), 'Classification code dependency closure mismatch')
    require(type(context['source_files']) is list, 'Source files required')
    ids = []
    for source in context['source_files']:
        keys(source, ('source_id', 'path', 'sha256'))
        string(source['source_id'], True)
        validate_file({k: source[k] for k in ('path', 'sha256')})
        ids.append(source['source_id'])
    require(ids == sorted(set(ids)), 'Classification sources must be sorted and unique')
    scope = context['scope']
    counts = ('observation_count', 'eligible_count', 'nonmaterial_count', 'narrative_field_count')
    hashes = ('physical_slots_sha256', 'observations_sha256', 'eligible_sha256', 'nonmaterial_sha256', 'narrative_fields_sha256')
    keys(scope, (*counts, *hashes))
    require(all(type(scope[k]) is int and scope[k] >= 0 for k in counts), 'Invalid classification count')
    require(scope['observation_count'] == scope['eligible_count'] + scope['nonmaterial_count'], 'Classification partition count mismatch')
    for key in hashes:
        sha(scope[key])


class NativeReplayStore:
    """Disk-backed physical evidence and candidate projection spool owned by one batch."""
    def __init__(self, directory):
        self.db = sqlite3.connect(str(Path(directory) / 'native-material.sqlite3'))
        self.db.execute('PRAGMA cache_size=-8192')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('CREATE TABLE native(slot TEXT PRIMARY KEY, body BLOB) WITHOUT ROWID')
        self.db.execute('CREATE TABLE observation(member TEXT PRIMARY KEY, source TEXT, number INTEGER, sheet TEXT, '
                        'adjudicated INTEGER, raw BLOB, obs BLOB, pre BLOB, support BLOB, eligible INTEGER, used INTEGER DEFAULT 0) WITHOUT ROWID')
        self.db.execute('CREATE TABLE support(id TEXT PRIMARY KEY, body BLOB) WITHOUT ROWID')
        self.db.execute('CREATE TABLE support_key(kind TEXT,value TEXT,id TEXT,PRIMARY KEY(kind,value,id)) WITHOUT ROWID')
        self.db.execute('CREATE TABLE field(source TEXT,id TEXT,slot TEXT,body BLOB,PRIMARY KEY(source,id)) WITHOUT ROWID')
        self.db.execute('CREATE TABLE document(source TEXT,member TEXT,ordinal INTEGER,body BLOB,PRIMARY KEY(source,member,ordinal)) WITHOUT ROWID')
        self.sealed = False

    def replay_native(self, source, locator):
        require(self.sealed, 'Native replay requires a sealed store')
        row = self.db.execute('SELECT body FROM native WHERE slot=?', (digest([source['source_id'], locator]),)).fetchone()
        require(row is not None, 'Native replay outside staged source scope')
        return decode(row[0])

    def observe_support(self, raw):
        import qsar_data as data
        require(not self.sealed, 'Native store is sealed')
        validate_raw_support(raw)
        sid = digest(raw)
        self.db.execute('INSERT INTO support VALUES(?,?)', (sid, canonical(raw)))
        matched = set()
        for kind, fields in raw['identifiers'].items():
            for field in fields:
                value = data.text(field['value'])
                if value and (kind != 'cas' or data.valid_cas(value)):
                    matched.add((kind, value, sid))
        self.db.executemany('INSERT INTO support_key VALUES(?,?,?)', sorted(matched))

    def supports(self, pre):
        import qsar_data as data
        matched = {}
        for kind in ('cas', 'dtxsid', 'npid'):
            value = pre.mapped.get(kind, '')
            if not value or (kind == 'cas' and not data.valid_cas(value)):
                continue
            for sid, body in self.db.execute('SELECT support.id,support.body FROM support JOIN support_key ON support.id=support_key.id '
                                            'WHERE kind=? AND value=?', (kind, value)):
                matched.setdefault(sid, dict(support_id=sid, raw=decode(body), matched_keys=[]))['matched_keys'].append(dict(kind=kind, value=value))
        supports, blockers = [], []
        graphs = set()
        for sid in sorted(matched):
            support = matched[sid]
            support['matched_keys'].sort(key=lambda k: (k['kind'], k['value']))
            supports.append(support)
            raw = support['raw']
            diagnostic = graph_diagnostics(raw['representations'])
            if diagnostic['input_graph_smiles'] is not None:
                graphs.add(diagnostic['input_graph_smiles'])
            reasons = list(raw['native_reasons'])
            if diagnostic['agreement'] in ('invalid', 'conflict'):
                reasons.append('invalid_or_conflicting_support_representations')
            if reasons:
                native = raw['native_reasons']
                category = ('ingestion' if any(r.startswith('missing_required_native_field:') or r == 'native_support_field_count_mismatch' for r in native)
                            else 'identity' if set(native) & NATIVE_SUPPORT_IDENTITY_REASONS or not native else 'chemical_join')
                blocker = dict(category=category,
                    reason='native_support_failure' if raw['native_reasons'] else 'material_identity_conflict',
                    evidence_record_ids=sorted(r['record_id'] for r in raw['records']), original_reasons=reasons)
                blockers.append(dict(blocker_id=digest(blocker), **blocker))
        if len(graphs) > 1:
            blocker = dict(category='identity', reason='material_identity_conflict',
                evidence_record_ids=sorted({r['record_id'] for s in supports for r in s['raw']['records']}),
                original_reasons=['conflicting_structure_candidates'])
            blockers.append(dict(blocker_id=digest(blocker), **blocker))
        return dict(support_candidates=supports, blocked_support=sorted(blockers, key=lambda b: b['blocker_id']))

    def stage(self, obs, raw, pre, number, sheet, adjudicated):
        require(not self.sealed, 'Native store is sealed')
        slot, member = slot_id(obs), member_key(obs)
        prior = self.db.execute('SELECT body FROM native WHERE slot=?', (slot,)).fetchone()
        require(prior is None or prior[0] == canonical(obs['body']), 'Physical siblings disagree on native evidence')
        self.db.execute('INSERT OR IGNORE INTO native VALUES(?,?)', (slot, canonical(obs['body'])))
        support = self.supports(pre)
        self.db.execute('INSERT INTO observation(member,source,number,sheet,adjudicated,raw,obs,pre,support,eligible) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (member, obs['source_id'], number, sheet, int(adjudicated), canonical(native_json(raw)), canonical(obs),
             canonical(native_json(pre._asdict())), canonical(support), int(pre.requires_classification)))
        return member

    def scope(self):
        import hashlib
        def hashed(records):
            h = hashlib.sha256(b'[')
            first = True
            for record in records:
                if not first: h.update(b',')
                h.update(canonical(record))
                first = False
            h.update(b']')
            return h.hexdigest()
        def members(where=''):
            for member, obs, support, pre in self.db.execute('SELECT member,obs,support,pre FROM observation ' + where + ' ORDER BY member'):
                record = dict(member_key=member, observation_id=digest(decode(obs)), support_sha256=digest(decode(support)))
                if where == 'WHERE eligible=0':
                    p = decode(pre)
                    record.update(nonmaterial_reasons=p['nonmaterial_reasons'], extraction_blockers=p['extraction_blockers'])
                yield record
        count = self.db.execute('SELECT COUNT(*),COALESCE(SUM(eligible),0) FROM observation').fetchone()
        slots = (row[0] for row in self.db.execute('SELECT slot FROM native UNION SELECT slot FROM field ORDER BY slot'))
        fields = (dict(source_id=source, field_id=field, field_sha256=digest(decode(body)))
                  for source, field, body in self.db.execute('SELECT source,id,body FROM field ORDER BY source,id'))
        return dict(observation_count=count[0], eligible_count=count[1], nonmaterial_count=count[0] - count[1],
            narrative_field_count=self.db.execute('SELECT COUNT(*) FROM field').fetchone()[0],
            physical_slots_sha256=hashed(slots), observations_sha256=hashed(members()),
            eligible_sha256=hashed(members('WHERE eligible=1')), nonmaterial_sha256=hashed(members('WHERE eligible=0')),
            narrative_fields_sha256=hashed(fields))

    def close(self):
        self.db.close()


def classification_context(packet, contract, registry, scope):
    return dict(full_contract=packet['full_contract'], canonical_contract_sha256=digest(contract),
        accepted_contract_review=packet['accepted_contract_review'], source_registry=packet['source_registry'],
        adjudication_config=packet['adjudication_config'],
        source_files=sorted((dict(source_id=e['source_id'], path=e['path'], sha256=e['sha256'])
                             for e in registry['files']), key=lambda e: e['source_id']),
        classification=packet['classification'], environment=packet['environment'], code=code_bindings(),
        predicate_id='pre_identity_v1', scope=scope)


def _review(descriptor, context, scope, synthetic):
    import qsar_verified_material as material
    keys(descriptor, ('receipt', 'actual_review'))
    receipt = decode(material._file(descriptor['receipt']).read_text(encoding='utf-8'))
    keys(receipt, ('schema_version', 'result', 'synthetic', 'producer_execution_id', 'reviewer_execution_id',
                   'context', 'scope_ids', 'native_review'))
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and receipt['result'] == 'PASS',
            'Classification review not accepted')
    require(type(receipt['synthetic']) is bool and receipt['synthetic'] == synthetic, 'Synthetic classification mismatch')
    for key in ('producer_execution_id', 'reviewer_execution_id'):
        string(receipt[key], True)
    require(receipt['producer_execution_id'] != receipt['reviewer_execution_id'], 'Independent classification reviewer required')
    require(canonical(receipt['context']) == canonical(context) and receipt['scope_ids'] == scope and
            canonical(descriptor['actual_review']) == canonical(dict(receipt, receipt=descriptor['receipt'])), 'Classification review scope/context mismatch')
    require(material._file(receipt['native_review']).stat().st_size > 0, 'Missing classification native review')
    return receipt


def _files(value):
    if type(value) is dict:
        if set(value) == {'path', 'sha256'}:
            validate_file(value)
            yield value
        else:
            for child in value.values():
                yield from _files(child)
    elif type(value) is list:
        for child in value:
            yield from _files(child)


class MaterialCurationBatch:
    """Closed U/E/N/T staging followed by exact reviewed material routing."""
    def __init__(self, packet_path, *, contract_path, registry_path, adjudication_config_path, work_dir):
        self.state = 'NEW'
        self.paths = packet_path, contract_path, registry_path, adjudication_config_path
        self.work_dir = work_dir
        self._temporary = self.store = self.materials = None
        self._observation_cursor = None

    def _state(self, state):
        if self.state != state:
            previous = self.state
            if self.state in ('NEW', 'COLLECTING', 'READY'):
                self.abort()
            raise MaterialError('Invalid material curation lifecycle: ' + previous)

    def __enter__(self):
        self._state('NEW')
        self.state = 'COLLECTING'
        try:
            import qsar_data as data
            import qsar_verified_material as material
            packet_path, contract_path, registry_path, adjudication_path = self.paths
            self.packet_binding = material.file_binding(packet_path)
            self.packet = decode(Path(packet_path).read_text(encoding='utf-8'))
            p = self.packet
            keys(p, ('schema_version', 'kind', 'full_contract', 'accepted_contract_review', 'source_registry',
                     'adjudication_config', 'classification', 'classification_review', 'materials', 'environment', 'replay_provider'))
            require(type(p['schema_version']) is int and p['schema_version'] == 1 and p['kind'] == 'qsar_material_curation_v1',
                    'Unknown material curation packet')
            require(p['full_contract'] == material.file_binding(contract_path) and
                    p['source_registry'] == material.file_binding(registry_path) and
                    p['adjudication_config'] == (material.file_binding(adjudication_path) if adjudication_path else None),
                    'Material packet invocation mismatch')
            require(p['environment'] == {'rdkit_version': rdBase.rdkitVersion}, 'Material packet environment mismatch')
            expected_provider = dict(implementation=material.file_binding(__file__), callable='NativeReplayStore.replay_native',
                dependencies=[b for b in code_bindings() if b['path'] != str(Path(__file__).resolve())])
            require(p['replay_provider'] == expected_provider, 'Material replay dependency closure mismatch')
            self.contract = decode(material._file(p['full_contract']).read_text(encoding='utf-8'))
            self.registry = decode(material._file(p['source_registry']).read_text(encoding='utf-8'))
            self.policy = native_json(self.contract['data_curation'])
            self.configs = {c['source_id']: c for c in self.policy['sources']}
            self.entries = {e['source_id']: e for e in self.registry['files']}
            require(len(self.configs) == len(self.policy['sources']) and len(self.entries) == len(self.registry['files']), 'Duplicate material source')
            acceptance = decode(material._file(p['accepted_contract_review']).read_text(encoding='utf-8'))
            keys(acceptance, ('schema_version', 'status', 'attestation', 'producer_execution_id', 'reviewer_execution_id',
                             'receipt_writer', 'review_evidence', 'findings', 'stage', 'contract_sha256', 'candidate'))
            data.adjudication_module().review_identity_v2(acceptance)
            accepted_candidate = decode(material._file(acceptance['candidate']).read_text(encoding='utf-8'))
            require(acceptance['stage'] == 'contract' and acceptance['contract_sha256'] == p['full_contract']['sha256'] and
                    accepted_candidate.get('contract') == p['full_contract'], 'Accepted material contract mismatch')
            if adjudication_path:
                mapping = decode(Path(adjudication_path).read_text(encoding='utf-8'))
                data.apply_adjudication_bindings(self.policy['sources'], mapping, contract_path)
            self.references = [self.packet_binding, *list(_files(p)), *list(_files(acceptance)),
                *[dict(path=e['path'], sha256=e['sha256']) for e in self.registry['files']], *code_bindings()]
            self.references += list(_files(self.policy))
            for config in self.configs.values():
                narrative = config.get('narrative_adjudication')
                if narrative:
                    narrative_receipt = decode(material._file(narrative['review_receipt']).read_text(encoding='utf-8'))
                    self.references.extend(_files(narrative_receipt))
                    if narrative.get('schema_version') == 2:
                        accepted = decode(material._file(narrative['accepted_contract_review']).read_text(encoding='utf-8'))
                        self.references.extend(_files(accepted))
            by_path = {}
            for file in self.references:
                require(file['path'] not in by_path or by_path[file['path']] == file, 'Conflicting material file bindings')
                by_path[file['path']] = file
            self.references = sorted({f['path']: f for f in self.references}.values(), key=lambda f: f['path'])
            for file in self.references:
                material._file(file)
            self._temporary = tempfile.TemporaryDirectory(prefix='.material-', dir=self.work_dir)
            self.store = NativeReplayStore(self._temporary.name)
            self.scientific_results = {}
            self.sql_inputs = {}
            self.store.db.execute('CREATE TABLE sql_results(member TEXT PRIMARY KEY,source TEXT,id TEXT,body BLOB) WITHOUT ROWID')
            self.store.db.execute('CREATE TABLE scientific(member TEXT PRIMARY KEY,body BLOB) WITHOUT ROWID')
            self.store.db.execute('CREATE TABLE classification(member TEXT PRIMARY KEY,body BLOB) WITHOUT ROWID')
            self.store.db.execute('CREATE TABLE native_field(source TEXT,id TEXT,slot TEXT,PRIMARY KEY(source,id)) WITHOUT ROWID')
            self.inferred_units = {}
            for source, config in self.configs.items():
                require(config['adapter'] in data.ADAPTERS, 'Unsupported material source adapter')
                entry = self.entries[source]
                if config['adapter'] == 'toxric':
                    self.inferred_units[source] = data.toxric_unit(config, self.registry, entry)
                if config['adapter'] == 'hsdb':
                    require('narrative_adjudication' in config, 'Material HSDB requires complete reviewed narrative accounting')
                    for doc in data.archive_sources().iter_hsdb_documents(entry['path']):
                        line = doc['native_lineage']
                        record = native_record(source, entry['sha256'], dict(kind='xml_document',
                            archive_member=line['archive_member'], document_ordinal=line['document_ordinal'], record_id=doc['record_id']),
                            doc['native_record'], line)
                        self.store.db.execute('INSERT INTO document VALUES(?,?,?,?)',
                            (source, line['archive_member'], line['document_ordinal'], canonical(record)))
                    for document in data.adjudication_module().native_fields(entry['path'], source, entry['sha256']):
                        for field in document:
                            if field['candidate_kind'] == 'context_only':
                                continue
                            locator = physical_locator(dict(native_lineage=field['locator']), 'hsdb', 1, '')
                            self.store.db.execute('INSERT INTO native_field VALUES(?,?,?)', (source, field['field_id'], digest([source, locator])))
            return self
        except BaseException:
            self.abort()
            raise

    def observe_support(self, raw):
        self._state('COLLECTING')
        try:
            require(raw['source_id'] in {c['source_id'] for c in self.policy.get('identity_sources', [])}, 'Unconfigured raw support')
            require(raw['source_sha256'] == self.entries[raw['source_id']]['sha256'], 'Raw support source mismatch')
            self.store.observe_support(raw)
        except BaseException:
            self.abort()
            raise

    def stage_observation(self, source_id, number, sheet, raw, *, adjudicated=False, scientific_result=None, sql_result=None):
        self._state('COLLECTING')
        try:
            import qsar_data as data
            config, entry = self.configs[source_id], self.entries[source_id]
            pre = data.pre_identity_observation(raw, config, self.policy, entry, number, sheet,
                                               self.inferred_units.get(source_id), adjudicated, scientific_result=scientific_result, sql_result=sql_result)
            document = None
            if config['adapter'] == 'hsdb':
                line = raw['native_lineage']
                found = self.store.db.execute('SELECT body FROM document WHERE source=? AND member=? AND ordinal=?',
                    (source_id, line['archive_member'], line['document_ordinal'])).fetchone()
                require(found is not None, 'Unknown material native document')
                document = decode(found[0])
            obs = observation(raw, config, entry, number, sheet, adjudicated=adjudicated, document_record=document, sql_result=sql_result)
            if scientific_result is not None:
                obs = observation(raw, config, entry, number, sheet, scientific_result=scientific_result)
            member = self.store.stage(obs, raw, pre, number, sheet, adjudicated)
            if scientific_result is not None:
                self.scientific_results[member] = scientific_result
                self.store.db.execute('INSERT INTO scientific VALUES(?,?)', (member, scientific_result._encoded))
            if sql_result is not None:
                value = sql_result.value(raw)
                self.store.db.execute('INSERT INTO sql_results VALUES(?,?,?,?)',
                    (member, source_id, value['candidate_id'], sql_result._encoded))
            return member
        except BaseException:
            self.abort()
            raise

    def stage_narrative_field(self, source_id, field_ledger_row):
        self._state('COLLECTING')
        try:
            field_id = field_ledger_row['field_id']
            found = self.store.db.execute('SELECT slot FROM native_field WHERE source=? AND id=?', (source_id, field_id)).fetchone()
            require(found is not None, 'Unknown narrative field in material scope')
            self.store.db.execute('INSERT INTO field VALUES(?,?,?,?)', (source_id, field_id, found[0], canonical(field_ledger_row)))
        except BaseException:
            self.abort()
            raise

    def seal_native(self):
        self._state('COLLECTING')
        try:
            import qsar_verified_material as material
            db = self.store.db
            require(not db.execute('SELECT 1 FROM native_field LEFT JOIN field USING(source,id) WHERE field.id IS NULL LIMIT 1').fetchone(),
                    'Omitted native narrative field')
            for source, config in self.configs.items():
                actual_count = db.execute('SELECT COUNT(*) FROM observation WHERE source=?', (source,)).fetchone()[0]
                if config['adapter'] == 'hsdb':
                    expected_ids = []
                    for (body,) in db.execute('SELECT body FROM field WHERE source=?', (source,)):
                        expected_ids.extend(decode(body)['observation_ids'])
                    actual_ids = [decode(raw)['record_id'] for (raw,) in db.execute('SELECT raw FROM observation WHERE source=?', (source,))]
                    require(len(expected_ids) == len(set(expected_ids)) and sorted(actual_ids) == sorted(expected_ids),
                            'Narrative material sibling membership mismatch')
                elif source in self.sql_inputs:
                    session = self.sql_inputs[source]
                    native_count = db.execute('SELECT COUNT(DISTINCT number) FROM observation WHERE source=?', (source,)).fetchone()[0]
                    require(native_count == session.scope['primary']['count'], 'Incomplete SQL material native universe')
                    from contextlib import closing
                    from itertools import zip_longest
                    sentinel = object()
                    with closing(db.execute('SELECT number,MIN(raw),COUNT(DISTINCT raw) FROM observation WHERE source=? GROUP BY number ORDER BY number', (source,))) as actual_native, closing(session.work.execute('SELECT n FROM members ORDER BY st,tu')) as expected_native:
                        for ordinal, (actual_row, expected_row) in enumerate(zip_longest(actual_native, expected_native, fillvalue=sentinel), 1):
                            require(actual_row is not sentinel and expected_row is not sentinel and actual_row[0] == ordinal and actual_row[2] == 1,
                                    'SQL material physical membership differs')
                            require(actual_row[1] == canonical(session.adapter(decode(expected_row[0]))),
                                    'SQL material native projection differs')
                    actual = db.execute('SELECT id FROM sql_results WHERE source=? ORDER BY id', (source,))
                    expected = session.work.execute('SELECT id FROM candidates ORDER BY id')
                    sentinel = object()
                    from itertools import zip_longest
                    require(all(a == b for a, b in zip_longest(actual, expected, fillvalue=sentinel)),
                            'SQL material candidate membership differs')
                else:
                    sheet = {'toxval': 'toxval', 'openfoodtox': 'END_STUDY_REC.HumanHealth'}.get(config['adapter'],
                        config.get('sheet', 'Data' if config['adapter'].startswith('ice_') else ''))
                    tables = [t for t in self.entries[source]['tables'] if t['sheet'] == sheet]
                    require(len(tables) == 1 and actual_count == tables[0]['row_count'], 'Incomplete material native observation universe')
            for (pre,) in db.execute('SELECT pre FROM observation'):
                require(not decode(pre)['extraction_blockers'], 'Unresolved native extraction blocks material integration')
            self.context = classification_context(self.packet, self.contract, self.registry, self.store.scope())
            validate_classification_context(self.context)
            with material._file(self.packet['classification']).open(encoding='utf-8') as stream:
                for line in stream:
                    classification = decode(line)
                    keys(classification, ('schema_version', 'member_key', 'source_id', 'source_sha256', 'native_locator',
                        'candidate_key', 'observation_id', 'support_sha256', 'classification', 'review_evidence'))
                    require(type(classification['schema_version']) is int and classification['schema_version'] == 1, 'Unknown classification version')
                    validate_locator(classification['native_locator'])
                    for field in ('member_key', 'source_sha256', 'observation_id', 'support_sha256'):
                        sha(classification[field])
                    for field in ('source_id', 'candidate_key'):
                        string(classification[field], True)
                    require(classification['classification'] in ('affected', 'unaffected_organic', 'unresolved'), 'Unknown material classification')
                    _evidence(classification['review_evidence'])
                    found = db.execute('SELECT obs,support,eligible FROM observation WHERE member=?', (classification['member_key'],)).fetchone()
                    require(found is not None and found[2] == 1, 'Classification outside preidentity E')
                    obs, support = decode(found[0]), decode(found[1])
                    require(all(classification[k] == obs[k] for k in ('source_id', 'source_sha256', 'native_locator', 'candidate_key'))
                        and classification['observation_id'] == digest(obs) and classification['support_sha256'] == digest(support),
                        'Classification native/support binding mismatch')
                    require(classification['classification'] != 'unresolved', 'Unresolved material classification')
                    require(not db.execute('SELECT 1 FROM classification WHERE member=?', (classification['member_key'],)).fetchone(),
                            'Duplicate material classification')
                    db.execute('INSERT INTO classification VALUES(?,?)', (classification['member_key'], canonical(classification)))
            require(not db.execute('SELECT 1 FROM observation LEFT JOIN classification USING(member) WHERE eligible=1 AND classification.member IS NULL LIMIT 1').fetchone(),
                    'Missing eligible material classification or sibling')
            scope_ids = [r[0] for r in db.execute('SELECT member FROM observation WHERE eligible=1 ORDER BY member')]
            receipt = _review(self.packet['classification_review'], self.context, scope_ids, bool(self.contract.get('synthetic', False)))
            self.references.append(receipt['native_review'])
            self.store.sealed = True
            self.materials = material.material_batch(self.packet['materials'], self.contract,
                dict(self.registry, replay_native=self.store.replay_native, replay_provider=self.packet['replay_provider']),
                self.packet['environment'], integration=dict(schema_version=1, classification_context=self.context,
                    classification_review=self.packet['classification_review']['receipt']))
            self.materials.__enter__()
            affected = {member for member, body in db.execute('SELECT member,body FROM classification') if decode(body)['classification'] == 'affected'}
            actual = set()
            for item in self.materials._data['inventory'].values():
                obs = item['observation']
                member = member_key(obs)
                require(member in affected, 'Material inventory outside affected classification')
                staged = db.execute('SELECT obs,support FROM observation WHERE member=?', (member,)).fetchone()
                require(staged is not None and canonical(obs) == staged[0] and
                        canonical({k: item[k] for k in ('support_candidates', 'blocked_support')}) == staged[1],
                        'Material inventory differs from reconstructed native/support evidence')
                # A known native formula cannot disappear by omitting an annotation.
                for record in obs['body']['records']:
                    literal = record['native_record']
                    if type(literal) is dict and literal.get('source_formula') not in (None, ''):
                        require(item['source_metadata']['source_formula'] == _field(record, 'source_formula'),
                                'Known source formula annotation omitted')
                actual.add(member)
            require(actual == affected, 'Missing affected material inventory or sibling')
            self.state = 'READY'
        except BaseException:
            self.abort()
            raise

    def iter_observations(self):
        self._state('READY')
        if self._observation_cursor is not None:
            self.abort()
            raise MaterialError('Concurrent material observation iteration is unsupported')
        cursor = self.store.db.execute(
            'SELECT source,number,sheet,raw,adjudicated,member FROM observation ORDER BY source,number,member')
        self._observation_cursor = cursor
        try:
            for source, number, sheet, raw, adjudicated, member in cursor:
                self._state('READY')
                yield source, number, sheet, decode(raw), bool(adjudicated), member
        except BaseException:
            if self.state not in ('ABORTED', 'FINISHED'):
                self.abort()
            raise
        finally:
            if self._observation_cursor is cursor:
                cursor.close()
                self._observation_cursor = None

    def sql_result(self, member):
        self._state('READY')
        row = self.store.db.execute('SELECT source,id,body FROM sql_results WHERE member=?', (member,)).fetchone()
        if row is None:
            return None
        from qsar_sql_curation import SQLCandidateResult
        result = SQLCandidateResult(self.sql_inputs[row[0]], row[1])
        require(result._encoded == row[2], 'Staged SQL result changed')
        return result

    def scientific_result(self, member):
        self._state('READY')
        try:
            result = self.scientific_results.get(member)
            row = self.store.db.execute('SELECT body FROM scientific WHERE member=?', (member,)).fetchone()
            require((result is None and row is None) or (result is not None and row is not None and row[0] == result._encoded),
                    'Staged scientific result mismatch')
            return result
        except BaseException:
            self.abort()
            raise

    def route(self, member):
        self._state('READY')
        try:
            found = self.store.db.execute('SELECT obs,support,eligible,used FROM observation WHERE member=?', (member,)).fetchone()
            require(found is not None and found[2] == 1 and found[3] == 0, 'Unknown, nonmaterial or consumed routing member')
            classification = decode(self.store.db.execute('SELECT body FROM classification WHERE member=?', (member,)).fetchone()[0])
            result = None
            route = 'legacy_organic'
            if classification['classification'] == 'affected':
                support = decode(found[1])
                result = self.materials.resolve(decode(found[0]), support['support_candidates'], support['blocked_support'])
                route = {'VerifiedMaterial': 'verified_material', 'ReviewedHold': 'reviewed_hold', 'PendingMaterialReview': 'pending_review'}[result['status']]
            self.store.db.execute('UPDATE observation SET used=1 WHERE member=?', (member,))
            return dict(route=route, member_key=member, classification=classification, material_result=result)
        except BaseException:
            self.abort()
            raise

    def finish(self):
        self._state('READY')
        try:
            import qsar_verified_material as material
            require(not self.store.db.execute('SELECT 1 FROM observation WHERE eligible=1 AND used!=1 LIMIT 1').fetchone(), 'Unconsumed material classification')
            result = self.materials.finish()
            self.references.extend(result['references'])
            references = sorted({f['path']: f for f in self.references}.values(), key=lambda f: f['path'])
            for file in references:
                material._file(file)
            summary = dict(schema_version=1, packet=self.packet_binding, classification_context=self.context,
                           material_context_sha256=result['context_sha256'], references=references)
            self.store.close()
            self.store = None
            self._temporary.cleanup()
            self._temporary = None
            self.state = 'FINISHED'
            return summary
        except BaseException:
            self.abort()
            raise

    def abort(self):
        require(self.state != 'FINISHED', 'Finished material curation cannot abort')
        if self._observation_cursor is not None:
            self._observation_cursor.close()
            self._observation_cursor = None
        if self.materials is not None and self.materials.state != 'FINISHED':
            self.materials.abort()
        if self.store is not None:
            self.store.close()
            self.store = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self.state = 'ABORTED'

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            if self.state != 'FINISHED': self.abort()
            return False
        if self.state != 'FINISHED':
            self.abort()
            raise MaterialError('Material curation exited without successful finish')
        return False


def open_material_curation(packet_path, *, contract_path, registry_path, adjudication_config_path, work_dir):
    return MaterialCurationBatch(packet_path, contract_path=contract_path, registry_path=registry_path,
        adjudication_config_path=adjudication_config_path, work_dir=work_dir)
