"""Strict coverage guards on small synthetic files and isolated runtime state."""
import csv
import gzip
import json
from pathlib import Path
import tempfile
import sys
import zipfile
import unittest
from unittest.mock import patch

from scripts.test_qsar_data import data
from scripts.test_qsar_prepare import MODULE as prepare
from scripts import test_qsar_runtime as runtime_tests
from scripts import test_qsar_adjudication as adjudication_tests
from scripts import test_qsar_native_sources as native_tests

GateError = runtime_tests.GateError

evidence = data.curation_evidence_module()


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strict-coverage-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.source = self.raw / "oecd.csv"
        rows = [dict(source_record_id=str(i), casrn="", species="rat", route="oral", endpoint_type="LD50",
                     numeric_value=100 + i, numeric_unit="mg/kg", smiles="C" * (i + 1), qualifier="=") for i in range(12)]
        with self.source.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self.contract = self.root / "contract.json"
        self.config = {"version": "strict-fixture", "execution_policy": {
            "allow_training_with_unresolved_required_source_coverage": False}, "data_curation": {
            "chemistry_policy": "single_component", "relation_policy": "exact_only", "replicate_policy": "preserve_flag",
            "tasks": [{"id": "rat", "species": "rat", "route": "oral", "endpoint": "LD50"}],
            "sources": [{"source_id": "oecd.csv", "adapter": "oecd", "columns": {"relation": "qualifier"}}]},
            "preparation": {"target_column": "target_pLD50", "group_column": "group_id", "seed": 9,
                "terminal_fraction": .25, "outer_folds": 3, "inner_folds": 2,
                "intended_generalization": "synthetic", "terminal_custodian": "fixture",
                "prior_exposure": {"status": "none_known", "evidence": [], "description": "synthetic"},
                "unfitted_transforms": {"scaling": "fold-local"}}}
        self.collection = self.root / "collection"
        self.out = self.root / "curation"

    def generate(self):
        data.dump(self.contract, self.config)
        self.collected = data.collect(self.raw, self.collection)
        self.produced = data.curate(self.contract, self.collection / "source_registry.json", self.out)
        self.manifest = self.out / "curation_manifest.json"
        return data.load(self.manifest)

    def check(self):
        return evidence.validate(self.contract, self.manifest, self.out / "curated.csv")

    def test_generated_evidence_and_strict_preparation(self):
        self.generate()
        self.assertTrue(self.check())
        result = prepare.prepare_splits(self.out / "curated.csv", self.contract, self.root / "split", self.manifest)
        self.assertEqual(result["input_rows"], 12)
        self.assertTrue(result["curation_evidence"])

    def test_summary_forgery_missing_and_duplicate_coverage(self):
        original = self.generate()
        changes = [{"status": "blocked"}, {"input_rows": 11}, {"output_rows": 13},
                   {"source_counts": {}}, {"source_counts": {"oecd.csv": 12, "unknown": 0}},
                   {"blockers": [{"source_id": "oecd.csv", "required": False}]},
                   {"unresolved_policy_cases": 1}, {"narrative_accounting": {"unknown": {}}}]
        for change in changes:
            with self.subTest(change=change):
                data.dump(self.manifest, {**original, **change})
                with self.assertRaises(evidence.EvidenceError):
                    self.check()
        self.manifest.write_text('{"status":"produced","status":"blocked"}', encoding="utf-8")
        with self.assertRaisesRegex(evidence.EvidenceError, "duplicate JSON"):
            self.check()

    def test_mutated_bound_files_fail_closed(self):
        manifest = self.generate()
        paths = [self.source, self.contract, self.collection / "source_registry.json",
                 self.collection / "source_records.jsonl", self.out / "dispositions.csv",
                 self.out / "identity_ledger.csv", self.out / "curated.csv"]
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaises(evidence.EvidenceError):
                    self.check()
                path.write_bytes(original)
        manifest["bound_evidence"]["code"][0]["sha256"] = "0" * 64
        data.dump(self.manifest, manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "code"):
            self.check()

    def test_rehashed_disposition_loss_is_not_a_valid_summary(self):
        manifest = self.generate()
        path = self.out / "dispositions.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows[:-1])
        manifest["bound_evidence"]["outputs"][path.name] = evidence.binding(path)
        data.dump(self.manifest, manifest)
        with self.assertRaises(evidence.EvidenceError):
            self.check()

    def test_strict_wrong_or_blocked_evidence_precedes_output(self):
        manifest = self.generate()
        other = self.root / "other.csv"
        other.write_bytes((self.out / "curated.csv").read_bytes())
        with self.assertRaises(prepare.PreparationError):
            prepare.prepare_splits(other, self.contract, self.root / "split", self.manifest)
        manifest["status"] = "blocked"
        data.dump(self.manifest, manifest)
        with self.assertRaises(prepare.PreparationError):
            prepare.prepare_splits(self.out / "curated.csv", self.contract, self.root / "split", self.manifest)
        self.assertFalse((self.root / "split").exists())

    def test_evidence_mutation_during_split_rejects_final_manifest(self):
        self.generate()
        original = prepare.read_rows
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            with self.source.open("a") as stream:
                stream.write("\n")
            return result
        with patch.object(prepare, "read_rows", side_effect=mutate):
            with self.assertRaisesRegex(prepare.PreparationError, "changed during"):
                prepare.prepare_splits(self.out / "curated.csv", self.contract, self.root / "split", self.manifest)
        self.assertFalse((self.root / "split/preprocessing_manifest.json").exists())

    def test_exact_damaged_comptox_exception_only(self):
        source_id = "toxvaldb/COMPTOX_Public.zip"
        path = self.raw / source_id
        path.parent.mkdir()
        path.write_bytes(b"deliberately truncated synthetic archive")
        self.config["data_curation"]["sources"].append({"source_id": source_id,
            "adapter": "unresolved_truncated_archive", "required": False,
            "scope_disposition": "operationally_unreadable; user authorized proceeding with all readable sources, not scientific irrelevance"})
        self.config["execution_policy"]["allow_disclosed_user_accepted_unreadable_bundle_gap"] = True
        self.config["user_decisions"] = {"unreadable_comptox_bundle":
            "Use all readable sources and disclose truncated COMPTOX_Public.zip coverage gap; user explicitly confirmed."}
        manifest = self.generate()
        self.assertTrue(self.check())
        manifest["blockers"].append({"source_id": "hsdb", "required": False})
        data.dump(self.manifest, manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "blocker"):
            self.check()

    def test_global_exception_flag_cannot_waive_required_gap(self):
        self.config["execution_policy"]["allow_disclosed_user_accepted_unreadable_bundle_gap"] = True
        manifest = self.generate()
        gap = [{"source_id": "oecd.csv", "reason": "unresolved extraction"}]
        path = self.collection / "collection_gaps.json"
        data.dump(path, gap)
        upstream_path = self.collection / "collection_manifest.json"
        upstream = data.load(upstream_path)
        upstream.update(gaps=gap, status="limited")
        data.dump(upstream_path, upstream)
        manifest["bound_evidence"]["collection"]["gaps"] = evidence.binding(path)
        manifest["bound_evidence"]["collection"]["manifest"] = evidence.binding(upstream_path)
        data.dump(self.manifest, manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "collection gap"):
            self.check()

    def test_runtime_acceptance_reuse_and_revise_remain_available(self):
        fixture = self.runtime_curated()
        with self.source.open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(GateError, "Independent PASS required for curation"):
            fixture.prepare("preprocessing")
        self.assertEqual(fixture.rt.status()['stages']['curation']['status'], 'stale')

    def runtime_curated(self, mutate=None):
        fixture = runtime_tests.RuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.contract = fixture.contract
        data.dump(self.contract, self.config)
        fixture.rt.replace_contract(self.contract, "strict synthetic evidence")
        fixture.through("contract")
        dispatch = fixture.bind(fixture.prepare("collection"))
        self.collection = Path(dispatch["owned_output_dir"]) / "collection"
        collected = data.collect(self.raw, self.collection)
        fixture.rt.submit(dispatch["dispatch_id"], collected)
        fixture.accept("collection")
        dispatch = fixture.bind(fixture.prepare("curation"))
        self.out = Path(dispatch["owned_output_dir"]) / "curation"
        produced = data.curate(self.contract, self.collection / "source_registry.json", self.out)
        self.manifest = self.out / "curation_manifest.json"
        if mutate:
            mutate()
            produced["coverage"] = data.load(self.manifest)
        fixture.rt.submit(dispatch["dispatch_id"], produced)
        if not mutate:
            fixture.accept("curation")
        return fixture

    def rewrite_csv(self, name, mutate):
        path = self.out / name
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        mutate(rows)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        self.rebind_output(name)

    def rebind_output(self, name):
        manifest = data.load(self.manifest)
        manifest["bound_evidence"]["outputs"][name] = evidence.binding(self.out / name)
        data.dump(self.manifest, manifest)

    def forge_locator(self):
        self.rewrite_csv("dispositions.csv", lambda rows: rows[0].update(native_row="999999"))

    def test_review_probe_nonexistent_native_locator_rejected_at_all_gates(self):
        fixture = self.runtime_curated(self.forge_locator)
        with self.assertRaises(evidence.EvidenceError):
            self.check()
        with self.assertRaises(prepare.PreparationError):
            prepare.prepare_splits(self.out / "curated.csv", self.contract, self.root / "split", self.manifest)
        self.assertFalse((self.root / "split").exists())
        with self.assertRaises(GateError):
            fixture.accept("curation")

    def test_review_probe_malformed_required_ledger_headers(self):
        self.generate()
        for name in ("identity_support_ledger.csv", "conflict_ledger.csv"):
            with self.subTest(name=name):
                path = self.out / name
                original = path.read_bytes()
                path.write_text("not,a,valid,ledger\n", encoding="utf-8")
                self.rebind_output(name)
                with self.assertRaises(evidence.EvidenceError):
                    self.check()
                path.write_bytes(original)
                self.rebind_output(name)

    def test_conflict_membership_status_and_missing_groups(self):
        with self.source.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[1]["smiles"] = rows[0]["smiles"]
        data.write_csv(self.source, rows, list(rows[0]))
        self.generate()
        self.assertTrue(self.check())
        path = self.out / "conflict_ledger.csv"
        original = path.read_bytes()
        mutations = [lambda rows: rows[0].update(row_ids=json.dumps(["invented", "also-invented"])),
                     lambda rows: rows[0].update(status="resolved"),
                     lambda rows: rows[0].update(group_id="invented"), lambda rows: rows.clear()]
        for mutate in mutations:
            self.rewrite_csv(path.name, mutate)
            with self.assertRaises(evidence.EvidenceError):
                self.check()
            path.write_bytes(original)
            self.rebind_output(path.name)

    def test_support_document_and_orphan_membership(self):
        path = self.raw / "chemid.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("chemid.xml", '<file><Chemical id="X1"><NumberList><CASRegistryNumber>64-17-5</CASRegistryNumber></NumberList></Chemical></file>')
            archive.writestr("chemid-SMILES.txt", "SUPERID\tSMILES\nX1\tCCO\nX2\tCCN\n")
            archive.writestr("chemid-InChI.txt", "SUPERID\tInChI\nX1\tInChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3\n")
        self.config["data_curation"]["identity_sources"] = [{"source_id": path.name, "adapter": "chemid_identity"}]
        self.generate()
        self.assertTrue(self.check())
        path = self.out / "identity_support_ledger.csv"
        original = path.read_bytes()
        mutations = [lambda rows: rows[0].update(source_id="unknown"), lambda rows: rows[0].update(record_id="unknown"),
                     lambda rows: rows[0].update(candidate_count="999"), lambda rows: rows[0].update(status="resolved"),
                     lambda rows: rows.pop()]
        for mutate in mutations:
            self.rewrite_csv(path.name, mutate)
            with self.assertRaises(evidence.EvidenceError):
                self.check()
            path.write_bytes(original)
            self.rebind_output(path.name)

    def npass_support_fixture(self):
        from rdkit import Chem
        molecule = Chem.MolFromSmiles("CCO")
        valid = {"np_id": "NPC1", "SMILES": "CCO", "InChI": Chem.MolToInchi(molecule),
                 "InChIKey": Chem.MolToInchiKey(molecule), "extra": "preserved native column"}
        records = [dict(valid), dict(valid), {**valid, "np_id": "NPC2"},
                   {**valid, "np_id": "NPC2", "InChIKey": Chem.MolToInchiKey(Chem.MolFromSmiles("O"))},
                   {**valid, "np_id": ""}]
        path = self.raw / "npass-structure.txt"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(valid), delimiter="\t")
            writer.writeheader()
            writer.writerows(records)
        self.config["data_curation"]["identity_sources"] = [{"source_id": path.name, "adapter": "npass_structure_v1"}]
        return self.generate()

    def test_npass_native_support_duplicates_and_blocked_keys(self):
        self.npass_support_fixture()
        self.assertTrue(self.check())
        with (self.out / "identity_support_ledger.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 5)
        self.assertEqual([r["native_row"] for r in rows], ["2", "3", "4", "5", "6"])
        self.assertEqual([r["status"] for r in rows], ["candidate", "candidate", "quarantined", "quarantined", "quarantined"])
        self.assertEqual([r["blocked_key"] for r in rows], ["False", "False", "True", "True", "False"])

    def test_npass_support_rejects_forged_rows_and_status(self):
        self.npass_support_fixture()
        path = self.out / "identity_support_ledger.csv"
        original = path.read_bytes()
        mutations = [lambda rows: rows[0].update(native_row="999999"),
                     lambda rows: rows[0].update(native_record=json.dumps({**json.loads(rows[0]["native_record"]), "SMILES": "C"})),
                     lambda rows: rows[0].update(sha256="0" * 64),
                     lambda rows: rows.append(dict(rows[0])), lambda rows: rows.pop(),
                     lambda rows: rows[2].update(status="candidate", reasons="[]", blocked_key="False"),
                     lambda rows: rows[0].update(blocked_key="True"),
                     lambda rows: rows[0].update(canonical_structure="C"),
                     lambda rows: rows[0].update(record_id="NPC999")]
        for mutate in mutations:
            self.rewrite_csv(path.name, mutate)
            with self.assertRaises(evidence.EvidenceError):
                self.check()
            path.write_bytes(original)
            self.rebind_output(path.name)

    def test_identity_and_curated_native_locators_match_dispositions(self):
        self.generate()
        for name in ("identity_ledger.csv", "curated.csv"):
            path = self.out / name
            original = path.read_bytes()
            self.rewrite_csv(name, lambda rows: rows[0].update(native_row="999999"))
            with self.assertRaises(evidence.EvidenceError):
                self.check()
            path.write_bytes(original)
            self.rebind_output(name)

    def test_npass_identity_claims_require_unblocked_native_support(self):
        path = self.raw / "npass-toxicity.txt"
        path.write_text("np_id\tactivity_type\tactivity_value\tactivity_units\tactivity_relation\tassay_organism\n"
                        "NPC1\tLD50\t100\tmg/kg\t=\trat\nNPC2\tLD50\t100\tmg/kg\t=\trat\n", encoding="utf-8")
        self.config["data_curation"]["sources"].append({"source_id": path.name, "adapter": "npass"})
        self.npass_support_fixture()
        self.assertTrue(self.check())
        path = self.out / "identity_ledger.csv"
        original = path.read_bytes()
        def unblock(rows):
            for row in rows:
                if row["source_id"] == "npass-toxicity.txt" and json.loads(row["native_record"])["np_id"] == "NPC2":
                    row["status"] = "exact_identifier_mapping"
        self.rewrite_csv(path.name, unblock)
        with self.assertRaisesRegex(evidence.EvidenceError, "blocked NPASS key"):
            self.check()
        path.write_bytes(original)
        self.rebind_output(path.name)
        def forge(rows):
            for row in rows:
                if row["source_id"] == "npass-toxicity.txt" and json.loads(row["native_record"])["np_id"] == "NPC1":
                    claims = json.loads(row["evidence"])
                    claims[0]["records"][0]["native_row"] = 999999
                    row["evidence"] = json.dumps(claims)
        self.rewrite_csv(path.name, forge)
        with self.assertRaisesRegex(evidence.EvidenceError, "NPASS support evidence"):
            self.check()

    def test_npass_exact_claim_is_bound_to_native_toxicity_identifier(self):
        from rdkit import Chem
        support = []
        for npid, smiles in (("A", "CCO"), ("B", "CCN")):
            molecule = Chem.MolFromSmiles(smiles)
            support.append(dict(np_id=npid, SMILES=smiles, InChI=Chem.MolToInchi(molecule),
                                InChIKey=Chem.MolToInchiKey(molecule)))
        with (self.raw / "identity.txt").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(support[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(support)
        (self.raw / "tox.txt").write_text(
            "np_id\tactivity_type\tactivity_value\tactivity_units\tactivity_relation\tassay_organism\n"
            "A\tLD50\t100\tmg/kg\t=\trat\nB\tLD50\t200\tmg/kg\t=\trat\n"
            "UNKNOWN\tLD50\t300\tmg/kg\t=\trat\n", encoding="utf-8")
        self.config["data_curation"]["identity_sources"] = [
            dict(source_id="identity.txt", adapter="npass_structure_v1")]
        self.config["data_curation"]["sources"].append(dict(source_id="tox.txt", adapter="npass"))
        self.generate()
        self.assertTrue(self.check())  # Unknown identifiers remain legitimate unresolved rows.
        path = self.out / "identity_ledger.csv"
        original = path.read_bytes()
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        toxicity = [r for r in rows if r["source_id"] == "tox.txt"]
        self.assertEqual(toxicity[2]["status"], "unresolved_identity")
        for claim in (toxicity[1]["evidence"], "[]", '[{"key":"npid","value":"A","records":[]}]'):
            with self.subTest(claim=claim):
                def replace(rows):
                    for row in rows:
                        if row["source_id"] == "tox.txt" and json.loads(row["native_record"])["np_id"] == "A":
                            row["evidence"] = claim
                self.rewrite_csv(path.name, replace)
                with self.assertRaises(evidence.EvidenceError):
                    self.check()
                path.write_bytes(original)
                self.rebind_output(path.name)

    def test_npass_unknown_adapter_remains_fail_closed(self):
        manifest = self.npass_support_fixture()
        self.config["data_curation"]["identity_sources"][0]["adapter"] = "unknown_support_adapter"
        data.dump(self.contract, self.config)
        manifest["policy"] = self.config["data_curation"]
        manifest["contract_sha256"] = evidence.binding(self.contract)["sha256"]
        manifest["bound_evidence"]["contract"] = evidence.binding(self.contract)
        data.dump(self.manifest, manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "unknown identity support adapter"):
            self.check()

    def test_native_oft_uses_physical_study_row_not_emission_ordinal(self):
        fixture = native_tests.NativeSourcesTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.root = self.raw
        source = fixture.workbook([fixture.study()])
        self.config["data_curation"]["sources"] = [{"source_id": source.name, "adapter": "openfoodtox", "allowed_test_materials": ["mono-constituent substance"]}]
        self.config["data_curation"]["tasks"][0]["species"] = "mouse"
        self.generate()
        self.assertTrue(self.check())
        self.rewrite_csv("dispositions.csv", lambda rows: rows[0].update(native_lineage=json.dumps({
            **json.loads(rows[0]["native_lineage"]), "native_row": 999999})))
        with self.assertRaises(evidence.EvidenceError):
            self.check()

    def test_native_sql_locator_join_does_not_waive_partial_collection(self):
        fixture = native_tests.NativeSourcesTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.root = self.raw
        source = fixture.toxval_sql("=", "=")
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            sql = stream.read().replace("CREATE TABLE `toxval` (", "CREATE TABLE `toxval` (`chemical_id` text,")
        sql = sql.replace("INSERT INTO `toxval` VALUES (", "INSERT INTO `toxval` VALUES ('missing',")
        with gzip.open(source, "wt", encoding="utf-8") as stream:
            stream.write(sql + "CREATE TABLE `source_chemical` (`chemical_id` text,`casrn` text,`dtxsid` text);\n")
        self.config["data_curation"]["sources"] = [{"source_id": source.name, "adapter": "toxval"}]
        manifest = self.generate()
        with self.assertRaisesRegex(evidence.EvidenceError, "required collection source"):
            self.check()
        entries = {e["source_id"]: e for e in data.load(self.collection / "source_registry.json")["files"]}
        sources = {s["source_id"]: s for s in self.config["data_curation"]["sources"]}
        outputs = {name: Path(item["path"]) for name, item in manifest["bound_evidence"]["outputs"].items()}
        def join():
            return evidence._ledger_integrity(self.collection / "source_records.jsonl", outputs, sources, entries, manifest["policy"])
        self.assertEqual(join()[source.name], 1)
        self.rewrite_csv("dispositions.csv", lambda rows: rows[0].update(native_row="999999"))
        with self.assertRaises(evidence.EvidenceError):
            join()

    def test_malformed_numeric_evidence_does_not_leak_values(self):
        self.generate()
        self.rewrite_csv("curated.csv", lambda rows: rows[0].update(target_mg_kg="SEALED_LABEL_SENTINEL"))
        with self.assertRaises(evidence.EvidenceError) as caught:
            self.check()
        self.assertNotIn("SEALED_LABEL_SENTINEL", str(caught.exception))
    def test_review_probe_mutation_after_dispatch_blocks_consumption(self):
        for operation in ("execute", "submit", "review"):
            with self.subTest(operation=operation):
                fixture = self.runtime_curated()
                options = {"command": [sys.executable, "-c", "from pathlib import Path; Path('executed.txt').write_text('ran')"],
                           "code": [str(runtime_tests.SCRIPT)]} if operation == "execute" else {}
                dispatch = fixture.bind(fixture.prepare("preprocessing", **options))
                output = Path(dispatch["owned_output_dir"]) / "result.json"
                output.write_text('{"fixture":true}')
                submission = {"outputs": [str(output)], "summary": "Fixture", "checks": ["fixture"]}
                if operation == "review":
                    fixture.rt.submit(dispatch["dispatch_id"], submission)
                    dispatch = fixture.bind(fixture.prepare("preprocessing", "review"))
                    report = Path(dispatch["owned_output_dir"]) / "report.json"
                    report.write_text('{"fixture":true}')
                    receipt = {"candidate_hash": dispatch["candidate_hash"], "producer_execution_id": dispatch["producer_execution_id"],
                               "reviewer_execution_id": dispatch["native_execution_id"], "verdict": "PASS", "summary": "Fixture",
                               "checks": ["fixture"], "evidence": [str(report)], "findings": []}
                self.source.write_bytes(self.source.read_bytes() + b"\n")
                with self.assertRaises(GateError):
                    if operation == "execute":
                        fixture.rt.execute(dispatch["dispatch_id"])
                    elif operation == "submit":
                        fixture.rt.submit(dispatch["dispatch_id"], submission)
                    else:
                        fixture.rt.review(dispatch["dispatch_id"], receipt)
                self.assertFalse((Path(dispatch["owned_output_dir"]) / "executed.txt").exists())

    def test_diagnostic_block_and_revise_are_still_recordable(self):
        for verdict in ("BLOCK", "REVISE"):
            with self.subTest(verdict=verdict):
                fixture = runtime_tests.RuntimeTests()
                fixture.setUp()
                try:
                    data.dump(fixture.contract, self.config)
                    fixture.rt.replace_contract(fixture.contract, "strict synthetic evidence")
                    fixture.through("collection")
                    fixture.produce("curation", coverage={"status": "blocked"})
                    self.assertEqual(fixture.accept("curation", verdict=verdict)["verdict"], verdict)
                finally:
                    fixture.tearDown()

    def narrative_fixture(self, extra_parser=False):
        fixture = adjudication_tests.AdjudicationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.source = self.raw / "hsdb.zip"
        fixture.policy["sources"][0]["source_id"] = "hsdb.zip"
        # Fixture export uses its explicit source ID; rename its inventory identity consistently.
        narratives = ["ETHANOL LD50 Rat oral male 20 and female 30 mg/kg Study"]
        if extra_parser:
            narratives.append("LD50 Rat oral 40 mg/kg[Study]")
        fixture.fixture(narratives)
        fixture.entry["source_id"] = "hsdb.zip"
        inventory_dir = fixture.root / "strict-inventory"
        adjudication_tests.adj.export_inventory(fixture.source, "hsdb.zip", inventory_dir)
        fixture.inventory = list(adjudication_tests.adj.jsonl(inventory_dir / "inventory.jsonl"))
        fixture.fields = [f for doc in adjudication_tests.adj.native_fields(fixture.source, "hsdb.zip", fixture.entry["sha256"]) for f in doc]
        fixture.decisions = [{"field_id": f["field_id"], "action": "excluded", "reason": "Synthetic reviewed exclusion",
                              "evidence": [fixture.evidence(f, f["native_record"]["text"])], "observations": []} for f in fixture.inventory]
        # bind() expects this fixture location; these files are synthetic and owned here.
        (fixture.root / "inventory/inventory.jsonl").write_bytes((inventory_dir / "inventory.jsonl").read_bytes())
        fixture.expand(0, [fixture.observation(fixture.inventory[0], "20", "male"),
                           fixture.observation(fixture.inventory[0], "30", "female")])
        if extra_parser:
            fixture.decisions[1].update(action="parser", reason="Reviewed synthetic scalar")
        support = self.raw / "identity.csv"
        support.write_text("cas,smiles\n64-17-5,CCO\n", encoding="utf-8")
        fixture.policy["identity_sources"] = [{"source_id": support.name, "columns": {"cas": "cas", "smiles": "smiles"}}]
        config = fixture.bind()
        fixture.policy["sources"][0]["narrative_adjudication"] = config
        self.config["data_curation"] = fixture.policy
        return fixture

    def test_hsdb_coordinated_unknown_field_and_native_text_rejected(self):
        self.narrative_fixture()
        manifest = self.generate()
        self.assertEqual(manifest["output_rows"], 2)
        self.assertTrue(self.check())
        for name in ("dispositions.csv", "identity_ledger.csv", "curated.csv"):
            self.rewrite_csv(name, lambda rows: [row.update(field_id="nonexistent-field-id",
                native_record=json.dumps({"text": "fabricated narrative"})) for row in rows])
        with self.assertRaises(evidence.EvidenceError):
            self.check()
        with self.assertRaises(prepare.PreparationError):
            prepare.prepare_splits(self.out / "curated.csv", self.contract, self.root / "split", self.manifest)
        self.assertFalse((self.root / "split").exists())

    def test_hsdb_parser_manual_and_swapped_valid_field_membership(self):
        fixture = self.narrative_fixture(extra_parser=True)
        manifest = self.generate()
        self.assertEqual(manifest["output_rows"], 3)
        self.assertTrue(self.check())
        originals = {name: (self.out / name).read_bytes() for name in ("dispositions.csv", "identity_ledger.csv", "curated.csv")}
        with (self.out / "dispositions.csv").open(encoding="utf-8", newline="") as stream:
            first = next(csv.DictReader(stream))["source_record_id"]
        alternate = fixture.inventory[1]
        mutations = [lambda row: row.update(field_id=alternate["field_id"], native_record=json.dumps(alternate["native_record"]),
                                             native_lineage=json.dumps(alternate["locator"])),
                     lambda row: row.update(native_lineage=json.dumps({"tag": "ntxv", "field_ordinal": 999999})),
                     lambda row: row.update(adjudication=json.dumps(fixture.decisions[0]["observations"][1]))]
        for mutate in mutations:
            for name in originals:
                self.rewrite_csv(name, lambda rows: [mutate(row) for row in rows if row["source_record_id"] == first])
            with self.assertRaisesRegex(evidence.EvidenceError, "reviewed native field"):
                self.check()
            for name, content in originals.items():
                (self.out / name).write_bytes(content)
                self.rebind_output(name)
        # The field ledger itself cannot reassign stable IDs to a different field.
        name = manifest["narrative_accounting"]["hsdb.zip"]["field_ledger"]
        ledger = list(evidence.json_rows(self.out / name))
        ledger[0]["observation_ids"], ledger[1]["observation_ids"] = ledger[1]["observation_ids"], ledger[0]["observation_ids"]
        (self.out / name).write_text("".join(json.dumps(row) + "\n" for row in ledger), encoding="utf-8")
        self.rebind_output(name)
        with self.assertRaisesRegex(evidence.EvidenceError, "reviewed native proposals"):
            self.check()

    def test_hsdb_wrong_field_cannot_receive_runtime_pass(self):
        self.narrative_fixture()
        def mutate():
            for name in ("dispositions.csv", "identity_ledger.csv", "curated.csv"):
                self.rewrite_csv(name, lambda rows: [row.update(field_id="invented") for row in rows])
        fixture = self.runtime_curated(mutate)
        with self.assertRaisesRegex(GateError, "reviewed native field"):
            fixture.accept("curation")

    def test_hsdb_reviewed_exclusions_have_zero_observations(self):
        fixture = self.narrative_fixture()
        fixture.decisions[0].update(action="excluded", observations=[], reason="Synthetic reviewed exclusion")
        fixture.policy["sources"][0].pop("narrative_adjudication")
        fixture.policy["sources"][0]["narrative_adjudication"] = fixture.bind()
        manifest = self.generate()
        self.assertEqual(manifest["output_rows"], 0)
        self.assertTrue(self.check())

    def test_narrative_review_and_unresolved_field_cannot_be_hidden(self):
        fixture = self.narrative_fixture()
        config = fixture.config
        manifest = self.generate()
        self.assertTrue(self.check())
        receipt = Path(config["review_receipt"]["path"])
        receipt.write_text(receipt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(evidence.EvidenceError, "[Ss]tale"):
            self.check()
        # A forged complete summary cannot convert a reviewed unresolved action.
        fixture.decisions[0].update(action="unresolved", observations=[])
        fixture.policy["sources"][0].pop("narrative_adjudication")
        fixture.policy["sources"][0]["narrative_adjudication"] = fixture.bind()
        data.dump(self.contract, self.config)
        self.out = self.root / "unresolved-curation"
        data.curate(self.contract, self.collection / "source_registry.json", self.out)
        self.manifest = self.out / "curation_manifest.json"
        manifest = data.load(self.manifest)
        manifest.update(status="produced", blockers=[])
        manifest["narrative_accounting"]["hsdb.zip"]["complete_narrative_accounting"] = True
        data.dump(self.manifest, manifest)
        with self.assertRaisesRegex(evidence.EvidenceError, "unresolved narrative"):
            self.check()


if __name__ == "__main__":
    unittest.main()
