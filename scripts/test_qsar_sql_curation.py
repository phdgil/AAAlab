"""Genuine synthetic SQL curation workflow; receipts are fixture-only."""
import copy
import csv
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts.test_qsar_sql_resolution import Package, TerminalPackage, TERMINAL_FACTS, q
from scripts.test_qsar_data import data
import qsar_sql_curation as sql
import qsar_curation_evidence as guard


class VariantPackage(Package):
    def __init__(self, root, variant):
        self.variant = variant
        values = dict(toxval_type='LD50', toxval_type_original='LD50')
        if variant == 'zero': values.update(toxval_numeric='0', toxval_numeric_original='0')
        if variant == 'unqualified': values.update(toxval_numeric_qualifier_original='-')
        if variant == 'inequality': values.update(toxval_numeric_qualifier_original='<', toxval_numeric_qualifier='<')
        if variant == 'chemical':
            values.update(chemical_id='C2', chemical_id_original='C', dtxsid='D2', dtxsid_original='D',
                exposure_route='dermal', exposure_route_original='oral', toxval_numeric_original='10',
                toxval_numeric='1000', toxval_units_original='mg/kg', toxval_units='ug/kg')
        original_scan = q.scan.scan
        def scan_source(source, *args, **kwargs):
            if variant == 'chemical':
                with Path(source).open('a', encoding='utf-8') as stream:
                    stream.write("CREATE TABLE `source_chemical` (`chemical_id` text, `casrn` text, `dtxsid` text);\n")
                    stream.write("INSERT INTO `source_chemical` VALUES ('C','64-17-5','D'),('C2','67-56-1','D2');\n")
            return original_scan(source, *args, **kwargs)
        with patch.object(q.scan, 'scan', side_effect=scan_source):
            super().__init__(root, mapping_values=values, outside_union=True)

    def anchor(self, native, field, quote, start=0):
        if field == 'toxval_numeric_original': quote = self.raw[native['toxval_id']][field]
        return super().anchor(native, field, quote, start)

    def refresh(self):
        if self.variant == 'inequality': self.clauses['v4_explicit_inequality']=dict(disposition='excluded',reason='explicit_inequality')
        for r in self.members: r['exact_current_ld50'] = int(self.raw[r['toxval_id']]['toxval_type']=='LD50')
        for d in self.decisions:
            if d['toxval_id'] == '3':
                d.update(disposition='preserve', contract_clause=None, applicable_fix_ids=[])
                if self.variant == 'inequality': d.update(disposition='excluded',contract_clause='v4_explicit_inequality')
                if self.variant == 'chemical':
                    for k in ('chemical_id','dtxsid','route','numeric','units'): d['consumed_values'][k]=d['original'][k]
        super().refresh()
        self.scope['exact_ld50']=self.md([n for n in self.natives if self.raw[n['toxval_id']]['toxval_type']=='LD50'])


class Workflow:
    def __init__(self, root, terminals=False, material_enabled=False, preparation=False, variant=None, organic_graph=None):
        self.root = root
        self.organic_graph = organic_graph
        packet_root = root / 'packet'; packet_root.mkdir()
        self.p = (TerminalPackage(packet_root, terminal_rows=copy.deepcopy(TERMINAL_FACTS), outside_union=True) if terminals
                  else VariantPackage(packet_root, variant) if variant else Package(packet_root, outside_union=True))
        p = self.p
        raw = root / 'raw'; (raw / 'synthetic').mkdir(parents=True)
        source = raw / p.sid
        shutil.copyfile(packet_root / 'source.sql', source)
        (raw / 'support.csv').write_text('dtxsid,smiles\nD,' + (organic_graph or 'CCO') + '\nD2,CO\n', encoding='utf-8')
        if preparation:
            rows = [dict(source_record_id=str(i), casrn='', species='rat', route='oral', endpoint_type='LD50',
                numeric_value=100+i, numeric_unit='mg/kg', smiles='C'*(i+1), qualifier='=') for i in range(12)]
            data.write_csv(raw / 'ordinary.csv', rows, list(rows[0]))
            p.contract['data_curation']['sources'].append(dict(source_id='ordinary.csv',adapter='oecd',columns={'relation':'qualifier'}))
            p.contract['preparation'] = dict(target_column='target_pLD50',group_column='group_id',seed=9,terminal_fraction=.25,
                outer_folds=3,inner_folds=2,intended_generalization='synthetic',terminal_custodian='fixture',
                prior_exposure=dict(status='none_known',evidence=[],description='synthetic'),unfitted_transforms={'scaling':'fold-local'})
        self.collection = root / 'collection'
        data.collect(raw, self.collection)
        self.registry = self.collection / 'source_registry.json'
        self.contract = packet_root / 'contract.json'
        # Rebuild the genuine native index at the collection-bound source path.
        (packet_root / 'native.sqlite').unlink()
        p.profile = q.scan.scan(source, packet_root / 'native.sqlite', p.sid)
        p.source = dict(source_id=p.sid, **q.small(p.profile['source']))
        p.gaps = data.load(self.collection / 'collection_gaps.json')
        p.obligations = q.scan.obligations(p.gaps, p.sid)
        p.contract['execution_policy'] = dict(allow_training_with_unresolved_required_source_coverage=False)
        p.contract['data_curation'].update(chemistry_policy='single_component', relation_policy='reported_numeric', replicate_policy='preserve_flag',
            tasks=[dict(id='rat', species='rat', route='oral', endpoint='LD50')],
            identity_sources=[dict(source_id='support.csv', columns=dict(dtxsid='dtxsid', smiles='smiles'))])
        if material_enabled:
            from scripts.test_qsar_verified_material import MaterialTests
            fixture = MaterialTests(); fixture.setUp()
            p.contract['scientific_policy'].update(copy.deepcopy(fixture.contract['scientific_policy']))
            fixture.doCleanups()
            if organic_graph:
                from scripts.test_qsar_material_integration import enable_organic
                enable_organic(p.contract)
        p.refresh()
        if not terminals:
            p.contract['scientific_policy']['typed_sql_gap_resolution_v4']['execution'] = q.SELECTOR_V2.copy()
            p.cb = p.write('contract.json', p.contract)
            ac = p.wrapper('acceptance.json', 'accepted_contract', stage='contract', contract_sha256=p.cb['sha256'],
                candidate=p.write('contract-candidate.json', dict(contract=p.cb)))
            p.ac = ac['receipt']; p.actual['accepted_contract'] = ac
            for e in p.bridge: e['source_row_terminal'] = None
            p.scope.update(schema_version=2, native_evidence=p.proof, source_row_terminals=p.md([]),
                candidate_bridge=p.write('bridge.jsonl', p.bridge, True))
            p.scope['bridge'].update(candidate_native_count=len(p.bridge), members_sha256=p.md(p.bridge)['members_sha256'])
            p.e['source_row_decisions'] = p.write('terminal-decisions.jsonl', [], True)
        p.gb = guard.binding(self.collection / 'collection_gaps.json')
        p.e['original_gap_artifact'] = p.gb
        self.inputs = dict(schema_version=1, kind=sql.KIND, full_contract=p.cb, accepted_contract_review=p.ac,
            review_key=p.key, **{k:p.e[k] for k in ('profile','native_index','scan_review','support_audit_manifest','support_review','candidate_decisions','source_row_decisions')},
            scope=p.write('scope.json', p.scope))
        self.external = packet_root / 'external.json'
        p.write('external.json', {p.sid:self.inputs})
        self.out = root / 'curation'
        self.material_packet = self.make_material() if material_enabled else None
        # Resolver fixture templates are not consumer inputs. Remove their dummy effects/receipt
        # before exercising the actual diagnostic -> separate synthetic review sequence.
        self.resolution_templates = q.loads(Path(p.e['resolutions']['path']).read_bytes())
        for name in ('consumption.jsonl','terminal-consumption.jsonl','outputs.json','science.json','resolutions.json'):
            (packet_root / name).unlink(missing_ok=True)

    def make_material(self):
        import qsar_material_curation as m
        import qsar_verified_material as v
        from rdkit import rdBase
        p = self.p
        registry = data.load(self.registry)
        packet = dict(schema_version=1, kind='qsar_material_curation_v1', full_contract=p.cb,
            accepted_contract_review=p.ac, source_registry=guard.binding(self.registry), adjudication_config=guard.binding(self.external),
            environment=dict(rdkit_version=rdBase.rdkitVersion), replay_provider=dict(implementation=guard.binding(m.__file__),
                callable='NativeReplayStore.replay_native', dependencies=[b for b in m.code_bindings() if b['path'] != str(Path(m.__file__).resolve())]))
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            store = m.NativeReplayStore(scratch)
            policy = copy.deepcopy(p.contract['data_curation'])
            data.apply_adjudication_bindings(policy['sources'], {p.sid:self.inputs}, self.contract)
            config = policy['sources'][0]
            data.identity_index(policy, registry, raw_support_observer=store.observe_support)
            classes, items, decision_rows = [], [], []
            with sql.SQLCurationInputs(self.inputs, contract_path=self.contract, registry_path=self.registry,
                    adjudication_config_path=self.external, source_id=p.sid, work_dir=scratch, bind=lambda b:None) as session:
                for number, n, raw, terminal in session.iter_primary():
                    carriers = list(session.candidates(n)) or [None]
                    for carrier in carriers:
                        pre = data.pre_identity_observation(raw, config, policy, session.entry, number, 'toxval', sql_result=carrier)
                        obs = m.observation(raw, config, session.entry, number, 'toxval', sql_result=carrier)
                        member = store.stage(obs, raw, pre, number, 'toxval', False)
                        if pre.requires_classification:
                            support = m.decode(store.db.execute('SELECT support FROM observation WHERE member=?', (member,)).fetchone()[0])
                            classes.append(dict(schema_version=1, member_key=member,
                                **{k:obs[k] for k in ('source_id','source_sha256','native_locator','candidate_key')},
                                observation_id=m.digest(obs), support_sha256=m.digest(support), classification='unaffected_organic',
                                review_evidence=['SYNTHETIC independently reviewed unaffected organic material']))
                            if self.organic_graph:
                                from scripts.test_qsar_material_integration import MaterialV2BatchTests
                                template = MaterialV2BatchTests(); template.setUp()
                                decision = copy.deepcopy(template.decision); template.doCleanups()
                                classes[-1]['classification'] = 'affected'
                                item = dict(observation=obs, **support, source_metadata=dict(source_formula=None,
                                    study_id=None, dose_basis_declaration=None))
                                facts = v.graph_facts(self.organic_graph)
                                principal = 'CC[NH3+]'
                                evidence = support['support_candidates'][0]['raw']['representations']['smiles']
                                record = obs['body']['records'][0]
                                source_field = next(iter(record['native_record']))
                                decision.update(schema_version=3, observation_id=m.digest(obs), material_class='organic',
                                    verified_smiles=self.organic_graph, formula_unit=facts,
                                    principal_component=dict(canonical_smiles=principal, multiplicity=facts['components'][principal],
                                        source_evidence=[evidence], supplemental_evidence=[dict(
                                            file=p.write('principal-source.json', {'synthetic':'A'}), locator='/synthetic')],
                                        selection_rationale='SYNTHETIC principal assignment, no actual material acceptance'),
                                    resolution=dict(kind='support_resolution', native_representations_sha256=m.digest(obs['body']['representations']),
                                        selected_support_ids=[s['support_id'] for s in support['support_candidates']],
                                        source_evidence=[m.field_evidence(record, '/' + source_field)],
                                        representation_dispositions=[], evidence=['SYNTHETIC support-bound graph']))
                                items.append(item); decision_rows.append(decision)
            scope = store.scope(); store.close()
        packet['classification'] = p.write('material-classification.jsonl', sorted(classes, key=lambda c:c['member_key']), True)
        context = m.classification_context(packet, p.contract, registry, scope)
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-material-producer',
            reviewer_execution_id='synthetic-classification-reviewer', context=context, scope_ids=sorted(c['member_key'] for c in classes),
            native_review=p.write('material-classification-return.json', {'synthetic_only':True}))
        rb = p.write('material-classification-receipt.json', receipt)
        packet['classification_review'] = dict(receipt=rb, actual_review=dict(receipt, receipt=rb))
        inventory, decisions = p.write('material-inventory.jsonl', items, True), p.write('material-decisions.jsonl', decision_rows, True)
        context = dict(contract_sha256=v.digest(p.contract), implementation_sha256=guard.binding(v.__file__)['sha256'],
            environment=packet['environment'], inventory=inventory, decisions=decisions, replay_provider=packet['replay_provider'],
            sources=registry['files'], integration=dict(schema_version=1, classification_context=context, classification_review=rb))
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-material-producer',
            reviewer_execution_id='synthetic-material-reviewer', context=context, scope_ids=sorted(d['observation_id'] for d in decision_rows),
            native_review=p.write('material-native-return.json', {'synthetic_only':True}))
        rb = p.write('material-receipt.json', receipt)
        packet['materials'] = dict(inventory=inventory, decisions=decisions, receipt=rb, actual_review=dict(receipt, receipt=rb))
        return p.write('material-packet.json', packet)['path']

    def generate(self):
        return data.curate(self.contract, self.registry, self.out, self.external, self.material_packet)

    def review(self):
        p = self.p
        diagnostic = guard.binding(self.out / 'curation_diagnostic.json')
        d = guard.load(diagnostic['path'])
        actual = d['sql_integration']['sources'][p.sid]
        p.ec = actual['expected_consumption']
        rs = copy.deepcopy(self.resolution_templates)
        for r in rs:
            r.update(original_gap_artifact=p.gb, exact_scope=p.scope, evidence=[diagnostic])
            r['resolution_id'] = q.digest({k:v for k,v in r.items() if k != 'resolution_id'})
        rb = p.write('final-resolutions.json', rs)
        context = copy.deepcopy(p.actual['scientific']['receipt_body']['context'])
        context.update(schema_version=2, selector=q.SELECTOR_V2, contract=p.cb, accepted_contract_review=p.ac,
            original_gap_artifact=p.gb, expected_scope=p.scope, expected_consumption=p.ec,
            source_row_decisions=self.inputs['source_row_decisions'], resolutions=rb)
        sr = p.wrapper('final-science.json', 'scientific', review_key=p.key, context=context)
        actual_review = dict(p.actual, scientific=sr)
        return p.write('final-review-config.json', dict(schema_version=1, diagnostic=diagnostic,
            sources={p.sid:dict(resolutions=rb, scientific_review=sr['receipt'], actual_review=actual_review)}))

    def finalize(self):
        review = self.review()
        return sql.finalize_curation(self.out / 'curation_diagnostic.json', review['path'])


class SQLWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f = Workflow(Path(self.temp.name))

    def test_actual_diagnostic_finalization_and_fresh_guard(self):
        f = self.f
        f.generate()
        self.assertFalse((f.out / 'manifest.json').exists())
        self.assertFalse((f.out / 'curation_manifest.json').exists())
        self.assertEqual(len(list(guard.csv_rows(f.out / 'curated.csv'))), 2)
        f.finalize()
        refs = guard.validate(f.contract, f.out / 'curation_manifest.json', f.out / 'curated.csv')
        self.assertTrue(any(Path(b['path']).name == 'final-review-config.json' for b in refs))
        self.assertEqual(guard.contract_acceptance_bindings(f.out / 'curation_manifest.json'), [f.p.ac])

    def test_two_source_row_terminals_and_outside_union(self):
        root = Path(self.temp.name) / 'terminals'; root.mkdir()
        f = Workflow(root, terminals=True)
        f.generate(); f.finalize()
        self.assertTrue(guard.validate(f.contract, f.out / 'curation_manifest.json'))
        rows = list(guard.json_rows(f.out / sql.ledger_names(f.p.sid)[1]))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['output_labels'] == [] for r in rows))

    def rebind_outputs(self):
        f = self.f
        path = f.out / 'curation_diagnostic.json'
        diagnostic = guard.load(path)
        outputs = diagnostic['bound_evidence']['outputs']
        for name in outputs: outputs[name] = guard.binding(f.out / name)
        for source, value in diagnostic['sql_integration']['sources'].items():
            cn, tn = sql.ledger_names(source)
            ec = value['expected_consumption']
            ec.update(ledger=outputs[cn], source_row_ledger=outputs[tn],
                output_artifacts=sorted([outputs[n] for n in sql.BASE_OUTPUTS], key=lambda b:(b['path'],b['sha256'])))
        data.dump(path, diagnostic)

    def test_rebound_consumption_attacks(self):
        f = self.f; f.generate()
        path = f.out / sql.ledger_names(f.p.sid)[0]
        original = path.read_bytes()
        attacks = [lambda rows: rows.pop(), lambda rows: rows.append(rows[-1]),
            lambda rows: rows[0].update(candidate_id='f'*64),
            lambda rows: next(r for r in rows if r['action']=='preserved').update(output_observation_ids=['invented']),
            lambda rows: next(r for r in rows if r['action']!='preserved').update(output_observation_ids=['excluded-output']),
            lambda rows: rows[0]['consumed_values'].update(numeric='999'),
            lambda rows: rows[0].update(output_artifacts=[f.p.proof])]
        for attack in attacks:
            with self.subTest(attack=attack):
                rows = [q.loads(line) for line in original.splitlines()]
                attack(rows); path.write_bytes(b''.join(q.canonical(r)+b'\n' for r in rows))
                self.rebind_outputs()
                with self.assertRaises(ValueError): f.finalize()
                self.assertFalse((f.out / 'manifest.json').exists())
        path.write_bytes(original)

    def test_full_replay_rejects_rebound_csv_changes(self):
        f = self.f; f.generate()
        for name, field, replacement in [('curated.csv','target_mg_kg','3'), ('curated.csv','reference','forged reference'),
                ('identity_ledger.csv','dtxsid','forged identity'), ('dispositions.csv','native_row','999'),
                ('conflict_ledger.csv','status','resolved')]:
            with self.subTest(role=name, field=field):
                path = f.out / name; original = path.read_bytes()
                with path.open(encoding='utf-8',newline='') as stream:
                    reader = csv.DictReader(stream); fields, rows = reader.fieldnames, list(reader)
                rows[0][field] = replacement
                with path.open('w',encoding='utf-8',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
                self.rebind_outputs()
                with self.assertRaises(ValueError): f.finalize()
                path.write_bytes(original); self.rebind_outputs()

    def test_source_sets_consumer_path_and_dependency_forgery(self):
        f = self.f; f.generate()
        path = f.out / 'curation_diagnostic.json'; original = path.read_bytes()
        attacks = [lambda d:d['sql_integration']['sources'].clear(),
            lambda d:d['sql_integration']['sources'].update(extra=next(iter(d['sql_integration']['sources'].values()))),
            lambda d:d['sql_integration']['sources'][f.p.sid]['expected_consumption'].update(consumer_code=f.p.proof),
            lambda d:d['bound_evidence']['code'].pop(),
            lambda d:d.update(output_rows=d['output_rows']+1)]
        for attack in attacks:
            with self.subTest(attack=attack):
                d=q.loads(original); attack(d); data.dump(path,d)
                # The envelope cannot restore a diagnostic with mismatched source sets.
                if set(d['sql_integration']['sources']) != {f.p.sid}:
                    review = f.p.write('attacked-review.json',dict(schema_version=1,diagnostic=guard.binding(path),sources={}))
                    with self.assertRaises(ValueError): sql.finalize_curation(path,review['path'])
                else:
                    with self.assertRaises(ValueError): f.finalize()
                path.write_bytes(original)

    def test_missing_or_changed_final_discovery_and_summary_reject(self):
        f = self.f; f.generate(); f.finalize()
        path=f.out/'curation_manifest.json';original=path.read_bytes()
        for attack in [lambda d:d.pop('sql_finalization'),lambda d:d['sql_finalization'].update(extra=True),
                       lambda d:d['sql_integration']['sources'].clear(),lambda d:d.update(output_rows=999),
                       lambda d:d['sql_obligation_closures'][f.p.sid].pop()]:
            with self.subTest(attack=attack):
                d=q.loads(original);attack(d);data.dump(path,d)
                with self.assertRaises(ValueError):guard.validate(f.contract,path)
                path.write_bytes(original)

    def test_receipt_obligation_and_review_source_attacks(self):
        f=self.f;f.generate()
        rb=f.review(); path=Path(rb['path']); original=path.read_bytes()
        for attack in [lambda d:d['sources'].clear(),lambda d:d['sources'][f.p.sid]['actual_review']['scientific']['receipt_body'].update(review_key='other')]:
            with self.subTest(attack=attack):
                d=q.loads(original);attack(d);data.dump(path,d)
                with self.assertRaises(ValueError):sql.finalize_curation(f.out/'curation_diagnostic.json',path)
                path.write_bytes(original)
        d=q.loads(original);rpath=Path(d['sources'][f.p.sid]['resolutions']['path']);rows=q.loads(rpath.read_bytes());rows.pop()
        data.dump(rpath,rows)
        source=d['sources'][f.p.sid]
        source['resolutions']=guard.binding(rpath)
        science=source['actual_review']['scientific']['receipt_body']
        science['context']['resolutions']=source['resolutions']
        receipt=f.p.write('final-science.json',science)
        source['scientific_review']=receipt
        source['actual_review']['scientific']['receipt']=receipt
        data.dump(path,d)
        with self.assertRaisesRegex(ValueError,'Missing/extra table resolutions'):
            sql.finalize_curation(f.out/'curation_diagnostic.json',path)

    def test_publication_failure_cleans_only_owned_final_markers(self):
        f=self.f;f.generate();review=f.review()
        original=Path.open
        for fail_name in ('curation_manifest.json','manifest.json'):
            def fail(path,mode='r',*args,**kwargs):
                if path.parent==f.out and path.name==fail_name and mode=='x':raise OSError('synthetic write failure')
                return original(path,mode,*args,**kwargs)
            with self.subTest(marker=fail_name),patch.object(Path,'open',fail):
                with self.assertRaises(OSError):sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
            self.assertFalse((f.out/'curation_manifest.json').exists());self.assertFalse((f.out/'manifest.json').exists())
            self.assertTrue((f.out/'curation_diagnostic.json').exists())
        original_dump=sql.json.dump
        for fail_name in ('curation_manifest.json','manifest.json'):
            def fail_write(value,stream,*args,**kwargs):
                if Path(stream.name).parent==f.out and Path(stream.name).name==fail_name:
                    stream.write('{partial');raise OSError('synthetic partial write failure')
                return original_dump(value,stream,*args,**kwargs)
            with self.subTest(partial_marker=fail_name),patch.object(sql.json,'dump',fail_write):
                with self.assertRaises(OSError):sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
            self.assertFalse((f.out/'curation_manifest.json').exists());self.assertFalse((f.out/'manifest.json').exists())
        sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
        before=(f.out/'curation_manifest.json').read_bytes()
        with self.assertRaises(ValueError):sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
        self.assertEqual((f.out/'curation_manifest.json').read_bytes(),before)

    def test_preconsumption_missing_decision_and_equal_count_native_substitution(self):
        f=self.f;p=f.p
        path=Path(f.inputs['candidate_decisions']['path']);rows=list(q.Files(lambda b:None).lines(f.inputs['candidate_decisions']));rows.pop()
        f.inputs['candidate_decisions']=p.write(path.name,rows,True);p.write('external.json',{p.sid:f.inputs})
        with self.assertRaisesRegex(ValueError,'Missing candidate'):f.generate()
        # Separate genuine package to isolate native equal-count substitution.
        root=Path(self.temp.name)/'membership';root.mkdir();f=Workflow(root);p=f.p
        rows=list(q.Files(lambda b:None).lines(p.scope['native_membership']));rows[0]['toxval_id']='invented'
        p.scope['native_membership']=p.write('source-scope-membership.jsonl',rows,True)
        f.inputs['scope']=p.write('scope.json',p.scope)
        audit=q.loads(Path(f.inputs['support_audit_manifest']['path']).read_bytes())
        audit['artifacts']=[p.scope['native_membership'] if Path(b['path']).name=='source-scope-membership.jsonl' else b for b in audit['artifacts']]
        f.inputs['support_audit_manifest']=p.write('audit-manifest.json',audit)
        receipt=q.loads(Path(f.inputs['support_review']['path']).read_bytes())
        receipt['context']['support_audit_manifest']=f.inputs['support_audit_manifest']
        f.inputs['support_review']=p.write('support-review.json',receipt)
        p.write('external.json',{p.sid:f.inputs})
        with self.assertRaisesRegex(ValueError,'Native primary membership'):f.generate()

    def test_populated_censored_candidate_remains_accounted_exclusion(self):
        root=Path(self.temp.name)/'inequality';root.mkdir();f=Workflow(root,variant='inequality')
        f.generate();f.finalize()
        consumed=list(guard.json_rows(f.out/sql.ledger_names(f.p.sid)[0]))
        self.assertTrue(any(r['clause']=='v4_explicit_inequality' and r['output_observation_ids']==[] for r in consumed))
        self.assertEqual(len(consumed),4)
        self.assertTrue(guard.validate(f.contract,f.out/'curation_manifest.json'))

    def test_terminal_consumption_membership_and_output_attacks(self):
        root=Path(self.temp.name)/'terminal-attacks';root.mkdir();f=Workflow(root,terminals=True)
        self.f=f;f.generate();path=f.out/sql.ledger_names(f.p.sid)[1];original=path.read_bytes()
        for attack in (lambda rows:rows.pop(),lambda rows:rows.append(rows[-1]),lambda rows:rows[0].update(output_labels=[1]),
                       lambda rows:rows[0].update(native=rows[1]['native'])):
            with self.subTest(attack=attack):
                rows=[q.loads(line) for line in original.splitlines()];attack(rows)
                path.write_bytes(b''.join(q.canonical(r)+b'\n' for r in rows));self.rebind_outputs()
                with self.assertRaises(ValueError):f.finalize()
                self.assertFalse((f.out/'manifest.json').exists())

    def test_otherwise_eligible_zero_without_clause_stays_blocked(self):
        root=Path(self.temp.name)/'zero';root.mkdir();f=Workflow(root,variant='zero')
        with self.assertRaisesRegex(ValueError,'Preserve decision lost'):f.generate()
        self.assertFalse((f.out/'manifest.json').exists())

    def test_unqualified_values_remain_reported_numeric_estimates(self):
        root=Path(self.temp.name)/'unqualified';root.mkdir();f=Workflow(root,variant='unqualified')
        f.generate();f.finalize()
        rows=list(guard.csv_rows(f.out/'curated.csv'))
        row=next(r for r in rows if r['source_record_id']=='3')
        self.assertEqual(row['target_kind'],'reported_numeric_estimate')
        self.assertEqual(row['normalized_relation'],'unknown')
        self.assertTrue(guard.validate(f.contract,f.out/'curation_manifest.json'))

    def test_selected_fields_and_chemical_join_precede_material_eligibility(self):
        root=Path(self.temp.name)/'chemical';root.mkdir();f=Workflow(root,variant='chemical',material_enabled=True)
        f.generate();f.finalize()
        row=next(r for r in guard.csv_rows(f.out/'curated.csv') if r['source_record_id']=='3')
        self.assertEqual((row['cas'],row['dtxsid'],row['route']),('64-17-5','D','oral'))
        self.assertEqual(float(row['target_mg_kg']),10)
        native=json.loads(row['native_record'])
        self.assertEqual((native['chemical_id'],native['exposure_route']),('C2','dermal'))
        self.assertEqual(guard.load(f.out/'curation_manifest.json')['material_integration']['classification_context']['scope']['eligible_count'],3)
        self.assertTrue(guard.validate(f.contract,f.out/'curation_manifest.json'))

    def test_sql_unknown_origin_does_not_inherit_ordinary_authorization(self):
        f=self.f;p=f.p
        rows=list(q.Files(lambda b:None).lines(f.inputs['candidate_decisions']))
        d=next(d for d in rows if d['disposition']=='preserve')
        d.update(disposition='scientific_quarantine',contract_clause='v4_origin_unknown_bounded_review')
        d['decision_id']=q.digest({k:v for k,v in d.items() if k!='decision_id'})
        f.inputs['candidate_decisions']=p.write('decisions.jsonl',rows,True);p.write('external.json',{p.sid:f.inputs})
        with self.assertRaises(ValueError):f.generate()
        self.assertFalse((f.out/'manifest.json').exists())

    def test_preparation_normal_entry_point(self):
        from scripts.test_qsar_prepare import MODULE as prepare
        root=Path(self.temp.name)/'preparation';root.mkdir();f=Workflow(root,preparation=True)
        f.generate();f.finalize()
        result=prepare.prepare_splits(f.out/'curated.csv',f.contract,root/'split',f.out/'curation_manifest.json')
        self.assertEqual(result['input_rows'],14)
        self.assertTrue(any(Path(b['path']).name=='final-review-config.json' for b in result['curation_evidence']))

    def test_runtime_acceptance_reuse_and_final_return_invalidation(self):
        from scripts import test_qsar_runtime as runtime_tests
        f=self.f;p=f.p
        runtime=runtime_tests.RuntimeTests();runtime.setUp();self.addCleanup(runtime.tearDown)
        runtime.rt.replace_contract(str(f.contract),'Synthetic SQL integration')
        dispatch=runtime.bind(runtime.prepare('contract'))
        candidate=Path(dispatch['owned_output_dir'])/'contract-candidate.json'
        data.dump(candidate,dict(contract=p.cb))
        runtime.rt.submit(dispatch['dispatch_id'],dict(outputs=[str(candidate)],summary='Synthetic contract',checks=['fixture']))
        review=runtime.accept('contract')
        acceptance=data.load(p.ac['path'])
        acceptance.update(candidate=guard.binding(candidate),producer_execution_id=dispatch['native_execution_id'],
            reviewer_execution_id=review['reviewer_execution_id'],review_evidence=guard.binding(review['evidence_bindings'][0]['path']))
        p.ac=p.write('acceptance.json',acceptance)
        p.actual['accepted_contract']=dict(receipt=p.ac,receipt_body=acceptance)
        f.inputs['accepted_contract_review']=p.ac;p.write('external.json',{p.sid:f.inputs})
        dispatch=runtime.bind(runtime.prepare('collection'))
        f.collection=Path(dispatch['owned_output_dir'])/'collection'
        collected=data.collect(f.root/'raw',f.collection)
        f.registry=f.collection/'source_registry.json';p.gb=guard.binding(f.collection/'collection_gaps.json')
        runtime.rt.submit(dispatch['dispatch_id'],collected);runtime.accept('collection')
        dispatch=runtime.bind(runtime.prepare('curation'))
        f.out=Path(dispatch['owned_output_dir'])/'curated'
        f.generate();produced=f.finalize()
        runtime.rt.submit(dispatch['dispatch_id'],produced);runtime.accept('curation')
        refs=runtime.rt.status()['stages']['curation']['review']['curation_evidence_bindings']
        self.assertIn(str(p.root/'final-review-config.json'),{b['path'] for b in refs})
        with patch.object(sql.SQLCurationInputs,'__enter__',side_effect=AssertionError('Cached reuse must not replay')):
            dispatch=runtime.bind(runtime.prepare('preprocessing'))
        output=Path(dispatch['owned_output_dir'])/'unused.json';data.dump(output,dict(synthetic=True))
        proof=Path(p.proof['path']);proof.write_bytes(proof.read_bytes()+b' ')
        with self.assertRaises(runtime_tests.GateError):
            runtime.rt.submit(dispatch['dispatch_id'],dict(outputs=[str(output)],summary='stale',checks=['fixture']))
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'],'stale')

    def test_final_freshness_deduplicates_normalized_dependency_paths(self):
        f=self.f;f.generate();review=f.review()
        probe=f.p.write('late-freshness-probe.json',dict(synthetic_only=True))
        alias=dict(probe,path=str(Path(probe['path']).parent)+os.sep+'.'+os.sep+Path(probe['path']).name)
        original_summary,original_checked=guard._validate_summary,guard.checked
        checks=[]
        def dependencies(*args,**kwargs):
            return [*original_summary(*args,**kwargs),probe,alias,probe]
        def checked(descriptor):
            if Path(descriptor['path']).resolve()==Path(probe['path']).resolve():checks.append(descriptor)
            return original_checked(descriptor)
        with patch.object(guard,'_validate_summary',dependencies),patch.object(guard,'checked',checked):
            result=sql.validate_sql_finalization(f.out/'curation_diagnostic.json',review['path'])
        self.assertEqual(len(checks),1)
        self.assertIn(Path(probe['path']).resolve(),{Path(b['path']).resolve() for b in result['references']})
        self.assertTrue(all(b in result['references'] for b in guard.load(f.out/'curation_diagnostic.json')['bound_evidence']['outputs'].values()))

    def test_final_freshness_rejects_conflicting_sha_for_path_alias(self):
        f=self.f;f.generate();review=f.review()
        probe=f.p.write('late-conflicting-probe.json',dict(synthetic_only=True))
        alias=dict(path=str(Path(probe['path']).parent)+os.sep+'.'+os.sep+Path(probe['path']).name,sha256='0'*64)
        original=guard._validate_summary
        def dependencies(*args,**kwargs):return [*original(*args,**kwargs),probe,alias]
        with patch.object(guard,'_validate_summary',dependencies):
            with self.assertRaisesRegex(ValueError,'Conflicting finalization dependency SHA256'):
                sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
        self.assertFalse((f.out/'curation_manifest.json').exists())

    def test_final_freshness_checks_every_unique_late_dependency(self):
        f=self.f;f.generate();review=f.review()
        probe=f.p.write('late-stale-probe.json',dict(synthetic_only=True))
        original=guard._validate_summary
        def dependencies(*args,**kwargs):
            refs=original(*args,**kwargs)
            Path(probe['path']).write_bytes(b'changed after earlier validation')
            return [*refs,probe]
        with patch.object(guard,'_validate_summary',dependencies):
            with self.assertRaisesRegex(ValueError,'stale file'):
                sql.finalize_curation(f.out/'curation_diagnostic.json',review['path'])
        self.assertFalse((f.out/'curation_manifest.json').exists())

    def test_material_projection_and_full_guard(self):
        root = Path(self.temp.name) / 'material'; root.mkdir()
        f = Workflow(root, material_enabled=True)
        f.generate(); f.finalize()
        self.assertTrue(guard.validate(f.contract, f.out / 'curation_manifest.json'))
        summary = guard.load(f.out / 'curation_manifest.json')
        scope = summary['material_integration']['classification_context']['scope']
        self.assertEqual(scope['observation_count'], 5)
        self.assertEqual(scope['eligible_count'], 2)
        self.assertEqual(len(scope['physical_slots_sha256']), 64)


if __name__ == '__main__': unittest.main()


class OrganicSQLWorkflowTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        return Workflow(Path(temp.name), material_enabled=True, organic_graph='CC[NH3+].O=C([O-])C(=O)O')

    def test_actual_organic_diagnostic_review_finalization_and_guard(self):
        f = self.fixture(); f.generate()
        self.assertFalse((f.out / 'curation_manifest.json').exists())
        rows = list(guard.csv_rows(f.out / 'curated.csv'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['material_schema_version'] == '3' for row in rows))
        self.assertTrue(all(row['parent_smiles'] == 'CC[NH3+]' for row in rows))
        f.finalize()
        refs = guard.validate(f.contract, f.out / 'curation_manifest.json', f.out / 'curated.csv')
        self.assertTrue(any(Path(b['path']).name == 'principal-source.json' for b in refs))

    def test_supplemental_drift_after_diagnostic_rejects_finalization(self):
        f = self.fixture(); f.generate()
        path = f.root / 'packet' / 'principal-source.json'
        stat = path.stat(); path.write_bytes(path.read_bytes().replace(b'A', b'B'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaises(ValueError): f.finalize()
        self.assertFalse((f.out / 'curation_manifest.json').exists())

    def test_rehashed_projection_and_target_attacks(self):
        f = self.fixture(); f.generate()
        helper = SQLWorkflowTests(); helper.f = f
        path = f.out / 'curated.csv'; original = path.read_bytes()
        for field, value in [('target_mg_kg','999'), ('original_mw','1'), ('parent_mw','1'),
                ('group_id','f'*64), ('material_group_id','f'*64), ('principal_component','{}')]:
            with self.subTest(field=field):
                rows = list(guard.csv_rows(path)); rows[0][field] = value
                data.write_csv(path, rows, list(rows[0]))
                helper.rebind_outputs()
                with self.assertRaises(ValueError): f.finalize()
                self.assertFalse((f.out / 'curation_manifest.json').exists())
                path.write_bytes(original); helper.rebind_outputs()

class OrganicSQLFreshProcessTests(unittest.TestCase):
    fixture=OrganicSQLWorkflowTests.fixture

    def test_supplemental_dependency_rejects_fresh_process_reuse(self):
        import subprocess,sys
        f=self.fixture(); f.generate(); f.finalize()
        script="import sys;sys.path.insert(0,sys.argv[1]);import qsar_curation_evidence as g;g.validate(sys.argv[2],sys.argv[3],sys.argv[4])"
        command=[sys.executable,'-B','-c',script,str(Path(guard.__file__).parent),str(f.contract),str(f.out/'curation_manifest.json'),str(f.out/'curated.csv')]
        options=dict(capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.assertEqual(subprocess.run(command,**options).returncode,0)
        path=f.root/'packet/principal-source.json'; stat=path.stat()
        path.write_bytes(path.read_bytes().replace(b'A',b'B')); os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        rejected=subprocess.run(command,**options)
        self.assertNotEqual(rejected.returncode,0)
        self.assertIn('EvidenceError',rejected.stderr)


if __name__ == '__main__':
    unittest.main()
