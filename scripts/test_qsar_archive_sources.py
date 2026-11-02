"""Archive adapter fixtures: preserved narratives, candidate sets, and integrity."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_archive_sources.py"
spec = importlib.util.spec_from_file_location("qsar_archive_sources", SCRIPT)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsar-archive-fixture-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def zip(self, files):
        path = self.root / "native.zip"
        with zipfile.ZipFile(path, "w") as book:
            for name, text in files.items():
                book.writestr(name, text)
        return path

    def hsdb(self, fields, extra=""):
        return self.zip({"hsdb.xml.20191028": "<hsdb><DOC><DOCNO>30</DOCNO><NameOfSubstance>ETHANOL</NameOfSubstance>"
                        "<CASRegistryNumber>64-17-5</CASRegistryNumber>" + fields + "</DOC>" + extra + "</hsdb>"})

    def test_hsdb_blank_qualifier_stays_unknown_reference_and_sex_preserved(self):
        path = self.hsdb("<ntxv>LD50 Rat oral (male) 822 mg/kg[Author, 2001, p. 605] **PEER REVIEWED** </ntxv>")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        row = next(archive.iter_hsdb(path))
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["relation"], "")
        self.assertEqual(row["normalized_relation"], "unknown")
        self.assertEqual(row["sex"], "male")
        self.assertEqual(row["reference"], "[Author, 2001, p. 605]")
        self.assertEqual(row["native_lineage"]["docno"], "30")
        self.assertEqual(row["cas"], "64-17-5")
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_hsdb_explicit_route_tokens_preserved_without_normalization(self):
        for route in ("oral", "intragastric", "gavage", "ingestion", "po", "PO"):
            row = archive.parse_hsdb_narrative(f"LD50 Rat {route} 20 mg/kg[Study]")
            self.assertEqual(row["status"], "candidate", route)
            self.assertEqual(row["route"], route)
        for route in ("oral/inhalation", "oral and dermal", "gavage/oral", "iv", "p.o."):
            row = archive.parse_hsdb_narrative(f"LD50 Rat {route} 20 mg/kg[Study]")
            self.assertEqual(row["status"], "quarantined", route)
            self.assertEqual(row["value"], "")

    def test_hsdb_inequality_not_turned_into_scalar_equality(self):
        for relation in ("=", ">", "<", ">=", "<="):
            row = archive.parse_hsdb_narrative(f"LD50 Rat oral {relation} 20 mg/kg[Study]")
            self.assertEqual(row["status"], "candidate")
            self.assertEqual(row["relation"], relation)

    def test_hsdb_real_shaped_scalar_formats(self):
        cases = [
            ("10,800 mg/kg", "10800", "mg/kg"),
            ("3,100 mg/kg.", "3100", "mg/kg"),
            ("3000 mg/kg body weight", "3000", "mg/kg body weight"),
            ("1,234,567.5 mg/kg bw", "1234567.5", "mg/kg bw"),
        ]
        for expression, value, unit in cases:
            with self.subTest(expression=expression):
                row = archive.parse_hsdb_narrative(f"LD50 Rat oral {expression}[Author; Study p.28 (1984)] **PEER REVIEWED** ")
                self.assertEqual(row["status"], "candidate")
                self.assertEqual((row["value"], row["unit"]), (value, unit))
                self.assertEqual(row["native_value"], expression.split()[0])
                self.assertEqual(row["normalized_relation"], "unknown")

    def test_hsdb_explicit_oral_gavage_preserves_context(self):
        for spacing in ("", " "):
            row = archive.parse_hsdb_narrative(f"LD50 Rat oral (gavage){spacing}1550 mg/kg[EPA; HPVIS (108-93-0)] **PEER REVIEWED** ")
            self.assertEqual(row["status"], "candidate")
            self.assertEqual((row["route"], row["route_note"], row["sex"]), ("oral", "gavage", ""))

    def test_hsdb_native_qc_citation_marker(self):
        row = archive.parse_hsdb_narrative("LD50 Rat oral 156 mg/kg[Lewis, R.J. Sax; p.960] **QC REVIEWED** ")
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["citation_review_marker"], "**QC REVIEWED**")
        self.assertEqual(row["normalized_relation"], "unknown")

    def test_hsdb_pubmed_tail_preserved_without_route_inference(self):
        tail = '<a href="http://www.ncbi.nlm.nih.gov/pubmed/1008029?dopt=Abstract" target=new>PubMed Abstract</a>'
        text = f"LD50 Rat intragastric 2000 mg/kg[Torkelson TR et al; Am Ind Hyg Assoc J 37: 697 (1976)] **PEER REVIEWED** {tail} "
        path = self.hsdb("<ntxv><![CDATA[" + text + "]]></ntxv>")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        row = next(archive.iter_hsdb(path))
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["route"], "intragastric")
        self.assertEqual(row["citation_tail"], tail)
        self.assertEqual(row["native_record"]["text"], text)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_hsdb_recovery_does_not_accept_ambiguous_or_extra_observations(self):
        cases = [
            "LD50 Rat oral 3,10 mg/kg[Study]", "LD50 Rat oral 1,5 mg/kg[Study]",
            "LD50 Rat oral 10, 800 mg/kg[Study]", "LD50 Rat oral 1,000,20 mg/kg[Study]",
            "LD50 Rat oral 3,100 mg/kg body weight/day[Study]",
            "LD50 Rat oral 3,100 mg/kg. Mouse oral 20 mg/kg[Study]",
            "LD50 Rat oral 3,100 mg/kg[Study][other]",
            "LD50 Rat oral 3,100 mg/kg[as metal 20 mg/kg]",
            "LD50 Rat oral (gavage and dermal) 1550 mg/kg[Study]",
            "LD50 Rat oral 3,100 mg/kg[Study] <a href='x'>20 mg/kg</a>",
            "LD50 Rat oral 3,100 mg/kg[Study] **PEER REVIEWED** extra prose",
            "LD50 Rat oral approximately 3,100 mg/kg[Study]",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(archive.parse_hsdb_narrative(text)["status"], "quarantined")
        row = archive.parse_hsdb_narrative("LD50 Rat oral > 3,100 mg/kg.[Study]")
        self.assertEqual((row["status"], row["relation"], row["value"]), ("candidate", ">", "3100"))

    def test_hsdb_ranges_multiple_endpoints_unknown_units_not_guessed(self):
        cases = ["LD50 Rat oral 10-20 mg/kg[Study]", "LD50 Rat oral 10 mg/kg; LD50 Rat oral 20 mg/kg[Study]",
                 "LD50 Rat oral approximately 20 mg/kg[Study]", "LD50 Rat oral 20 mg/kg/day[Study]",
                 "LD50 Rat oral 20 mL/kg[Study]", "LD(50) Rat oral 20 mg/kg[Study]"]
        for text in cases:
            row = archive.parse_hsdb_narrative(text)
            self.assertEqual(row["status"], "quarantined", text)
            self.assertEqual(row["value"], "")

    def test_hsdb_reference_cannot_swallow_second_observation(self):
        for text in ["LD50 Rat oral 20 mg/kg[Study A] LD50 Mouse oral 50 mg/kg[Study B]",
                     "LD50 Rat oral 20 mg/kg[Study; LD50 Mouse oral 50 mg/kg]"]:
            row = archive.parse_hsdb_narrative(text)
            self.assertEqual(row["status"], "quarantined")
            self.assertEqual(row["value"], "")

    def test_hsdb_parenthetical_is_sex_only_not_censoring_or_other_context(self):
        for note in ["approximately", "mouse 10 mg/kg", "male, 14-day repeated dose", "male and female"]:
            row = archive.parse_hsdb_narrative(f"LD50 Rat oral ({note}) 20 mg/kg[Study]")
            self.assertEqual(row["status"], "quarantined")
            self.assertEqual(row["why"], ["unverified_parenthetical_context"])
        self.assertEqual(archive.parse_hsdb_narrative("LD50 Rat oral (female) 20 mg/kg[Study]")["status"], "candidate")

    def test_hsdb_all_ntxv_and_other_ld50_fields_accounted(self):
        path = self.hsdb("<ntxv>LDLo Mouse oral 50 mg/kg[Study]</ntxv><ntxv>LD50 Mouse oral 10 mg/kg[Study]</ntxv>"
                         "<ntox>LD50 Rat oral 20 mg/kg[Study]</ntox><refs>Unrelated reference</refs>",
                         "<DOC><DOCNO>31</DOCNO><NameOfSubstance>Metadata only</NameOfSubstance></DOC>")
        rows = list(archive.iter_hsdb(path))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["why"], ["no_ld50_mention"])
        self.assertEqual(rows[1]["status"], "quarantined")
        self.assertEqual(rows[2]["native_lineage"]["tag"], "ntox")
        self.assertEqual(rows[3]["why"], ["no_toxicity_candidate_fields"])
        docs = list(archive.iter_hsdb_documents(path))
        self.assertEqual(len(docs), 2)
        self.assertIn("Unrelated reference", docs[0]["native_record"]["serialized_xml"])

    def test_hsdb_multiple_cas_preserved_and_quarantined(self):
        path = self.hsdb("<CASRegistryNumber>67-56-1</CASRegistryNumber><ntxv>LD50 Rat oral 20 mg/kg[Study]</ntxv>")
        row = next(archive.iter_hsdb(path))
        self.assertEqual(row["cas_candidates"], ["64-17-5", "67-56-1"])
        self.assertEqual(row["status"], "quarantined")
        self.assertIn("ambiguous_native_cas", row["why"])

    def chemid(self, smiles="X1\tCCO\n", inchi="X1\tInChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3\tKEY\n"):
        xml = '<file><Chemical id="X1" displayName="Ethanol"><NumberList><CASRegistryNumber>64-17-5<SourceList/></CASRegistryNumber></NumberList><NoteList><Note>LD50 (oral, mice)=956 mg/kg</Note></NoteList></Chemical></file>'
        return self.zip({"chemid.xml": xml, "chemid-SMILES.txt": "SUPERID\tSMILES\n" + smiles,
                         "chemid-InChI.txt": "SUPERID\tInChI\tInChIKey\n" + inchi})

    def test_chemid_join_preserves_candidates_and_measurement_note(self):
        path = self.chemid()
        rows = list(archive.iter_chemid_identity(path))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["cas"], "64-17-5")
        self.assertEqual(row["smiles"], "CCO")
        self.assertEqual(len(row["candidate_structures"]), 2)
        self.assertEqual(len(row["measurement_notes"]), 1)
        self.assertEqual(row["structure_validation"], "chemistry_comparison_required_downstream")

    def test_chemid_conflicting_candidates_not_first_wins(self):
        row = next(archive.iter_chemid_identity(self.chemid(smiles="X1\tCCO\nX1\tCCN\n")))
        self.assertEqual(row["status"], "quarantined")
        self.assertEqual(row["smiles"], "")
        self.assertEqual(len(row["candidate_structures"]), 3)
        self.assertIn("ambiguous_structure_candidates", row["why"])

    def test_chemid_orphan_structure_not_dropped(self):
        rows = list(archive.iter_chemid_identity(self.chemid(smiles="X1\tCCO\nX2\tCCN\n")))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["record_id"], "X2")
        self.assertEqual(rows[1]["why"], ["orphan_structure_superid"])

    def test_hsdb_no_record_cap(self):
        path = self.hsdb("<ntxv>LD50 Rat oral 20 mg/kg[Study]</ntxv>" * 1100)
        rows = list(archive.iter_hsdb(path))
        self.assertEqual(len(rows), 1100)
        self.assertEqual(rows[-1]["native_lineage"]["tag_ordinal"], 1100)

    def test_corrupt_zip_is_integrity_blocker(self):
        path = self.root / "broken.zip"
        path.write_bytes(b"PK\x03\x04truncated")
        with self.assertRaisesRegex(archive.ArchiveSourceError, "integrity failure"):
            list(archive.iter_hsdb(path))


if __name__ == "__main__":
    unittest.main()
