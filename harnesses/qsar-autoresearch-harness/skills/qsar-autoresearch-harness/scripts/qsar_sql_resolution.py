"""Read-only SQL closure checks; caller attestations are an explicit trust boundary.

No runtime activation, correction, waiver or scientific receipt is produced.
"""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile

import qsar_adjudication as identity
import qsar_native_sources as native
import qsar_sql_coverage as scan


class SQLResolutionError(ValueError):
    pass


SELECTOR = dict(schema_version=1, adapter='toxval_reviewed_support_resolution_v1',
                scope_algorithm='toxval_native_membership_v1', candidate_bridge='sql_native_candidate_bridge_v1',
                candidate_identity='adjudication_v2_identity_with_sql_anchors_v1',
                review_envelope='sql_resolution_leader_attested_v1', corrections='unsupported')
SELECTOR_V2 = dict(SELECTOR, schema_version=2, adapter='toxval_reviewed_support_resolution_v2',
                   candidate_bridge='sql_native_candidate_bridge_v2',
                   source_row_terminal='source_row_terminal_exclusion_v1')
ATTESTATION = 'local_leader_attested_not_authenticated'
VALUES = dict(endpoint='toxval_type', species='species_original', route='exposure_route',
              qualifier='toxval_numeric_qualifier', numeric='toxval_numeric',
              units='toxval_units', chemical_id='chemical_id', dtxsid='dtxsid')
FIX_FIELDS = ('exposure_form exposure_method exposure_route generation lifestage media sex '
              'study_duration_class study_duration_units study_type toxval_numeric_qualifier '
              'toxval_subtype toxval_type toxval_units').split()
PAIRS = ('toxval_type toxval_subtype toxval_numeric toxval_units toxval_numeric_qualifier '
         'study_type study_duration_class study_duration_value study_duration_units strain sex '
         'critical_effect population exposure_route exposure_method exposure_form media lifestage generation year').split()
AUDIT_FILES = ('table-role-ledger.json join-and-scope-summary.json source-scope-membership.jsonl '
               'support-cross-join-summary.json species-comparison.json species-alias-disagreements.jsonl '
               'dictionary-native.jsonl endpoint-dictionary-native.jsonl all-field-definition-effects.json '
               'fix-effects-ledger.json fix-effect-counts.json relevant-fix-effects-membership.jsonl '
               'conflicting-fix-affected-records.jsonl conflicting-fix-scope-summary.json '
               'source-info-alternative-summary.json relevant-source-role-ledger.json '
               'relationship_types.jsonl uf_types.jsonl nonprimary-role-evidence.json '
               'unresolved-implications-ledger.json projection-correspondence-verification.json verification.json').split()
JOIN_ROLES = ('chemical__dtxsid__dtxsid source_chemical__chemical_id__chemical_id species__species_id__species_id '
              'record_source__toxval_id__toxval_id source_info__source__source source_info__source_table__source_table '
              'source_info__subsource__subsource toxval_type_dictionary__toxval_type__toxval_type '
              'toxval_relationship__toxval_id__toxval_id_1 toxval_relationship__toxval_id__toxval_id_2 '
              'toxval_uf__toxval_id__toxval_id toxval_uf__toxval_id__parent_id').split()
AUDIT_FILES += [prefix + role + '.jsonl' for role in JOIN_ROLES for prefix in ('join-anomalies-', 'orphan-support-')]
AUDIT_FILES += ['source-info-alternatives-' + role + '.jsonl' for role in
                ('numeric_source_id', 'case_insensitive_source', 'source_subsource', 'source_table')]
AUDIT_FILES += ['nonprimary-lexical-' + table + '.jsonl' for table in
                ('bcfbaf', 'cancer_summary', 'genetox_details', 'genetox_summary', 'skin_eye')]
NATIVE_KEYS = 'source_id table_name statement_ordinal tuple_ordinal line toxval_id row_sha256'
LOCATOR_KEYS = 'table_name statement_ordinal tuple_ordinal line'
DECISION_KEYS = ('candidate_id measurement_id population_id native_locator toxval_id native_row_sha256 '
                 'decision_id disposition extraction_complete contract_clause original current consumed_values '
                 'support_native_locators applicable_fix_ids audit_anomaly_membership missing_optional_metadata '
                 'rationale_evidence critical_metadata_review')
CONSUMPTION_KEYS = ('candidate_id decision_id native_locator action consumed_values output_observation_ids '
                    'output_artifacts clause rationale_evidence')
RESOLUTION_KEYS = ('original_gap_artifact original_gap_sha256 table_name obligation_id resolution_id '
                   'source_id source_sha256 source_release schema scientific_role exact_scope disposition '
                   'counts joins dictionary_fix_effects evidence reviewer_receipt')
REVIEW_KEYS = ('schema_version status producer_execution_id reviewer_execution_id receipt_writer '
              'attestation review_evidence findings')
OPTIONAL = {'source_study_id', 'reference_detail', 'source_info_numeric_id',
            'source_info_label_match', 'optional_strain_detail'}


def require(condition, message):
    if not condition:
        raise SQLResolutionError(message)


def keys(value, names):
    expected = set(names.split() if isinstance(names, str) else names)
    require(isinstance(value, dict) and set(value) == expected, 'Exact keys required: ' + ','.join(sorted(expected)))


def text(value):
    require(isinstance(value, str) and bool(value.strip()), 'Nonempty text required')


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, 'Invalid integer')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(items):
    out = {}
    for k, v in items:
        require(k not in out, 'Duplicate JSON key')
        out[k] = v
    return out


def loads(raw):
    def nonfinite(_):
        raise SQLResolutionError('Nonfinite JSON')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=nonfinite)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, float):
                require(math.isfinite(item), 'Nonfinite JSON number')
            elif isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value
    except (ValueError, UnicodeError) as exc:
        raise SQLResolutionError('Invalid strict JSON: ' + str(exc)) from exc


def _sha(value):
    require(isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value), 'Invalid SHA256')


def small(binding):
    return {k: binding[k] for k in ('path', 'sha256')}


class Files:
    def __init__(self, bind):
        require(callable(bind), 'Binding collector required')
        self.bind, self.cache = bind, {}

    def file(self, descriptor):
        keys(descriptor, 'path sha256'); text(descriptor['path']); _sha(descriptor['sha256'])
        p = Path(descriptor['path']).resolve()
        if str(p) in self.cache:
            require(self.cache[str(p)][0]['sha256'] == descriptor['sha256'], 'Conflicting file binding')
        else:
            require(p.is_file(), 'Missing bound file: ' + str(p))
            before = p.stat()
            h = hashlib.sha256()
            with p.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            require(h.hexdigest() == descriptor['sha256'], 'Stale file binding: ' + str(p))
            stat = p.stat()
            require((before.st_size, before.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns),
                    'File changed while hashing: ' + str(p))
            self.cache[str(p)] = (dict(descriptor), stat.st_size, stat.st_mtime_ns)
            self.bind(dict(descriptor))
        return p

    def json(self, descriptor):
        p = self.file(descriptor)
        require(p.stat().st_size <= 16 * 1024 * 1024, 'Structured artifact exceeds bounded loader')
        return loads(p.read_bytes())

    def lines(self, descriptor):
        with self.file(descriptor).open('rb') as stream:
            while True:
                line = stream.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                require(len(line) <= 16 * 1024 * 1024 and line.strip(), 'Invalid JSONL record')
                row = loads(line)
                require(isinstance(row, dict), 'JSONL objects required; arrays unsupported')
                yield row

    def done(self):
        for path, (_, size, mtime) in self.cache.items():
            stat = Path(path).stat()
            require((stat.st_size, stat.st_mtime_ns) == (size, mtime), 'Bound input changed during validation')
        return [x[0] for x in self.cache.values()]


def review(files, envelope, kind):
    keys(envelope, 'receipt receipt_body' if kind == 'accepted_contract' else 'receipt receipt_body review_evidence')
    body = files.json(envelope['receipt'])
    require(canonical(body) == canonical(envelope['receipt_body']), 'Caller-attested review differs from file')
    extra = ('stage contract_sha256 candidate' if kind == 'accepted_contract' else
             'review_key context' if kind == 'scientific' else 'phase native_receipt context')
    keys(body, REVIEW_KEYS + ' ' + extra)
    require(type(body['schema_version']) is int and body['schema_version'] == (2 if kind == 'accepted_contract' else 1), 'Review schema')
    require(body['status'] == 'PASS' and body['attestation'] == ATTESTATION and body['findings'] == [], 'Review not accepted')
    for k in ('producer_execution_id', 'reviewer_execution_id', 'receipt_writer'):
        text(body[k])
    require(body['producer_execution_id'].strip() != body['reviewer_execution_id'].strip(), 'Separate review required')
    require(files.file(body['review_evidence']).stat().st_size > 0, 'Empty returned review')
    if kind != 'accepted_contract':
        require(envelope['review_evidence'] == body['review_evidence'], 'Returned review mismatch')
    if kind in ('scan', 'support'):
        require(body['phase'] == kind, 'Wrong review phase'); files.file(body['native_receipt'])
    return body


def native_row(db, loc):
    keys(loc, LOCATOR_KEYS); text(loc['table_name'])
    for k in ('statement_ordinal', 'tuple_ordinal', 'line'):
        integer(loc[k], 1)
    row = db.execute('SELECT body_json,row_sha256,line FROM native_rows WHERE table_name=? AND statement_ordinal=? AND tuple_ordinal=?',
                     (loc['table_name'], loc['statement_ordinal'], loc['tuple_ordinal'])).fetchone()
    require(row is not None and row[2] == loc['line'], 'Missing/wrong native locator')
    return loads(row[0]), row[1]


def locator(n):
    return {k: n[k] for k in LOCATOR_KEYS.split()}


def anchor(db, a, sid, source_hash):
    keys(a, 'field_id field start end quote native_locator')
    text(a['field']); text(a['quote']); integer(a['start']); integer(a['end'], 1)
    body, _ = native_row(db, a['native_locator'])
    value = body.get(a['field'])
    require(isinstance(value, str) and a['start'] < a['end'] <= len(value) and value[a['start']:a['end']] == a['quote'], 'Native anchor mismatch')
    require(a['field_id'] == digest(dict(source_id=sid, source_sha256=source_hash,
                                       native_locator=a['native_locator'], field=a['field'])), 'Anchor field ID mismatch')


def _member(n, sid):
    keys(n, NATIVE_KEYS)
    require(n['source_id'] == sid and n['table_name'] == 'toxval', 'Wrong native population')
    for k in ('statement_ordinal', 'tuple_ordinal', 'line'):
        integer(n[k], 1)
    text(n['toxval_id']); _sha(n['row_sha256'])
    return n['statement_ordinal'], n['tuple_ordinal']


def _hash_add(h, row):
    h.update(canonical(row) + b'\n')


def _counter(value, count, h):
    keys(value, 'count members_sha256'); integer(value['count']); _sha(value['members_sha256'])
    require(value == dict(count=count, members_sha256=h.hexdigest()), 'Scope membership differs')


def _evidence(files, items, nonempty=False):
    require(isinstance(items, list) and (items or not nonempty), 'Evidence list required')
    seen = set()
    for item in items:
        p = files.file(item); require(str(p) not in seen, 'Duplicate evidence'); seen.add(str(p))


def _values(raw, original):
    return {k: raw.get(v + '_original', raw.get(v)) if original else raw.get(v) for k, v in VALUES.items()}


def native_scope(db, work, source_id):
    """Stream the complete fixed native scope, including outside-union rows.

    Mapping terms live in the bounded-cache work database rather than an
    unbounded Python set. SQLite upper/trim semantics match the reviewed audit.
    """
    work.execute('CREATE TABLE scope_endpoint_mappings(term TEXT PRIMARY KEY)')
    with closing(db.execute("""SELECT json_extract(body_json,'$.term_original')
            FROM native_rows WHERE table_name='toxval_fix'
            AND json_extract(body_json,'$.field')='toxval_type'
            AND upper(trim(json_extract(body_json,'$.term_final')))='LD50'""")) as fixes:
        for term, in fixes:
            if term is not None:
                work.execute('INSERT OR IGNORE INTO scope_endpoint_mappings VALUES(?)',(term,))
    query = """SELECT statement_ordinal,tuple_ordinal,line,row_sha256,
            json_extract(body_json,'$.toxval_id'),json_extract(body_json,'$.toxval_type_original'),
            CASE WHEN json_extract(body_json,'$.toxval_type')='LD50' THEN 1 ELSE 0 END,
            CASE WHEN upper(trim(json_extract(body_json,'$.toxval_type')))='LD50'
                OR upper(trim(json_extract(body_json,'$.toxval_type_original')))='LD50' THEN 1 ELSE 0 END
            FROM native_rows WHERE table_name='toxval' ORDER BY statement_ordinal,tuple_ordinal"""
    with closing(db.execute(query)) as rows:
        for st,tu,line,row_hash,toxval_id,original,exact,direct in rows:
            mapped = False
            if not direct and original is not None:
                mapped = work.execute('SELECT 1 FROM scope_endpoint_mappings WHERE term=?',(original,)).fetchone() is not None
            n=dict(source_id=source_id,table_name='toxval',statement_ordinal=st,tuple_ordinal=tu,
                   line=line,toxval_id=toxval_id,row_sha256=row_hash)
            yield n,exact,int(bool(direct or mapped))


def scope_and_bridge(files, db, work, scope, source, profile):
    v2 = scope.get('schema_version') == 2
    keys(scope, 'schema_version source_id source_sha256 algorithm native_membership primary exact_ld50 diagnostic_union candidate_bridge bridge candidate_membership' + (' source_row_terminals native_evidence' if v2 else ''))
    require(type(scope['schema_version']) is int and scope['schema_version'] in (1, 2) and scope['algorithm'] == SELECTOR['scope_algorithm'], 'Scope algorithm')
    require((scope['source_id'], scope['source_sha256']) == (source['source_id'], source['sha256']), 'Scope source mismatch')
    if v2: files.file(scope['native_evidence'])
    work.execute('CREATE TABLE members(st INTEGER,tu INTEGER,id TEXT UNIQUE,n TEXT,exact INTEGER,relevant INTEGER,PRIMARY KEY(st,tu))')
    work.execute('CREATE TABLE candidates(id TEXT PRIMARY KEY,n TEXT,c TEXT,decision TEXT)')
    work.execute('CREATE TABLE terminals(st INTEGER,tu INTEGER,n TEXT,id TEXT UNIQUE,decision TEXT,PRIMARY KEY(st,tu))')
    hs = [hashlib.sha256() for _ in range(3)]; counts = [0, 0, 0]; previous = (0, 0)
    with closing(native_scope(db,work,source['source_id'])) as reconstructed:
        for r in files.lines(scope['native_membership']):
            keys(r, 'toxval_id _line _statement _tuple _hash exact_current_ld50 task_relevant')
            n = dict(source_id=source['source_id'], table_name='toxval', statement_ordinal=r['_statement'],
                     tuple_ordinal=r['_tuple'], line=r['_line'], toxval_id=r['toxval_id'], row_sha256=r['_hash'])
            order = _member(n, source['source_id']); require(order > previous, 'Duplicate/unordered native membership'); previous = order
            for f in ('exact_current_ld50', 'task_relevant'):
                require(type(r[f]) is int and r[f] in (0, 1), 'Invalid scope flag')
            require(not r['exact_current_ld50'] or r['task_relevant'], 'Exact LD50 omitted from union')
            expected = next(reconstructed, None)
            require(expected is not None and canonical(n)==canonical(expected[0]), 'Native primary membership differs')
            require((r['exact_current_ld50'],r['task_relevant'])==expected[1:], 'Native scope flags differ')
            try:
                work.execute('INSERT INTO members VALUES(?,?,?,?,?,?)', (*order, n['toxval_id'], canonical(n).decode(), r['exact_current_ld50'], r['task_relevant']))
            except sqlite3.IntegrityError as exc:
                raise SQLResolutionError('Duplicate primary membership') from exc
            for i, included in enumerate((1, r['exact_current_ld50'], r['task_relevant'])):
                if included:
                    counts[i] += 1; _hash_add(hs[i], n)
        require(next(reconstructed, None) is None, 'Missing native primary membership')
    for i, k in enumerate(('primary', 'exact_ld50', 'diagnostic_union')):
        _counter(scope[k], counts[i], hs[i])
    require(counts[0] == profile['tables']['toxval']['parsed_rows'], 'Primary scope count differs from reviewed scan')
    bh, ch, th = hashlib.sha256(), hashlib.sha256(), hashlib.sha256(); bn = cn = empty = tn = 0
    with closing(work.execute('SELECT n,exact FROM members WHERE relevant=1 ORDER BY st,tu')) as expected:
        for entry in files.lines(scope['candidate_bridge']):
            keys(entry, 'native enumeration candidates no_candidate_review' + (' source_row_terminal' if v2 else ''))
            target = next(expected, None)
            require(target is not None and entry['native'] == loads(target[0]), 'Bridge does not cover exact diagnostic union')
            n = entry['native']; raw, rh = native_row(db, locator(n))
            require(rh == n['row_sha256'] and raw.get('toxval_id') == n['toxval_id'], 'Bridge native identity differs')
            require(bool(target[1]) == (raw.get('toxval_type') == 'LD50'), 'Exact LD50 flag differs from native endpoint')
            require(entry['enumeration'] == 'complete' and isinstance(entry['candidates'], list), 'Incomplete SQL enumeration')
            terminal = entry.get('source_row_terminal')
            if terminal is not None:
                _sha(terminal)
                require(not entry['candidates'] and entry['no_candidate_review'] is None, 'Terminal branches overlap')
                require(all(f in raw and raw[f] in (None, '', '-') for f in ('toxval_numeric_original', 'toxval_numeric')),
                        'Terminal hides populated numeric channel')
                work.execute('INSERT INTO terminals VALUES(?,?,?,?,NULL)', (n['statement_ordinal'],n['tuple_ordinal'],canonical(n).decode(),terminal))
                _hash_add(th, dict(native=n, decision_id=terminal)); tn += 1
            elif not entry['candidates']:
                nr = entry['no_candidate_review']; keys(nr, 'conclusion evidence rationale review_evidence')
                numeric_fields = ('toxval_numeric_original', 'toxval_numeric')
                absent = (None, '', '-')
                require(not target[1] and all(raw.get(f) in absent for f in numeric_fields),
                        'No-candidate hides measurement')
                require(nr['conclusion'] == 'no_measurement_candidates', 'Explicit no-candidate review required'); text(nr['rationale'])
                require(isinstance(nr['evidence'], list) and nr['evidence'], 'No-candidate anchors required')
                for a in nr['evidence']:
                    anchor(db, a, source['source_id'], source['sha256'])
                    anchored, _ = native_row(db, a['native_locator'])
                    require(a['field'] not in numeric_fields or anchored.get(a['field']) in absent,
                            'No-candidate hides measurement anchor')
                _evidence(files, nr['review_evidence'], True); empty += 1
            else:
                require(entry['no_candidate_review'] is None, 'Contradictory no-candidate claim')
            last = ''
            for c in entry['candidates']:
                keys(c, 'candidate_id measurement_id population_id measurement_evidence population_evidence population_scope')
                for k in ('candidate_id', 'measurement_id', 'population_id'): _sha(c[k])
                require(c['candidate_id'] > last, 'Duplicate/unordered sibling'); last = c['candidate_id']
                for k in ('measurement_evidence', 'population_evidence'): anchor(db, c[k], source['source_id'], source['sha256'])
                require(c['measurement_evidence']['native_locator'] == locator(n), 'Measurement not anchored to bridge native row')
                keys(c['population_scope'], 'species sex strain study_id')
                for a in c['population_scope'].values():
                    if a is not None: anchor(db, a, source['source_id'], source['sha256'])
                ids = identity.candidate_ids_v2(dict(source_id=source['source_id'], source_sha256=source['sha256']), c)
                require(all(c[k] == v for k, v in ids.items()), 'Candidate identity mismatch')
                try: work.execute('INSERT INTO candidates VALUES(?,?,?,NULL)', (c['candidate_id'], canonical(n).decode(), canonical(c).decode()))
                except sqlite3.IntegrityError as exc: raise SQLResolutionError('Duplicate candidate ID') from exc
                _hash_add(ch, dict(native=n, **c)); cn += 1
            _hash_add(bh, entry); bn += 1
        require(next(expected, None) is None, 'Missing native bridge entry')
    keys(scope['bridge'], 'native_count candidate_count no_candidate_count members_sha256' + (' candidate_native_count' if v2 else ''))
    for k in ('native_count', 'candidate_count', 'no_candidate_count'): integer(scope['bridge'][k])
    expected_bridge = dict(native_count=bn, candidate_count=cn, no_candidate_count=empty, members_sha256=bh.hexdigest())
    if v2:
        integer(scope['bridge']['candidate_native_count'])
        expected_bridge['candidate_native_count'] = bn-empty-tn
    require(scope['bridge'] == expected_bridge, 'Bridge membership mismatch')
    _counter(scope['candidate_membership'], cn, ch)
    if v2: _counter(scope['source_row_terminals'], tn, th)
    return cn


def terminal_facts(db, n):
    """Finite native/context/support/fix inventory, ordered by physical locator.

    Includes all dictionary definitions and every matching support alternative;
    no proposed scientific choice may prune this inventory.
    """
    raw, rh = native_row(db, locator(n))
    require(rh == n['row_sha256'] and raw.get('toxval_id') == n['toxval_id'], 'Terminal native identity')
    facts = [dict(native_locator=locator(n), row_sha256=rh, fields=raw)]
    joins = {
        'chemical': [('dtxsid','dtxsid')], 'source_chemical': [('chemical_id','chemical_id')],
        'species': [('species_id','species_id')], 'record_source': [('toxval_id','toxval_id')],
        'toxval_relationship': [('toxval_id_1','toxval_id'),('toxval_id_2','toxval_id')],
        'toxval_uf': [('toxval_id','toxval_id'),('parent_id','toxval_id')],
        'source_info': [('source_id','source_source_id'),('source','source'),('source','source_table'),
                        ('source','subsource'),('source_table','source_table'),('subsource','subsource')],
    }
    for table in sorted(set(joins) | {'toxval_dictionary','toxval_type_dictionary','toxval_fix'}):
        conditions, params = [], [table]
        if table in joins:
            for field, primary in joins[table]:
                for key in (primary, primary+'_original'):
                    value = raw.get(key)
                    if value is not None:
                        conditions.append("lower(CAST(json_extract(body_json, '$."+field+"') AS TEXT))=lower(?)")
                        params.append(value)
            if not conditions: continue
        elif table == 'toxval_fix':
            for field in FIX_FIELDS:
                value = raw.get(field+'_original')
                if value is not None:
                    conditions.append("(json_extract(body_json, '$.field')=? AND json_extract(body_json, '$.term_original')=?)")
                    params.extend((field,value))
            if not conditions: continue
        query = 'SELECT statement_ordinal,tuple_ordinal,line,row_sha256,body_json FROM native_rows WHERE table_name=?'
        if conditions: query += ' AND ('+' OR '.join(conditions)+')'
        for st,tu,line,row_hash,body in db.execute(query, params):
            facts.append(dict(native_locator=dict(table_name=table,statement_ordinal=st,tuple_ordinal=tu,line=line),
                              row_sha256=row_hash,fields=loads(body)))
    return sorted(facts, key=lambda f: canonical(f['native_locator']))


def terminal_decisions(files, db, work, evidence, scope, contract, review_key):
    """Separate nonmeasurement decisions and zero-emission actual consumption."""
    previous = (0,0); total = 0
    for d in files.lines(evidence['source_row_decisions']):
        keys(d, 'kind native contract_clause review decision_id')
        require(d['kind']=='source_row_terminal_exclusion_v1', 'Terminal kind')
        n=d['native']; order=_member(n,evidence['source_id'])
        require(order>previous, 'Duplicate/unordered terminal decision'); previous=order
        row=work.execute('SELECT n,id FROM terminals WHERE st=? AND tu=?',order).fetchone()
        require(row is not None and loads(row[0])==n and row[1]==d['decision_id'], 'Terminal outside bridge')
        require(d['decision_id']==digest({k:v for k,v in d.items() if k!='decision_id'}), 'Terminal decision hash')
        clause=contract['scientific_policy']['narrative_adjudication_v2']['clauses'].get(d['contract_clause'])
        require(d['contract_clause']=='v4_explicit_inequality' and isinstance(clause,dict) and
                clause.get('disposition')=='excluded' and clause.get('reason')=='explicit_inequality', 'Unapproved terminal clause')
        raw,_=native_row(db,locator(n))
        require(any(raw.get(f) in ('<','>','<=','>=','≤','≥') for f in
                    ('toxval_numeric_qualifier_original','toxval_numeric_qualifier')), 'Terminal requires native inequality')
        require(all(raw.get(f) in (None,'','-') for f in
                    ('toxval_numeric_converted','toxval_numeric_human','toxval_numeric_standard')), 'Terminal hides alternative numeric')
        r=files.json(d['review'])
        keys(r,'schema_version native source_sha256 contract native_evidence facts interpretations conclusion contract_clause independent_return')
        require(type(r['schema_version']) is int and r['schema_version']==1 and canonical(r['native'])==canonical(n) and
                r['contract_clause']==d['contract_clause'] and r['conclusion']=='no_unenumerated_measurement', 'Terminal review identity/conclusion')
        require(r['source_sha256']==evidence['source_sha256'] and r['contract']==evidence['contract'] and
                r['native_evidence']==scope['native_evidence'], 'Terminal review source/contract/sidecar binding')
        facts=terminal_facts(db,n)
        require(canonical(r['facts'])==canonical(facts), 'Terminal native/support/fix inventory differs')
        wanted=[dict(native_locator=f['native_locator'],field=k,value=v) for f in facts for k,v in sorted(f['fields'].items())]
        require(isinstance(r['interpretations'],list) and len(r['interpretations'])==len(wanted), 'Terminal field inventory differs')
        for interpreted, fact in zip(r['interpretations'],wanted):
            keys(interpreted,'native_locator field value classification rationale')
            require(canonical({k:interpreted[k] for k in fact})==canonical(fact),'Terminal field inventory differs')
            require(interpreted['classification'] in ('not_measurement','administered_dose'), 'Unenumerated narrative measurement or unresolved interpretation')
            text(interpreted['rationale'])
        returned=files.json(r['independent_return'])
        keys(returned,'schema_version review_key producer_execution_id reviewer_execution_id review_payload')
        require(type(returned['schema_version']) is int and returned['schema_version']==1 and returned['review_key']==review_key, 'Terminal independent return scope')
        for k in ('producer_execution_id','reviewer_execution_id'): text(returned[k])
        require(returned['producer_execution_id'].strip()!=returned['reviewer_execution_id'].strip(), 'Separate terminal review required')
        require(canonical(returned['review_payload'])==canonical({k:v for k,v in r.items() if k!='independent_return'}), 'Terminal independent returned evidence differs')
        work.execute('UPDATE terminals SET decision=? WHERE st=? AND tu=?',(canonical(d).decode(),*order)); total+=1
    count=work.execute('SELECT COUNT(*) FROM terminals').fetchone()[0]
    require(total==count,'Missing terminal decision')


def source_row_decisions(files, db, work, evidence, scope, consumption, contract, review_key):
    terminal_decisions(files, db, work, evidence, scope, contract, review_key)
    count=work.execute('SELECT COUNT(*) FROM terminals').fetchone()[0]
    require(evidence['source_row_consumption']==consumption['source_row_ledger'],'Wrong actual terminal consumption')
    previous=(0,0); total=0; h=hashlib.sha256()
    for r in files.lines(consumption['source_row_ledger']):
        keys(r,'kind native decision_id action clause output_observation_ids output_artifacts output_labels')
        order=_member(r['native'],evidence['source_id'])
        require(order>previous,'Duplicate/unordered terminal consumption'); previous=order
        row=work.execute('SELECT decision FROM terminals WHERE st=? AND tu=?',order).fetchone()
        require(row is not None and row[0] is not None,'Terminal consumption outside decisions')
        d=loads(row[0])
        require(r['kind']==d['kind'] and r['native']==d['native'] and r['decision_id']==d['decision_id'] and
                r['action']=='excluded' and r['clause']==d['contract_clause'],'Terminal consumption decision differs')
        require(all(r[k]==[] for k in ('output_observation_ids','output_artifacts','output_labels')),'Terminal exclusion emitted output')
        _hash_add(h,r); total+=1
    require(total==count,'Missing terminal consumption')
    _counter(consumption['source_row_membership'],total,h)


def _critical(files, db, d, review_key, contract):
    b = d['critical_metadata_review']
    if d['contract_clause'] != 'v4_unavailable_critical_metadata':
        require(b is None, 'Unexpected critical metadata review'); return
    require(b in d['rationale_evidence'], 'Critical review missing from evidence')
    r = files.json(b)
    keys(r, 'missing_property native_candidate_locators reviewed_assets_and_releases applicable_joins_and_retrieval_attempts competing_interpretations remaining_uncertainty independent_decision reopening_trigger')
    require(r['missing_property'] in ('exposure_route', 'indispensable_experiment_linkage'), 'Optional missing metadata cannot quarantine')
    policy = contract['scientific_policy']['candidate_policy_clause_details'][d['contract_clause']]
    require(set(policy['allowed_missing_properties']) == {'exposure_route', 'indispensable_experiment_linkage'} and
            set(policy['required_evidence']) == set(r), 'Critical metadata clause requirements mismatch')
    require(isinstance(policy['prerequisites'], list) and len(policy['prerequisites']) == 4, 'Complete extraction review prerequisites required')
    for prerequisite in policy['prerequisites']: text(prerequisite)
    require(isinstance(r['native_candidate_locators'], list) and d['native_locator'] in r['native_candidate_locators'], 'Critical native scope')
    for l in r['native_candidate_locators']: native_row(db, l)
    require(isinstance(r['reviewed_assets_and_releases'], list) and r['reviewed_assets_and_releases'], 'Reviewed assets required')
    for a in r['reviewed_assets_and_releases']: keys(a, 'artifact release'); files.file(a['artifact']); text(a['release'])
    require(isinstance(r['applicable_joins_and_retrieval_attempts'], list) and r['applicable_joins_and_retrieval_attempts'], 'Reviewed joins required')
    for a in r['applicable_joins_and_retrieval_attempts']: keys(a, 'evidence result'); files.file(a['evidence']); text(a['result'])
    require(isinstance(r['competing_interpretations'], list) and r['competing_interpretations'], 'Competing interpretations required')
    for v in r['competing_interpretations']: text(v)
    text(r['remaining_uncertainty']); text(r['reopening_trigger'])
    keys(r['independent_decision'], 'review_key candidate_id conclusion indispensable_property')
    require(r['independent_decision']['review_key'] == review_key and r['independent_decision']['candidate_id'] == d['candidate_id'] and r['independent_decision']['conclusion'] == 'scientific_quarantine', 'Critical review decision mismatch')
    text(r['independent_decision']['indispensable_property'])


def candidate_decisions(files, db, work, evidence, contract, review_key, count):
    key = review_key
    clauses = contract['scientific_policy']['narrative_adjudication_v2']['clauses']
    fixes = {}
    for body, in db.execute("SELECT body_json FROM native_rows WHERE table_name='toxval_fix'"):
        f = loads(body); fixes.setdefault((f['field'], f['term_original']), []).append(f['toxval_fix_id'])
    last = ''; total = 0
    for d in files.lines(evidence['candidate_decisions']):
        keys(d, DECISION_KEYS); require(d['candidate_id'] > last, 'Duplicate/unordered decisions'); last = d['candidate_id']
        row = work.execute('SELECT n,c FROM candidates WHERE id=?', (d['candidate_id'],)).fetchone()
        require(row is not None, 'Decision outside bridge'); n, c = map(loads, row)
        require(d['decision_id'] == digest({k:v for k,v in d.items() if k != 'decision_id'}), 'Decision hash mismatch')
        require(all(d[k] == c[k] for k in ('measurement_id','population_id')) and d['native_locator'] == locator(n) and d['toxval_id'] == n['toxval_id'] and d['native_row_sha256'] == n['row_sha256'], 'Decision candidate bridge mismatch')
        require(d['extraction_complete'] is True and d['disposition'] in ('preserve','scientific_quarantine','excluded'), 'Unresolved extraction/correction blocks closure')
        raw, _ = native_row(db, d['native_locator'])
        for k in ('original','current','consumed_values'): keys(d[k], VALUES)
        require(d['original'] == _values(raw, True) and d['current'] == _values(raw, False), 'Decision native values differ')
        require(all(d['consumed_values'][k] in (d['original'][k], d['current'][k]) for k in VALUES), 'Corrections unsupported')
        require((d['consumed_values']['numeric'],d['consumed_values']['units']) in ((d['original']['numeric'],d['original']['units']),(d['current']['numeric'],d['current']['units'])), 'Mixed numeric/unit bases')
        if d['disposition'] == 'preserve' and any(x in (d['original']['qualifier'] or '') for x in '<>'):
            require(d['consumed_values']['qualifier'] == d['original']['qualifier'], 'Original inequality erased')
        require(isinstance(d['missing_optional_metadata'], list) and len(set(d['missing_optional_metadata'])) == len(d['missing_optional_metadata']) and set(d['missing_optional_metadata']) <= OPTIONAL, 'Unknown optional metadata disclosure')
        if d['disposition'] == 'preserve': require(d['contract_clause'] is None, 'Preserve cannot imply exclusion')
        else:
            clause = clauses.get(d['contract_clause']); require(isinstance(clause, dict) and clause.get('disposition') == d['disposition'], 'Unapproved disposition clause')
            permitted = identity.QUARANTINE_REASONS_V2 if d['disposition'] == 'scientific_quarantine' else identity.EXCLUSION_REASONS_V2
            require(clause.get('reason') in permitted and d['contract_clause'] == 'v4_' + clause['reason'], 'Unsupported policy reason')
        _critical(files, db, d, key, contract); _evidence(files, d['rationale_evidence'], True)
        files.file(d['audit_anomaly_membership'])
        require(isinstance(d['support_native_locators'], list), 'Support locators required')
        for l in d['support_native_locators']: native_row(db, l)
        wanted = sorted(f for field in FIX_FIELDS for f in fixes.get((field, raw.get(field+'_original')), []))
        require(d['applicable_fix_ids'] == wanted, 'Omitted/extra applicable native fix')
        work.execute('UPDATE candidates SET decision=? WHERE id=?', (canonical(d).decode(), d['candidate_id'])); total += 1
    require(total == count, 'Missing candidate decision/sibling')


def decisions_and_consumption(files, db, work, evidence, expected_consumption, contract, key, count):
    candidate_decisions(files, db, work, evidence, contract, key, count)
    keys(expected_consumption, 'consumer_code ledger output_artifacts' +
         (' source_row_ledger source_row_membership' if evidence['schema_version']==2 else '')); files.file(expected_consumption['consumer_code'])
    require(evidence['candidate_consumption'] == expected_consumption['ledger'], 'Wrong actual consumption ledger')
    _evidence(files, expected_consumption['output_artifacts'])
    require(expected_consumption['output_artifacts'] == sorted(expected_consumption['output_artifacts'], key=lambda b:(b['path'],b['sha256'])), 'Output bindings not canonically ordered')
    work.execute('CREATE TABLE emitted(id TEXT PRIMARY KEY)'); last = ''; total = 0
    for r in files.lines(expected_consumption['ledger']):
        keys(r, CONSUMPTION_KEYS); require(r['candidate_id'] > last, 'Duplicate/unordered consumption'); last = r['candidate_id']
        native_row(db, r['native_locator'])
        row = work.execute('SELECT decision FROM candidates WHERE id=?', (r['candidate_id'],)).fetchone()
        require(row is not None and row[0] is not None, 'Consumption outside decisions'); d = loads(row[0])
        action = 'preserved' if d['disposition']=='preserve' else d['disposition']
        require(r['decision_id']==d['decision_id'] and r['native_locator']==d['native_locator'] and r['action']==action and r['consumed_values']==d['consumed_values'] and r['clause']==d['contract_clause'], 'Consumption decision mismatch')
        _evidence(files, r['rationale_evidence'], True); _evidence(files, r['output_artifacts'])
        require(isinstance(r['output_observation_ids'], list), 'Output IDs required')
        if action != 'preserved': require(not r['output_observation_ids'] and not r['output_artifacts'], 'Quarantine/exclusion emitted labels')
        else:
            require(r['output_observation_ids'] and r['output_artifacts'], 'Preserved candidate missing output')
            require(all(x in expected_consumption['output_artifacts'] for x in r['output_artifacts']), 'Unreviewed output artifact')
        for oid in r['output_observation_ids']:
            text(oid)
            try: work.execute('INSERT INTO emitted VALUES(?)', (oid,))
            except sqlite3.IntegrityError as exc: raise SQLResolutionError('Duplicate output observation ID') from exc
        total += 1
    require(total==count, 'Missing candidate consumption/sibling')


def validate(contract_binding, accepted_contract_review, source_config, source_entry,
             original_gap_artifact, evidence, expected_scope, expected_consumption,
             actual_review, bind):
    """Return exact obligation IDs and bindings, never activation or a receipt.

    expected_scope/consumption and actual_review MUST come from the trusted caller,
    not be copied from producer assertions. Authentic review is leader-attested.
    """
    try:
        return _validate(contract_binding, accepted_contract_review, source_config, source_entry,
                         original_gap_artifact, evidence, expected_scope, expected_consumption, actual_review, bind)
    except SQLResolutionError:
        raise
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        raise SQLResolutionError('Invalid SQL resolution package: ' + str(exc)) from exc


def _validate(contract_binding, accepted_contract_review, source_config, source_entry,
              original_gap_artifact, evidence, expected_scope, expected_consumption, actual_review, bind):
    files = Files(bind)
    v2 = evidence.get('schema_version') == 2
    selector = SELECTOR_V2 if v2 else SELECTOR
    keys(evidence, 'schema_version adapter contract accepted_contract_review source_id source_sha256 source_release original_gap_artifact profile native_index scan_review support_audit_manifest support_review scope_membership candidate_decisions candidate_consumption resolutions scientific_review' + (' source_row_decisions source_row_consumption' if v2 else ''))
    require(type(evidence['schema_version']) is int and evidence['schema_version']==selector['schema_version'] and evidence['adapter']==selector['adapter'], 'SQL evidence selector')
    require(type(expected_scope['schema_version']) is int and expected_scope['schema_version']==selector['schema_version'], 'SQL scope version mismatch')
    contract = files.json(contract_binding)
    execution = contract['scientific_policy']['typed_sql_gap_resolution_v4']['execution']
    require(execution == selector and type(execution['schema_version']) is int, 'Contract lacks exact SQL execution selector')
    matches = [x for x in contract['data_curation']['sources'] if x.get('source_id')==source_entry['source_id']]
    require(matches==[source_config] and source_config.get('required') is True, 'Full-contract source mismatch')
    require(evidence['contract']==contract_binding and evidence['accepted_contract_review']==accepted_contract_review and evidence['original_gap_artifact']==original_gap_artifact, 'Contract/gap binding mismatch')
    require(evidence['source_id']==source_entry['source_id'] and evidence['source_sha256']==source_entry['sha256'] and evidence['source_release']=='unknown', 'Source identity mismatch')
    source_descriptor = small(source_entry); files.file(source_descriptor)
    keys(actual_review, 'schema_version attestation receipt_writer accepted_contract scan support scientific')
    require(type(actual_review['schema_version']) is int and actual_review['schema_version']==1 and actual_review['attestation']==ATTESTATION, 'Trusted caller attestation required'); text(actual_review['receipt_writer'])
    reviews = {k:review(files,actual_review[k],k) for k in ('accepted_contract','scan','support','scientific')}
    require(actual_review['accepted_contract']['receipt']==accepted_contract_review, 'Wrong accepted review')
    acceptance = reviews['accepted_contract']
    require(acceptance['stage']=='contract' and acceptance['contract_sha256']==contract_binding['sha256'] and files.json(acceptance['candidate']).get('contract')==contract_binding, 'Accepted full contract mismatch')
    for k, name in (('scan','scan_review'),('support','support_review'),('scientific','scientific_review')):
        require(actual_review[k]['receipt']==evidence[name], 'Caller review binding mismatch')
    gaps = files.json(original_gap_artifact); require(isinstance(gaps,list), 'Gap list required')
    obligations = scan.obligations(gaps, source_entry['source_id']); require(obligations, 'No original SQL obligations')
    ids = sorted(x['obligation_id'] for x in obligations)
    require(len({x['table_name'] for x in obligations})==len(obligations), 'Duplicate table obligations')
    profile = files.json(evidence['profile']); files.file(evidence['native_index'])
    require(profile['format']=='qsar-native-sql-scan-v1' and profile['scope']=='all_native_tables_all_rows' and profile['source_release']=='unknown' and profile['source_id']==source_entry['source_id'], 'Wrong scan profile')
    require(small(profile['source'])==source_descriptor and profile['source']==profile['source_after'] and small(profile['index'])==evidence['native_index'], 'Scan source/index binding mismatch')
    require(profile['clean_eof'] is True and all(type(profile[k]) is int and profile[k]==0 for k in ('parser_errors','unsupported_operations')), 'Incomplete scan')
    require(profile['independent_native_audit'] is False and profile['scientific_coverage_accepted'] is False, 'Producer scan claims review authority')
    codes = {'scanner_code':small(scan.file_binding(scan.__file__)), 'native_helper_code':small(scan.file_binding(native.__file__)),
             'candidate_identity_code':small(scan.file_binding(identity.__file__)), 'resolver_code':small(scan.file_binding(__file__))}
    for b in codes.values(): files.file(b)
    require(profile['code']==scan.code_bindings(), 'Stale scanner/helper')
    source_context = dict(source_id=source_entry['source_id'], artifact=source_descriptor, release='unknown')
    sc = reviews['scan']['context']; keys(sc,'source profile native_index scanner_code native_helper_code scan_evidence_manifest')
    require(sc==dict(source=source_context,profile=evidence['profile'],native_index=evidence['native_index'],scanner_code=codes['scanner_code'],native_helper_code=codes['native_helper_code'],scan_evidence_manifest=sc['scan_evidence_manifest']), 'Scan review context mismatch'); files.file(sc['scan_evidence_manifest'])
    su = reviews['support']['context']; keys(su,'scan_review support_audit_manifest support_review_evidence_manifest obligation_ids')
    require(su['scan_review']==evidence['scan_review'] and su['support_audit_manifest']==evidence['support_audit_manifest'] and su['obligation_ids']==ids,'Support review context mismatch'); files.file(su['support_review_evidence_manifest'])
    manifest = files.json(evidence['support_audit_manifest']); artifacts = {}
    for a in manifest['artifacts']:
        name=Path(a['path']).name; require(name not in artifacts,'Duplicate audit artifact role'); artifacts[name]=small(a); files.file(small(a))
    required = set(AUDIT_FILES) | {'field-pairs-'+f+'.jsonl' for f in PAIRS}
    require(required <= artifacts.keys(), 'Incomplete reviewed factual audit catalogue')
    require(evidence['scope_membership']==expected_scope['native_membership']==artifacts['source-scope-membership.jsonl'], 'Wrong full native scope artifact')
    role_rows = files.json(artifacts['table-role-ledger.json']); roles={r['table']:r for r in role_rows}
    require(len(roles)==len(role_rows) and set(roles)=={o['table_name'] for o in obligations}, 'Role obligation set mismatch')
    expected_context = dict(schema_version=selector['schema_version'],selector=selector,contract=contract_binding,accepted_contract_review=accepted_contract_review,
                            source=source_context,original_gap_artifact=original_gap_artifact,profile=evidence['profile'],native_index=evidence['native_index'],
                            **codes,scan_review=evidence['scan_review'],support_audit_manifest=evidence['support_audit_manifest'],support_review=evidence['support_review'],
                            expected_scope=expected_scope,expected_consumption=expected_consumption,candidate_decisions=evidence['candidate_decisions'],
                            resolutions=evidence['resolutions'],obligation_ids=ids)
    if v2: expected_context['source_row_decisions'] = evidence['source_row_decisions']
    sr=reviews['scientific']; require(canonical(sr['context'])==canonical(expected_context), 'Actual scientific review context mismatch'); text(sr['review_key'])
    resolutions=files.json(evidence['resolutions'])
    require(isinstance(resolutions,list) and len(resolutions)==len(obligations),'Missing/extra table resolutions')
    by_id={r['obligation_id']:r for r in resolutions}; require(len(by_id)==len(resolutions) and sorted(by_id)==ids,'Duplicate/wrong obligations')
    uri=files.file(evidence['native_index']).as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as db, tempfile.TemporaryDirectory(prefix='qsar-sql-resolution-') as tmp, closing(sqlite3.connect(str(Path(tmp)/'scope.sqlite'))) as work:
        db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA cache_size=-8192'); db.execute('PRAGMA temp_store=FILE')
        work.execute('PRAGMA cache_size=-8192'); work.execute('PRAGMA temp_store=FILE')
        schemas={t:loads(s) for t,s in db.execute('SELECT table_name,schema_json FROM schemas')}
        require(set(schemas)==set(profile['tables']), 'Schema membership mismatch')
        actual_counts=dict(db.execute('SELECT table_name,COUNT(*) FROM native_rows GROUP BY table_name'))
        require(set(actual_counts)<=set(schemas), 'Rows outside declared schemas')
        for t, s in schemas.items():
            m=profile['tables'][t]
            require(all(m.get(k)==v for k,v in s.items()) and s['schema_sha256']==digest({'columns':s['columns'],'create_sql':s['create_sql']}),'Native schema mismatch')
            for k in ('native_rows','parsed_rows','errors'): integer(m[k])
            require(m['native_rows']==m['parsed_rows']==actual_counts.get(t,0) and m['errors']==0,'Forged native table counts')
        count=scope_and_bridge(files,db,work,expected_scope,source_entry,profile)
        decisions_and_consumption(files,db,work,evidence,expected_consumption,contract,sr['review_key'],count)
        if v2: source_row_decisions(files,db,work,evidence,expected_scope,expected_consumption,contract,sr['review_key'])
        for o in obligations:
            r=by_id[o['obligation_id']]; keys(r,RESOLUTION_KEYS)
            require(r['resolution_id']==digest({k:v for k,v in r.items() if k!='resolution_id'}),'Resolution payload hash mismatch')
            require(all(r[k]==v for k,v in o.items()) and r['original_gap_artifact']==original_gap_artifact,'Resolution gap identity mismatch')
            require(r['source_sha256']==source_entry['sha256'] and r['source_release']=='unknown' and r['exact_scope']==expected_scope,'Resolution source/scope mismatch')
            keys(r['reviewer_receipt'],'review_key'); require(r['reviewer_receipt']['review_key']==sr['review_key'],'Resolution review key mismatch')
            t=o['table_name']; m=profile['tables'][t]
            require(r['schema']==schemas[t] and r['scientific_role']==roles[t]['role'],'Wrong reviewed schema/role')
            keys(r['counts'],'native parsed errors')
            for v in r['counts'].values(): integer(v)
            require(r['counts']==dict(native=m['native_rows'],parsed=m['parsed_rows'],errors=m['errors']),'Resolution counts mismatch')
            require(r['disposition'] in ('verified_empty','implemented_and_reconciled','reviewed_not_applicable_to_scope'),'Unresolved table obligation')
            require((r['disposition']=='verified_empty') == (m['native_rows']==0),'False empty/nonempty disposition')
            for k in ('joins','dictionary_fix_effects'):
                keys(r[k],'reviewed_fact_artifact'); require(r[k]['reviewed_fact_artifact']==evidence['support_audit_manifest'],'Wrong reviewed fact set')
            _evidence(files,r['evidence'],True)
            forbidden={str(Path(evidence[x]['path']).resolve()) for x in ('scientific_review','resolutions')}
            require(not any(str(Path(x['path']).resolve()) in forbidden for x in r['evidence']),'Circular resolution evidence')
    return {'obligation_ids':ids,'consumed_bindings':files.done()}
