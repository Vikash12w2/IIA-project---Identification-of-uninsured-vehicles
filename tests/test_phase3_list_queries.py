"""Phase 3 list queries: counts over a period, owner age and origin filters, the user's intent, and refining a result."""
import sqlite3
import unittest
from unittest.mock import patch

from test_phase3_context import BASES, FederationCase, anpr_service, context_engine, cs, rto_service

TODAY = "2026-11-05"


class ParseTests(unittest.TestCase):
    def parse(self, text):
        return cs.parse_query(text, today=TODAY)

    def test_a_count_over_a_period_is_read_from_the_query(self):
        p = self.parse("cars with more than 2 violations in the last 3 months")
        self.assertEqual(p["aggregate"], {"event": "violation", "op": ">", "n": 2, "text": "more than 2 violations"})
        self.assertEqual((p["period"]["start"], p["period"]["end"]), ("2026-08-05", "2026-11-05"))
        self.assertEqual(p["types"], ["car"])
        self.assertEqual(p["action"]["kind"], "count")
        self.assertIn("more than 2 traffic violations", p["action"]["text"])

    def test_other_ways_of_saying_counts_and_periods(self):
        cases = {
            "vehicles seen at least 4 times this month": ("capture", ">=", 4, "2026-11-01", "2026-11-05"),
            "three violations or more since August 2026": ("violation", ">=", 3, "2026-08-01", "2026-11-05"),
            "fewer than 2 challans in 2026": ("challan", "<", 2, "2026-01-01", "2026-12-31"),
            "repeat offenders between 1 sept 2026 and 31 oct 2026": ("violation", ">=", 2, "2026-09-01", "2026-10-31"),
            "exactly 1 claim before 2025": ("claim", "=", 1, None, "2024-12-31"),
            "cars with violations yesterday": ("violation", ">=", 1, "2026-11-04", "2026-11-04"),
        }
        for text, (event, op, n, start, end) in cases.items():
            p = self.parse(text)
            self.assertEqual((p["aggregate"]["event"], p["aggregate"]["op"], p["aggregate"]["n"]), (event, op, n), text)
            self.assertEqual((p["period"]["start"], p["period"]["end"]), (start, end), text)

    def test_owner_age_origin_place_and_status(self):
        p = self.parse("uninsured cars from Maharashtra with 2 or more speeding violations in Pune owners under 30")
        self.assertEqual((p["age"]["min"], p["age"]["max"]), (18, 29))
        self.assertEqual(p["origin"], ["maharashtra"])
        self.assertEqual(p["places"], ["pune"])
        self.assertEqual(p["statuses"], ["uninsured"])
        self.assertEqual(p["aggregate"]["detail"], "speeding")
        self.assertEqual((cs.parse_query("owners aged 25-35", today=TODAY)["age"]["min"],
                          cs.parse_query("owners in their 40s", today=TODAY)["age"]["max"]), (25, 49))

    def test_an_age_is_not_mistaken_for_a_count(self):
        p = self.parse("owners under 30 with 2 violations")
        self.assertEqual(p["age"]["max"], 29)
        self.assertEqual(p["aggregate"]["n"], 2)

    def test_ordinary_searches_are_unchanged(self):
        p = self.parse("stolen black Skoda in Pune")
        self.assertFalse(cs.list_mode(p))
        self.assertIn("pune", p["terms"])
        self.assertEqual(p["action"]["kind"], "search")
        self.assertEqual(self.parse("MHI4ZZOOOO")["action"]["kind"], "lookup")


class ListQueryTests(FederationCase):
    """The seeded databases plus a few sightings and violations added here."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(anpr_service.DB_PATH)
        rows = [("MH12AB1234", "2026-10-01 09:00:00", "Speeding"), ("MH12AB1234", "2026-10-12 18:30:00", "Speeding"),
                ("MH12AB1234", "2026-10-20 08:10:00", "Red light jump"), ("KA05MZ9090", "2026-10-02 10:00:00", "Speeding"),
                ("KA05MZ9090", "2026-10-03 10:00:00", "Riding without helmet"), ("MH14ZZ0000", "2026-10-05 11:00:00", "Speeding"),
                ("MH14ZZ0000", "2026-10-06 11:00:00", "Wrong lane"), ("UP16CD4321", "2025-01-01 10:00:00", "Speeding"),
                ("UP16CD4321", "2025-01-02 10:00:00", "Speeding")]
        for plate, ts, kind in rows:
            cur = conn.execute("INSERT INTO vehicle_capture (plate_number, capture_ts, location, vehicle_color, vehicle_type, "
                               "make_model_seen, camera_id) VALUES (?, ?, 'NH48 Toll Plaza, Pune', 'White', 'Car', '', 'CAM-014')",
                               (plate, ts))
            conn.execute("INSERT INTO capture_violation (capture_id, violation_type) VALUES (?, ?)", (cur.lastrowid, kind))
        conn.commit()
        conn.close()
        anpr_service.cleanser.run()

    def run_query(self, text, within=None):
        return context_engine.run(text, post=self.post, devices=BASES, within=within, today=TODAY)

    def test_vehicles_with_more_than_n_violations_in_a_period(self):
        out = self.run_query("vehicles with at least 2 violations in the last 3 months")
        self.assertEqual(out["mode"], "list")
        counts = {r["plate"]: r["count"] for r in out["rows"]}
        # the seeded September violations count too; UP16's two from 2025 are older than 3 months
        self.assertEqual(counts, {"MH12AB1234": 4, "KA05MZ9090": 3, "MH14ZZ0000": 3})
        self.assertEqual(out["rows"][0]["plate"], "MH12AB1234")
        self.assertIn("Speeding x3", out["rows"][0]["what"])
        self.assertEqual(self.run_query("vehicles with more than 3 violations in the last 3 months")["total"], 1)
        self.assertEqual(self.run_query("vehicles with at least 3 violations")["rows"][-1]["plate"], "UP16CD4321")

    def test_the_counting_device_sends_only_the_vehicles_that_pass(self):
        sent = {}
        real = self.post

        def spy(base, path, payload):
            out = real(base, path, payload)
            sent.setdefault(path, []).append((payload, out))
            return out

        out = context_engine.run("vehicles with more than 3 violations in the last 3 months", post=spy, devices=BASES,
                                 today=TODAY)
        anpr = next(o for p, o in sent["/context/aggregate"] if o and o.get("device") == "ANPR")
        self.assertEqual(list(anpr["counts"]), ["MH12AB1234"])                       # only the vehicle that passes
        self.assertGreater(anpr["counted"], 1)
        self.assertEqual([p["plates"] for p, _ in sent["/context/profile"]], [["MH12AB1234"]] * 3)  # RTO, Insurance, Theft
        self.assertEqual(out["total"], 1)
        # "fewer than 2" also passes vehicles with none, so then the device sends every vehicle it counted
        context_engine.run("vehicles with fewer than 2 violations in the last 3 months", post=spy, devices=BASES,
                           today=TODAY)
        payload, _ = sent["/context/aggregate"][-1]
        self.assertIsNone(payload["op"])

    def test_rows_show_where_the_vehicle_comes_from_and_the_owners_age(self):
        out = self.run_query("vehicles with at least 2 violations in the last 3 months")
        row = next(r for r in out["rows"] if r["plate"] == "KA05MZ9090")
        self.assertEqual((row["owner"], row["age"], row["age_group"]), ("Priya Nair", 25, "18-25"))
        self.assertEqual((row["office_code"], row["city"], row["state"]), ("KA05", "Bengaluru", "Karnataka"))
        self.assertIn(("18-25", 1), out["breakdown"]["age"])
        self.assertIn(("Maharashtra", 2), out["breakdown"]["state"])

    def test_documents_follow_the_filtered_result(self):
        first = self.run_query("vehicles with at least 2 violations in the last 3 months")
        narrowed = self.run_query("owners over 60", within=first["result_id"])
        self.assertEqual([r["plate"] for r in narrowed["rows"]], ["MH14ZZ0000"])
        self.assertTrue(narrowed["documents"])
        for doc in narrowed["documents"]:
            self.assertEqual([link["plate"] for link in doc["plates"]], ["MH14ZZ0000"])
        empty = self.run_query("vehicles with more than 100 violations in the last 3 months")
        self.assertEqual(empty["documents"], [])
        rounds = [r["name"] for r in empty["rounds"] if r["sent"]["path"] == "/docs/search"]
        self.assertEqual(rounds, ["background documents"])

    def test_age_and_origin_filters_in_one_query(self):
        out = self.run_query("vehicles with at least 2 violations in the last 3 months from Maharashtra owners over 60")
        self.assertEqual([r["plate"] for r in out["rows"]], ["MH14ZZ0000"])          # born 1958
        self.assertEqual(self.run_query("speeding violations last 3 months owners aged 30-40")["rows"][0]["plate"],
                         "MH12AB1234")

    def test_refining_the_previous_result(self):
        first = self.run_query("vehicles with at least 2 violations in the last 3 months")
        second = self.run_query("from Maharashtra", within=first["result_id"])
        self.assertEqual(sorted(r["plate"] for r in second["rows"]), ["MH12AB1234", "MH14ZZ0000"])
        self.assertEqual(second["within"]["count"], 3)
        third = self.run_query("owners under 40", within=second["result_id"])
        self.assertEqual([r["plate"] for r in third["rows"]], ["MH12AB1234"])         # 32; MH14's owner is 68
        self.assertEqual(self.run_query("Pune", within=first["result_id"])["total"], 2)   # a bare place: where it comes from

    def test_a_status_filter_uses_insurance_and_theft(self):
        out = self.run_query("vehicles with at least 2 violations in the last 3 months")
        insured = {r["plate"]: r["insured"] for r in out["rows"]}
        refined = self.run_query("uninsured", within=out["result_id"])
        self.assertEqual(sorted(r["plate"] for r in refined["rows"]), sorted(p for p, i in insured.items() if not i))

    def test_count_requires_a_reply_even_when_zero_would_pass(self):
        self.down.add("ANPR")
        out = self.run_query("vehicles with fewer than 2 violations in the last 3 months")
        self.assertIsNone(out["total"])
        self.assertFalse(out["rows"] or out["documents"] or out["result_id"])
        self.assertIn("ANPR", out["notes"][0])

    def test_count_is_scoped_to_the_plate_and_accepts_written_dates(self):
        q = "MH12AB1234 with at least 2 violations since August 2026"
        out = self.run_query(q)
        self.assertEqual([(r["plate"], r["count"]) for r in out["rows"]], [("MH12AB1234", 4)])
        self.assertEqual(out["parsed"]["plates"], ["MH12AB1234"])
        self.assertEqual(self.run_query("vehicles with at least 2 violations since August 2026")["total"], 3)

    def test_colour_and_type_apply_to_the_final_list(self):
        white = self.run_query("white cars with owners under 40")
        self.assertTrue(white["rows"])
        self.assertTrue(all(r["color"] == "White" and cs.matches_vehicle_type(r["type"], ["car"]) for r in white["rows"]))
        self.assertFalse(self.run_query("green cars with owners under 40")["rows"])
        zero = self.run_query("cars with exactly 0 violations in the last 3 months")
        self.assertTrue(zero["rows"])
        self.assertTrue(all(r["count"] == 0 and cs.matches_vehicle_type(r["type"], ["car"]) for r in zero["rows"]))

    def test_a_device_that_is_down_is_reported(self):
        self.down.add("RTO")
        out = self.run_query("vehicles with at least 2 violations in the last 3 months owners under 30")
        self.assertTrue(any("RTO is down" in n for n in out["notes"]))
        self.assertEqual(out["total"], 3)                                             # counted, not filtered

    def test_the_page_shows_intent_breakdown_and_refine_box(self):
        import gui_app
        real = context_engine.run
        with patch.object(gui_app.context_engine, "run",
                          side_effect=lambda q, **kw: real(q, post=self.post, devices=BASES, today=TODAY, **kw)):
            page = gui_app.app.test_client().get("/context", query_string={
                "q": "cars with at least 2 violations in the last 3 months"}).get_data(as_text=True)
        for text in ("What you asked for", "Refine these results", "Owner age group", "Registration state",
                     "MH12AB1234", "Priya Nair", "Each document mentions a plate in the filtered results."):
            self.assertIn(text, page)

    def test_a_new_registration_can_carry_the_owners_date_of_birth(self):
        form = {"reg_number": "MH12QQ1111", "owner_name": "Asha Rao", "owner_address": "Pune", "chassis_number": "MAQQ1",
                "engine_number": "ENQQ1", "make": "Honda", "model": "City", "reg_date": "2026-01-10",
                "reg_valid_upto": "2041-01-09", "fitness_valid_upto": "2041-01-09", "owner_dob": "2010-01-01"}
        page = rto_service.app.test_client().post("/add", data=form).get_data(as_text=True)
        self.assertIn("at least 18", page)
        rto_service.app.test_client().post("/add", data=dict(form, owner_dob="2000-05-05"))
        conn = sqlite3.connect(rto_service.DB_PATH)
        try:
            row = conn.execute("SELECT d.date_of_birth FROM driving_licence d JOIN registration r ON r.reg_id = d.reg_id "
                               "WHERE r.reg_number = 'MH12QQ1111'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("2000-05-05",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
