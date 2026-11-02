"""Synthetic accepted envelopes only; never creates real SQL gap resolutions."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts'
sys.path.insert(0, str(SCRIPTS))
import qsar_sql_resolution as q


class Package:
    def __init__(self, root, mapping_values=None, terminal_rows=None, outside_union=False):
        self.root = root
        self.terminal_rows = terminal_rows
        self.sid = 'synthetic/source.sql'
        self.key = 'synthetic-review-key'
        self.config = dict(source_id=self.sid, required=True, adapter='toxval')
        self.clauses = {k:dict(disposition=d,reason=r) for k,d,r in [
            ('v4_unavailable_critical_metadata','scientific_quarantine','unavailable_critical_metadata'),
            ('v4_incompatible_task','excluded','incompatible_task')]}
        self.contract = dict(synthetic=True,contract_version='synthetic-v5',
                             data_curation=dict(sources=[self.config]),
                             scientific_policy=dict(typed_sql_gap_resolution_v4=dict(execution=q.SELECTOR.copy()),
                                                    narrative_adjudication_v2=dict(schema_version=2,clauses=self.clauses)))
        self.contract['scientific_policy']['candidate_policy_clause_details'] = {
            'v4_unavailable_critical_metadata': dict(allowed_missing_properties=['exposure_route','indispensable_experiment_linkage'],
                required_evidence='missing_property native_candidate_locators reviewed_assets_and_releases applicable_joins_and_retrieval_attempts competing_interpretations remaining_uncertainty independent_decision reopening_trigger'.split(),
                prerequisites=['complete source-version-bound extraction review','complete field/context interpretation',
                               'complete applicable support/linkage review','independent source-bound decision'])}
        self.proof = self.write('review-return.json', {'synthetic_only':True,'actual_scientific_approval':False})
        cols = list(dict.fromkeys(list(q.VALUES.values()) + [x+'_original' for x in q.VALUES.values() if not x.endswith('_original')] + ['toxval_id','sex_original'] + (list(terminal_rows[0]) if terminal_rows else [])))
        sql = 'CREATE TABLE `toxval` (' + ','.join('`'+x+'` text' for x in cols) + ');\n'
        self.raw = {}
        for i in range(1,5 if outside_union else 4):
            r = {x:'-' for x in cols}
            r.update(toxval_id=str(i),chemical_id='C',dtxsid='D',species_original='rat',exposure_route='oral',exposure_route_original='oral',
                     toxval_numeric='1000',toxval_numeric_original='1000',toxval_units='mg/kg',toxval_units_original='mg/kg',
                     toxval_numeric_qualifier='=',toxval_numeric_qualifier_original='=',toxval_type='LD50' if i<3 else '-',
                     toxval_type_original='LD50' if i<3 else 'Mortality',sex_original='male female')
            # Original/current identity source fields, if present, are identical.
            r['chemical_id_original']='C';r['dtxsid_original']='D'
            if i == 3 and mapping_values:
                r.update(mapping_values)
            if terminal_rows and i < 3:
                r.update(terminal_rows[i-1])
            if i == 4:
                r.update(toxval_type='LC50',toxval_type_original='LC50')
            self.raw[str(i)]=r
            sql += 'INSERT INTO `toxval` VALUES ('+','.join('NULL' if r[x] is None else "'"+r[x].replace("'", "''")+"'" for x in cols)+');\n'
        sql += 'CREATE TABLE `chemical` (`dtxsid` text);\nINSERT INTO `chemical` VALUES (\'D\');\n'
        sql += 'CREATE TABLE `toxval_fix` (`toxval_fix_id` text, `field` text, `term_original` text, `term_final` text);\n'
        sql += "INSERT INTO `toxval_fix` VALUES ('1','toxval_type','Mortality','LD50');\n"
        if terminal_rows:
            sql += "INSERT INTO `toxval_fix` VALUES ('2','toxval_numeric_qualifier','<','<');\n"
            sql += 'CREATE TABLE `record_source` (`toxval_id` text, `source_study_id` text, `document_name` text);\n'
            sql += "INSERT INTO `record_source` VALUES ('1','-1','synthetic dermal report'),('2','-1','synthetic vapor report');\n"
        sql += 'CREATE TABLE `study_details` (`id` text);\n'
        src=root/'source.sql';src.write_text(sql,encoding='utf8')
        self.profile=q.scan.scan(src,root/'native.sqlite',self.sid)
        self.source=dict(source_id=self.sid,**q.small(self.profile['source']))
        self.gaps=[dict(source_id=self.sid,reason='synthetic gap',unparsed_tables=['chemical','toxval_fix','study_details'])]
        self.obligations=q.scan.obligations(self.gaps,self.sid)
        self.natives=[];self.schemas={}
        with closing(sqlite3.connect(root/'native.sqlite')) as db:
            self.schemas={t:json.loads(s) for t,s in db.execute('SELECT table_name,schema_json FROM schemas')}
            for st,tu,line,body,rh in db.execute("SELECT statement_ordinal,tuple_ordinal,line,body_json,row_sha256 FROM native_rows WHERE table_name='toxval' ORDER BY statement_ordinal,tuple_ordinal"):
                r=json.loads(body);self.natives.append(dict(source_id=self.sid,table_name='toxval',statement_ordinal=st,tuple_ordinal=tu,line=line,toxval_id=r['toxval_id'],row_sha256=rh))
        self.members=[dict(toxval_id=n['toxval_id'],_line=n['line'],_statement=n['statement_ordinal'],_tuple=n['tuple_ordinal'],_hash=n['row_sha256'],exact_current_ld50=int(n['toxval_id'] in ('1','2')),task_relevant=int(n['toxval_id']!='4')) for n in self.natives]
        self.bridge=[]
        for n in self.natives:
            if n['toxval_id']=='4':continue
            cs=[]
            for sex in ([] if terminal_rows and n['toxval_id']!='3' else ['male','female'] if n['toxval_id']=='1' else ['male']):
                a=self.anchor(n,'toxval_numeric_original','1000')
                population=dict(species=None,sex=self.anchor(n,'sex_original',sex,0 if sex=='male' else 5),strain=None,study_id=None)
                c=dict(measurement_evidence=a,population_evidence=population['sex'],population_scope=population)
                c.update(q.identity.candidate_ids_v2(dict(source_id=self.sid,source_sha256=self.source['sha256']),c));cs.append(c)
            self.bridge.append(dict(native=n,enumeration='complete',candidates=sorted(cs,key=lambda c:c['candidate_id']),no_candidate_review=None))
        self.expected_bridge=copy.deepcopy(self.bridge)
        self.decisions=[];self.consumption=[];self.metadata={}
        for e in self.bridge:
            n=e['native'];raw=self.raw[n['toxval_id']]
            for c in e['candidates']:
                held=n['toxval_id']=='1' and c['population_scope']['sex']['quote']=='female'
                state='scientific_quarantine' if held else 'excluded' if n['toxval_id']=='3' else 'preserve'
                clause='v4_unavailable_critical_metadata' if held else 'v4_incompatible_task' if state=='excluded' else None
                d=dict(candidate_id=c['candidate_id'],measurement_id=c['measurement_id'],population_id=c['population_id'],native_locator=q.locator(n),
                       toxval_id=n['toxval_id'],native_row_sha256=n['row_sha256'],disposition=state,extraction_complete=True,contract_clause=clause,
                       original=q._values(raw,True),current=q._values(raw,False),consumed_values=q._values(raw,False),support_native_locators=[],
                       applicable_fix_ids=['1'] if n['toxval_id']=='3' else [],audit_anomaly_membership=self.proof,
                       missing_optional_metadata=['source_study_id'],rationale_evidence=[self.proof],critical_metadata_review=None)
                if held:
                    self.metadata[c['candidate_id']]=dict(missing_property='exposure_route',native_candidate_locators=[q.locator(n)],
                        reviewed_assets_and_releases=[dict(artifact=q.small(self.source),release='synthetic')],
                        applicable_joins_and_retrieval_attempts=[dict(evidence=self.proof,result='synthetic complete contextual review')],
                        competing_interpretations=['oral route applies to male only','female route unavailable'],remaining_uncertainty='female route unavailable',
                        independent_decision=dict(review_key=self.key,candidate_id=c['candidate_id'],conclusion='scientific_quarantine',indispensable_property='exposure_route'),
                        reopening_trigger='new source-native route evidence')
                self.decisions.append(d)
                self.consumption.append(dict(candidate_id=c['candidate_id'],decision_id='',native_locator=q.locator(n),action='preserved' if state=='preserve' else state,
                                             consumed_values=d['consumed_values'].copy(),output_observation_ids=[c['candidate_id']+'-out'] if state=='preserve' else [],
                                             output_artifacts=[],clause=clause,rationale_evidence=[self.proof]))
        self.decisions.sort(key=lambda d:d['candidate_id']);self.consumption.sort(key=lambda d:d['candidate_id'])
        self.resolution_mutator=None;self.context_mutator=None
        self.refresh()

    def write(self,name,value,lines=False):
        p=self.root/name
        p.write_bytes(b''.join(q.canonical(x)+b'\n' for x in value) if lines else q.canonical(value)+b'\n')
        return q.small(q.scan.file_binding(p))

    def anchor(self,n,field,quote,start=0):
        loc=q.locator(n)
        return dict(field_id=q.digest(dict(source_id=self.sid,source_sha256=self.source['sha256'],native_locator=loc,field=field)),
                    field=field,start=start,end=start+len(quote),quote=quote,native_locator=loc)

    def wrapper(self,name,kind,**extra):
        b=dict(schema_version=2 if kind=='accepted_contract' else 1,status='PASS',producer_execution_id='synthetic-producer',
               reviewer_execution_id='synthetic-reviewer',receipt_writer='synthetic-leader',attestation=q.ATTESTATION,review_evidence=self.proof,findings=[],**extra)
        if kind in ('scan','support'): b.update(phase=kind,native_receipt=self.proof)
        desc=self.write(name,b)
        return dict(receipt=desc,receipt_body=b,**({} if kind=='accepted_contract' else dict(review_evidence=self.proof)))

    @staticmethod
    def md(rows):
        h=hashlib.sha256()
        for r in rows:q._hash_add(h,r)
        return dict(count=len(rows),members_sha256=h.hexdigest())

    def refresh(self):
        self.cb=self.write('contract.json',self.contract)
        candidate=self.write('contract-candidate.json',dict(contract=self.cb))
        ac=self.wrapper('acceptance.json','accepted_contract',stage='contract',contract_sha256=self.cb['sha256'],candidate=candidate)
        self.ac=ac['receipt'];self.gb=self.write('gaps.json',self.gaps)
        pb=self.write('profile.json',self.profile);ib=q.small(q.scan.file_binding(self.root/'native.sqlite'))
        codes={k:q.small(q.scan.file_binding(p)) for k,p in [('scanner_code',q.scan.__file__),('native_helper_code',q.native.__file__),('candidate_identity_code',q.identity.__file__),('resolver_code',q.__file__)]}
        source_context=dict(source_id=self.sid,artifact=q.small(self.source),release='unknown')
        sc=self.wrapper('scan-review.json','scan',context=dict(source=source_context,profile=pb,native_index=ib,
            scanner_code=codes['scanner_code'],native_helper_code=codes['native_helper_code'],scan_evidence_manifest=self.proof))
        nb=self.write('source-scope-membership.jsonl',self.members,True);bb=self.write('bridge.jsonl',self.bridge,True)
        expected_members=[dict(native=e['native'],**c) for e in self.expected_bridge for c in e['candidates']]
        self.scope=dict(schema_version=1,source_id=self.sid,source_sha256=self.source['sha256'],algorithm=q.SELECTOR['scope_algorithm'],native_membership=nb,
                        primary=self.md(self.natives),exact_ld50=self.md(self.natives[:2]),diagnostic_union=self.md([n for n in self.natives if n['toxval_id']!='4']),candidate_bridge=bb,
                        bridge=dict(native_count=len(self.expected_bridge),candidate_count=len(expected_members),no_candidate_count=sum(not e['candidates'] for e in self.expected_bridge),members_sha256=self.md(self.expected_bridge)['members_sha256']),
                        candidate_membership=self.md(expected_members))
        arts=[]
        for name in sorted(set(q.AUDIT_FILES)|{'field-pairs-'+f+'.jsonl' for f in q.PAIRS}):
            if name=='source-scope-membership.jsonl':b=nb
            elif name=='table-role-ledger.json':b=self.write(name,[dict(table=o['table_name'],role='fixture-role-'+o['table_name']) for o in self.obligations])
            else:b=self.write(name,dict(synthetic_reviewed_fact=True,role=name))
            arts.append(b)
        manifest=self.write('audit-manifest.json',dict(artifacts=arts))
        ids=sorted(o['obligation_id'] for o in self.obligations)
        su=self.wrapper('support-review.json','support',context=dict(scan_review=sc['receipt'],support_audit_manifest=manifest,support_review_evidence_manifest=self.proof,obligation_ids=ids))
        out=self.write('outputs.json',[dict(id=r['output_observation_ids'][0],values=r['consumed_values']) for r in self.consumption if r['output_observation_ids']])
        by_id={d['candidate_id']:d for d in self.decisions}
        for d in self.decisions:
            if d['candidate_id'] in self.metadata:
                b=self.write('metadata-'+d['candidate_id']+'.json',self.metadata[d['candidate_id']]);d['critical_metadata_review']=b;d['rationale_evidence']=[self.proof,b]
            d['decision_id']=q.digest({k:v for k,v in d.items() if k!='decision_id'})
        for r in self.consumption:
            if r['candidate_id'] in by_id:
                d=by_id[r['candidate_id']];r['decision_id']=d['decision_id'];r['rationale_evidence']=d['rationale_evidence']
            r['output_artifacts']=[out] if r['output_observation_ids'] else []
        dd=self.write('decisions.jsonl',self.decisions,True);cd=self.write('consumption.jsonl',self.consumption,True)
        self.ec=dict(consumer_code=codes['resolver_code'],ledger=cd,output_artifacts=[out])
        rs=[]
        for o in self.obligations:
            t=o['table_name'];m=self.profile['tables'][t]
            r=dict(o,original_gap_artifact=self.gb,source_sha256=self.source['sha256'],source_release='unknown',schema=self.schemas[t],
                   scientific_role='fixture-role-'+t,exact_scope=self.scope,disposition='implemented_and_reconciled' if m['native_rows'] else 'verified_empty',
                   counts=dict(native=m['native_rows'],parsed=m['parsed_rows'],errors=0),joins=dict(reviewed_fact_artifact=manifest),
                   dictionary_fix_effects=dict(reviewed_fact_artifact=manifest),evidence=[manifest,dd,cd],reviewer_receipt=dict(review_key=self.key))
            r['resolution_id']=q.digest(r);rs.append(r)
        if self.resolution_mutator:self.resolution_mutator(rs)
        for r in rs:r['resolution_id']=q.digest({k:v for k,v in r.items() if k!='resolution_id'})
        rb=self.write('resolutions.json',rs)
        context=dict(schema_version=1,selector=q.SELECTOR,contract=self.cb,accepted_contract_review=self.ac,source=source_context,
            original_gap_artifact=self.gb,profile=pb,native_index=ib,**codes,scan_review=sc['receipt'],support_audit_manifest=manifest,support_review=su['receipt'],
            expected_scope=self.scope,expected_consumption=self.ec,candidate_decisions=dd,resolutions=rb,obligation_ids=ids)
        if self.context_mutator:self.context_mutator(context)
        sr=self.wrapper('science.json','scientific',review_key=self.key,context=context)
        self.actual=dict(schema_version=1,attestation=q.ATTESTATION,receipt_writer='synthetic-leader',accepted_contract=ac,scan=sc,support=su,scientific=sr)
        self.e=dict(schema_version=1,adapter=q.SELECTOR['adapter'],contract=self.cb,accepted_contract_review=self.ac,source_id=self.sid,source_sha256=self.source['sha256'],
                    source_release='unknown',original_gap_artifact=self.gb,profile=pb,native_index=ib,scan_review=sc['receipt'],support_audit_manifest=manifest,support_review=su['receipt'],
                    scope_membership=nb,candidate_decisions=dd,candidate_consumption=cd,resolutions=rb,scientific_review=sr['receipt'])

    def check(self, bind=None):
        return q.validate(self.cb,self.ac,self.config,self.source,self.gb,self.e,self.scope,self.ec,self.actual,bind or (lambda _:None))


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Package(Path(self.temp.name))

    def reject(self,pattern):
        with self.assertRaisesRegex(q.SQLResolutionError,pattern):self.p.check()

    def test_complete_constructible_package(self):
        bindings=[];r=self.p.check(bindings.append)
        self.assertEqual(len(r['obligation_ids']),3)
        self.assertEqual(r['consumed_bindings'],bindings)
        self.assertTrue(any(x['path']==q.__file__ for x in bindings))

    def test_native_index_read_only(self):
        p=self.p.root/'native.sqlite'
        before=(p.stat().st_size,p.stat().st_mtime_ns,q.hashlib.sha256(p.read_bytes()).hexdigest())
        self.p.check()
        self.assertEqual(before,(p.stat().st_size,p.stat().st_mtime_ns,q.hashlib.sha256(p.read_bytes()).hexdigest()))
        self.assertFalse(Path(str(p)+'-journal').exists())

    def empty_mapping(self, values, evidence_field='toxval_numeric', root=None):
        root=(Path(self.temp.name) if root is None else root)/'mapping';root.mkdir()
        self.p=Package(root,values)
        e=self.p.bridge[-1];removed={c['candidate_id'] for c in e['candidates']}
        e['candidates']=[]
        raw=self.p.raw['3']
        e['no_candidate_review']=dict(conclusion='no_measurement_candidates',
            evidence=[self.p.anchor(e['native'],evidence_field,raw[evidence_field])],
            rationale='reviewed mapping-only diagnostic',review_evidence=[self.p.proof])
        self.p.decisions=[d for d in self.p.decisions if d['candidate_id'] not in removed]
        self.p.consumption=[d for d in self.p.consumption if d['candidate_id'] not in removed]
        self.p.expected_bridge=copy.deepcopy(self.p.bridge);self.p.refresh()

    def test_empty_mapping_cannot_hide_current_measurement(self):
        self.empty_mapping(dict(toxval_numeric_original='-'))
        self.reject('hides measurement')

    def test_empty_mapping_cannot_hide_original_measurement(self):
        self.empty_mapping(dict(toxval_numeric='-'),'toxval_numeric_original')
        self.reject('hides measurement')

    def test_valid_empty_mapping_and_optional_metadata(self):
        self.empty_mapping(dict(toxval_numeric='-',toxval_numeric_original='-'))
        self.assertEqual(len(self.p.check()['obligation_ids']),3)

    def test_empty_mapping_cannot_hide_unusable_numeric_lexemes(self):
        for value in ('0','-1','-999','<10','unusable'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                self.empty_mapping(dict(toxval_numeric=value,toxval_numeric_original='-'),root=Path(temp))
                self.reject('hides measurement')

    def test_empty_mapping_cannot_anchor_other_native_measurement(self):
        self.empty_mapping(dict(toxval_numeric='-',toxval_numeric_original='-'))
        self.p.bridge[-1]['no_candidate_review']['evidence']=[self.p.anchor(self.p.natives[0],'toxval_numeric','1000')]
        self.p.expected_bridge=copy.deepcopy(self.p.bridge);self.p.refresh()
        self.reject('hides measurement anchor')

    def test_consumption_float_locator(self):
        self.p.consumption=copy.deepcopy(self.p.consumption)
        for r in self.p.consumption:
            r['native_locator']['statement_ordinal']=float(r['native_locator']['statement_ordinal'])
        self.p.refresh();self.reject('Invalid integer')

    def test_critical_invalid_extra_locator(self):
        next(iter(self.p.metadata.values()))['native_candidate_locators'].append(
            dict(table_name='nonexistent',statement_ordinal=False,tuple_ordinal=-10,line='made-up'))
        self.p.refresh();self.reject('Invalid integer')

    def test_critical_nonexistent_well_typed_locator(self):
        next(iter(self.p.metadata.values()))['native_candidate_locators'].append(
            dict(table_name='toxval',statement_ordinal=999,tuple_ordinal=1,line=999))
        self.p.refresh();self.reject('Missing/wrong native locator')

    def test_critical_valid_shared_native_locator(self):
        next(iter(self.p.metadata.values()))['native_candidate_locators'].append(q.locator(self.p.natives[1]))
        self.p.refresh();self.p.check()

    def test_exponent_overflow_json_and_jsonl(self):
        for spelling in ('1e999','-1e999'):
            raw='{"nested":[{"value":'+spelling+'}]}'
            with self.subTest(spelling=spelling,loader='json'):
                with self.assertRaises(q.SQLResolutionError):q.loads(raw)
            p=Path(self.temp.name)/'overflow.jsonl';p.write_text(raw+'\n',encoding='utf8')
            with self.subTest(spelling=spelling,loader='jsonl'):
                with self.assertRaises(q.SQLResolutionError):
                    list(q.Files(lambda b:None).lines(q.small(q.scan.file_binding(p))))

    def test_boolean_is_not_resolution_count(self):
        self.p.resolution_mutator=lambda rs:rs[0]['counts'].update(native=True)
        self.p.refresh();self.reject('Invalid integer')

    def test_caller_receipt_cannot_differ_from_bound_review(self):
        self.p.actual['scientific']['receipt_body']['review_key']='forged-caller-key'
        self.reject('Caller-attested review differs')

    def test_omit_mapping_only_native_after_rebinding(self):
        self.p.bridge.pop();self.p.refresh();self.reject('Missing native bridge')

    def test_omit_quarantined_sibling_after_rebinding(self):
        e=self.p.bridge[0];e['candidates']=[c for c in e['candidates'] if c['population_scope']['sex']['quote']!='female']
        self.p.refresh();self.reject('Bridge membership')

    def test_no_candidate_cannot_hide_measured_mapping_row(self):
        e=self.p.bridge[-1];e['candidates']=[];e['no_candidate_review']=dict(conclusion='no_measurement_candidates',evidence=[],rationale='not enough',review_evidence=[self.p.proof])
        self.p.refresh();self.reject('hides measurement')

    def test_missing_decision(self):
        self.p.decisions.pop();self.p.refresh();self.reject('Missing candidate decision')

    def test_missing_consumption(self):
        self.p.consumption.pop();self.p.refresh();self.reject('Missing candidate consumption')

    def test_incomplete_extraction_cannot_quarantine(self):
        d=next(d for d in self.p.decisions if d['disposition']=='scientific_quarantine');d['extraction_complete']=False
        self.p.refresh();self.reject('Unresolved extraction')

    def test_quarantine_cannot_emit_label(self):
        r=next(r for r in self.p.consumption if r['action']=='scientific_quarantine');r['output_observation_ids']=['illegal']
        self.p.refresh();self.reject('emitted labels')

    def test_missing_critical_metadata_key(self):
        next(iter(self.p.metadata.values())).pop('reopening_trigger');self.p.refresh();self.reject('Exact keys')

    def test_optional_study_id_cannot_quarantine(self):
        next(iter(self.p.metadata.values()))['missing_property']='source_study_id';self.p.refresh();self.reject('Optional missing metadata')

    def test_unapproved_clause(self):
        next(d for d in self.p.decisions if d['disposition']=='scientific_quarantine')['contract_clause']='v4_unknown'
        self.p.refresh();self.reject('Unapproved disposition')

    def test_correction_rejected(self):
        self.p.decisions[0]['consumed_values']['numeric']='1234';self.p.refresh();self.reject('Corrections unsupported')

    def test_consumption_value_mismatch(self):
        self.p.consumption[0]['consumed_values']['numeric']='1234';self.p.refresh();self.reject('Consumption decision mismatch')

    def test_missing_native_fix_membership(self):
        next(d for d in self.p.decisions if d['toxval_id']=='3')['applicable_fix_ids']=[]
        self.p.refresh();self.reject('applicable native fix')

    def test_false_empty_resolution_rebound(self):
        self.p.resolution_mutator=lambda rs:rs[0].update(disposition='verified_empty')
        self.p.refresh();self.reject('False empty')

    def test_forged_resolution_counts_rebound(self):
        self.p.resolution_mutator=lambda rs:rs[0]['counts'].update(native=999)
        self.p.refresh();self.reject('counts mismatch')

    def test_missing_extra_duplicate_obligations(self):
        for mutate in (lambda rs:rs.pop(),lambda rs:rs.append(copy.deepcopy(rs[0])),lambda rs:rs.__setitem__(1,copy.deepcopy(rs[0]))):
            self.p.resolution_mutator=mutate;self.p.refresh();self.reject('resolutions|obligations')

    def test_unresolved_table(self):
        self.p.resolution_mutator=lambda rs:rs[0].update(disposition='unresolved')
        self.p.refresh();self.reject('Unresolved table')

    def test_wrong_review_key(self):
        self.p.resolution_mutator=lambda rs:rs[0]['reviewer_receipt'].update(review_key='other')
        self.p.refresh();self.reject('review key')

    def test_stale_code_context(self):
        self.p.context_mutator=lambda c:c.update(resolver_code=self.p.proof)
        self.p.refresh();self.reject('context mismatch')

    def test_same_reviewer_rejected(self):
        body=self.p.actual['scientific']['receipt_body'];body['reviewer_execution_id']=body['producer_execution_id']
        b=self.p.write('science.json',body);self.p.actual['scientific']['receipt']=b;self.p.e['scientific_review']=b
        self.reject('Separate review')

    def test_stale_native_index(self):
        with (self.p.root/'native.sqlite').open('ab') as f:f.write(b'changed')
        self.reject('Stale file')

    def test_v3_no_execution_selector(self):
        self.p.contract['scientific_policy']['typed_sql_gap_resolution_v4'].pop('execution')
        self.p.refresh();self.reject('execution')

    def test_array_is_not_jsonl(self):
        b=self.p.write('decisions.jsonl',self.p.decisions)
        self.p.e['candidate_decisions']=b
        ctx=self.p.actual['scientific']['receipt_body']['context'];ctx['candidate_decisions']=b
        body=self.p.actual['scientific']['receipt_body'];sr=self.p.write('science.json',body)
        self.p.e['scientific_review']=sr;self.p.actual['scientific']['receipt']=sr
        self.reject('JSONL objects')

    def test_strict_json(self):
        for s in ('{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}'):
            with self.assertRaises(q.SQLResolutionError):q.loads(s)

    def test_mutation_during_binding(self):
        def mutate(b):
            if b==self.p.e['candidate_decisions']:
                p=Path(b['path']);p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(q.SQLResolutionError):self.p.check(mutate)


TERMINAL_FACTS = [
    dict(toxval_numeric=None, toxval_numeric_original=None, toxval_numeric_qualifier='<',
         toxval_numeric_qualifier_original='<', species_original='rabbit', exposure_route='dermal',
         exposure_route_original='dermal', strain_original='White rabbits  were treated with CASRN 106-93-4 via the dermal route at 210  or 300, 650 and 1100 mg/kg-bw  under impervious sleeves and heavy cloth bandages for 24 hours and observed for 2 weeks following dosing',
         exposure_method_original='-', sex_original='-',toxval_numeric_converted=None,toxval_numeric_human=None,toxval_numeric_standard=None),
    dict(toxval_numeric=None, toxval_numeric_original=None, toxval_numeric_qualifier='>',
         toxval_numeric_qualifier_original='>', species_original='rat', exposure_route='inhalation',
         exposure_route_original='inhalation', strain_original='Wistar rats',
         exposure_method_original='vapor', sex_original='male and female',toxval_numeric_converted=None,toxval_numeric_human=None,toxval_numeric_standard=None),
]


class TerminalPackage(Package):
    """Synthetic source identities; native facts mirror reviewed 183816/184013."""
    terminal_mutator = None
    terminal_consumption_mutator = None
    terminal_return_mutator = None
    terminal_clause = 'v4_explicit_inequality'
    terminal_policy = None

    def refresh(self):
        super().refresh()
        self.contract['scientific_policy']['typed_sql_gap_resolution_v4']['execution'] = q.SELECTOR_V2.copy()
        self.clauses['v4_explicit_inequality'] = self.terminal_policy or dict(disposition='excluded', reason='explicit_inequality')
        self.cb = self.write('contract.json', self.contract)
        ac = self.wrapper('acceptance.json', 'accepted_contract', stage='contract',
                          contract_sha256=self.cb['sha256'], candidate=self.write('contract-candidate.json', dict(contract=self.cb)))
        self.ac = ac['receipt']; self.actual['accepted_contract'] = ac
        self.scope['schema_version'] = 2
        self.scope['native_evidence'] = self.write('native-sidecar.jsonl', [dict(native=n,fields=self.raw[n['toxval_id']]) for n in self.natives], True)
        self.scope['bridge']['no_candidate_count'] = 0
        self.scope['bridge']['candidate_native_count'] = 1
        decisions = []
        with closing(sqlite3.connect(self.root/'native.sqlite')) as db:
            for entry in self.bridge[:2]:
                n = entry['native']
                facts = q.terminal_facts(db, n)
                interpretations = []
                for fact in facts:
                    for field, value in sorted(fact['fields'].items()):
                        interpretations.append(dict(native_locator=fact['native_locator'], field=field, value=value,
                            classification='administered_dose' if field=='strain_original' and n['toxval_id']=='1' else 'not_measurement',
                            rationale='Synthetic reviewed field interpretation'))
                payload = dict(schema_version=1, native=n, source_sha256=self.source['sha256'],contract=self.cb,
                               native_evidence=self.scope['native_evidence'],facts=facts, interpretations=interpretations,
                               conclusion='no_unenumerated_measurement', contract_clause=self.terminal_clause)
                if self.terminal_mutator: self.terminal_mutator(payload)
                returned_body = dict(schema_version=1,
                    review_key=self.key, producer_execution_id='synthetic-producer', reviewer_execution_id='synthetic-reviewer',
                    review_payload=copy.deepcopy(payload))
                if self.terminal_return_mutator: self.terminal_return_mutator(returned_body)
                returned = self.write('terminal-return-'+n['toxval_id']+'.json', returned_body)
                rb = self.write('terminal-review-'+n['toxval_id']+'.json', dict(payload, independent_return=returned))
                d = dict(kind='source_row_terminal_exclusion_v1', native=n, contract_clause=self.terminal_clause, review=rb)
                d['decision_id'] = q.digest(d)
                decisions.append(d)
                entry.update(no_candidate_review=None, source_row_terminal=d['decision_id'])
            self.bridge[2]['source_row_terminal'] = None
        self.terminal_decisions = decisions
        bb = self.write('bridge.jsonl', self.bridge, True)
        self.scope['candidate_bridge'] = bb
        self.scope['bridge']['members_sha256'] = self.md(self.bridge)['members_sha256']
        self.scope['source_row_terminals'] = self.md([dict(native=d['native'], decision_id=d['decision_id']) for d in decisions])
        dd = self.write('terminal-decisions.jsonl', decisions, True)
        consumption = [dict(kind=d['kind'],native=d['native'],decision_id=d['decision_id'],action='excluded',
                            clause=d['contract_clause'],output_observation_ids=[],output_artifacts=[],output_labels=[]) for d in decisions]
        if self.terminal_consumption_mutator: self.terminal_consumption_mutator(consumption)
        cd = self.write('terminal-consumption.jsonl', consumption, True)
        self.ec.update(source_row_ledger=cd, source_row_membership=self.md(consumption))
        self.e.update(schema_version=2, adapter=q.SELECTOR_V2['adapter'],contract=self.cb,
                      accepted_contract_review=self.ac,source_row_decisions=dd,source_row_consumption=cd)
        rs = q.loads(Path(self.e['resolutions']['path']).read_bytes())
        for r in rs:
            r['exact_scope'] = self.scope
            r['resolution_id'] = q.digest({k:v for k,v in r.items() if k!='resolution_id'})
        self.e['resolutions'] = self.write('resolutions.json',rs)
        context = self.actual['scientific']['receipt_body']['context']
        context.update(schema_version=2,selector=q.SELECTOR_V2,contract=self.cb,accepted_contract_review=self.ac,
                       expected_scope=self.scope,expected_consumption=self.ec,source_row_decisions=dd,resolutions=self.e['resolutions'])
        sr = self.wrapper('science.json','scientific',review_key=self.key,context=context)
        self.actual['scientific'] = sr; self.e['scientific_review'] = sr['receipt']


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=TerminalPackage(Path(self.temp.name),terminal_rows=copy.deepcopy(TERMINAL_FACTS))

    def test_two_native_fact_patterns(self):
        self.p.check()
        self.assertEqual(self.p.scope['source_row_terminals']['count'],2)
        self.assertEqual(self.p.scope['bridge']['candidate_count'],1)
        self.assertEqual(self.p.scope['exact_ld50']['count'],2)

    def test_hidden_narrative_measurement(self):
        def mutate(p):
            next(x for x in p['interpretations'] if x['field']=='strain_original')['classification']='measured_target'
        self.p.terminal_mutator=mutate;self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'Unenumerated'):self.p.check()

    def test_omitted_context_field(self):
        self.p.terminal_mutator=lambda p:p['interpretations'].pop()
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'field inventory'):self.p.check()

    def test_rebound_native_snapshot(self):
        self.p.terminal_mutator=lambda p:p['facts'][0]['fields'].update(strain_original='other row')
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'native/support/fix'):self.p.check()

    def test_omitted_support(self):
        self.p.terminal_mutator=lambda p:p['facts'].pop()
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'native/support/fix'):self.p.check()

    def test_emissions_and_consumption_attacks(self):
        for attack in (lambda rows:rows.pop(),lambda rows:rows.append(rows[-1]),
                       lambda rows:rows[0].update(native=rows[1]['native']),
                       lambda rows:rows[0].update(output_labels=[1]),
                       lambda rows:rows[0].update(output_observation_ids=['fabricated']),
                       lambda rows:rows[0].update(output_artifacts=[self.p.proof])):
            with self.subTest(attack=attack):
                self.p.terminal_consumption_mutator=attack;self.p.refresh()
                with self.assertRaises(q.SQLResolutionError):self.p.check()

    def package_with(self, changes):
        facts=copy.deepcopy(TERMINAL_FACTS)
        facts[0].update(changes)
        root=Path(self.temp.name)/str(len(list(Path(self.temp.name).iterdir())))
        root.mkdir()
        return TerminalPackage(root,terminal_rows=facts)

    def rebind_decisions(self, rows):
        b=self.p.write('terminal-decisions.jsonl',rows,True)
        self.p.e['source_row_decisions']=b
        body=self.p.actual['scientific']['receipt_body']
        body['context']['source_row_decisions']=b
        sr=self.p.write('science.json',body)
        self.p.actual['scientific']['receipt']=sr;self.p.e['scientific_review']=sr

    def test_populated_either_channel_never_terminal(self):
        for field in ('toxval_numeric','toxval_numeric_original'):
            for value in ('0','-0','-999','-1','unusable','NULL','<10'):
                with self.subTest(field=field,value=value):
                    p=self.package_with({field:value})
                    with self.assertRaisesRegex(q.SQLResolutionError,'populated numeric'):p.check()

    def test_absence_without_native_inequality_rejected(self):
        for qualifier in ('','-','=','unknown'):
            for route in ('oral','unknown','dermal'):
                with self.subTest(qualifier=qualifier,route=route):
                    p=self.package_with(dict(toxval_numeric_qualifier=qualifier,toxval_numeric_qualifier_original=qualifier,
                                            species_original='rat',exposure_route=route,exposure_route_original=route))
                    with self.assertRaisesRegex(q.SQLResolutionError,'requires native inequality'):p.check()

    def test_original_inequality_survives_current_equality(self):
        self.package_with(dict(toxval_numeric_qualifier='=')).check()

    def test_current_inequality_survives_original_blank(self):
        self.package_with(dict(toxval_numeric_qualifier_original='')).check()

    def test_populated_numeric_alternative_rejected(self):
        self.p=self.package_with(dict(toxval_numeric_converted='0'))
        with self.assertRaisesRegex(q.SQLResolutionError,'alternative numeric'):self.p.check()

    def test_fix_membership_not_optional(self):
        def remove_fix(p):
            p['facts'][:]=[f for f in p['facts'] if f['native_locator']['table_name']!='toxval_fix']
        self.p.terminal_mutator=remove_fix;self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'native/support/fix'):self.p.check()

    def test_native_field_anchor_rebound(self):
        def rebound(p):
            p['interpretations'][0]['native_locator']=q.locator(self.p.natives[2])
        self.p.terminal_mutator=rebound;self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'field inventory'):self.p.check()

    def test_missing_duplicate_fabricated_terminal_decisions(self):
        attacks=(lambda r:r.pop(),lambda r:r.append(r[-1]),lambda r:r[0].update(candidate_id='0'*64),
                 lambda r:r[0].update(native=r[1]['native']),lambda r:r[0].update(decision_id='0'*64))
        for attack in attacks:
            with self.subTest(attack=attack):
                self.p.refresh();rows=copy.deepcopy(self.p.terminal_decisions);attack(rows);self.rebind_decisions(rows)
                with self.assertRaises(q.SQLResolutionError):self.p.check()

    def test_independent_return_binds_exact_payload(self):
        self.p.terminal_return_mutator=lambda r:r['review_payload'].update(conclusion='boolean-approval')
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'returned evidence'):self.p.check()

    def test_independent_return_not_same_execution(self):
        self.p.terminal_return_mutator=lambda r:r.update(reviewer_execution_id=r['producer_execution_id'])
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'Separate terminal review'):self.p.check()

    def test_rebound_source_contract_or_sidecar(self):
        for field,value in (('source_sha256','0'*64),('contract',self.p.proof),('native_evidence',self.p.proof)):
            with self.subTest(field=field):
                self.p.terminal_mutator=lambda p:p.update({field:value});self.p.refresh()
                with self.assertRaisesRegex(q.SQLResolutionError,'source/contract/sidecar'):self.p.check()

    def test_unaccepted_or_incompatible_clause(self):
        for clause in ('v4_incompatible_task','v4_unavailable_critical_metadata','invented'):
            with self.subTest(clause=clause):
                self.p.terminal_clause=clause;self.p.refresh()
                with self.assertRaisesRegex(q.SQLResolutionError,'Unapproved terminal clause'):self.p.check()

    def test_wrong_accepted_clause_semantics(self):
        self.p.terminal_policy=dict(disposition='excluded',reason='missing_numeric')
        self.p.refresh()
        with self.assertRaisesRegex(q.SQLResolutionError,'Unapproved terminal clause'):self.p.check()

    def test_equal_count_wrong_terminal_membership(self):
        self.p.scope['source_row_terminals']['members_sha256']='0'*64
        body=self.p.actual['scientific']['receipt_body'];sr=self.p.write('science.json',body)
        self.p.actual['scientific']['receipt']=sr;self.p.e['scientific_review']=sr
        with self.assertRaisesRegex(q.SQLResolutionError,'Scope membership differs'):self.p.check()

    def test_equal_count_wrong_consumption_membership(self):
        self.p.ec['source_row_membership']['members_sha256']='0'*64
        body=self.p.actual['scientific']['receipt_body'];sr=self.p.write('science.json',body)
        self.p.actual['scientific']['receipt']=sr;self.p.e['scientific_review']=sr
        with self.assertRaisesRegex(q.SQLResolutionError,'Scope membership differs'):self.p.check()

    def test_old_selector_cannot_activate_new_variant(self):
        self.p.e['schema_version']=1;self.p.e['adapter']=q.SELECTOR['adapter']
        with self.assertRaisesRegex(q.SQLResolutionError,'Exact keys'):self.p.check()

    def test_terminal_and_candidate_branches_disjoint(self):
        self.p.bridge[2]['source_row_terminal']=self.p.terminal_decisions[0]['decision_id']
        b=self.p.write('bridge.jsonl',self.p.bridge,True);self.p.scope['candidate_bridge']=b
        # Context/scope is shared deliberately; rebind receipt and resolutions.
        body=self.p.actual['scientific']['receipt_body'];sr=self.p.write('science.json',body)
        self.p.actual['scientific']['receipt']=sr;self.p.e['scientific_review']=sr
        with self.assertRaisesRegex(q.SQLResolutionError,'branches overlap'):self.p.check()


def rebind_scope_claims(p):
    """Adversary rebinds every scope/audit/receipt dependency, not native bytes."""
    natives=[dict(source_id=p.sid,table_name='toxval',statement_ordinal=r['_statement'],tuple_ordinal=r['_tuple'],
                  line=r['_line'],toxval_id=r['toxval_id'],row_sha256=r['_hash']) for r in p.members]
    nb=p.write('source-scope-membership.jsonl',p.members,True)
    p.e['scope_membership']=nb;p.scope['native_membership']=nb
    p.scope['primary']=p.md(natives)
    p.scope['exact_ld50']=p.md([n for n,r in zip(natives,p.members) if r['exact_current_ld50']])
    p.scope['diagnostic_union']=p.md([n for n,r in zip(natives,p.members) if r['task_relevant']])
    p.scope['candidate_bridge']=p.write('bridge.jsonl',p.bridge,True)
    candidates=[dict(native=e['native'],**c) for e in p.bridge for c in e['candidates']]
    p.scope['candidate_membership']=p.md(candidates)
    p.scope['bridge']=dict(native_count=len(p.bridge),candidate_count=len(candidates),
        no_candidate_count=sum(not e['candidates'] and e.get('source_row_terminal') is None for e in p.bridge),
        members_sha256=p.md(p.bridge)['members_sha256'])
    if p.e['schema_version']==2:
        p.scope['bridge']['candidate_native_count']=sum(bool(e['candidates']) for e in p.bridge)
        ids={e['source_row_terminal'] for e in p.bridge if e['source_row_terminal'] is not None}
        ds=[d for d in p.terminal_decisions if d['decision_id'] in ids]
        p.scope['source_row_terminals']=p.md([dict(native=d['native'],decision_id=d['decision_id']) for d in ds])
        p.e['source_row_decisions']=p.write('terminal-decisions.jsonl',ds,True)
        rows=[q.loads(line) for line in Path(p.ec['source_row_ledger']['path']).read_bytes().splitlines()]
        rows=[r for r in rows if r['decision_id'] in ids]
        cd=p.write('terminal-consumption.jsonl',rows,True)
        p.ec.update(source_row_ledger=cd,source_row_membership=p.md(rows));p.e['source_row_consumption']=cd
    ids={c['candidate_id'] for c in candidates}
    p.e['candidate_decisions']=p.write('decisions.jsonl',[d for d in p.decisions if d['candidate_id'] in ids],True)
    p.e['candidate_consumption']=p.write('consumption.jsonl',[r for r in p.consumption if r['candidate_id'] in ids],True)
    p.ec['ledger']=p.e['candidate_consumption']
    ma=q.loads(Path(p.e['support_audit_manifest']['path']).read_bytes())
    ma['artifacts']=[nb if Path(b['path']).name=='source-scope-membership.jsonl' else b for b in ma['artifacts']]
    mb=p.write('audit-manifest.json',ma);p.e['support_audit_manifest']=mb
    su=p.actual['support']['receipt_body'];su['context']['support_audit_manifest']=mb
    p.actual['support']['receipt']=p.write('support-review.json',su);p.e['support_review']=p.actual['support']['receipt']
    rs=q.loads(Path(p.e['resolutions']['path']).read_bytes())
    for r in rs:
        r['exact_scope']=p.scope;r['joins']['reviewed_fact_artifact']=mb
        r['dictionary_fix_effects']['reviewed_fact_artifact']=mb;r['evidence'][0]=mb
        r['resolution_id']=q.digest({k:v for k,v in r.items() if k!='resolution_id'})
    p.e['resolutions']=p.write('resolutions.json',rs)
    context=p.actual['scientific']['receipt_body']['context']
    context.update(expected_scope=p.scope,expected_consumption=p.ec,resolutions=p.e['resolutions'],
                   candidate_decisions=p.e['candidate_decisions'],support_audit_manifest=mb,support_review=p.e['support_review'])
    if p.e['schema_version']==2:context['source_row_decisions']=p.e['source_row_decisions']
    sr=p.wrapper('science.json','scientific',review_key=p.key,context=context)
    p.actual['scientific']=sr;p.e['scientific_review']=sr['receipt']


class NativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def package(self, version, **kwargs):
        root=Path(self.temp.name)/str(len(list(Path(self.temp.name).iterdir())));root.mkdir()
        return TerminalPackage(root,terminal_rows=copy.deepcopy(TERMINAL_FACTS),**kwargs) if version==2 else Package(root,**kwargs)

    def test_independent_false_zero_terminal_omission_repro(self):
        p=self.package(2)
        p.members[0].update(exact_current_ld50=0,task_relevant=0)
        p.bridge=p.bridge[1:]
        rebind_scope_claims(p)
        with self.assertRaisesRegex(q.SQLResolutionError,'Native scope flags'):p.check()

    def test_false_exact_flags_both_selectors(self):
        for version in (1,2):
            with self.subTest(version=version):
                p=self.package(version);p.members[0]['exact_current_ld50']=0
                rebind_scope_claims(p)
                with self.assertRaisesRegex(q.SQLResolutionError,'Native scope flags'):p.check()

    def test_hidden_mapping_flags_both_selectors(self):
        for version in (1,2):
            with self.subTest(version=version):
                p=self.package(version);p.members[2]['task_relevant']=0;p.bridge.pop()
                rebind_scope_claims(p)
                with self.assertRaisesRegex(q.SQLResolutionError,'Native scope flags'):p.check()

    def test_outside_union_equal_count_identity_replacement(self):
        for version in (1,2):
            for field,value in (('toxval_id','fabricated'),('_line',9000),('_statement',9000),('_tuple',9000),('_hash','0'*64)):
                with self.subTest(version=version,field=field):
                    p=self.package(version);p.members[-1].update(exact_current_ld50=0,task_relevant=0)
                    p.members[-1][field]=value;p.bridge.pop();rebind_scope_claims(p)
                    with self.assertRaisesRegex(q.SQLResolutionError,'Native primary membership'):p.check()

    def test_missing_primary_row_even_after_count_rebinding(self):
        for version in (1,2):
            with self.subTest(version=version):
                p=self.package(version);p.members.pop();p.bridge.pop();rebind_scope_claims(p)
                with self.assertRaisesRegex(q.SQLResolutionError,'Missing native primary'):p.check()

    def test_genuine_outside_union_row_is_still_verified(self):
        for version in (1,2):
            for field,value in (('toxval_id','substitute'),('_hash','0'*64),('_line',9000),('_tuple',9000),('_statement',9000)):
                with self.subTest(version=version,field=field):
                    p=self.package(version,outside_union=True)
                    p.check()
                    p.members[-1][field]=value;rebind_scope_claims(p)
                    with self.assertRaisesRegex(q.SQLResolutionError,'Native primary membership'):p.check()

    def test_outside_union_omission_rejected(self):
        for version in (1,2):
            with self.subTest(version=version):
                p=self.package(version,outside_union=True);p.members.pop();rebind_scope_claims(p)
                with self.assertRaisesRegex(q.SQLResolutionError,'Missing native primary'):p.check()

    def test_extra_primary_row_rejected(self):
        p=self.package(2);extra=copy.deepcopy(p.members[-1])
        extra.update(toxval_id='extra',_statement=9000,_line=9000,exact_current_ld50=0,task_relevant=0)
        p.members.append(extra);rebind_scope_claims(p)
        with self.assertRaisesRegex(q.SQLResolutionError,'Native primary membership'):p.check()

    def test_native_normalized_endpoint_union_controls(self):
        for version in (1,2):
            for values in (dict(toxval_type_original=' ld50 '),dict(toxval_type=' ld50 ',toxval_type_original='Other')):
                with self.subTest(version=version,values=values):
                    p=self.package(version,mapping_values=values)
                    next(d for d in p.decisions if d['toxval_id']=='3')['applicable_fix_ids']=[]
                    p.refresh();p.check()
                    self.assertEqual(p.scope['exact_ld50']['count'],2)
                    self.assertEqual(p.scope['diagnostic_union']['count'],3)


if __name__ == '__main__':
    unittest.main()
