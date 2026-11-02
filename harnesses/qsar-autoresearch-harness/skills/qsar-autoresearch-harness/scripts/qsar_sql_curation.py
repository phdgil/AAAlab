"""Indexed SQL curation inputs and actual-effect accounting; no review authority."""
from contextlib import ExitStack, closing
from pathlib import Path
import copy
import hashlib
import json
import sqlite3
import tempfile

import qsar_sql_resolution as q

KIND = 'qsar_sql_curation_inputs_v2'
BASE_OUTPUTS = ('curated.csv', 'dispositions.csv', 'identity_ledger.csv',
                'identity_support_ledger.csv', 'conflict_ledger.csv')


def binding(path):
    return q.small(q.scan.file_binding(path))


def ledger_names(source):
    token = hashlib.sha256(source.encode('utf-8')).hexdigest()
    return ('sql_candidate_consumption_' + token + '.jsonl',
            'sql_source_row_consumption_' + token + '.jsonl')


class SQLCandidateResult:
    """Only an active indexed input session can construct this carrier."""
    __slots__ = ('_owner', '_encoded')

    def __init__(self, owner, candidate_id):
        q.require(type(owner) is SQLCurationInputs, 'Actual indexed SQL inputs required')
        self._owner = owner
        self._encoded = q.canonical(owner.canonical_result(candidate_id))

    def value(self, raw):
        value = q.loads(self._encoded)
        q.require(q.digest(raw) == value['native_adapter_sha256'], 'SQL carrier native adapter differs')
        q.require(self._encoded == q.canonical(self._owner.canonical_result(value['candidate_id'])),
                  'SQL carrier differs from current indexed decision')
        return value

    def projection(self, raw):
        value = self.value(raw)
        return self._owner.project(raw, value['consumed_values'])


class SQLCurationInputs:
    def __init__(self, packet, *, contract_path, registry_path, adjudication_config_path,
                 source_id, work_dir, bind):
        self.packet = packet
        self.paths = contract_path, registry_path, adjudication_config_path
        self.source_id, self.work_dir = source_id, work_dir
        self.files = q.Files(bind)
        self.stack = ExitStack()
        self._cursors = set()

    def __enter__(self):
        try:
            p = self.packet
            q.keys(p, 'schema_version kind full_contract accepted_contract_review review_key profile native_index scan_review support_audit_manifest support_review scope candidate_decisions source_row_decisions')
            q.require(type(p['schema_version']) is int and p['schema_version'] == 1 and p['kind'] == KIND,
                      'Unknown SQL curation input packet')
            q.text(p['review_key'])
            contract_path, registry_path, adjudication_path = self.paths
            q.require(p['full_contract'] == binding(contract_path), 'SQL full contract mismatch')
            self.contract = self.files.json(p['full_contract'])
            q.require(self.contract['scientific_policy']['typed_sql_gap_resolution_v4']['execution'] == q.SELECTOR_V2,
                      'SQL consumer requires exact v2 selector')
            configs = [s for s in self.contract['data_curation']['sources'] if s['source_id'] == self.source_id]
            q.require(len(configs) == 1 and configs[0]['adapter'] == 'toxval' and configs[0].get('required') is True,
                      'SQL consumer source configuration mismatch')
            self.config = configs[0]
            registry = self.files.json(binding(registry_path))
            matches = [e for e in registry['files'] if e['source_id'] == self.source_id]
            q.require(len(matches) == 1, 'SQL registry source mismatch')
            self.entry = matches[0]
            self.files.file(q.small(self.entry))
            mapping = self.files.json(binding(adjudication_path))
            q.require(mapping.get(self.source_id) == p, 'SQL external mapping mismatch')
            self.gaps = binding(Path(registry_path).parent / 'collection_gaps.json')
            q.require(q.scan.obligations(self.files.json(self.gaps), self.source_id), 'SQL original obligations missing')
            accepted = self.files.json(p['accepted_contract_review'])
            q.review(self.files, dict(receipt=p['accepted_contract_review'], receipt_body=accepted), 'accepted_contract')
            q.require(accepted['stage'] == 'contract' and accepted['contract_sha256'] == p['full_contract']['sha256'] and
                      self.files.json(accepted['candidate']).get('contract') == p['full_contract'], 'SQL acceptance mismatch')
            self.profile = self.files.json(p['profile'])
            q.scan.check_profile(self.profile)
            q.require(q.small(self.profile['source']) == q.small(self.entry) and
                      q.small(self.profile['index']) == p['native_index'] and self.profile['code'] == q.scan.code_bindings(),
                      'SQL native profile binding mismatch')
            required = {'toxval_id', 'chemical_id', 'dtxsid', 'toxval_type', 'toxval_numeric', 'toxval_units',
                        'toxval_numeric_qualifier', 'species_original', 'exposure_route'}
            q.require(required <= set(self.profile['tables']['toxval']['columns']), 'Required indexed native ToxVal columns missing')
            reviews = {}
            for role in ('scan', 'support'):
                receipt = self.files.json(p[role + '_review'])
                reviews[role] = q.review(self.files, dict(receipt=p[role + '_review'], receipt_body=receipt,
                         review_evidence=receipt['review_evidence']), role)
            sc = reviews['scan']['context']
            q.keys(sc, 'source profile native_index scanner_code native_helper_code scan_evidence_manifest')
            q.require(sc == dict(source=dict(source_id=self.source_id, artifact=q.small(self.entry), release='unknown'),
                profile=p['profile'], native_index=p['native_index'], scanner_code=binding(q.scan.__file__),
                native_helper_code=binding(q.native.__file__), scan_evidence_manifest=sc['scan_evidence_manifest']),
                'SQL scan review context mismatch')
            self.files.file(sc['scan_evidence_manifest'])
            su = reviews['support']['context']
            q.keys(su, 'scan_review support_audit_manifest support_review_evidence_manifest obligation_ids')
            ids = sorted(o['obligation_id'] for o in q.scan.obligations(self.files.json(self.gaps), self.source_id))
            q.require(su['scan_review'] == p['scan_review'] and su['support_audit_manifest'] == p['support_audit_manifest']
                      and su['obligation_ids'] == ids, 'SQL support review context mismatch')
            self.files.file(su['support_review_evidence_manifest'])
            audit = self.files.json(p['support_audit_manifest'])
            roles = {}
            for descriptor in audit['artifacts']:
                name = Path(descriptor['path']).name
                q.require(name not in roles, 'Duplicate SQL support role')
                roles[name] = q.small(descriptor)
                self.files.file(roles[name])
            q.require(set(q.AUDIT_FILES) | {'field-pairs-' + f + '.jsonl' for f in q.PAIRS} <= roles.keys(),
                      'Incomplete SQL reviewed factual audit catalogue')
            self.scope = self.files.json(p['scope'])
            q.require(self.scope['schema_version'] == 2, 'SQL consumer requires v2 scope')
            q.require(self.scope['native_membership'] == roles['source-scope-membership.jsonl'], 'SQL scope differs from reviewed audit')
            scratch = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.sql-', dir=self.work_dir))
            self.db = self.stack.enter_context(closing(sqlite3.connect(self.files.file(p['native_index']).as_uri() + '?mode=ro', uri=True)))
            self.work = self.stack.enter_context(closing(sqlite3.connect(str(Path(scratch) / 'work.sqlite'))))
            for db in (self.db, self.work):
                db.execute('PRAGMA cache_size=-8192'); db.execute('PRAGMA temp_store=FILE')
            self.db.execute('PRAGMA query_only=ON')
            count = q.scope_and_bridge(self.files, self.db, self.work, self.scope, self.entry, self.profile)
            evidence = dict(schema_version=2, source_id=self.source_id, source_sha256=self.entry['sha256'],
                            contract=p['full_contract'], candidate_decisions=p['candidate_decisions'],
                            source_row_decisions=p['source_row_decisions'])
            q.candidate_decisions(self.files, self.db, self.work, evidence, self.contract, p['review_key'], count)
            q.require(not self.work.execute("SELECT 1 FROM candidates WHERE json_extract(decision,'$.contract_clause') LIKE '%origin_unknown%' LIMIT 1").fetchone(),
                      'SQL unknown-origin quarantine requires a separately accepted typed attachment')
            q.terminal_decisions(self.files, self.db, self.work, evidence, self.scope, self.contract, p['review_key'])
            self.work.executescript('''CREATE INDEX candidates_native ON candidates(n);
                CREATE TABLE visited(st INTEGER,tu INTEGER,PRIMARY KEY(st,tu));
                CREATE TABLE effects(id TEXT PRIMARY KEY,body TEXT);
                CREATE TABLE actual_rows(role TEXT,id TEXT,body TEXT,used INTEGER DEFAULT 0,PRIMARY KEY(role,id));
                CREATE TABLE terminal_effects(st INTEGER,tu INTEGER,body TEXT,PRIMARY KEY(st,tu));
                CREATE TABLE support(role TEXT,key TEXT,body TEXT);
                CREATE INDEX support_key ON support(role,key);''')
            for table, fields in (('source_chemical', ('chemical_id',)), ('record_source', ('toxval_id',)),
                                  ('toxval_relationship', ('toxval_id_1', 'toxval_id_2'))):
                for row in self.db.execute('SELECT * FROM native_rows WHERE table_name=? ORDER BY statement_ordinal,tuple_ordinal', (table,)):
                    raw, lineage = self.native(row)
                    item = q.canonical(dict(record=raw, lineage=lineage)).decode()
                    for key in sorted({q.native.source_text(raw.get(f)) for f in fields}):
                        self.work.execute('INSERT INTO support VALUES(?,?,?)', (table, key, item))
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        for cursor in tuple(self._cursors): cursor.close()
        self._cursors.clear()
        return self.stack.__exit__(*args)

    def _rows(self, query, args=()):
        cursor = self.work.execute(query, args)
        self._cursors.add(cursor)
        try:
            for row in cursor:
                yield row
        finally:
            if cursor in self._cursors:
                cursor.close()
                self._cursors.discard(cursor)

    def native(self, row):
        table, st, line, tu, columns, statement_hash, sql_tuple, body, rh = row
        return q.loads(body), dict(path=str(Path(self.entry['path']).resolve()), table=table, line=line,
            tuple=tu, columns=q.loads(columns), sql_tuple=sql_tuple, statement_sha256=statement_hash)

    def support(self, role, key):
        return [q.loads(r[0]) for r in self.work.execute('SELECT body FROM support WHERE role=? AND key=?',
                                                       (role, q.native.source_text(key)))]

    def adapter(self, n):
        row = self.db.execute('SELECT * FROM native_rows WHERE table_name=? AND statement_ordinal=? AND tuple_ordinal=?',
                              (n['table_name'], n['statement_ordinal'], n['tuple_ordinal'])).fetchone()
        raw, lineage = self.native(row)
        s = q.native.source_text
        relation, original = s(raw.get('toxval_numeric_qualifier')), s(raw.get('toxval_numeric_qualifier_original'))
        why = []
        if original and q.native.RELATIONS.get(original, original) != q.native.RELATIONS.get(relation, relation):
            why.append('current_original_qualifier_conflict')
        # Both native identity channels are evidence; selection controls projection only.
        matches = self.support('source_chemical', raw.get('chemical_id'))
        for item in self.support('source_chemical', raw.get('chemical_id_original', raw.get('chemical_id'))):
            if item not in matches: matches.append(item)
        lineage['source_chemical_matches'] = matches
        result = dict(record_id=s(raw['toxval_id']), dtxsid=s(raw.get('dtxsid')), cas='', smiles='', inchi='', npid='',
            species=s(raw.get('species_original')), route=s(raw.get('exposure_route')), endpoint=s(raw.get('toxval_type')),
            value=s(raw.get('toxval_numeric')), unit=s(raw.get('toxval_units')), relation=relation,
            normalized_relation=q.native.RELATIONS.get(relation, 'unknown'),
            relation_origin='source_normalized_column_not_verified_original_qualifier',
            original_value=raw.get('toxval_numeric_original'), original_unit=raw.get('toxval_units_original'),
            original_relation=raw.get('toxval_numeric_qualifier_original'), sex=s(raw.get('sex')),
            test_material=s(raw.get('exposure_form')), reference=' | '.join(s(raw.get(k)) for k in ('source','subsource','year') if s(raw.get(k))),
            source_url=s(raw.get('source_url')), native_record=raw, native_lineage=lineage,
            metadata_warnings=[], qc_status=raw.get('qc_status'), status='quarantined' if why else 'candidate', why=why)
        return self.project(result, q._values(raw, False))

    def project(self, raw, values):
        result = copy.deepcopy(raw)
        s = q.native.source_text
        for name, field in dict(endpoint='endpoint', species='species', route='route', qualifier='relation',
                                numeric='value', units='unit', dtxsid='dtxsid').items():
            result[field] = s(values[name])
        result['why'] = [x for x in result['why'] if x not in ('ambiguous_source_chemical_join','source_chemical_dtxsid_conflict')]
        result['metadata_warnings'] = []
        result['cas'] = ''
        matches = self.support('source_chemical', values['chemical_id'])
        if len(matches) > 1: result['why'].append('ambiguous_source_chemical_join')
        elif not matches: result['metadata_warnings'].append('missing_source_chemical_join')
        else:
            chemical = matches[0]['record']; linked = s(chemical.get('dtxsid'))
            if result['dtxsid'] and linked and result['dtxsid'] != linked:
                result['why'].append('source_chemical_dtxsid_conflict')
            else:
                result['dtxsid'] = result['dtxsid'] or linked
                result['cas'] = s(chemical.get('casrn'))
        result['status'] = 'quarantined' if result['why'] else 'candidate'
        return result

    def iter_primary(self):
        for number, (body,) in enumerate(self._rows('SELECT n FROM members ORDER BY st,tu'), 1):
            n = q.loads(body)
            self.work.execute('INSERT INTO visited VALUES(?,?)', (n['statement_ordinal'], n['tuple_ordinal']))
            terminal = self.work.execute('SELECT decision FROM terminals WHERE st=? AND tu=?',
                                        (n['statement_ordinal'], n['tuple_ordinal'])).fetchone()
            yield number, n, self.adapter(n), q.loads(terminal[0]) if terminal else None

    def canonical_result(self, candidate_id):
        row = self.work.execute('SELECT n,decision FROM candidates WHERE id=?', (candidate_id,)).fetchone()
        q.require(row is not None, 'Unknown SQL candidate')
        n, d = map(q.loads, row)
        return dict(schema_version=1, kind='sql_candidate_result_v2', source_sha256=self.entry['sha256'], native=n,
            native_adapter_sha256=q.digest(self.adapter(n)), candidate_id=candidate_id, measurement_id=d['measurement_id'],
            population_id=d['population_id'], decision_id=d['decision_id'], disposition=d['disposition'],
            contract_clause=d['contract_clause'], consumed_values=d['consumed_values'])

    def candidates(self, native):
        for candidate_id, in self._rows('SELECT id FROM candidates WHERE n=? ORDER BY id', (q.canonical(native).decode(),)):
            yield SQLCandidateResult(self, candidate_id)

    def record(self, result, *, disposition, identity_record, emitted_rows):
        value = q.loads(result._encoded)
        q.require(result._owner is self and result._encoded == q.canonical(self.canonical_result(value['candidate_id'])),
                  'Foreign or changed SQL candidate')
        uid = self.source_id + ':toxval:candidate:' + value['candidate_id']
        q.require(disposition['row_id'] == identity_record['row_id'] == uid, 'SQL effect identity mismatch')
        ids = [r['output_row_id'] for r in emitted_rows]
        q.require(disposition['output_row_ids'] == ids and len(ids) == len(set(ids)), 'SQL effect output membership mismatch')
        state = value['disposition']
        if state == 'preserve':
            q.require(disposition['disposition'] == 'retained' and ids, 'Preserve decision lost during actual curation')
            for row in emitted_rows:
                q.require(all(row[f] == q.native.source_text(value['consumed_values'][k]) for f,k in
                              (('raw_value','numeric'),('raw_unit','units'),('raw_relation','qualifier'))),
                          'SQL selected values differ from actual emitted values')
                q.require(row['row_id'] == uid and row['output_row_id'] == uid + ':' + row['task_id'], 'SQL emitted identity mismatch')
        else:
            q.require(not ids and disposition['disposition'] == ('quarantined' if state == 'scientific_quarantine' else 'excluded'),
                      'SQL exclusion/quarantine emitted output or changed action')
        d = q.loads(self.work.execute('SELECT decision FROM candidates WHERE id=?', (value['candidate_id'],)).fetchone()[0])
        effect = dict(candidate_id=value['candidate_id'], decision_id=value['decision_id'], native_locator=q.locator(value['native']),
            action='preserved' if state == 'preserve' else state, consumed_values=value['consumed_values'],
            output_observation_ids=ids, output_artifacts=[], clause=value['contract_clause'], rationale_evidence=d['rationale_evidence'])
        self.work.execute('INSERT INTO effects VALUES(?,?)', (value['candidate_id'], q.canonical(effect).decode()))
        for role, row in [('dispositions.csv', disposition), ('identity_ledger.csv', identity_record),
                          *[('curated.csv', r) for r in emitted_rows]]:
            identifier = row['output_row_id'] if role == 'curated.csv' else row['row_id']
            self.work.execute('INSERT INTO actual_rows(role,id,body) VALUES(?,?,?)', (role, identifier, q.canonical(row).decode()))

    def record_terminal(self, native, *, disposition, identity_record, emitted_rows):
        order = native['statement_ordinal'], native['tuple_ordinal']
        row = self.work.execute('SELECT decision FROM terminals WHERE st=? AND tu=?', order).fetchone()
        q.require(row is not None, 'Unknown source-row terminal')
        d = q.loads(row[0])
        q.require(not emitted_rows and disposition['output_row_ids'] == [] and disposition['disposition'] == 'excluded'
                  and disposition['row_id'] == identity_record['row_id'], 'Source-row terminal emitted output')
        effect = dict(kind=d['kind'], native=native, decision_id=d['decision_id'], action='excluded', clause=d['contract_clause'],
                      output_observation_ids=[], output_artifacts=[], output_labels=[])
        self.work.execute('INSERT INTO terminal_effects VALUES(?,?,?)', (*order, q.canonical(effect).decode()))

    def selected_metadata(self, ids):
        references, relationships = {}, {}
        for key in sorted(ids):
            a, b = self.support('record_source', key), self.support('toxval_relationship', key)
            if a: references[key] = a
            if b: relationships[key] = b
        return references, relationships

    def finish(self, *, output_dir, output_bindings):
        q.require(self.work.execute('SELECT COUNT(*) FROM visited').fetchone()[0] == self.scope['primary']['count'],
                  'Missing primary SQL execution')
        q.require(self.work.execute('SELECT COUNT(*) FROM effects').fetchone()[0] == self.scope['candidate_membership']['count'],
                  'Missing SQL candidate actual consumption')
        q.require(self.work.execute('SELECT COUNT(*) FROM terminal_effects').fetchone()[0] == self.scope['source_row_terminals']['count'],
                  'Missing SQL terminal actual consumption')
        for name in BASE_OUTPUTS:
            q.require(output_bindings[name] == binding(Path(output_dir) / name), 'SQL fixed output binding mismatch')
        import qsar_curation_evidence as guard
        for role in ('dispositions.csv', 'identity_ledger.csv', 'curated.csv'):
            for actual in guard.csv_rows(Path(output_dir) / role):
                identifier = actual['output_row_id'] if role == 'curated.csv' else actual['row_id']
                found = self.work.execute('SELECT body,used FROM actual_rows WHERE role=? AND id=?', (role, identifier)).fetchone()
                if found is None:
                    continue  # Other sources and physical noncandidate rows are replayed by the common builder.
                q.require(not found[1], 'Duplicate SQL serialized row')
                for field, expected in q.loads(found[0]).items():
                    if field in ('flags', 'replicate_flag'):
                        continue  # Deterministic selected metadata/grouping runs after record().
                    observed = actual.get(field, '')
                    equal = q.loads(observed) == expected if isinstance(expected, (dict, list)) else observed == ('' if expected is None else str(expected))
                    q.require(equal, 'SQL serialization differs from actual effect: ' + role + ':' + field)
                self.work.execute('UPDATE actual_rows SET used=1 WHERE role=? AND id=?', (role, identifier))
        q.require(not self.work.execute('SELECT 1 FROM actual_rows WHERE used=0 LIMIT 1').fetchone(), 'Missing SQL serialized effect')
        self.work.execute('CREATE TABLE serialized(id TEXT PRIMARY KEY)')
        for row in guard.csv_rows(Path(output_dir) / 'curated.csv'):
            if row['source_id'] == self.source_id:
                self.work.execute('INSERT INTO serialized VALUES(?)', (row['output_row_id'],))
        candidate_name, terminal_name = ledger_names(self.source_id)
        with (Path(output_dir) / candidate_name).open('wb') as stream:
            for body, in self.work.execute('SELECT body FROM effects ORDER BY id'):
                effect = q.loads(body)
                for oid in effect['output_observation_ids']:
                    q.require(self.work.execute('DELETE FROM serialized WHERE id=?', (oid,)).rowcount == 1,
                              'SQL actual emitted ID missing from serialization')
                if effect['output_observation_ids']: effect['output_artifacts'] = [output_bindings['curated.csv']]
                stream.write(q.canonical(effect) + b'\n')
        q.require(not self.work.execute('SELECT 1 FROM serialized LIMIT 1').fetchone(), 'Unaccounted SQL serialized output')
        h, count = hashlib.sha256(), 0
        with (Path(output_dir) / terminal_name).open('wb') as stream:
            for body, in self.work.execute('SELECT body FROM terminal_effects ORDER BY st,tu'):
                effect = q.loads(body); q._hash_add(h, effect); count += 1
                stream.write(q.canonical(effect) + b'\n')
        consumption = dict(consumer_code=binding(Path(__file__).with_name('qsar_data.py')),
            ledger=binding(Path(output_dir) / candidate_name), source_row_ledger=binding(Path(output_dir) / terminal_name),
            source_row_membership=dict(count=count, members_sha256=h.hexdigest()),
            output_artifacts=sorted([output_bindings[n] for n in BASE_OUTPUTS], key=lambda b:(b['path'], b['sha256'])))
        return dict(expected_scope=self.scope, expected_consumption=consumption,
                    references=sorted(self.files.done(), key=lambda b:b['path']))


def _compare_outputs(original, replay, sources):
    """Compare every serialized field; only ledger artifact locations may differ."""
    import itertools
    import qsar_curation_evidence as guard
    q.require(set(original) == set(replay), 'SQL replay output role mismatch')
    sentinel = object()
    ledgers = {ledger_names(s)[0] for s in sources}
    for name in original:
        a, b = original[name], replay[name]
        if name.endswith('.csv'):
            left, right = guard.csv_rows(a['path']), guard.csv_rows(b['path'])
        else:
            left, right = guard.json_rows(a['path']), guard.json_rows(b['path'])
        with closing(left), closing(right):
            for x, y in itertools.zip_longest(left, right, fillvalue=sentinel):
                q.require(x is not sentinel and y is not sentinel, 'SQL replay output membership mismatch: ' + name)
                if name in ledgers:
                    expected = [original['curated.csv']] if y['output_observation_ids'] else []
                    q.require(x['output_artifacts'] == expected, 'SQL consumption fixed artifact role mismatch')
                    y = dict(y, output_artifacts=expected)
                q.require(x == y, 'SQL replay serialized output mismatch: ' + name)


def validate_sql_finalization(diagnostic_path, review_config_path, *, candidate_outputs=None, collection_outputs=None):
    import qsar_data as data
    import qsar_curation_evidence as guard
    refs = []
    files = q.Files(refs.append)
    q.require(Path(diagnostic_path).name == 'curation_diagnostic.json', 'SQL diagnostic fixed filename required')
    diagnostic_binding, review_binding = binding(diagnostic_path), binding(review_config_path)
    diagnostic = files.json(diagnostic_binding)
    envelope = files.json(review_binding)
    q.keys(envelope, 'schema_version diagnostic sources')
    q.require(type(envelope['schema_version']) is int and envelope['schema_version'] == 1 and envelope['diagnostic'] == diagnostic_binding,
              'SQL finalization diagnostic binding mismatch')
    q.require('sql_finalization' not in diagnostic and diagnostic['status'] == 'blocked', 'SQL diagnostic cannot claim finalization')
    integration = diagnostic['sql_integration']
    q.keys(integration, 'schema_version phase sources')
    q.require(type(integration['schema_version']) is int and integration['schema_version'] == 1 and integration['phase'] == 'diagnostic',
              'Wrong SQL diagnostic phase')
    from qsar_material_curation import _files
    for descriptor in _files(diagnostic): files.file(descriptor)
    evidence = diagnostic['bound_evidence']
    contract_path = files.file(evidence['contract'])
    registry_path = files.file(evidence['registry'])
    contract, registry = files.json(evidence['contract']), files.json(evidence['registry'])
    external = diagnostic['external_adjudication_config']
    mapping = files.json(external)
    sql_sources = {s: p for s, p in mapping.items() if type(p) is dict and p.get('kind') == KIND}
    q.require(sql_sources and set(sql_sources) == set(integration['sources']) == set(envelope['sources']), 'SQL finalization source-set mismatch')
    originals = evidence['outputs']
    expected_names = set(BASE_OUTPUTS)
    for source in sql_sources: expected_names.update(ledger_names(source))
    for config in diagnostic['policy']['sources']:
        if config.get('narrative_adjudication'):
            expected_names.add('narrative_fields_' + hashlib.sha256(config['source_id'].encode()).hexdigest()[:16] + '.jsonl')
    q.require(set(originals) == expected_names, 'SQL fixed output roles differ')
    parent = Path(diagnostic_path).resolve().parent
    for name, descriptor in originals.items():
        q.require(files.file(descriptor) == parent / name, 'SQL output outside immutable diagnostic directory')
    material = diagnostic.get('external_material_config')
    with tempfile.TemporaryDirectory(prefix='qsar-sql-replay-') as scratch:
        built = data.build_curation(contract_path, registry_path, Path(scratch) / 'outputs',
            adjudication_config_path=files.file(external), material_config_path=files.file(material) if material else None)
        expected = built['summary']
        _compare_outputs(originals, built['outputs'], sql_sources)
        expected['bound_evidence']['outputs'] = originals
        for source in sql_sources:
            actual = integration['sources'][source]
            rebuilt = expected['sql_integration']['sources'][source]
            q.keys(actual, 'expected_scope expected_consumption')
            q.require(actual['expected_scope'] == rebuilt['expected_scope'], 'SQL diagnostic scope differs from replay')
            ec = copy.deepcopy(rebuilt['expected_consumption'])
            cn, tn = ledger_names(source)
            ec.update(ledger=originals[cn], source_row_ledger=originals[tn],
                      output_artifacts=sorted([originals[n] for n in BASE_OUTPUTS], key=lambda b:(b['path'], b['sha256'])))
            q.require(actual['expected_consumption'] == ec, 'SQL actual consumption differs from replay')
            rebuilt['expected_consumption'] = ec
        q.require(diagnostic == expected, 'SQL diagnostic summary differs from complete replay')
        for descriptor in built['references']: files.file(descriptor)
    configs = {s['source_id']: s for s in contract['data_curation']['sources']}
    entries = {e['source_id']: e for e in registry['files']}
    closures = {}
    for source, packet in sql_sources.items():
        review = envelope['sources'][source]
        q.keys(review, 'resolutions scientific_review actual_review')
        actual = integration['sources'][source]
        resolutions = files.json(review['resolutions'])
        q.require(all(diagnostic_binding in r['evidence'] for r in resolutions), 'Resolution omits actual diagnostic dependency')
        science = files.json(review['scientific_review'])
        q.require(science['review_key'] == packet['review_key'], 'SQL review key changed after consumption')
        e = dict(schema_version=2, adapter=q.SELECTOR_V2['adapter'], contract=evidence['contract'],
            accepted_contract_review=packet['accepted_contract_review'], source_id=source, source_sha256=entries[source]['sha256'],
            source_release='unknown', original_gap_artifact=evidence['collection']['gaps'],
            **{k:packet[k] for k in ('profile','native_index','scan_review','support_audit_manifest','support_review','candidate_decisions','source_row_decisions')},
            scope_membership=actual['expected_scope']['native_membership'],
            candidate_consumption=actual['expected_consumption']['ledger'], source_row_consumption=actual['expected_consumption']['source_row_ledger'],
            resolutions=review['resolutions'], scientific_review=review['scientific_review'])
        result = q.validate(evidence['contract'], packet['accepted_contract_review'], configs[source], entries[source],
            evidence['collection']['gaps'], e, actual['expected_scope'], actual['expected_consumption'], review['actual_review'], refs.append)
        closures[source] = result['obligation_ids']
    final = copy.deepcopy(diagnostic)
    final['status'] = 'limited' if final['blockers'] else 'produced'
    final['sql_integration']['phase'] = 'final'
    final['sql_obligation_closures'] = closures
    final['sql_finalization'] = dict(schema_version=1, diagnostic=diagnostic_binding, review_config=review_binding)
    refs.extend(guard._validate_summary(contract_path, contract, final, None, None, collection_outputs,
                                       sql_closures=closures))
    if candidate_outputs is not None:
        accepted = {(str(Path(b['path']).resolve()), b['sha256']) for b in candidate_outputs}
        q.require(all((b['path'], b['sha256']) in accepted for b in originals.values()), 'SQL fixed outputs not in candidate')
    refs.extend(files.done())
    distinct = {}
    for descriptor in refs:
        normalized = Path(descriptor['path']).resolve()
        previous = distinct.get(normalized)
        q.require(previous is None or previous['sha256'] == descriptor['sha256'],
                  'Conflicting finalization dependency SHA256')
        distinct.setdefault(normalized, descriptor)
    for descriptor in distinct.values(): guard.checked(descriptor)
    return dict(summary=final, references=sorted({b['path']:b for b in refs}.values(), key=lambda b:b['path']))


def finalize_curation(diagnostic_path, sql_review_config_path):
    import qsar_data as data
    parent = Path(diagnostic_path).resolve().parent
    names = ('curation_manifest.json', 'manifest.json')
    q.require(all(not (parent / n).exists() for n in names), 'SQL final markers already exist')
    validated = validate_sql_finalization(diagnostic_path, sql_review_config_path)
    summary = validated['summary']
    owned = []
    try:
        # Fixed outputs and diagnostic are the only published handoff roles.
        with (parent / names[0]).open('x', encoding='utf-8') as stream:
            owned.append(names[0])
            json.dump(summary, stream, ensure_ascii=False, indent=2)
        outputs = sorted([b['path'] for b in summary['bound_evidence']['outputs'].values()] +
                         [str(parent / 'curation_diagnostic.json'), str(parent / names[0])])
        handoff = dict(schema_version=1, adapter_version=data.VERSION, stage='curation', outputs=outputs,
            output_hashes={path: data.digest(path) for path in outputs},
            summary='Curation produced; independent review required',
            checks=['SQL finalization and complete native replay verified'], limitations=summary['limitations'], coverage=summary)
        with (parent / names[1]).open('x', encoding='utf-8') as stream:
            owned.append(names[1])
            json.dump(handoff, stream, ensure_ascii=False, indent=2)
        return handoff
    except BaseException:
        for name in owned: (parent / name).unlink(missing_ok=True)
        raise
