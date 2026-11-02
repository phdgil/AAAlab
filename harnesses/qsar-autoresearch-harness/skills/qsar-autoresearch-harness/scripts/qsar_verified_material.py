"""Isolated reviewed-material mechanics; no runtime activation or scientific approval.

The caller supplies an actual-review descriptor and a bound native replay provider.
Those are leader/source-adapter trust boundaries, not cryptographic authentication.
Neither parseable graphs nor populated certificates establish material truth.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors


class MaterialError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_binding(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return {'path': str(path), 'sha256': h.hexdigest()}


def _require(condition, message):
    if not condition:
        raise MaterialError(message)


def _file(binding):
    _require(isinstance(binding, dict) and set(binding) == {'path', 'sha256'}, 'Invalid file binding')
    _require(file_binding(binding['path']) == binding, 'Stale or tampered file binding')
    return Path(binding['path'])


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _evidence(value):
    return isinstance(value, list) and bool(value) and all(_text(x) for x in value)


def observation_id(observation):
    """Full native/candidate/body identity, including raw projection declarations."""
    for field in ('source_id', 'source_sha256', 'candidate_key'):
        _require(_text(observation.get(field)), 'Missing observation ' + field)
    _require(isinstance(observation.get('native_locator'), dict) and observation['native_locator'],
             'Missing native locator')
    _require(isinstance(observation.get('body'), dict), 'Missing exact native body')
    return digest(observation)


def _slot(observation):
    # Deliberately excludes mutable source hash/body: changed affected evidence cannot fall back.
    return digest([observation['source_id'], observation['native_locator']])


def graph_facts(smiles):
    """Full graph facts; isotope/explicit/implicit hydrogen retained in atom inventory."""
    _require(_text(smiles), 'Missing fixed-composition graph')
    mol = Chem.MolFromSmiles(smiles)
    _require(mol is not None and mol.GetNumAtoms() > 0, 'Invalid material graph')
    _require(all(a.GetAtomicNum() > 0 for a in mol.GetAtoms()), 'Dummy/variable atoms unsupported')
    # Atom maps are provenance labels, not material identity; refuse ambiguous grouping input.
    _require(all(a.GetAtomMapNum() == 0 for a in mol.GetAtoms()), 'Atom maps require reviewed removal')
    expanded = Chem.AddHs(mol)
    atoms = Counter(f'{a.GetSymbol()}:{a.GetIsotope()}' for a in expanded.GetAtoms())
    charges = Counter(f'{a.GetSymbol()}:{a.GetIsotope()}:{a.GetFormalCharge()}' for a in expanded.GetAtoms())
    components = Counter(Chem.MolToSmiles(f, canonical=True, isomericSmiles=True)
                         for f in Chem.GetMolFrags(mol, asMols=True))
    mw = Descriptors.MolWt(mol)
    _require(math.isfinite(mw) and mw > 0, 'Nonpositive/nonfinite formula-unit mass')
    graph = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    group_graph = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)
    return {'canonical_smiles': graph, 'group_smiles': group_graph,
            'formula': rdMolDescriptors.CalcMolFormula(mol, separateIsotopes=True),
            'atoms': dict(sorted(atoms.items())), 'atom_formal_charges': dict(sorted(charges.items())),
            'components': dict(sorted(components.items())),
            'formal_charge': sum(a.GetFormalCharge() for a in mol.GetAtoms()), 'mw': mw}


def _rows(binding):
    return [json.loads(line) for line in _file(binding).read_text(encoding='utf-8').splitlines() if line.strip()]


@dataclass(frozen=True)
class ReviewedMaterials:
    """JSON snapshot prevents mutation of caller dictionaries after binding."""
    payload: bytes


def load_reviewed_materials(binding, contract, registry, environment, *, integration=None):
    """Load exact reviewed decisions. Missing decisions or replay fail closed.

    registry.files contains source_id/path/sha256. replay_native(source, locator)
    must reconstruct one exact native body through the reviewed source adapter.
    replay_provider = {implementation: file_binding, callable: __qualname__}; its
    implementation and every source binding are included in the review context.
    """
    if integration is not None:
        from qsar_material_curation import load_materials_v2
        return load_materials_v2(binding, contract, registry, environment, integration=integration)
    _require(isinstance(contract, dict) and _text(contract.get('contract_version')),
             'Explicit full material contract required')
    science = contract.get('scientific_policy')
    _require(isinstance(science, dict), 'Explicit scientific policy required')
    policy = science.get('verified_tested_material_v4')
    _require(isinstance(policy, dict), 'Explicit material policy required')
    execution = policy.get('execution')
    supported = {
        'schema_version': 1,
        'adapter': 'reviewed_material_full_graph_v1',
        'rdkit_version': rdBase.rdkitVersion,
        'mass_algorithm': 'rdkit_descriptors_molwt',
        'predictor_policy': 'complete_verified_material_graph',
        'grouping_algorithm': 'sha256_utf8_rdkit_canonical_nonisomeric_smiles',
    }
    _require(isinstance(execution, dict) and set(execution) == set(supported),
             'Exact material execution policy required')
    for field, value in supported.items():
        _require(type(execution[field]) is type(value) and execution[field] == value,
                 'Unsupported material execution policy: ' + field)
    _require(isinstance(environment, dict) and
             environment.get('rdkit_version') == execution['rdkit_version'], 'Pinned RDKit mismatch')
    replay = registry.get('replay_native')
    provider = registry.get('replay_provider')
    _require(callable(replay) and isinstance(provider, dict), 'Bound native replay provider required')
    provider_file = _file(provider['implementation'])
    _require(Path(inspect.getsourcefile(replay)).resolve() == provider_file and
             provider.get('callable') == replay.__qualname__, 'Native replay provider mismatch')
    sources = {}
    for entry in registry['files']:
        sid = entry['source_id']
        _require(_text(sid) and sid not in sources, 'Duplicate or missing source identity')
        _file({k: entry[k] for k in ('path', 'sha256')})
        sources[sid] = entry
    context = {'contract_sha256': digest(contract), 'implementation_sha256': file_binding(__file__)['sha256'],
               'environment': environment, 'inventory': binding['inventory'], 'decisions': binding['decisions'],
               'replay_provider': provider, 'sources': registry['files']}
    items, decisions = _rows(binding['inventory']), _rows(binding['decisions'])
    receipt = json.loads(_file(binding['receipt']).read_text(encoding='utf-8'))
    _require(binding.get('actual_review') == dict(receipt, receipt=binding['receipt']),
             'Missing or mismatched actual independent review descriptor')
    _require(receipt.get('schema_version') == 1 and receipt.get('result') == 'PASS', 'Review not accepted')
    _require(receipt.get('context') == context, 'Review context mismatch')
    _require(_text(receipt.get('producer_execution_id')) and _text(receipt.get('reviewer_execution_id')) and
             receipt['producer_execution_id'] != receipt['reviewer_execution_id'], 'Independent reviewer required')
    _require(_file(receipt['native_review']).stat().st_size > 0, 'Missing native review return')
    _require(bool(receipt.get('synthetic', False)) == bool(contract.get('synthetic', False)),
             'Synthetic receipt cannot certify real contract')
    inventory, slots, candidates = {}, set(), set()
    for item in items:
        obs = item['observation']
        key = observation_id(obs)
        slot = _slot(obs)
        candidate = (slot, obs['candidate_key'])
        _require(key not in inventory and candidate not in candidates, 'Duplicate native candidate membership')
        _require(obs['source_id'] in sources, 'Unregistered observation source')
        source = sources[obs['source_id']]
        _require(source['sha256'] == obs['source_sha256'], 'Observation source hash mismatch')
        _require(canonical(replay(source, obs['native_locator'])) == canonical(obs['body']),
                 'Native body membership mismatch')
        _require(_text(obs.get('raw_smiles_field')) and obs['raw_smiles_field'] in obs['body'] and
                 obs.get('raw_smiles') == obs['body'][obs['raw_smiles_field']], 'Raw structure not native-bound')
        for field in ('support_candidates', 'blocked_support'):
            _require(isinstance(item.get(field), list), 'Complete support inventory required')
            _require(len({digest(x) for x in item[field]}) == len(item[field]), 'Duplicate support item')
        inventory[key] = item
        slots.add(slot)
        candidates.add(candidate)
    _require(receipt.get('scope_ids') == sorted(inventory), 'Exact review scope mismatch')
    indexed = {}
    for decision in decisions:
        key = decision['observation_id']
        _require(key in inventory and key not in indexed, 'Duplicate or wrong-scope decision')
        _require(decision.get('status') in ('verified', 'reviewed_hold', 'unresolved'), 'Unknown review status')
        _require(decision.get('material_class') in ('inorganic', 'organic', 'unresolved'), 'Unknown reviewed class')
        _require(_evidence(decision.get('review_evidence')) and _evidence(decision.get('reopening_conditions')),
                 'Source review evidence and reopening conditions required')
        _require(isinstance(decision.get('limitations'), list) and
                 isinstance(decision.get('critical_conflicts'), list), 'Explicit limitations/conflicts required')
        supports = inventory[key]['blocked_support']
        resolutions = decision.get('support_resolutions')
        _require(isinstance(resolutions, list), 'Explicit support_resolutions array required')
        _require(len(resolutions) == len(supports) and
                 sorted(r['support_sha256'] for r in resolutions) == sorted(digest(x) for x in supports),
                 'Every support conflict needs an exact disposition')
        for resolution in resolutions:
            _require(resolution.get('disposition') in ('resolved_material_identity', 'unresolved') and
                     _evidence(resolution.get('evidence')), 'Unsupported support conflict disposition')
            if resolution['disposition'] == 'resolved_material_identity':
                support = next(x for x in supports if digest(x) == resolution['support_sha256'])
                _require(support.get('reason') == 'material_identity_conflict',
                         'Material review cannot waive ingestion or nonmaterial conflicts')
        if decision['status'] == 'verified':
            _require(decision['material_class'] == 'inorganic', 'Only reviewed inorganic path implemented')
            _require(decision.get('fixed_composition') is True and _text(decision.get('hydration_interpretation')),
                     'Fixed formula unit and hydration review required')
            facts = graph_facts(decision['verified_smiles'])
            _require(canonical(decision.get('formula_unit')) == canonical(facts), 'Formula-unit graph facts mismatch')
            source_formula = inventory[key]['observation']['body'].get('source_formula')
            if source_formula is not None and source_formula != '':
                interpretation = decision.get('source_formula_interpretation')
                _require(isinstance(interpretation, dict) and interpretation.get('source_formula') == source_formula and
                         interpretation.get('formula') == facts['formula'] and _evidence(interpretation.get('evidence')),
                         'Source formula requires graph-consistent reviewed interpretation')
            raw = inventory[key]['observation']['raw_smiles']
            correction = decision.get('correction')
            try:
                unchanged = graph_facts(raw)['canonical_smiles'] == facts['canonical_smiles']
            except MaterialError:
                unchanged = False
            if not unchanged or correction is not None:
                _require(isinstance(correction, dict) and correction.get('observation_id') == key and
                         correction.get('raw_smiles') == raw and correction.get('verified_smiles') == decision['verified_smiles'] and
                         _evidence(correction.get('evidence')), 'Exact source-bound material correction required')
        indexed[key] = decision
    _require(set(indexed) == set(inventory), 'Missing material decision')
    files = [binding[x] for x in ('inventory', 'decisions', 'receipt')] + [receipt['native_review'], provider['implementation']]
    files += [{k: entry[k] for k in ('path', 'sha256')} for entry in registry['files']]
    return ReviewedMaterials(canonical({'inventory': inventory, 'decisions': indexed, 'slots': sorted(slots),
                                       'context': context, 'files': files}))


def _current(index):
    _require(isinstance(index, ReviewedMaterials), 'Reviewed index required')
    data = json.loads(index.payload)
    _require(data['context']['environment']['rdkit_version'] == rdBase.rdkitVersion and
             data['context']['implementation_sha256'] == file_binding(__file__)['sha256'], 'Stale implementation/environment')
    for binding in data['files']:
        _file(binding)
    return data


def _resolve(observation, support_candidates, blocked_support, data):
    key = observation_id(observation)
    pending = {'status': 'PendingMaterialReview', 'observation_id': key}
    if key not in data['inventory']:
        return pending if _slot(observation) in data['slots'] else {'status': 'NotApplicable'}
    item, decision = data['inventory'][key], data['decisions'][key]
    if (canonical(support_candidates) != canonical(item['support_candidates']) or
            canonical(blocked_support) != canonical(item['blocked_support'])):
        return pending
    if decision['status'] == 'unresolved':
        return pending
    reasons = list(decision['critical_conflicts'])
    if any(r['disposition'] == 'unresolved' for r in decision['support_resolutions']):
        reasons.append('unresolved_support')
    if decision.get('dose_basis') not in ('reported_material_mass', 'explicit_formula_unit_molar'):
        reasons.append('unsupported_dose_basis')
    if decision['status'] == 'reviewed_hold' or reasons:
        return {'status': 'ReviewedHold', 'observation_id': key, 'reasons': reasons,
                'review_evidence': decision['review_evidence']}
    facts = decision['formula_unit']
    try:
        raw_facts = graph_facts(observation['raw_smiles'])
    except MaterialError:
        raw_facts = None
    return {'status': 'VerifiedMaterial', 'observation_id': key,
            'material_class': decision['material_class'], 'raw_structure': observation['raw_smiles'],
            'raw_mw': raw_facts['mw'] if raw_facts else None,
            'source_formula': observation['body'].get('source_formula'),
            'verified_material_graph': facts['canonical_smiles'], 'formula_unit': facts,
            'formula_unit_mw': facts['mw'], 'predictor_graph': facts['canonical_smiles'],
            'material_group_id': hashlib.sha256(facts['group_smiles'].encode('utf-8')).hexdigest(),
            'original_structure': facts['canonical_smiles'], 'original_mw': facts['mw'],
            'parent_smiles': facts['canonical_smiles'], 'parent_mw': facts['mw'],
            'connectivity_id': hashlib.sha256(facts['group_smiles'].encode('utf-8')).hexdigest(),
            'normalization': 'verified_material_full_graph', 'dose_basis': decision['dose_basis'],
            'material_binding_sha256': digest(data['context']), 'limitations': decision['limitations']}


def resolve_material(observation, support_candidates, blocked_support, index):
    """Resolve only material identity; never grants route/endpoint/numeric eligibility."""
    data = _current(index)
    if data.get('schema_version') == 2:
        from qsar_material_curation import resolve_v2
        return resolve_v2(observation, support_candidates, blocked_support, data)
    return _resolve(observation, support_candidates, blocked_support, data)


def validate_material_projection(curated_row, index, environment):
    """Recompute authoritative material columns, allowing independent task columns."""
    data = _current(index)
    _require(environment == data['context']['environment'], 'Projection environment mismatch')
    if data.get('schema_version') == 2:
        from qsar_material_curation import validate_projection_v2
        return validate_projection_v2(curated_row, data)
    key = curated_row.get('observation_id')
    _require(key in data['inventory'], 'Projection outside reviewed scope')
    item = data['inventory'][key]
    expected = _resolve(item['observation'], item['support_candidates'], item['blocked_support'], data)
    _require(expected['status'] == 'VerifiedMaterial', 'Held material cannot have accepted projection')
    _require(all(k in curated_row and canonical(curated_row[k]) == canonical(v) for k, v in expected.items()),
             'Raw/verified/predictor/group material projection mismatch')
    target_fields = ('target_mg_kg', 'target_mol_kg', 'target_pLD50')
    if any(field in curated_row for field in target_fields):
        required = target_fields if expected['dose_basis'] == 'reported_material_mass' else target_fields[1:]
        _require(all(field in curated_row for field in required), 'Incomplete target projection')
        for field in required:
            value = curated_row[field]
            _require(type(value) in (int, float) and math.isfinite(value), 'Nonfinite/non-numeric target')
        molar = curated_row['target_mol_kg']
        _require(molar > 0, 'Nonpositive molar target')
        if expected['dose_basis'] == 'reported_material_mass':
            mg = curated_row['target_mg_kg']
            _require(mg > 0 and math.isclose(molar, mg / (1000 * expected['formula_unit_mw']), rel_tol=1e-12),
                     'Formula-unit mass target mismatch')
        _require(math.isclose(curated_row['target_pLD50'], -math.log10(molar), rel_tol=1e-12, abs_tol=1e-12),
                 'Logarithmic target mismatch')
    return True


def material_batch(binding, contract, registry, environment, *, integration=None):
    """Use one explicitly closed batch lifetime for reviewed material lookups."""
    from qsar_material_curation import MaterialBatch
    return MaterialBatch(binding, contract, registry, environment, integration=integration)
