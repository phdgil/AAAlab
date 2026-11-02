"""Synthetic adjudication evidence: semantic claims still require real review."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


adj, data = module("qsar_adjudication"), module("qsar_data")


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "hsdb.zip"
        self.policy = {"chemistry_policy": "single_component", "relation_policy": "reported_numeric",
                       "replicate_policy": "preserve_flag", "tasks": [{"id": "rat", "species": "Rat", "route": "oral", "endpoint": "LD50"}],
                       "sources": [{"source_id": "hsdb", "adapter": "hsdb"}]}

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, narratives):
        with zipfile.ZipFile(self.source, "w") as book:
            book.writestr("hsdb.xml", "<hsdb><DOC><DOCNO>7</DOCNO><NameOfSubstance>ETHANOL</NameOfSubstance>"
                          "<CASRegistryNumber>64-17-5</CASRegistryNumber>" + "".join("<ntxv><![CDATA[" + n + "]]></ntxv>" for n in narratives) + "</DOC>"
                          "<DOC><DOCNO>8</DOCNO><NameOfSubstance>OTHER</NameOfSubstance></DOC></hsdb>")
        self.entry = {"source_id": "hsdb", "path": str(self.source), "sha256": adj.digest(self.source)}
        adj.export_inventory(self.source, "hsdb", self.root / "inventory")
        self.inventory = list(adj.jsonl(self.root / "inventory/inventory.jsonl"))
        self.fields = [f for document in adj.native_fields(self.source, "hsdb", self.entry["sha256"]) for f in document]
        self.decisions = [{"field_id": f["field_id"], "action": "excluded", "reason": "Synthetic no relevant observation",
                           "evidence": [self.evidence(f, f["native_record"]["text"])], "observations": []} for f in self.inventory]

    def evidence(self, field, quote):
        start = field["native_record"]["text"].index(quote)
        return {"field_id": field["field_id"], "start": start, "end": start + len(quote), "quote": quote}

    def observation(self, field, point, sex=None, unit="mg/kg", relation=None):
        fields = dict.fromkeys(adj.SCIENTIFIC)
        for name, quote in {"endpoint": "LD50", "species": "Rat", "route": "oral", "value": point,
                            "unit": unit, "sex": sex, "relation": relation, "reference": "Study"}.items():
            fields[name] = self.evidence(field, quote) if quote else None
        cas_field = next(f for f in self.fields if f["locator"]["tag"] == "CASRegistryNumber")
        return {"fields": fields, "chemical_attribution": {"cas": self.evidence(cas_field, "64-17-5"),
                    "subject": self.evidence(field, "ETHANOL"), "context": self.evidence(field, field["native_record"]["text"]),
                    "rationale": "Synthetic narrative explicitly names the document chemical"},
                "confidence_interval": None, "dependence": {"status": "shared_study", "evidence": self.evidence(field, "Study")}}

    def document_observation(self, field, point, species="Rat", route="oral", endpoint="LD50", unit="mg/kg"):
        fields = dict.fromkeys(adj.SCIENTIFIC)
        for name, quote in {"endpoint": endpoint, "species": species, "route": route, "value": point, "unit": unit}.items():
            fields[name] = self.evidence(field, quote)
        name = next(f for f in self.fields if f["locator"]["tag"] == "NameOfSubstance")
        cas = next(f for f in self.fields if f["locator"]["tag"] == "CASRegistryNumber")
        return {"fields": fields, "chemical_attribution": {"mode": "document_subject",
                    "subject": self.evidence(name, name["native_record"]["text"]),
                    "cas": self.evidence(cas, cas["native_record"]["text"]),
                    "context": self.evidence(field, field["native_record"]["text"]),
                    "rationale": "Reviewed standard scalar explicitly attributed to this native document subject"},
                "confidence_interval": None, "dependence": {"status": "unknown", "evidence": None}}

    def expand(self, index, observations):
        self.decisions[index].update(action="observations", observations=observations, reason="Reviewed synthetic observations")

    def bind(self, receipt_changes=None):
        decisions = self.root / "decisions.jsonl"
        decisions.write_text("".join(json.dumps(d) + "\n" for d in self.decisions), encoding="utf-8")
        descriptor = lambda p: {"path": str(p.resolve()), "sha256": adj.digest(p)}
        config = {"inventory": descriptor(self.root / "inventory/inventory.jsonl"), "decisions": descriptor(decisions)}
        evidence = self.root / "review.txt"
        evidence.write_text("Synthetic test review evidence, not real scientific approval", encoding="utf-8")
        receipt = {"schema_version": 1, "status": "PASS", "attestation": "local_leader_attested_not_authenticated",
                   "producer_execution_id": "synthetic-producer", "reviewer_execution_id": "synthetic-reviewer",
                   "receipt_writer": "synthetic-leader", "review_evidence": descriptor(evidence),
                   "source_sha256": self.entry["sha256"], "inventory_sha256": config["inventory"]["sha256"],
                   "decisions_sha256": config["decisions"]["sha256"], "config_sha256": adj.stable(config),
                   "policy_sha256": adj.policy_hash(self.policy), "code_hashes": adj.code_hashes(),
                   "review_scope": "all_fields_and_observations_including_exclusions", "findings": []}
        receipt.update(receipt_changes or {})
        receipt_path = self.root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        config["review_receipt"] = descriptor(receipt_path)
        self.config = config
        return config

    def engine(self, bind=True):
        return adj.Adjudication(self.bind() if bind else self.config, self.policy, self.entry, data.UNITS, self.root / "ledger.jsonl")

    def test_point_ci_is_not_bounds_and_full_field_accounting(self):
        self.fixture(["ETHANOL LD50 Rat oral 2900 (95% C.I. 2300-3500) mg/kg Study"])
        field = self.inventory[0]
        observation = self.observation(field, "2900")
        observation["confidence_interval"] = {k: self.evidence(field, q) for k, q in
                                               (("lower", "2300"), ("upper", "3500"), ("context", "95% C.I. 2300-3500"))}
        self.expand(0, [observation])
        engine = self.engine()
        rows = list(engine.rows())
        self.assertEqual([r["value"] for r in rows], ["2900"])
        self.assertEqual(rows[0]["relation"], "")
        self.assertEqual(engine.summary["fields"], 2)
        self.assertEqual(engine.summary["observations"], 1)
        self.assertTrue(engine.summary["complete_narrative_accounting"])

    def test_sex_expansion_and_jsonl_reorder_stable(self):
        self.fixture(["ETHANOL LD50 Rat oral male 10,700 and female 9,300 mg/kg Study"])
        self.expand(0, [self.observation(self.inventory[0], "10,700", "male"), self.observation(self.inventory[0], "9,300", "female")])
        rows = list(self.engine().rows())
        self.assertEqual([(r["value"], r["sex"]) for r in rows], [("10700", "male"), ("9300", "female")])
        self.decisions.reverse()
        self.decisions[1]["observations"].reverse()
        reordered = list(self.engine().rows())
        self.assertEqual({r["record_id"] for r in rows}, {r["record_id"] for r in reordered})

    def test_source_unit_preserved_with_reviewed_normalization(self):
        self.fixture(["ETHANOL LD50 Rat oral > 20 MG/KG Study"])
        observation = self.observation(self.inventory[0], "20", unit="MG/KG", relation=">")
        observation["unit_normalization"] = {"unit": "mg/kg", "evidence": self.evidence(self.inventory[0], "MG/KG"), "rationale": "Reviewed source spelling"}
        self.expand(0, [observation])
        row = next(self.engine().rows())
        self.assertEqual((row["unit"], row["adjudicated_unit"], row["relation"]), ("MG/KG", "mg/kg", ">"))

    def test_shared_point_distinct_explicit_populations_and_cosmetic_duplicate(self):
        self.fixture(["ETHANOL LD50 Rat oral male and female 20 mg/kg Study"])
        male = self.observation(self.inventory[0], "20", "male")
        female = self.observation(self.inventory[0], "20", "female")
        self.expand(0, [male, female])
        rows = list(self.engine().rows())
        self.assertEqual(len({r["record_id"] for r in rows}), 2)
        cosmetic = copy.deepcopy(male)
        cosmetic["chemical_attribution"]["rationale"] += " reviewed again"
        cosmetic["dependence"]["status"] = "unknown"
        self.expand(0, [male, cosmetic])
        with self.assertRaises(adj.AdjudicationError):
            list(self.engine().rows())

    def test_document_subject_requires_named_mode_and_correct_context(self):
        self.fixture(["LD50 Rat oral 20 mg/kg[Study]"])
        good = self.document_observation(self.inventory[0], "20")
        self.expand(0, [good])
        self.assertEqual(next(self.engine().rows())["cas"], "64-17-5")
        changes = []
        bad = copy.deepcopy(good); bad["chemical_attribution"].pop("mode"); changes.append(bad)
        bad = copy.deepcopy(good); bad["chemical_attribution"].pop("context"); changes.append(bad)
        bad = copy.deepcopy(good); bad["chemical_attribution"]["context"] = bad["chemical_attribution"]["subject"]; changes.append(bad)
        bad = copy.deepcopy(good); bad["chemical_attribution"]["context"] = self.evidence(self.inventory[0], "LD50"); changes.append(bad)
        bad = copy.deepcopy(good); bad["chemical_attribution"]["subject"] = self.evidence(self.inventory[0], "Rat"); changes.append(bad)
        bad = copy.deepcopy(good); bad["chemical_attribution"]["rationale"] = "  "; changes.append(bad)
        for changed in changes:
            self.expand(0, [changed])
            with self.assertRaises(adj.AdjudicationError):
                list(self.engine().rows())

    def test_document_subject_rejects_other_narrative_cas(self):
        self.fixture(["LD50 Rat oral 20 mg/kg for methanol CAS 67-56-1 [Study]"])
        self.expand(0, [self.document_observation(self.inventory[0], "20")])
        with self.assertRaisesRegex(adj.AdjudicationError, "CAS conflict"):
            list(self.engine().rows())

    def test_partial_numeric_tokens_rejected_and_adjacent_unit_allowed(self):
        self.fixture(["LD50 Rat oral 5mg/kg; alternatives 1,005 0.5 2e+5 2e-5 25 [Study]"])
        field = self.inventory[0]
        text = field["native_record"]["text"]
        valid = self.document_observation(field, "5")
        valid["fields"]["value"] = self.evidence(field, "5mg/kg")
        valid["fields"]["value"].update(end=text.index("5mg/kg") + 1, quote="5")
        self.expand(0, [valid])
        self.assertEqual(next(self.engine().rows())["value"], "5")
        for token, offset, length in (("LD50", 2, 1), ("LD50", 2, 2), ("1,005", 2, 3),
                                      ("1,005", 0, 1), ("0.5", 2, 1), ("0.5", 0, 1),
                                      ("2e+5", 3, 1), ("2e-5", 3, 1), ("2e+5", 0, 1), ("25", 1, 1)):
            bad = copy.deepcopy(valid)
            start = text.index(token) + offset
            bad["fields"]["value"] = {"field_id": field["field_id"], "start": start,
                                         "end": start + length, "quote": text[start:start + length]}
            self.expand(0, [bad])
            with self.subTest(token=token, offset=offset), self.assertRaises(adj.AdjudicationError):
                list(self.engine().rows())

    def test_negative_range_and_composite_power_components_are_not_scalar_points(self):
        expressions = ["-5", "\u22125", "- 5", "2-5", "2\u20105", "2\u20115", "2\u20125", "2\u20135", "2\u20145", "2\u22125",
                       "2 x 10^5", "2 \u00d7 10^5", "2 x 10^\u22125", "2 * 10**5",
                       "2e\u22125", "2e\u20135", "2e\u20145", "2e\u20105", "2e\u20115", "2e\u20125"]
        self.fixture(["LD50 Rat oral " + value + " mg/kg[Study]" for value in expressions] +
                     ["LD50 Rat oral 2e-5 mg/kg[Study]", "LD50 Rat oral 5mg/kg[Study]"])
        for index, expression in enumerate(expressions):
            field = self.inventory[index]
            base = self.document_observation(field, "5")
            start = field["native_record"]["text"].index(expression)
            import re
            for token in re.finditer(r"\d+", expression):
                bad = copy.deepcopy(base)
                bad["fields"]["value"] = {"field_id": field["field_id"], "start": start + token.start(),
                                             "end": start + token.end(), "quote": token.group()}
                self.expand(index, [bad])
                with self.subTest(expression=expression, token=token.group()), self.assertRaises(adj.AdjudicationError):
                    list(self.engine().rows())
            self.decisions[index].update(action="excluded", observations=[])
        for index, point in ((len(expressions), "2e-5"), (len(expressions) + 1, "5")):
            field = self.inventory[index]
            good = self.document_observation(field, point)
            # Avoid the first '5' in LD50; choose the actual complete dose token.
            start = field["native_record"]["text"].index("oral ") + len("oral ")
            good["fields"]["value"] = {"field_id": field["field_id"], "start": start, "end": start + len(point), "quote": point}
            self.expand(index, [good])
        self.assertEqual([r["value"] for r in self.engine().rows()], ["2e-5", "5"])
        # Unicode full-value spelling remains explicitly unsupported; sign
        # normalization is lexical context only, never an absolute-value repair.
        index = expressions.index("2e\u22125")
        self.expand(index, [self.document_observation(self.inventory[index], "2e\u22125")])
        with self.assertRaisesRegex(adj.AdjudicationError, "Point must be a scalar"):
            list(self.engine().rows())

    def test_explicit_terminal_bibliography_region_preserves_foreign_body_cas_guard(self):
        reference = "[Agency; report for comparator (67-56-1) and ethanol (64-17-5). 2006]"
        self.fixture(["LD50 Rat oral 689 mg/kg" + reference + " **PEER REVIEWED**",
                      "LD50 Rat oral 752 mg/kg for comparator 67-56-1" + reference])
        good = self.document_observation(self.inventory[0], "689")
        evidence = self.evidence(self.inventory[0], reference)
        good["fields"]["reference"] = evidence
        good["chemical_attribution"]["bibliographic_reference"] = {"evidence": evidence,
            "rationale": "Reviewed report title lists multiple chemicals; the body is attributed to this document subject"}
        self.expand(0, [good])
        self.assertEqual(next(self.engine().rows())["value"], "689")
        bad = self.document_observation(self.inventory[1], "752")
        evidence = self.evidence(self.inventory[1], reference)
        bad["fields"]["reference"] = evidence
        bad["chemical_attribution"]["bibliographic_reference"] = {"evidence": evidence, "rationale": "Reviewed citation"}
        self.expand(1, [bad])
        with self.assertRaisesRegex(adj.AdjudicationError, "CAS conflict"):
            list(self.engine().rows())

    def test_bibliographic_region_cannot_hide_measurements_or_arbitrary_prose(self):
        reference = "[Agency; comparator CAS 67-56-1]"
        self.fixture(["LD50 Rat oral 20 mg/kg" + reference + " subsequent comparator statement",
                      "[LD50 Rat oral 30 mg/kg comparator 67-56-1]",
                      "LD50 Rat oral 40 mg/kg" + reference])
        for index, point in ((0, "20"), (1, "30"), (2, "40")):
            field = self.inventory[index]
            bad = self.document_observation(field, point)
            quote = field["native_record"]["text"] if index == 1 else reference
            evidence = self.evidence(field, quote)
            bad["fields"]["reference"] = evidence
            bad["chemical_attribution"]["bibliographic_reference"] = {"evidence": evidence, "rationale": "Claimed reference"}
            if index == 2:
                bad["chemical_attribution"]["bibliographic_reference"]["evidence"] = self.evidence(self.inventory[0], reference)
            self.expand(index, [bad])
            with self.assertRaises(adj.AdjudicationError):
                list(self.engine().rows())
            self.decisions[index].update(action="excluded", observations=[])

    def test_field_normalization_is_projection_with_stable_ids_and_exact_policy_targets(self):
        self.fixture(["LD(50) Rats p.o. 20 MG/KG[Study]"])
        self.policy["tasks"][0]["species"] = "rat"
        good = self.document_observation(self.inventory[0], "20", "Rats", "p.o.", "LD(50)", "MG/KG")
        self.expand(0, [good])
        original = next(self.engine().rows())
        good["field_normalizations"] = {name: {"value": target, "evidence": good["fields"][name], "rationale": "Reviewed source notation"}
                                        for name, target in (("species", "rat"), ("route", "oral"), ("endpoint", "LD50"))}
        good["unit_normalization"] = {"unit": "mg/kg", "evidence": good["fields"]["unit"], "rationale": "Reviewed source unit spelling"}
        with self.assertRaises(adj.AdjudicationError):
            adj.observation(good, self.inventory[0], {f["field_id"]: f for f in self.fields}, data.UNITS)
        self.expand(0, [good])
        projected = next(self.engine().rows())
        self.assertEqual(projected["record_id"], original["record_id"])
        self.decisions.reverse()
        self.assertEqual(next(self.engine().rows())["record_id"], projected["record_id"])
        self.assertEqual((projected["species"], projected["route"], projected["endpoint"]), ("Rats", "p.o.", "LD(50)"))
        self.assertEqual(projected["adjudicated_fields"], {"species": "rat", "route": "oral", "endpoint": "LD50"})
        support = self.root / "support.csv"
        support.write_text("cas,smiles\n64-17-5,CCO\n", encoding="utf-8")
        self.policy["identity_sources"] = [{"source_id": "support", "columns": {"cas": "cas", "smiles": "smiles"}}]
        self.policy["sources"][0]["narrative_adjudication"] = self.bind()
        contract, registry = self.root / "contract.json", self.root / "registry.json"
        data.dump(contract, {"data_curation": self.policy})
        data.dump(registry, {"files": [self.entry, {"source_id": "support", "path": str(support), "sha256": adj.digest(support)}]})
        data.curate(contract, registry, self.root / "out")
        with (self.root / "out/curated.csv").open(encoding="utf-8", newline="") as stream:
            retained = list(csv.DictReader(stream))
        self.assertEqual(len(retained), 1)
        self.assertEqual(tuple(retained[0][k] for k in ("raw_species", "raw_route", "raw_endpoint", "raw_unit")),
                         ("Rats", "p.o.", "LD(50)", "MG/KG"))
        self.assertEqual(tuple(retained[0][k] for k in ("species", "route", "endpoint")), ("rat", "oral", "LD50"))

    def test_field_normalization_rejects_overrides_outside_policy_and_wrong_evidence(self):
        self.fixture(["LD50 Rats oral 20 mg/kg[Study]"])
        good = self.document_observation(self.inventory[0], "20", "Rats")
        rule = {"value": "Rat", "evidence": good["fields"]["species"], "rationale": "Reviewed plural"}
        normalizations = [{name: rule} for name in ("value", "relation", "cas", "unit")]
        normalizations.extend(({"species": dict(rule, value="mouse")}, {"species": dict(rule, value="rat")},
                               {"species": dict(rule, rationale=" ")},
                               {"species": dict(rule, evidence=good["fields"]["route"])}))
        for normalization in normalizations:
            self.expand(0, [dict(good, field_normalizations=normalization)])
            with self.assertRaises(adj.AdjudicationError):
                list(self.engine().rows())

    def test_missing_unknown_duplicate_decisions_rejected(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        original = copy.deepcopy(self.decisions)
        for decisions in (original[:-1], original + [original[0]], original + [dict(original[0], field_id="unknown")]):
            self.decisions = decisions
            with self.assertRaises(adj.AdjudicationError):
                self.engine()

    def test_invalid_quote_span_free_identity_and_duplicate_observation_rejected(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        good = self.observation(self.inventory[0], "20")
        mutations = []
        bad = copy.deepcopy(good); bad["fields"]["value"]["quote"] = "21"; mutations.append([bad])
        bad = copy.deepcopy(good); bad["chemical_attribution"]["cas"]["quote"] = "67-56-1"; mutations.append([bad])
        bad = copy.deepcopy(good); bad["smiles"] = "CCO"; mutations.append([bad])
        bad = copy.deepcopy(good); bad["fields"]["value"]["start"] = -1; mutations.append([bad])
        mutations.append([good, good])
        for observations in mutations:
            self.expand(0, observations)
            with self.assertRaises(adj.AdjudicationError):
                list(self.engine().rows())

    def test_review_and_policy_hashes_fail_closed(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        for changes in ({"reviewer_execution_id": "synthetic-producer"}, {"policy_sha256": "stale"},
                        {"code_hashes": {}}, {"config_sha256": "stale"}, {"findings": ["unresolved"]},
                        {"producer_execution_id": True}, {"reviewer_execution_id": "  "},
                        {"receipt_writer": []}, {"reviewer_execution_id": " synthetic-producer "}):
            self.bind(changes)
            with self.assertRaises(adj.AdjudicationError):
                self.engine(bind=False)

    def test_source_inventory_mutation_and_unused_fields_rejected(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        self.bind()
        with self.source.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(adj.AdjudicationError):
            list(self.engine(bind=False).rows())

    def test_rehashed_inventory_tampering_and_duplicate_json_keys_rejected(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        path = self.root / "inventory/inventory.jsonl"
        records = list(adj.jsonl(path))
        records[0]["native_record"]["text"] += " changed"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        with self.assertRaises(adj.AdjudicationError):
            list(self.engine().rows())
        path.write_text('{"field_id":"a","field_id":"b"}\n', encoding="utf-8")
        with self.assertRaises(adj.AdjudicationError):
            list(adj.jsonl(path))

    def test_decision_mutation_after_start_fails_end_check(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        self.expand(0, [self.observation(self.inventory[0], "20")])
        engine = self.engine()
        rows = engine.rows()
        next(rows)
        with (self.root / "decisions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{}\n")
        with self.assertRaises(adj.AdjudicationError):
            list(rows)

    def test_unresolved_is_not_complete_and_parser_cannot_double_emit(self):
        self.fixture(["LD50 Rat oral 20 mg/kg[Study]"])
        self.decisions[0]["action"] = "parser"
        rows = list(self.engine().rows())
        self.assertEqual(len(rows), 1)
        self.decisions[0]["action"] = "unresolved"
        engine = self.engine()
        self.assertEqual(list(engine.rows()), [])
        self.assertFalse(engine.summary["complete_narrative_accounting"])
        self.decisions[0].update(action="parser", observations=[{}])
        with self.assertRaises(adj.AdjudicationError):
            list(self.engine().rows())

    def test_interval_cannot_be_point(self):
        self.fixture(["ETHANOL LD50 Rat oral 2300-3500 mg/kg Study"])
        self.expand(0, [self.observation(self.inventory[0], "2300-3500")])
        with self.assertRaises(adj.AdjudicationError):
            list(self.engine().rows())

    def test_curation_still_runs_identity_and_censoring_gates(self):
        self.fixture(["ETHANOL LD50 Rat oral > 20 mg/kg Study", "ETHANOL LD50 Rat oral 30 mg/kg Study"])
        self.expand(0, [self.observation(self.inventory[0], "20", relation=">")])
        self.expand(1, [self.observation(self.inventory[1], "30")])
        self.policy["sources"][0]["narrative_adjudication"] = self.bind()
        contract, registry = self.root / "contract.json", self.root / "registry.json"
        data.dump(contract, {"data_curation": self.policy})
        data.dump(registry, {"files": [self.entry]})
        # Gate behavior does not require RDKit: inject an explicit conflicting
        # identity outcome and verify both rows reach the existing resolver.
        with patch.object(data, "resolve_identity", return_value=(None, "identity_conflict", [])) as resolver:
            result = data.curate(contract, registry, self.root / "out")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(result["coverage"]["output_rows"], 0)
        with (self.root / "out/dispositions.csv").open(encoding="utf-8", newline="") as stream:
            dispositions = list(csv.DictReader(stream))
        self.assertIn("nonexact_or_unknown_relation", dispositions[0]["reasons"])
        self.assertTrue(all("identity_conflict" in d["reasons"] for d in dispositions))
        self.assertEqual(result["coverage"]["narrative_accounting"]["hsdb"]["observations"], 2)

    def test_real_identity_supported_rows_retain_sex_and_source_units(self):
        self.fixture(["ETHANOL LD50 Rat oral male 10,700 and female 9,300 MG/KG Study"])
        observations = [self.observation(self.inventory[0], value, sex, "MG/KG")
                        for value, sex in (("10,700", "male"), ("9,300", "female"))]
        for observation in observations:
            observation["unit_normalization"] = {"unit": "mg/kg", "evidence": self.evidence(self.inventory[0], "MG/KG"),
                                                  "rationale": "Source-specific spelling reviewed"}
        self.expand(0, observations)
        support = self.root / "support.csv"
        support.write_text("cas,smiles\n64-17-5,CCO\n", encoding="utf-8")
        self.policy["identity_sources"] = [{"source_id": "support", "columns": {"cas": "cas", "smiles": "smiles"}}]
        config = self.bind()
        contract, registry = self.root / "contract.json", self.root / "registry.json"
        data.dump(contract, {"data_curation": self.policy})
        original_contract_hash = adj.digest(contract)
        external = self.root / "adjudication-config.json"
        data.dump(external, {"hsdb": config})
        data.dump(registry, {"files": [self.entry, {"source_id": "support", "path": str(support), "sha256": adj.digest(support)}]})
        result = data.curate(contract, registry, self.root / "out", external)
        with (self.root / "out/curated.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r["sex"], float(r["target_mg_kg"])) for r in rows], [("male", 10700), ("female", 9300)])
        self.assertTrue(all(r["raw_unit"] == "MG/KG" and r["normalized_unit"] == "mg/kg" for r in rows))
        self.assertEqual(result["coverage"]["input_rows"], 2)
        self.assertEqual(result["coverage"]["narrative_accounting"]["hsdb"]["fields"], 2)
        self.assertEqual(result["coverage"]["contract_sha256"], original_contract_hash)
        self.assertEqual(adj.digest(contract), original_contract_hash)
        self.assertEqual(result["coverage"]["external_adjudication_config"]["sha256"], adj.digest(external))

    def test_external_map_rejects_unknown_sources_and_policy_overrides(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        config = self.bind()
        contract, registry = self.root / "contract.json", self.root / "registry.json"
        data.dump(contract, {"data_curation": self.policy})
        data.dump(registry, {"files": [self.entry]})
        external = self.root / "adjudication-config.json"
        for mapping in ({"unknown": config}, {"relation_policy": "reported_numeric"},
                        {"hsdb": dict(config, chemistry_policy="largest_organic")}):
            data.dump(external, mapping)
            with self.assertRaises(ValueError):
                data.curate(contract, registry, self.root / "out", external)

    def test_external_map_mutation_fails_and_inline_collision_rejected(self):
        self.fixture(["ETHANOL LD50 Rat oral 20 mg/kg Study"])
        self.expand(0, [self.observation(self.inventory[0], "20")])
        config = self.bind()
        contract, registry = self.root / "contract.json", self.root / "registry.json"
        data.dump(contract, {"data_curation": self.policy})
        data.dump(registry, {"files": [self.entry]})
        external = self.root / "adjudication-config.json"
        data.dump(external, {"hsdb": config})
        def mutate(*args):
            external.write_text("{}", encoding="utf-8")
            return None, "identity_conflict", []
        with patch.object(data, "resolve_identity", side_effect=mutate):
            with self.assertRaisesRegex(data.DataError, "External adjudication configuration changed"):
                data.curate(contract, registry, self.root / "out", external)
        self.policy["sources"][0]["narrative_adjudication"] = config
        data.dump(contract, {"data_curation": self.policy})
        data.dump(external, {"hsdb": config})
        with self.assertRaisesRegex(data.DataError, "no inline adjudication"):
            data.curate(contract, registry, self.root / "out2", external)


if __name__ == "__main__":
    unittest.main()
