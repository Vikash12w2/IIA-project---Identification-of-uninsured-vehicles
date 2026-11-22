"""Phase 3: related tables with FK-PK, federated context discovery, and search of unstructured documents."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for device in ("device_mot", "device_anpr", "device_insurance", "device_theft", "device_rto", "device_client"):
    sys.path.insert(0, str(ROOT / device))
import anpr_service  # noqa: E402
import insurance_service  # noqa: E402
import theft_service  # noqa: E402
import rto_service  # noqa: E402
import mot_service  # noqa: E402
import context_search as cs  # noqa: E402
import context_engine  # noqa: E402

DEVICES = {"ANPR": anpr_service, "Insurance": insurance_service, "RTO": rto_service, "Theft/Shredding": theft_service,
           "MoT": mot_service}
BASES = {d: f"http://{d.lower().replace('/', '-')}.test" for d in DEVICES}


class FederationCase(unittest.TestCase):
    """The five services on fresh copies of their databases, reaching each other in-process."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.down = set()
        for device, module in DEVICES.items():
            patches = [(module, "DB_PATH", str(Path(tmp.name) / f"{module.__name__}.db"))]
            if hasattr(module, "cleanser"):
                patches += [(module.desk.book, "path", str(Path(tmp.name) / f"{module.__name__}_dictionary.db")),
                            (module.cleanser, "fetch", lambda d, since=0, epoch=None: DEVICES[d].cleanser.evidence(since, epoch)),
                            (module.cleanser, "post", self.to_mot), (module.cleanser, "notify_peers", lambda r: None),
                            (module.cleanser, "peers", {"MoT": "http://mot"})]
            for target, attr, value in patches:
                p = patch.object(target, attr, value)
                p.start()
                self.addCleanup(p.stop)
            module.init_db()
        for _ in range(2):
            for module in DEVICES.values():
                if hasattr(module, "cleanser"):
                    module.cleanser.run()

    @staticmethod
    def to_mot(url, payload):
        return mot_service.app.test_client().post("/reports", json=payload).status_code == 201

    def post(self, base, path, payload):
        device = next(d for d, b in BASES.items() if b == base)
        if device in self.down:
            raise ConnectionError(f"{device} is down")
        reply = DEVICES[device].app.test_client().post(path, json=payload)
        self.assertEqual(reply.status_code, 200, (device, path))
        return reply.get_json()

    def run_query(self, text):
        return context_engine.run(text, post=self.post, devices=BASES)


class SchemaTests(FederationCase):
    def test_every_database_has_four_or_more_tables_joined_by_foreign_keys(self):
        for device, module in DEVICES.items():
            info = module.app.test_client().get("/schema.json").get_json()
            tables = {t["table"]: t for t in info["tables"]}
            self.assertGreaterEqual(len(tables), 4, device)
            fks = [f for t in tables.values() for f in t["foreign_keys"]]
            self.assertGreaterEqual(len(fks), 2, device)
            self.assertTrue(all(t["rows"] for name, t in tables.items() if name not in ("enforcement_action", "challan")), device)
        self.assertIn("Foreign keys", rto_service.app.test_client().get("/schema").get_data(as_text=True))

    def test_related_tables_attach_to_existing_records_by_natural_key(self):
        conn = cs.sqlite3.connect(insurance_service.DB_PATH)
        try:
            row = conn.execute("SELECT p.vehicle_reg_no, c.status FROM claim c JOIN policy p ON p.policy_id = c.policy_id "
                               "WHERE c.claim_no = 'CLM-ICL-26-0977'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("MH14ZZ0000", "Rejected"))

    def test_report_retries_and_deletion_keep_challans_consistent(self):
        client = mot_service.app.test_client()
        body = {"plate_no": "TEST", "event_id": "one-road-event", "generated_on": "2026-08-13 17:05:00",
                "insurance_status": "EXPIRED", "recommended_action": "Issue notice", "risk_flags": "UNINSURED"}
        for has_id in (True, False):
            if not has_id:
                del body["event_id"]
            first = client.post("/reports", json=body).get_json()["report_id"]
            retry = client.post("/reports", json=body).get_json()["report_id"]
            self.assertEqual(first, retry)
            conn = cs.sqlite3.connect(mot_service.DB_PATH)
            try:
                for table in ("challan", "enforcement_action"):
                    self.assertEqual(conn.execute(f"SELECT count(*) FROM {table} WHERE report_id=?", (first,)).fetchone()[0], 1)
                self.assertEqual(client.post("/delete/confirm", data={"plate_no": "TEST", "report_id": first}).status_code, 302)
                self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
            finally:
                conn.close()

    def test_mot_assigns_every_report_to_the_zone_officer_and_fines_uninsured_vehicles(self):
        conn = cs.sqlite3.connect(mot_service.DB_PATH)
        try:
            rows = conn.execute("SELECT r.plate_no, o.name, z.state_code, c.amount FROM mot_report r "
                                "JOIN enforcement_action a ON a.report_id = r.report_id JOIN officer o ON o.officer_id = a.officer_id "
                                "JOIN zone z ON z.zone_id = o.zone_id LEFT JOIN challan c ON c.report_id = r.report_id "
                                "WHERE r.plate_no = 'DL8CAF5555'").fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [("DL8CAF5555", "R. Bhatia", "DL", 2000.0)])


class CoverageTests(unittest.TestCase):
    def test_coverage_uses_both_inclusive_dates_and_any_active_policy(self):
        import insurance_rules
        from datetime import date
        day = date(2026, 11, 21)
        last_day = {"status": "Active", "start_date": "2025-11-22", "end_date": "2026-11-21"}
        future = {"status": "Active", "start_date": "2026-11-22", "end_date": "2027-11-21"}
        self.assertEqual(insurance_rules.evaluate_insurance([future], day)[0], "NOT_INSURED")
        self.assertEqual(insurance_rules.evaluate_insurance([last_day, future, dict(future, status="Cancelled")], day),
                         ("VALID", last_day))
        self.assertEqual(insurance_rules.evaluate_insurance([last_day, future], date(2026, 11, 22)), ("VALID", future))


class QueryTests(unittest.TestCase):
    def test_free_text_is_split_into_plates_colours_makes_and_terms(self):
        q = cs.parse_query("stolen black Skoda in Pune near MH 14 ZZ 0000")
        self.assertEqual((q["plates"], q["colors"], q["makes"]), (["MH14ZZ0000"], ["black"], ["skoda"]))
        self.assertEqual(q["terms"], ["stolen", "pune"])
        self.assertIn("Theft/Shredding", q["intents"])

    def test_a_misread_plate_is_repaired_by_slot(self):
        self.assertEqual(cs.parse_query("MHI4ZZOOOO")["plates"], ["MH14ZZ0000"])
        self.assertEqual(cs.canonical_plate("KA05MZ909O"), "KA05MZ9090")
        self.assertIsNone(cs.canonical_plate("HELLO"))


class ContextMatchTests(unittest.TestCase):
    def test_a_record_that_contradicts_the_query_is_left_out_unless_its_plate_matches(self):
        ctx = cs.Context("ANPR", cs.parse_query("stolen black Skoda in Pune"))
        maruti = ctx.facets("Black", "Car", "Maruti Swift", ["NH48 Toll Plaza, Pune"])
        self.assertEqual(maruti.conflicts, ["model Maruti Swift"])
        self.assertEqual(ctx.match("MH12AB1234", maruti)[0], 0.0)
        skoda = ctx.facets("Black", "Car", "Skoda Rapid", ["Shivaji Nagar, Pune"])
        self.assertGreater(ctx.match("MH14ZZ0000", skoda)[0], 0.4)
        by_plate = cs.Context("ANPR", cs.parse_query("MH12AB1234 black skoda"))
        self.assertEqual(by_plate.match("MH12AB1234", by_plate.facets("White", "Car", "Maruti Swift"))[0], 1.0)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        docs = {"fir.txt": "FIR-2026-4471\nThe black Skoda Rapid MH 14 ZZ 0000 was stolen at Shivaji Nagar, Pune.",
                "update.txt": "Camera update\nCamera read MH14ZZ0O00 on a black sedan near Shivaji Nagar.",
                "news.txt": "Car theft ring\nSedans stolen in Pune around Shivaji Nagar.",
                "noise.txt": "Cricket final\nMumbai won the final at Wankhede."}
        for name, text in docs.items():
            Path(tmp.name, name).write_text(text, encoding="utf-8")
        self.index = cs.DocIndex(tmp.name, "test")

    def test_documents_are_ranked_by_the_context_and_linked_to_plates(self):
        found = self.index.search({"terms": {"stolen": 2, "pune": 2, "shivaji": 1, "nagar": 1, "skoda": 2},
                                   "plates": ["MH14ZZ0000"], "ids": ["FIR-2026-4471"]})
        names = [d["name"] for d in found]
        self.assertEqual(names[0], "fir.txt")
        self.assertIn("update.txt", names)
        self.assertNotIn("noise.txt", names)
        update = next(d for d in found if d["name"] == "update.txt")
        self.assertEqual(update["plates"][0]["how"], "possible misread (1 character different)")
        self.assertIn("MH14ZZ0O00", update["snippet"])


class FederationTests(FederationCase):
    def test_a_description_finds_the_vehicle_in_every_database_and_its_documents(self):
        out = self.run_query("stolen black Skoda in Pune")
        self.assertEqual([r["hop"] for r in out["rounds"]], [1, 2, 3])
        top = out["vehicles"][0]
        self.assertEqual(top["plate"], "MH14ZZ0000")
        self.assertEqual(top["devices"], ["ANPR", "Insurance", "MoT", "RTO", "Theft/Shredding"])
        self.assertEqual([v["plate"] for v in out["vehicles"]], ["MH14ZZ0000"])        # nothing unrelated
        rto = next(e for e in top["entities"] if e["device"] == "RTO")
        self.assertIn("rto_office MH14 RTO Pimpri-Chinchwad, Pune", rto["path"][1])
        insurance = next(e for e in top["entities"] if e["device"] == "Insurance")
        self.assertEqual(insurance["hops"], [2])                                          # found through another database
        titles = [d["title"] for d in out["documents"]]
        self.assertTrue(any("FIR-2026-4471" in t for t in titles))
        misread = next(d for d in out["documents"] if d["name"] == "investigation_update_4471.txt")
        self.assertEqual(misread["plates"][0]["written"], "MH14ZZ0O00")
        self.assertNotIn("Mumbai win the domestic cricket final", titles)

    def test_round_two_carries_the_keys_found_in_round_one(self):
        out = self.run_query("insurance claim rejected")
        keys = out["rounds"][1]["subquery"]["keys"]
        self.assertEqual(sorted(keys["plates"]), ["MH14ZZ0000", "TN09EF7777"])
        self.assertIn("Vikas Kulkarni", keys["names"])
        self.assertEqual(sorted(v["plate"] for v in out["vehicles"]), ["MH14ZZ0000", "TN09EF7777"])

    def test_a_plate_query_reaches_the_joined_tables_of_every_database(self):
        out = self.run_query("TN09EF7777")
        v = out["vehicles"][0]
        paths = {e["device"]: " | ".join(e["path"]) for e in v["entities"]}
        self.assertIn("scrap_center", paths["Theft/Shredding"])
        self.assertIn("camera CAM-303", paths["ANPR"])
        self.assertIn("claim", paths["Insurance"])
        self.assertIn("enforcement_action", paths["MoT"])
        self.assertTrue(any(d["name"] == "anonymous_tip_anna_salai.txt" for d in out["documents"]))

    def test_a_device_that_is_down_is_shown_and_the_others_still_answer(self):
        self.down = {"Insurance"}
        out = self.run_query("MH14ZZ0000")
        status = {d["device"]: d["ok"] for d in out["rounds"][0]["devices"]}
        self.assertFalse(status["Insurance"])
        self.assertTrue(status["RTO"])
        self.assertEqual(out["vehicles"][0]["plate"], "MH14ZZ0000")
        self.assertNotIn("Insurance", out["vehicles"][0]["devices"])


class VehicleViewLookupTests(FederationCase):
    def test_noisy_query_keeps_exact_and_similar_plates_with_device_side_filtering(self):
        import integration_engine as engine
        q = "MH12AB1234"
        rto = DEVICES["RTO"]
        conn = cs.sqlite3.connect(rto.DB_PATH)
        conn.execute("INSERT INTO registration (reg_number, owner_name, chassis_number, engine_number) "
                     "VALUES ('MH12AB1243','Owner','TEST-CHASSIS','TEST-ENGINE')")
        conn.commit()
        conn.close()
        rto.cleanser.run()
        asked = []

        def fetch(source, base, endpoint):
            self.assertTrue(endpoint.startswith("/clean/candidates?"))
            payload = DEVICES[source].app.test_client().get(endpoint).get_json()
            if source == "RTO":
                self.assertEqual({r["reg_number"] for r in payload}, {q, "MH12AB1243"})
            asked.append(source)
            return payload, None

        with patch.object(engine, "_fetch_live_records", side_effect=fetch):
            result = engine.find_plate_candidates(q, max_slips=2)
        self.assertEqual(set(asked), set(BASES) - {"MoT"})
        self.assertEqual({q, "MH12AB1243"} - {c["normalized_plate"] for c in result["candidates"]}, set())


    def test_the_main_page_sends_the_plates_and_gets_back_only_their_records(self):
        import integration_engine
        asked = []

        def fake_post(url, json=None, timeout=None):
            base, path = url.rsplit("/clean/", 1)
            device = {integration_engine.config.ANPR_SERVICE: "ANPR", integration_engine.config.RTO_SERVICE: "RTO",
                      integration_engine.config.INSURANCE_SERVICE: "Insurance",
                      integration_engine.config.THEFT_SERVICE: "Theft/Shredding"}[base]
            asked.append((device, path, tuple(json["plates"])))
            reply = DEVICES[device].app.test_client().post("/clean/" + path, json=json)

            class Response:
                def raise_for_status(self):
                    pass

                def json(self):
                    return reply.get_json()
            return Response()

        with patch.object(integration_engine.requests, "post", side_effect=fake_post),                 patch.object(integration_engine.requests, "get", side_effect=AssertionError("no whole-table read")):
            views = integration_engine.build_views(["MH14ZZ0000", "TN09EF7777"])
        self.assertEqual(sorted(d for d, _p, _plates in asked), ["ANPR", "Insurance", "RTO", "Theft/Shredding"])
        self.assertTrue(all(p == "lookup" and plates == ("MH14ZZ0000", "TN09EF7777") for _d, p, plates in asked))
        self.assertEqual(views["MH14ZZ0000"]["theft_status"], "REPORTED_STOLEN")
        self.assertTrue(views["TN09EF7777"]["shredded"])
        rto = rto_service.app.test_client().post("/clean/lookup", json={"plates": ["MH14ZZ0000"]}).get_json()
        self.assertEqual([r["reg_number"] for r in rto], ["MH14ZZ0000"])


class ClientPageTests(FederationCase):
    def test_the_page_shows_query_federation_context_and_documents(self):
        import gui_app
        real = context_engine.run
        with patch.object(gui_app.context_engine, "run", side_effect=lambda q, **kw: real(q, post=self.post, devices=BASES, **kw)):
            page = gui_app.app.test_client().get("/context", query_string={"q": "MHI4ZZOOOO"}).get_data(as_text=True)
        for heading in ("1. The query", "2. Federation", "3. Context discovered", "4. Unstructured documents"):
            self.assertIn(heading, page)
        self.assertIn("plate MH14ZZ0000", page)
        self.assertIn("<mark>", page)
        self.assertIn("possible misread", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
