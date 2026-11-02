"""Native parser fixtures; isolated from the scientific pipeline and real data."""
import gzip
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_native_sources.py"
spec = importlib.util.spec_from_file_location("qsar_native_sources", SCRIPT)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-native-source-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, content):
        path = self.root / "dump.sql.gz"
        with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
            stream.write(content)
        return path

    def test_sql_schema_order_extended_insert_null_and_escaped_text(self):
        path = self.sql("""CREATE TABLE `toxval` (
 `note` text,
 `id` int,
 `value` decimal(10,2),
 PRIMARY KEY (`id`)
);
INSERT INTO `other` VALUES ('not selected');
INSERT INTO `toxval` VALUES ('comma, semi; paren) and \\'quote\\' and \\\\ path',1,NULL),('it''s fine',2,1.2e-3);
INSERT INTO `toxval` (`value`,`id`,`note`) VALUES (4,3,'NULL');
""")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = list(native.iter_sql_table(path))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0]["note"], "comma, semi; paren) and 'quote' and \\ path")
        self.assertIsNone(rows[0][0]["value"])
        self.assertEqual(rows[1][0]["note"], "it's fine")
        self.assertEqual(rows[1][0]["value"], "1.2e-3")
        self.assertEqual(rows[2][0], {"id": "3", "value": "4", "note": "NULL"})
        self.assertEqual(rows[1][1]["tuple"], 2)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_sql_multiline_string_with_semicolon_and_newline(self):
        path = self.sql("CREATE TABLE `toxval` (`id` int, `note` text);\nINSERT INTO `toxval` VALUES (1,'first;\nsecond'),\n(2,'third');\n")
        rows = list(native.iter_sql_table(path))
        self.assertEqual(rows[0][0]["note"], "first;\nsecond")
        self.assertEqual(len(rows), 2)

    def test_sql_unknown_columns_malformed_width_and_functions_fail(self):
        cases = ["INSERT INTO `toxval` VALUES (1,2);", "INSERT INTO `toxval` (`unknown`) VALUES (1);",
                 "INSERT INTO `toxval` VALUES (NOW());", "INSERT INTO `toxval` VALUES ('unterminated);"]
        for case in cases:
            with self.subTest(case=case):
                path = self.sql("CREATE TABLE `toxval` (`id` int);\n" + case + "\n")
                with self.assertRaises(native.NativeSourceError):
                    list(native.iter_sql_table(path))

    def test_sql_requires_create_no_default_equality_invention(self):
        with self.assertRaisesRegex(native.NativeSourceError, "precedes"):
            list(native.iter_sql_table(self.sql("INSERT INTO `toxval` VALUES (1);\n")))
        path = self.sql("CREATE TABLE `toxval` (`id` int, `qualifier` varchar(20) DEFAULT '=');\nINSERT INTO `toxval` (`id`) VALUES (1);\n")
        self.assertIsNone(next(native.iter_sql_table(path))[0]["qualifier"])
        with self.assertRaisesRegex(native.NativeSourceError, "Required SQL columns"):
            list(native.iter_toxval(self.sql("CREATE TABLE `toxval` (`id` int);\n")))

    def test_unsupported_selected_dml_fails_closed_instead_of_zero_rows(self):
        for statement in ["INSERT IGNORE INTO `toxval` VALUES (1);", "INSERT INTO toxval VALUES (1);",
                          "REPLACE INTO `toxval` VALUES (1);", "UPDATE `toxval` SET id=1;",
                          "INSERT\nINTO `toxval` VALUES (1);", "INSERT INTO db.toxval VALUES (1);",
                          "INSERT INTO /*native comment*/ `toxval` VALUES (1);", "TRUNCATE TABLE `toxval`;",
                          "DELETE LOW_PRIORITY FROM `toxval`;"]:
            path = self.sql("CREATE TABLE `toxval` (`id` int);\n" + statement + "\n")
            with self.subTest(statement=statement), self.assertRaisesRegex(native.NativeSourceError, "Unsupported selected-table"):
                list(native.iter_sql_table(path))

    def test_selected_statement_after_other_statement_is_never_skipped(self):
        for prefix in ["INSERT INTO `other` VALUES (0);", "SET @x=1;", "SET\n@x=1;"]:
            path = self.sql("CREATE TABLE `toxval` (`id` int);\n" + prefix + " INSERT INTO `toxval` VALUES (1);\n")
            with self.subTest(prefix=prefix):
                rows = list(native.iter_sql_table(path))
                self.assertEqual([row[0]["id"] for row in rows], ["1"])

    def test_statement_boundaries_respect_quoted_and_comment_semicolons(self):
        path = self.sql("CREATE TABLE `toxval` (`id` int,`note` text);\n"
                        "SET @x='; INSERT INTO `toxval` VALUES (99)'; /* ignored; INSERT INTO `toxval` VALUES (98); */\n"
                        "INSERT INTO `toxval` VALUES (1,'literal /* comment; */ -- not a comment;'); -- ignored; INSERT INTO `toxval` VALUES (97);\n"
                        "/* multiline comment;\n more; */ INSERT INTO `toxval` VALUES (2,'second'); # ignored;\n")
        rows = list(native.iter_sql_table(path))
        self.assertEqual([row[0]["id"] for row in rows], ["1", "2"])
        self.assertEqual(rows[0][0]["note"], "literal /* comment; */ -- not a comment;")

    def toxval_sql(self, qualifier, original):
        columns = ["toxval_units", "toxval_id", "dtxsid", "toxval_type", "toxval_numeric", "toxval_numeric_qualifier",
                   "species_original", "exposure_route", "toxval_units_original", "toxval_numeric_original", "toxval_numeric_qualifier_original"]
        schema = ", ".join(f"`{key}` text" for key in columns)
        values = ["mg/kg", "1", "DTX1", "LD50", "100", qualifier, "Mouse", "oral", "g/kg", "0.1", original]
        row = ",".join("NULL" if v is None else "'" + v + "'" for v in values)
        return self.sql(f"CREATE TABLE `toxval` ({schema});\nINSERT INTO `toxval` VALUES ({row});\n")

    def test_toxval_preserves_current_pair_and_original_qualifier_conflict(self):
        row = next(native.iter_toxval(self.toxval_sql("=", "<")))
        self.assertEqual((row["value"], row["unit"]), ("100", "mg/kg"))
        self.assertEqual((row["original_value"], row["original_unit"]), ("0.1", "g/kg"))
        self.assertEqual(row["status"], "quarantined")
        self.assertIn("current_original_qualifier_conflict", row["why"])
        row = next(native.iter_toxval(self.toxval_sql(None, None)))
        self.assertEqual(row["normalized_relation"], "unknown")
        self.assertEqual(row["relation"], "")

    def test_selected_metadata_schema_aware_join_preserves_quality_and_relationship(self):
        path = self.sql("CREATE TABLE `record_source` (`long_ref` text,`toxval_id` int,`qa_status` text);\n"
                        "INSERT INTO `record_source` VALUES ('Study A',1,'native pending'),('Study B',2,'pass'),('Second A',1,'pass');\n"
                        "CREATE TABLE `toxval_relationship` (`relationship` text,`toxval_id_2` int,`toxval_id_1` int);\n"
                        "INSERT INTO `toxval_relationship` VALUES ('related',2,1),('unselected',4,3);\n")
        refs, links = native.toxval_selected_metadata(path, {"1"})
        self.assertEqual(len(refs["1"]), 2)
        self.assertNotIn("2", refs)
        self.assertEqual(refs["1"][0]["record"]["qa_status"], "native pending")
        self.assertEqual(len(links["1"]), 1)
        self.assertEqual(links["1"][0]["record"]["toxval_id_2"], "2")

    def test_source_chemical_recovery_and_conflict_quarantine(self):
        columns = ["toxval_id", "dtxsid", "toxval_type", "toxval_numeric", "toxval_units", "toxval_numeric_qualifier",
                   "species_original", "exposure_route", "chemical_id"]
        schema = ",".join(f"`{c}` text" for c in columns)
        path = self.sql("CREATE TABLE `source_chemical` (`chemical_id` text,`casrn` text,`dtxsid` text);\n"
                        "INSERT INTO `source_chemical` VALUES ('C1','64-17-5','DTX1');\n"
                        f"CREATE TABLE `toxval` ({schema});\n"
                        "INSERT INTO `toxval` VALUES ('1','-','LD50','10','mg/kg','=','Rat','oral','C1'),"
                        "('2','DTX2','LD50','10','mg/kg','=','Rat','oral','C1');\n")
        rows = list(native.iter_toxval(path, recover_identity=True))
        self.assertEqual(rows[0]["cas"], "64-17-5")
        self.assertEqual(rows[0]["dtxsid"], "DTX1")
        self.assertEqual(rows[0]["native_record"]["dtxsid"], "-")
        self.assertEqual(rows[1]["status"], "quarantined")
        self.assertIn("source_chemical_dtxsid_conflict", rows[1]["why"])

    def workbook(self, studies, duplicate_sub=False, duplicate_ref=False, missing_sub=False, missing_ref=False):
        from openpyxl import Workbook
        book = Workbook()
        book.remove(book.active)
        sub = book.create_sheet("SUB")
        sub.append([None, "Document UUID", "ReferenceSubstance.ReferenceSubstance", "TypeOfSubstance.Composition"])
        if not missing_sub:
            sub.append([0, "S1", "R1", "mono-constituent substance"])
        if duplicate_sub:
            sub.append([1, "S1", "R1", "mono-constituent substance"])
        ref = book.create_sheet("REF_SUB")
        ref.append([None, "Document UUID", "Inventory.CASNumber", "MolecularStructuralInfo.SmilesNotation", "MolecularStructuralInfo.InChl"])
        if not missing_ref:
            ref.append([0, "R1", "64-17-5", "CCO", None])
        if duplicate_ref:
            ref.append([1, "R1", "64-17-5", "CCO", None])
        sheet = book.create_sheet("END_STUDY_REC.HumanHealth")
        columns = list(dict.fromkeys(k for row in studies for k in row))
        sheet.append([None] + columns)
        for i, row in enumerate(studies):
            sheet.append([i] + [row.get(k) for k in columns])
        path = self.root / "oft.xlsx"
        book.save(path)
        book.close()
        return path

    def study(self, lower=10, upper=None, lq=None, uq=None):
        prefix = "ResultsAndDiscussion.EffectLevels."
        return {"Document UUID": "E1", "Parent UUID": "S1", "MaterialsAndMethods.TestAnimals.Species": "mouse",
                "MaterialsAndMethods.AdministrationExposure.RouteOfAdministration": "oral", "DataSource.Reference": "REF-PAPER",
                prefix + "Endpoint": "LD50", prefix + "EffectLevel.lowerValue": lower,
                prefix + "EffectLevel.upperValue": upper, prefix + "EffectLevel.lowerQualifier": lq,
                prefix + "EffectLevel.upperQualifier": uq, prefix + "EffectLevel.Unit": "mg/kg bw"}

    def test_oft_exact_key_joins_and_unknown_modifier(self):
        path = self.workbook([self.study()])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        row = next(native.iter_openfoodtox(path))
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["smiles"], "CCO")
        self.assertEqual(row["unit"], "mg/kg bw")
        self.assertEqual(row["normalized_relation"], "unknown")
        self.assertEqual(row["reference"], "REF-PAPER")
        self.assertEqual(row["native_record"]["__native_index"], 0)
        self.assertEqual(len(row["native_lineage"]["joins"]), 2)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_oft_ranges_upper_only_and_stray_qualifiers_not_point(self):
        records = [self.study(10, 20), self.study(None, 20), self.study(10, None, None, "<"), self.study(10, 10)]
        rows = list(native.iter_openfoodtox(self.workbook(records)))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["status"] == "quarantined" and r["value"] == "" for r in rows))
        self.assertEqual(rows[0]["effect_blocks"][0]["upper_value"], 20)
        self.assertIn("upper_bound_not_point", rows[1]["why"])

    def test_oft_censored_single_bound_preserves_inequality(self):
        row = next(native.iter_openfoodtox(self.workbook([self.study(10, lq=">")])))
        self.assertEqual(row["value"], "10")
        self.assertEqual(row["normalized_relation"], ">")

    def test_oft_other_unit_uses_explicit_native_detail_not_global_alias(self):
        records = []
        for unit in ("mg/kg", "mg/kg bw/day", None):
            row = self.study()
            row["ResultsAndDiscussion.EffectLevels.EffectLevel.Unit"] = "other:"
            row["ResultsAndDiscussion.EffectLevels.EffectLevel.Unit.Other"] = unit
            records.append(row)
        rows = list(native.iter_openfoodtox(self.workbook(records)))
        self.assertEqual(rows[0]["unit"], "mg/kg")
        self.assertEqual(rows[0]["native_unit_parent"], "other:")
        self.assertEqual(rows[1]["unit"], "mg/kg bw/day")
        self.assertIn("missing_native_unit_other_detail", rows[2]["why"])

    def test_oft_ambiguous_and_missing_joins_not_arbitrarily_collapsed(self):
        for flag, reason in [("duplicate_sub", "ambiguous_sub_join"), ("duplicate_ref", "ambiguous_reference_join"),
                             ("missing_sub", "missing_sub_join"), ("missing_ref", "missing_reference_join")]:
            with self.subTest(flag=flag):
                row = next(native.iter_openfoodtox(self.workbook([self.study()], **{flag: True})))
                self.assertEqual(row["status"], "quarantined")
                self.assertIn(reason, row["why"])

    def test_oft_multiple_effect_blocks_all_preserved_no_first_selection(self):
        study = self.study()
        study["ResultsAndDiscussion.EffectLevels.Efflevel.EffectLevel.lowerValue"] = 20
        study["ResultsAndDiscussion.EffectLevels.Efflevel.Endpoint"] = "NOAEL"
        row = next(native.iter_openfoodtox(self.workbook([study])))
        self.assertEqual(len(row["effect_blocks"]), 2)
        self.assertIn("multiple_effect_blocks", row["why"])
        self.assertEqual(row["value"], "")

    def test_oft_no_cap_and_all_native_rows_accounted(self):
        rows = list(native.iter_openfoodtox(self.workbook([self.study()] * 1051)))
        self.assertEqual(len(rows), 1051)
        self.assertEqual(rows[-1]["native_lineage"]["native_row"], 1052)


if __name__ == "__main__":
    unittest.main()
