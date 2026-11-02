"""Native-format synthetic fixtures only; no research pipeline execution."""
import csv
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import tracemalloc
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_data.py"
spec = importlib.util.spec_from_file_location("qsar_data", SCRIPT)
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-native-fixture-")
        self.base = Path(self.temp.name)
        self.raw = self.base / "raw"
        self.raw.mkdir()
        self.policy = {"chemistry_policy": "single_component", "relation_policy": "exact_only",
                       "replicate_policy": "preserve_flag", "tasks": [{"id": "mouse_oral", "species": "mouse", "route": "oral", "endpoint": "LD50"}],
                       "sources": [{"source_id": "oecd.csv", "adapter": "oecd", "columns": {"relation": "qualifier"}}]}

    def tearDown(self):
        self.temp.cleanup()

    def table(self, name, records, delimiter=","):
        with (self.raw / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter=delimiter)
            writer.writeheader()
            writer.writerows(records)

    def row(self, **changes):
        row = dict(source_record_id="id", casrn="64-17-5", species="mouse", route="oral", endpoint_type="LD50",
                   numeric_value="100", numeric_unit="mg/kg", smiles="CCO", qualifier="=", reference="study")
        row.update(changes)
        return row

    def run_curate(self):
        self.collection = self.base / "collection"
        data.collect(self.raw, self.collection)
        contract = self.base / "contract.json"
        data.dump(contract, {"version": "fixture", "data_curation": self.policy})
        self.out = self.base / "curation"
        result = data.curate(contract, self.collection / "source_registry.json", self.out)
        return result, self.read("curated.csv"), self.read("dispositions.csv")

    def read(self, name):
        with (self.out / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_many_row_ledger_preserves_order_native_evidence_and_counts(self):
        records = [self.row(source_record_id=str(i), species="rat", reference=f"study {i}; \"quoted\"\nUnicode \u03bc",
                            native_extra="payload-" + str(i) + "x" * 1024) for i in range(1200)]
        self.table("oecd.csv", records)
        result, kept, dispositions = self.run_curate()
        identities = self.read("identity_ledger.csv")
        self.assertEqual(kept, [])
        self.assertEqual(result["coverage"]["input_rows"], len(records))
        self.assertEqual(len(dispositions), len(records))
        self.assertEqual(len(identities), len(records))
        for number, (native, disposition, identity) in enumerate(zip(records, dispositions, identities)):
            self.assertEqual(disposition["native_row"], str(3 + 2 * number))
            self.assertEqual(json.loads(disposition["native_record"]), native)
            self.assertEqual(identity["native_record"], disposition["native_record"])
            self.assertEqual(identity["row_id"], disposition["row_id"])
        self.assertFalse(list(self.out.glob(".ledger-*")))

    def test_disk_ledger_csv_bytes_match_list_with_late_columns_and_final_flags(self):
        records = [{"row_id": "first", "flags": [], "native_record": {"value": "\u03bc\nquoted \";", "nested": [None, 1.25]}},
                   {"row_id": "last", "flags": [], "late_column": {"reference": "late"}}]
        with ExitStack() as stack:
            ledger = data.DiskLedger(self.base / "ledger.jsonl", stack)
            for row in records:
                ledger.append(row)
            records[0]["flags"].append("late_metadata_flag")
            ledger.final_flags["first"] = records[0]["flags"]
            data.write_csv(self.base / "disk.csv", ledger, ["row_id", "flags"])
            data.write_csv(self.base / "list.csv", records, ["row_id", "flags"])
            self.assertEqual((self.base / "disk.csv").read_bytes(), (self.base / "list.csv").read_bytes())
            self.assertEqual(len(ledger), 2)

    def test_disk_ledger_many_large_records_has_bounded_python_memory(self):
        with ExitStack() as stack:
            ledger = data.DiskLedger(self.base / "ledger.jsonl", stack)
            tracemalloc.start()
            try:
                for i in range(4000):
                    ledger.append({"row_id": str(i), "native_record": {"evidence": str(i) + "x" * 8192}})
                data.write_csv(self.base / "large.csv", ledger, ["row_id"])
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(len(ledger), 4000)
            self.assertGreater(ledger.path.stat().st_size, 32_000_000)
            self.assertLess(peak, 2_000_000)

    def test_failed_curation_cleans_only_owned_spool(self):
        self.table("oecd.csv", [self.row()])
        self.policy["sources"][0]["constants"] = {"inchi": "invented"}
        sentinel = self.base / "unrelated.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaises(data.DataError):
            self.run_curate()
        self.assertFalse(list(self.out.glob(".ledger-*")))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_constant_inchi_cannot_invent_identity(self):
        self.table("oecd.csv", [self.row(smiles="", casrn="")])
        self.policy["sources"][0]["constants"] = {"inchi": "InChI=1S/CH4/h1H4"}
        with self.assertRaisesRegex(data.DataError, "cannot invent"):
            self.run_curate()

    def test_direct_conflicting_representations_excluded(self):
        self.table("oecd.csv", [self.row(inchi="InChI=1S/CH4/h1H4")])
        self.policy["sources"][0]["columns"]["inchi"] = "inchi"
        _, kept, dispositions = self.run_curate()
        self.assertEqual(kept, [])
        self.assertIn("conflicting_structure_representations", dispositions[0]["reasons"])
        self.assertEqual(dispositions[0]["disposition"], "quarantined")

    def test_identity_support_conflict_blocks(self):
        self.table("oecd.csv", [self.row()])
        self.table("support.csv", [{"cas": "64-17-5", "smiles": "CCO", "inchi": "InChI=1S/CH4/h1H4"}])
        self.policy["identity_sources"] = [{"source_id": "support.csv", "columns": {"cas": "cas", "smiles": "smiles", "inchi": "inchi"}}]
        with self.assertRaisesRegex(data.DataError, "Conflicting identity support"):
            self.run_curate()

    def test_empty_wrong_schema_rejected(self):
        (self.raw / "oecd.csv").write_text("wrong,schema\n", encoding="utf-8")
        with self.assertRaisesRegex(data.DataError, "column missing"):
            self.run_curate()

    def test_dummy_structure_excluded_without_losing_accounting(self):
        self.table("oecd.csv", [self.row(smiles="*"), self.row()])
        _, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dispositions), 2)
        self.assertIn("unsupported_dummy_or_zero_mass_structure", dispositions[0]["reasons"])

    def test_exact_units_and_no_substring_conversion(self):
        self.table("oecd.csv", [self.row(numeric_unit=u, numeric_value=v) for u, v in
                               [("mg/kg", "100"), ("ug/kg", "100000"), ("g/kg", "0.1"), ("mol/kg", "0.1"), ("mg/kg/day", "100")]])
        result, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 4)
        self.assertEqual([float(r["target_mg_kg"]) for r in kept[:3]], [100] * 3)
        self.assertAlmostEqual(float(kept[3]["target_mol_kg"]), 0.1)
        self.assertIn("unsupported_or_unknown_unit", dispositions[-1]["reasons"])
        self.assertEqual(result["coverage"]["input_rows"], 5)

    def test_missing_and_censored_qualifiers_never_exact(self):
        self.table("oecd.csv", [self.row(qualifier=q) for q in ("", "<", "approx", "=")])
        _, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dispositions), 4)

    def test_reported_numeric_requires_explicit_policy_and_preserves_unknown(self):
        self.policy["relation_policy"] = "reported_numeric"
        self.table("oecd.csv", [self.row(qualifier=q) for q in ("", "<", "approx")])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["normalized_relation"], "unknown")
        self.assertEqual(kept[0]["target_kind"], "reported_numeric_estimate")

    def test_invalid_cas_direct_smiles_survives_and_checksum(self):
        self.assertTrue(data.valid_cas("64-17-5"))
        self.assertFalse(data.valid_cas("64-17-6"))
        self.table("oecd.csv", [self.row(casrn="59"), self.row(casrn="64-17-6")])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 2)
        self.assertTrue(all("invalid_cas" in x["flags"] for x in kept))

    def test_ambiguous_mapping_not_arbitrary_first_or_drop_duplicates(self):
        self.table("support.csv", [{"dtxsid": "DTX1", "cas": "64-17-5", "smiles": "CCO"},
                                   {"dtxsid": "DTX1", "cas": "64-17-5", "smiles": "CCN"}])
        self.policy["identity_sources"] = [{"source_id": "support.csv", "columns": {"dtxsid": "dtxsid", "cas": "cas", "smiles": "smiles"}}]
        self.table("oecd.csv", [self.row(smiles="")])
        _, kept, dispositions = self.run_curate()
        self.assertFalse(kept)
        self.assertIn("ambiguous_identity", dispositions[0]["reasons"])
        self.assertEqual(dispositions[0]["disposition"], "quarantined")

    def test_dtxsid_recovery_and_repeated_identical_support(self):
        self.table("support.csv", [{"dtxsid": "DTX1", "inchi": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"}] * 2)
        self.policy["identity_sources"] = [{"source_id": "support.csv", "columns": {"dtxsid": "dtxsid", "inchi": "inchi"}}]
        self.policy["sources"][0]["columns"]["dtxsid"] = "dtxsid"
        self.table("oecd.csv", [self.row(smiles="", casrn="", dtxsid="DTX1")])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.read("identity_ledger.csv")[0]["status"], "exact_identifier_mapping")

    def test_parent_policy_preserves_original_mw_and_stereo(self):
        value = data.chemistry("C[C@H](O)C(=O)[O-].[Na+]", "", "largest_organic")
        self.assertGreater(value["original_mw"], value["parent_mw"])
        self.assertIn("@", value["parent_smiles"])
        other = data.chemistry("C[C@@H](O)C(=O)[O-].[Na+]", "", "largest_organic")
        self.assertEqual(value["connectivity_id"], other["connectivity_id"])
        with self.assertRaisesRegex(data.DataError, "ambiguous_multiorganic"):
            data.chemistry("CCO.CCN", "", "largest_organic")
        with self.assertRaisesRegex(data.DataError, "multiple_components"):
            data.chemistry("CCO.[Na+]", "", "single_component")

    def test_molar_conversion_uses_original_salt_not_parent_mw(self):
        self.policy["chemistry_policy"] = "largest_organic"
        self.table("oecd.csv", [self.row(smiles="CC(=O)[O-].[Na+]", numeric_unit="mol/kg", numeric_value="1")])
        _, kept, _ = self.run_curate()
        self.assertAlmostEqual(float(kept[0]["target_mg_kg"]), float(kept[0]["original_mw"]) * 1000)
        self.assertNotAlmostEqual(float(kept[0]["original_mw"]), float(kept[0]["parent_mw"]))

    def test_no_cap_preserve_replicates_and_input_bytes(self):
        self.table("oecd.csv", [self.row(source_record_id=str(i)) for i in range(1101)])
        before = data.digest(self.raw / "oecd.csv")
        result, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1101)
        self.assertEqual(len(dispositions), 1101)
        self.assertEqual(result["coverage"]["groups"], 1)
        self.assertTrue(all(x["replicate_flag"] == "True" for x in kept))
        self.assertEqual(before, data.digest(self.raw / "oecd.csv"))

    def test_native_inventory_counts_blank_rows_and_archive_gap(self):
        self.table("oecd.csv", [self.row(), self.row(numeric_value="")])
        with zipfile.ZipFile(self.raw / "archive.zip", "w") as archive:
            archive.writestr("native.xml", "<records/>")
        result = data.collect(self.raw, self.base / "collection")
        registry = data.load(self.base / "collection/source_registry.json")
        self.assertEqual(len(registry["files"]), 2)
        self.assertEqual(registry["files"][1]["row_count"], 2)
        self.assertEqual(registry["files"][0]["status"], "unsupported_native_parser")
        self.assertEqual(result["coverage"]["status"], "limited")

    def test_unsupported_oft_intervals_block_not_lower_bound_exact(self):
        self.table("oft.csv", [{"lowerValue": "10", "upperValue": "20"}])
        self.policy["sources"] = [{"source_id": "oft.csv", "adapter": "unimplemented_interval_source"}]
        result, kept, _ = self.run_curate()
        self.assertEqual(result["coverage"]["status"], "blocked")
        self.assertFalse(kept)
        self.assertEqual(result["coverage"]["blockers"][0]["native_rows"], 1)

    def test_optional_unsupported_source_reports_disclosed_gap_not_blocked(self):
        self.table("unknown.csv", [{"native": "unresolved"}])
        self.policy["sources"] = [{"source_id": "unknown.csv", "adapter": "unsupported", "required": False}]
        result, kept, _ = self.run_curate()
        self.assertEqual(result["coverage"]["status"], "limited")
        self.assertIn("disclosed gaps", result["summary"])
        self.assertNotIn("blocked", result["summary"])
        self.assertFalse(kept)

    def test_npass_native_relation_and_identity(self):
        self.table("npass.txt", [{"np_id": "NP1", "activity_type": "LD50", "activity_value": "10", "activity_units": "mg/kg",
                                  "activity_relation": r, "assay_organism": "mouse", "ref_id": "PM1"} for r in ("=", ">")] , "\t")
        self.table("structures.txt", [{"np_id": "NP1", "SMILES": "CCO"}], "\t")
        self.policy["sources"] = [{"source_id": "npass.txt", "adapter": "npass", "constants": {"route": "oral"}}]
        self.policy["identity_sources"] = [{"source_id": "structures.txt", "columns": {"npid": "np_id", "smiles": "SMILES"}}]
        _, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["reference"], "PM1")
        self.assertEqual(len(dispositions), 2)

    def test_ice_xlsx_preserves_material_qualifiers_and_notes_inventory(self):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.title = "Data"
        sheet.append(["Record ID", "Species", "Endpoint", "Response", "Response Unit", "Response Modifier", "Mixture", "SMILES"])
        sheet.append(["A", "mouse", "LD50", 20, "mg/kg", "=", "Chemical", "CCO"])
        sheet.append(["B", "mouse", "LD50", 30, "mg/kg", "", "Formulation", "CCO"])
        notes = book.create_sheet("Notes")
        notes.append(["Column", "Description"])
        notes.append(["Response", "Native observation"])
        book.save(self.raw / "ice.xlsx")
        book.close()
        self.policy["sources"] = [{"source_id": "ice.xlsx", "adapter": "ice_oral", "columns": {"smiles": "SMILES"},
                                   "constants": {"route": "oral"}, "allowed_test_materials": ["Chemical"]}]
        _, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["test_material"], "Chemical")
        self.assertEqual(len(dispositions), 2)
        registry = data.load(self.collection / "source_registry.json")
        self.assertEqual(registry["files"][0]["row_count"], 3)

    def test_toxric_requires_receipt_and_exact_hash_binding(self):
        self.table("toxric.csv", [{"TAID": "T1", "Canonical SMILES": "CCO", "Toxicity Value": "20"}])
        data.dump(self.raw / "receipt.json", {"scope": "original-value download variant", "files": [{"filename": "toxric.csv",
                  "sha256": data.digest(self.raw / "toxric.csv"), "url": "https://example.test/original"}]})
        self.policy["sources"] = [{"source_id": "toxric.csv", "adapter": "toxric", "constants": {"species": "mouse", "route": "oral", "endpoint": "LD50"},
                                   "unit_evidence": {"receipt_source_id": "receipt.json", "unit": "mg/kg", "citation": "fixture-primary-evidence"}}]
        self.policy["relation_policy"] = "reported_numeric"
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["raw_unit"], "mg/kg")
        self.assertEqual(kept[0]["normalized_relation"], "unknown")
        registry = data.load(self.collection / "source_registry.json")
        config = self.policy["sources"][0]
        entry = data.bound_file(registry, "toxric.csv")
        entry["sha256"] = "wrong"
        with self.assertRaisesRegex(data.DataError, "exact native bytes"):
            data.toxric_unit(config, registry, entry)

    def test_rejects_source_mutation_and_output_overlap(self):
        self.table("oecd.csv", [self.row()])
        data.collect(self.raw, self.base / "collection")
        registry = data.load(self.base / "collection/source_registry.json")
        (self.raw / "oecd.csv").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(data.DataError, "digest mismatch"):
            data.bound_file(registry, "oecd.csv")
        with self.assertRaisesRegex(data.DataError, "overlaps"):
            data.collect(self.raw, self.raw / "output")

    def test_cli_and_missing_policy_fail_closed(self):
        self.table("oecd.csv", [self.row()])
        command = [sys.executable, str(SCRIPT), "collect", "--raw-root", str(self.raw), "--output-dir", str(self.base / "collection")]
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
        self.assertEqual(result.returncode, 0, result.stderr)
        del self.policy["relation_policy"]
        data.dump(self.base / "contract.json", {"data_curation": self.policy})
        command = [sys.executable, str(SCRIPT), "curate", "--contract", str(self.base / "contract.json"),
                   "--registry", str(self.base / "collection/source_registry.json"), "--output-dir", str(self.base / "curation")]
        result = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
        self.assertEqual(result.returncode, 2)
        self.assertIn("relation_policy", result.stderr)

    def test_empty_native_table_still_has_schema(self):
        (self.raw / "empty.csv").write_text("id,value,unit\n", encoding="utf-8")
        data.collect(self.raw, self.base / "collection")
        entry = data.load(self.base / "collection/source_registry.json")["files"][0]
        self.assertEqual(entry["row_count"], 0)
        self.assertEqual(entry["tables"][0]["columns"], ["id", "value", "unit"])

    def native_toxval(self):
        import gzip
        columns = ["toxval_id", "dtxsid", "toxval_type", "toxval_numeric", "toxval_units", "toxval_numeric_qualifier",
                   "toxval_numeric_qualifier_original", "species_original", "exposure_route", "chemical_id"]
        schema = ", ".join(f"`{c}` text" for c in columns)
        with gzip.open(self.raw / "toxval.sql.gz", "wt", encoding="utf-8") as stream:
            stream.write("CREATE TABLE `metadata` (`id` int);\nINSERT INTO `metadata` VALUES (1);\n")
            stream.write(f"CREATE TABLE `toxval` ({schema});\n")
            for i, (current, original) in enumerate([( "=", "="), ("=", "-"), ("=", ">"), ("<", "<")]):
                values = [str(i), "DTX1", "LD50", "20", "mg/kg", current, original, "mouse", "oral", "C1"]
                stream.write("INSERT INTO `toxval` VALUES (" + ",".join("'" + v + "'" for v in values) + ");\n")
            stream.write("CREATE TABLE `source_chemical` (`chemical_id` text, `casrn` text, `dtxsid` text);\nINSERT INTO `source_chemical` VALUES ('C1','64-17-5','DTX1');\n")
            stream.write("CREATE TABLE `record_source` (`toxval_id` int, `long_ref` text, `qa_status` text, `quality` text);\n")
            for i in range(4):
                stream.write(f"INSERT INTO `record_source` VALUES ({i},'Native study {i}','unchecked','native label');\n")
            stream.write("CREATE TABLE `toxval_relationship` (`toxval_id_1` int,`toxval_id_2` int,`relationship` text);\nINSERT INTO `toxval_relationship` VALUES (0,2,'related');\n")
        self.table("identity.csv", [{"dtxsid": "DTX1", "smiles": "CCO"}])
        self.policy["identity_sources"] = [{"source_id": "identity.csv", "columns": {"dtxsid": "dtxsid", "smiles": "smiles"}}]
        self.policy["sources"] = [{"source_id": "toxval.sql.gz", "adapter": "toxval", "required": True}]

    def test_toxval_end_to_end_exact_policy_blocks_unknown_original(self):
        self.native_toxval()
        result, kept, dispositions = self.run_curate()
        self.assertEqual(result["coverage"]["input_rows"], 4)
        self.assertEqual(len(kept), 1)
        self.assertIn("nonexact_or_unknown_relation", dispositions[1]["reasons"])
        self.assertIn("current_original_qualifier_conflict", dispositions[2]["reasons"])
        self.assertEqual(dispositions[2]["disposition"], "quarantined")
        registry = data.load(self.collection / "source_registry.json")
        sql = next(e for e in registry["files"] if e["format"] == "sql")
        self.assertEqual(sql["status"], "partial_native_parser")
        self.assertIsNone(sql["row_count"])
        self.assertEqual(sql["inventoried_record_count"], 4)
        self.assertIn("sql_tuple", json.loads(kept[0]["native_lineage"]))
        self.assertEqual(json.loads(kept[0]["record_source_matches"])[0]["record"]["qa_status"], "unchecked")
        self.assertIn("native_relationship_present", kept[0]["flags"])
        identity = next(row for row in self.read("identity_ledger.csv") if row["row_id"] == kept[0]["row_id"])
        self.assertEqual(identity["flags"], kept[0]["flags"])

    def test_toxval_reported_numeric_keeps_source_equality_unknown(self):
        self.native_toxval()
        self.policy["relation_policy"] = "reported_numeric"
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[1]["raw_relation"], "=")
        self.assertEqual(kept[1]["normalized_relation"], "unknown")
        self.assertEqual(kept[1]["target_kind"], "reported_numeric_estimate")

    def native_oft(self):
        from openpyxl import Workbook
        book = Workbook()
        book.remove(book.active)
        sub = book.create_sheet("SUB")
        sub.append([None, "Document UUID", "ReferenceSubstance.ReferenceSubstance", "TypeOfSubstance.Composition"])
        sub.append([0, "S1", "R1", "mono-constituent substance"])
        ref = book.create_sheet("REF_SUB")
        ref.append([None, "Document UUID", "Inventory.CASNumber", "MolecularStructuralInfo.SmilesNotation"])
        ref.append([0, "R1", "64-17-5", "CCO"])
        sheet = book.create_sheet("END_STUDY_REC.HumanHealth")
        prefix = "ResultsAndDiscussion.EffectLevels."
        sheet.append([None, "Document UUID", "Parent UUID", "MaterialsAndMethods.TestAnimals.Species", "AdministrativeData.Endpoint",
                      prefix + "Endpoint", prefix + "EffectLevel.lowerValue", prefix + "EffectLevel.upperValue",
                      prefix + "EffectLevel.lowerQualifier", prefix + "EffectLevel.Unit"])
        for i, (upper, relation) in enumerate([(None, "="), (30, "="), (None, ">"), (None, "")]):
            sheet.append([i, str(i), "S1", "mouse", "acute toxicity: oral", "LD50", 20, upper, relation, "mg/kg bw"])
        book.save(self.raw / "oft.xlsx")
        book.close()
        self.policy["sources"] = [{"source_id": "oft.xlsx", "adapter": "openfoodtox", "required": True,
            "allowed_test_materials": ["mono-constituent substance"],
            "exact_unit_aliases": {"mg/kg bw": {"unit": "mg/kg", "evidence": "fixture body-weight unit dictionary"}},
            "administrative_endpoint_routes": {"acute toxicity: oral": {"route": "oral", "evidence": "fixture native endpoint definition"}}}]

    def test_oft_end_to_end_required_source_bounds_and_evidenced_mappings(self):
        self.native_oft()
        result, kept, dispositions = self.run_curate()
        self.assertEqual(result["coverage"]["input_rows"], 4)
        self.assertEqual(result["coverage"]["status"], "produced")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["raw_unit"], "mg/kg bw")
        self.assertEqual(kept[0]["normalized_unit"], "mg/kg")
        self.assertIn("interval_not_point", dispositions[1]["reasons"])
        self.assertEqual(dispositions[1]["disposition"], "quarantined")
        self.assertEqual(len(json.loads(kept[0]["native_lineage"])["joins"]), 2)
        registry = data.load(self.collection / "source_registry.json")
        self.assertEqual(registry["files"][0]["row_count"], 6)

    def test_native_unit_alias_without_evidence_rejected(self):
        self.native_oft()
        self.policy["sources"][0]["exact_unit_aliases"]["mg/kg bw"].pop("evidence")
        with self.assertRaisesRegex(data.DataError, "requires a supported unit and evidence"):
            self.run_curate()

    def test_oft_route_alias_applies_before_administrative_consistency_check(self):
        import openpyxl
        self.native_oft()
        path = self.raw / "oft.xlsx"
        book = openpyxl.load_workbook(path)
        sheet = book["END_STUDY_REC.HumanHealth"]
        sheet.cell(1, 11, "MaterialsAndMethods.AdministrationExposure.RouteOfAdministration")
        for number in range(2, 6):
            sheet.cell(number, 11, "oral: unspecified")
        book.save(path)
        book.close()
        self.policy["sources"][0]["exact_route_aliases"] = {"oral: unspecified": {"route": "oral", "evidence": "fixture native route definition"}}
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["raw_route"], "oral: unspecified")
        self.assertEqual(kept[0]["route"], "oral")

    def test_species_alias_is_explicit_and_preserves_native_species(self):
        self.table("oecd.csv", [self.row(species="Mus musculus")])
        self.policy["sources"][0]["exact_species_aliases"] = {"Mus musculus": {"species": "mouse", "evidence": "fixture taxonomy reference"}}
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["raw_species"], "Mus musculus")

    def test_explicit_native_study_filter_preserves_excluded_row(self):
        self.table("oecd.csv", [self.row(study_type="acute"), self.row(study_type="repeat dose other")])
        self.policy["sources"][0]["native_field_filters"] = {"study_type": {"allowed_values": ["acute"], "evidence": "fixture accepted acute-only scope"}}
        _, kept, dispositions = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dispositions), 2)
        self.assertIn("native_field_not_allowed:study_type", dispositions[1]["reasons"])

    def test_agilent_comment_header_inventory_not_invented_structure(self):
        (self.raw / "formula.csv").write_text("# Compound database,,,,\n# Formula, RT, Mass, Cpd, Comments\nC,,12,C,\nCH4,,16,CH4,\n", encoding="utf-8")
        data.collect(self.raw, self.base / "collection")
        entry = data.load(self.base / "collection/source_registry.json")["files"][0]
        self.assertEqual(entry["row_count"], 2)
        self.assertEqual(entry["tables"][0]["columns"], ["Formula", "RT", "Mass", "Cpd", "Comments"])

    def test_corrupt_archive_collection_is_explicit_parse_failure(self):
        (self.raw / "broken.zip").write_bytes(b"PK\x03\x04truncated")
        result = data.collect(self.raw, self.base / "collection")
        entry = data.load(self.base / "collection/source_registry.json")["files"][0]
        self.assertEqual(entry["status"], "parse_failed")
        self.assertEqual(result["coverage"]["status"], "limited")

    def test_hsdb_end_to_end_full_narrative_accounting(self):
        narratives = ["LD50 Rat oral 20 mg/kg[Study]", "LD50 Rat oral = 30 mg/kg[Study]", "LD50 Rat oral &gt; 50 mg/kg[Study]",
                      "LD50 Rat oral 10-20 mg/kg[Study]", "LD50 Mouse oral 20 mg/kg[Study]"]
        with zipfile.ZipFile(self.raw / "hsdb.zip", "w") as book:
            book.writestr("hsdb.xml.20191028", "<hsdb><DOC><DOCNO>1</DOCNO><CASRegistryNumber>64-17-5</CASRegistryNumber>" +
                          "".join("<ntxv>" + n + "</ntxv>" for n in narratives) + "</DOC></hsdb>")
        self.table("support.csv", [{"cas": "64-17-5", "smiles": "CCO"}])
        self.policy["identity_sources"] = [{"source_id": "support.csv", "columns": {"cas": "cas", "smiles": "smiles"}}]
        self.policy["sources"] = [{"source_id": "hsdb.zip", "adapter": "hsdb", "required": True}]
        self.policy["tasks"] = [{"id": "rat_oral_ld50", "species": "rat", "route": "oral", "endpoint": "LD50"}]
        self.policy["relation_policy"] = "reported_numeric"
        result, kept, dispositions = self.run_curate()
        self.assertEqual(result["coverage"]["input_rows"], 5)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dispositions), 5)
        self.assertEqual(kept[0]["normalized_relation"], "unknown")
        self.assertIn("narrative_not_verified_single_rat_oral_scalar", dispositions[3]["reasons"])

    def test_hsdb_route_variants_require_explicit_contract_aliases(self):
        routes = ["intragastric", "gavage", "ingestion", "po", "oral/dermal"]
        with zipfile.ZipFile(self.raw / "hsdb.zip", "w") as book:
            book.writestr("hsdb.xml", "<hsdb><DOC><DOCNO>1</DOCNO><CASRegistryNumber>64-17-5</CASRegistryNumber>" +
                          "".join(f"<ntxv>LD50 Rat {route} 20 mg/kg[Study]</ntxv>" for route in routes) + "</DOC></hsdb>")
        self.table("support.csv", [{"cas": "64-17-5", "smiles": "CCO"}])
        self.policy["identity_sources"] = [{"source_id": "support.csv", "columns": {"cas": "cas", "smiles": "smiles"}}]
        self.policy["sources"] = [{"source_id": "hsdb.zip", "adapter": "hsdb", "required": True,
                                   "exact_route_aliases": {route: {"route": "oral", "evidence": "fixture reviewed route dictionary"}
                                                           for route in routes[:3]}}]
        self.policy["tasks"] = [{"id": "rat_oral_ld50", "species": "rat", "route": "oral", "endpoint": "LD50"}]
        self.policy["relation_policy"] = "reported_numeric"
        result, kept, dispositions = self.run_curate()
        self.assertEqual(result["coverage"]["input_rows"], 5)
        self.assertEqual([row["raw_route"] for row in kept], routes[:3])
        self.assertEqual([row["route"] for row in kept], ["oral"] * 3)
        self.assertEqual(len(dispositions), 5)
        self.assertEqual(dispositions[3]["disposition"], "excluded")
        self.assertIn("outside_declared_task_or_missing_stratum", dispositions[3]["reasons"])
        self.assertIn("narrative_not_verified_single_rat_oral_scalar", dispositions[4]["reasons"])

    def chemid_support(self, conflict=False):
        with zipfile.ZipFile(self.raw / "chemid.zip", "w") as book:
            book.writestr("chemid.xml", '<file><Chemical id="X1"><NumberList><CASRegistryNumber>64-17-5</CASRegistryNumber></NumberList></Chemical></file>')
            book.writestr("chemid-SMILES.txt", "SUPERID\tSMILES\nX1\tCCO\n" + ("X1\tCCN\n" if conflict else ""))
            book.writestr("chemid-InChI.txt", "SUPERID\tInChI\tInChIKey\nX1\tInChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3\tKEY\n")
        self.policy["identity_sources"] = [{"source_id": "chemid.zip", "adapter": "chemid_identity"}]
        self.table("oecd.csv", [self.row(smiles="")])

    def test_chemid_identity_support_join_end_to_end(self):
        self.chemid_support()
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        support = self.read("identity_support_ledger.csv")
        self.assertEqual(support[0]["candidate_count"], "2")
        registry = data.load(self.collection / "source_registry.json")
        archive = next(e for e in registry["files"] if e["source_id"] == "chemid.zip")
        self.assertEqual(archive["row_count"], 3)

    def test_chemid_conflict_support_cannot_be_arbitrarily_resolved(self):
        self.chemid_support(conflict=True)
        _, kept, dispositions = self.run_curate()
        self.assertFalse(kept)
        self.assertEqual(dispositions[0]["disposition"], "quarantined")
        self.assertIn("conflicting_structure_candidates", self.read("identity_support_ledger.csv")[0]["reasons"])

    def test_chemid_ambiguous_cas_blocks_every_valid_candidate_key(self):
        with zipfile.ZipFile(self.raw / "chemid.zip", "w") as book:
            book.writestr("chemid.xml", '<file><Chemical id="X1"><NumberList><CASRegistryNumber>64-17-5</CASRegistryNumber><CASRegistryNumber>67-56-1</CASRegistryNumber></NumberList></Chemical><Chemical id="X2"><NumberList><CASRegistryNumber>64-17-5</CASRegistryNumber></NumberList></Chemical><Chemical id="X3"><NumberList><CASRegistryNumber>67-56-1</CASRegistryNumber></NumberList></Chemical></file>')
            book.writestr("chemid-SMILES.txt", "SUPERID\tSMILES\nX1\tCCN\nX2\tCCO\nX3\tCO\n")
            book.writestr("chemid-InChI.txt", "SUPERID\tInChI\tInChIKey\n")
        self.policy["identity_sources"] = [{"source_id": "chemid.zip", "adapter": "chemid_identity"}]
        self.table("oecd.csv", [self.row(smiles="", casrn="64-17-5"), self.row(smiles="", casrn="67-56-1")])
        _, kept, dispositions = self.run_curate()
        self.assertFalse(kept)
        self.assertTrue(all("unresolved_support_identity" in row["reasons"] for row in dispositions))
        self.assertTrue(all(row["disposition"] == "quarantined" for row in dispositions))

    def missing_chemid_support(self, structure_row=""):
        with zipfile.ZipFile(self.raw / "chemid.zip", "w") as book:
            book.writestr("chemid.xml", '<file><Chemical id="X1"><NumberList><CASRegistryNumber>64-17-5</CASRegistryNumber></NumberList></Chemical></file>')
            book.writestr("chemid-SMILES.txt", "SUPERID\tSMILES\n" + structure_row)
            book.writestr("chemid-InChI.txt", "SUPERID\tInChI\tInChIKey\n")
        self.policy["identity_sources"] = [{"source_id": "chemid.zip", "adapter": "chemid_identity"}]

    def test_missing_chemid_support_does_not_override_valid_direct_structure(self):
        self.missing_chemid_support()
        self.table("oecd.csv", [self.row()])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)
        self.assertIn("missing_structure_candidates", self.read("identity_support_ledger.csv")[0]["reasons"])

    def test_missing_chemid_support_does_not_override_exact_dsstox_recovery(self):
        self.missing_chemid_support()
        self.table("support.csv", [{"cas": "64-17-5", "dtxsid": "DTX1", "smiles": "CCO"}])
        self.policy["identity_sources"].append({"source_id": "support.csv", "columns": {"cas": "cas", "dtxsid": "dtxsid", "smiles": "smiles"}})
        self.policy["sources"][0]["columns"]["dtxsid"] = "dtxsid"
        self.table("oecd.csv", [self.row(smiles="", dtxsid="DTX1")])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)

    def test_blank_optional_structure_record_is_absence_not_contradiction(self):
        self.missing_chemid_support("X1\t\n")
        self.table("oecd.csv", [self.row()])
        _, kept, _ = self.run_curate()
        self.assertEqual(len(kept), 1)

    def test_explicit_malformed_support_structure_still_blocks(self):
        self.missing_chemid_support("X1\t[invalid\n")
        self.table("oecd.csv", [self.row()])
        _, kept, dispositions = self.run_curate()
        self.assertFalse(kept)
        self.assertIn("unresolved_support_identity", dispositions[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
