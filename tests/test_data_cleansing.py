"""Automatic data cleansing (cleansing.py): plate correction from evidence, duplicates, validity, triggers, MoT."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cleansing  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, 0)


def reg(i, plate, chassis="C1", engine="E1", make="Maruti Suzuki", model="Swift", reg_date="2018-06-15",
        upto="2033-06-14", fitness="2027-06-14", owner="Rohan", address="Pune"):
    return {"reg_id": i, "reg_number": plate, "owner_name": owner, "owner_address": address, "chassis_number": chassis,
            "engine_number": engine, "make": make, "model": model, "reg_date": reg_date, "reg_valid_upto": upto,
            "fitness_valid_upto": fitness}


def cap(i, plate, ts="2026-09-01 08:00:00", color="White", vtype="Car", model="Maruti Swift", camera="CAM-1"):
    return {"capture_id": i, "plate_number": plate, "capture_ts": ts, "location": "Pune", "vehicle_color": color,
            "vehicle_type": vtype, "make_model_seen": model, "camera_id": camera}


def pol(i, plate, number="P-1", insurer="ICICI", start="2026-01-01", end="2027-01-01", status="Active", premium=1000.0):
    return {"policy_id": i, "vehicle_reg_no": plate, "insurer_name": insurer, "policy_number": number,
            "policy_type": "Comprehensive", "start_date": start, "end_date": end, "premium_amount": premium, "status": status}


def theft(i, plate, fir="FIR-1", day="2026-08-01", status="Reported"):
    return {"theft_id": i, "vehicle_no": plate, "report_date": day, "police_station": "PS", "fir_number": fir, "status": status}


def shred(i, plate, cert="SCR-1", day="2026-07-01"):
    return {"shred_id": i, "vehicle_no": plate, "shred_date": day, "scrap_center": "Scrap", "certificate_no": cert}


SWIFT = reg(1, "MH12AB1234")
SWIFT_SEEN = cap(90, "MH12AB1234", ts="2026-08-01 08:00:00")


def clean(name, own, **peers):
    """Clean one table against the given evidence of the other tables."""
    return cleansing.clean_tables([name], {name: own}, peers, now=NOW)[name]


def decision(result, rid):
    return result["decisions"][str(rid)]


def actions(result, rid):
    return [n["action"] for n in decision(result, rid)["notes"]]


class PlateCorrectionTests(unittest.TestCase):
    def test_a_registered_plate_is_never_changed(self):
        r = clean("ANPR", [cap(1, "MH12AB1234", color="Red", model="Skoda Rapid")], RTO=[SWIFT])
        self.assertEqual(decision(r, 1)["plate_to"], "MH12AB1234")
        self.assertNotIn("corrected", actions(r, 1))

    def test_misread_plate_is_corrected_when_the_other_databases_match(self):
        r = clean("ANPR", [cap(1, "MH12A81234"), SWIFT_SEEN], RTO=[SWIFT])
        self.assertEqual(decision(r, 1)["plate_to"], "MH12AB1234")
        text = cleansing.describe(decision(r, 1))
        self.assertIn("corrected to MH12AB1234", text)
        self.assertIn("make / model matches MH12AB1234", text)
        self.assertEqual([c["plate_number"] for c in r["kept"]], ["MH12AB1234", "MH12AB1234"])

    def test_formatting_noise_is_normalised(self):
        r = clean("ANPR", [cap(1, "mh 12 ab-1234")], RTO=[SWIFT])
        self.assertEqual(actions(r, 1), ["normalised"])
        self.assertEqual(r["kept"][0]["plate_number"], "MH12AB1234")

    def test_valid_unregistered_plate_is_corrected_only_with_evidence(self):
        slip = clean("ANPR", [cap(1, "MH12AB1284"), SWIFT_SEEN], RTO=[SWIFT])          # same white Maruti Swift
        self.assertEqual(decision(slip, 1)["plate_to"], "MH12AB1234")
        other = clean("ANPR", [cap(1, "MH12AB1284", model="Honda City"), SWIFT_SEEN], RTO=[SWIFT])
        self.assertEqual(decision(other, 1)["plate_to"], "MH12AB1284")              # another vehicle: kept, flagged
        self.assertIn("flagged", actions(other, 1))
        self.assertIn("not registered in RTO", cleansing.describe(decision(other, 1)))

    def test_two_slips_are_corrected_only_when_colour_type_and_model_all_match(self):
        r = clean("ANPR", [cap(16, "MH12AB1342"), SWIFT_SEEN], RTO=[SWIFT])          # 1234 -> 1342: two slips
        self.assertEqual(decision(r, 16)["plate_to"], "MH12AB1234")
        red = clean("ANPR", [cap(16, "MH12AB1342", color="Red"), SWIFT_SEEN], RTO=[SWIFT])
        self.assertEqual(decision(red, 16)["plate_to"], "MH12AB1342")                # colour differs: another vehicle
        far = clean("ANPR", [cap(16, "MH12AB9994"), SWIFT_SEEN], RTO=[SWIFT])
        self.assertEqual(decision(far, 16)["plate_to"], "MH12AB9994")                # three slips: never

    def test_a_registration_entered_twice_with_a_typo_keeps_the_plate_the_others_know(self):
        typo = reg(13, "MH12AB1342")                                                  # same chassis / engine as #1
        r = clean("RTO", [SWIFT, typo], ANPR=[SWIFT_SEEN, cap(91, "MH12AB1234"), cap(16, "MH12AB1342")],
                  Insurance=[pol(1, "MH12AB1234")])
        self.assertEqual(decision(r, 13)["plate_to"], "MH12AB1234")
        self.assertIn("the other databases hold 3 record(s) of MH12AB1234 and 1 of MH12AB1342",
                      cleansing.describe(decision(r, 13)))
        self.assertEqual([(row["reg_number"], row["raw_ids"]) for row in r["kept"]], [("MH12AB1234", "1,13")])
        self.assertEqual(decision(r, 1)["plate_to"], "MH12AB1234")                    # the right plate stays

    def test_a_new_registration_near_another_plate_is_left_alone(self):
        r = clean("RTO", [SWIFT, reg(2, "MH12AB1243", chassis="C9", engine="E9")], ANPR=[SWIFT_SEEN])
        self.assertEqual(decision(r, 2)["plate_to"], "MH12AB1243")
        self.assertEqual(len(r["kept"]), 2)

    def test_a_shared_policy_number_corrects_a_plate_however_different_it_looks(self):
        r = clean("Insurance", [pol(1, "MH12AB1234", number="ICL-7"), pol(2, "MH12AB9876", number="ICL-7")], RTO=[SWIFT])
        self.assertEqual(decision(r, 2)["plate_to"], "MH12AB1234")
        self.assertEqual(len(r["kept"]), 1)

    def test_a_valid_plate_other_databases_hold_is_a_real_plate(self):
        r = clean("ANPR", [cap(1, "MH12AB1284"), SWIFT_SEEN], RTO=[SWIFT], Insurance=[pol(5, "MH12AB1284")])
        self.assertEqual(decision(r, 1)["plate_to"], "MH12AB1284")

    def test_insurance_plate_is_corrected_by_its_policy_number_and_the_duplicate_merged(self):
        r = clean("Insurance", [pol(1, "MH12AB1234", number="ICL-7"), pol(2, "MH12AB1284", number="ICL-7", premium=900.0)],
                  RTO=[SWIFT])
        self.assertEqual(decision(r, 2)["plate_to"], "MH12AB1234")
        self.assertIn("policy ICL-7 is already recorded for MH12AB1234", cleansing.describe(decision(r, 2)))
        self.assertEqual(len(r["kept"]), 1)
        self.assertEqual((r["kept"][0]["raw_ids"], r["kept"][0]["premium_amount"]), ("1,2", 900.0))   # newer wins
        self.assertEqual(decision(r, 1)["status"], "merged")

    def test_invalid_plate_without_evidence_is_left_out(self):
        r = clean("Insurance", [pol(1, "XX12")], RTO=[SWIFT])
        self.assertEqual(r["kept"], [])
        self.assertIn("could not be matched to a known vehicle", cleansing.describe(decision(r, 1)))

    def test_two_equally_likely_vehicles_are_not_guessed(self):
        twin = reg(2, "MH12AB1235", chassis="C2", engine="E2")
        r = clean("ANPR", [cap(1, "MH12AB123"), SWIFT_SEEN, cap(91, "MH12AB1235")], RTO=[SWIFT, twin])
        self.assertEqual(decision(r, 1)["status"], "removed")
        self.assertIn("could be MH12AB1234 and MH12AB1235", cleansing.describe(decision(r, 1)))

    def test_a_registered_plate_outside_the_standard_format_is_still_real(self):
        delhi = reg(3, "DL8CAF5555", chassis="C3", engine="E3", make="Hyundai", model="Creta")
        r = clean("Insurance", [pol(1, "DL8CAF5555")], RTO=[delhi])
        self.assertEqual(len(r["kept"]), 1)
        self.assertTrue(cleansing.trivial(decision(r, 1)))


class DuplicateTests(unittest.TestCase):
    def test_anpr_double_entries_are_removed_but_sightings_are_kept(self):
        r = clean("ANPR", [cap(1, "MH12AB1234"), cap(2, "MH12AB1234"), cap(3, "MH12AB1234", ts="2026-09-02 08:00:00")],
                  RTO=[SWIFT])
        self.assertEqual([c["capture_id"] for c in r["kept"]], [1, 3])
        self.assertIn("double entry of capture #1", cleansing.describe(decision(r, 2)))

    def test_same_chassis_and_engine_registrations_are_merged_newer_values_win(self):
        r = clean("RTO", [reg(1, "MH12AB1234", owner="Old", address="Pune"), reg(2, "MH12AB1234", owner="New", address=None)])
        self.assertEqual(len(r["kept"]), 1)
        row = r["kept"][0]
        self.assertEqual((row["owner_name"], row["owner_address"], row["raw_ids"]), ("New", "Pune", "1,2"))
        self.assertIn("same chassis and engine number", cleansing.describe(decision(r, 1)))

    def test_same_plate_under_another_chassis_is_flagged_as_a_conflict(self):
        r = clean("RTO", [reg(1, "MH12AB1234"), reg(2, "MH12AB1234", chassis="C9", engine="E9")])
        self.assertEqual(len(r["kept"]), 2)
        self.assertIn("plate also registered under chassis C9", r["kept"][0]["flags"])

    def test_different_policies_of_one_vehicle_are_kept_even_when_they_overlap(self):
        r = clean("Insurance", [pol(1, "MH12AB1234", number="P-1"), pol(2, "MH12AB1234", number="P-2", start="2026-06-01")],
                  RTO=[SWIFT])
        self.assertEqual(len(r["kept"]), 2)

    def test_theft_reports_merge_on_the_same_fir_only(self):
        r = clean("Theft", [theft(1, "MH12AB1234"), theft(2, "MH12AB1234", status="Recovered"),
                            theft(3, "MH12AB1234", fir="FIR-2")], RTO=[SWIFT])
        self.assertEqual([(t["theft_id"], t["status"]) for t in r["kept"]], [(2, "Recovered"), (3, "Reported")])

    def test_a_vehicle_is_shredded_once(self):
        r = clean("Shredding", [shred(1, "MH12AB1234"), shred(2, "MH12AB1234", cert="SCR-9")], RTO=[SWIFT])
        self.assertEqual(len(r["kept"]), 1)
        self.assertIn("shredded only once", cleansing.describe(decision(r, 1)))


class ValidityTests(unittest.TestCase):
    def reasons(self, name, row, **peers):
        r = clean(name, [row], **peers)
        return cleansing.describe(decision(r, row[cleansing.TABLES[name]["id"]]))

    def test_insurance_rules(self):
        self.assertIn("not registered in RTO", self.reasons("Insurance", pol(1, "ZZ99ZZ9999"), RTO=[SWIFT]))
        expired = reg(1, "MH12AB1234", upto="2025-12-31")
        self.assertIn("after the registration expired (2025-12-31)", self.reasons("Insurance", pol(1, "MH12AB1234"), RTO=[expired]))
        self.assertIn("before the vehicle was registered",
                      self.reasons("Insurance", pol(1, "MH12AB1234", start="2010-01-01", end="2011-01-01", status="Lapsed"), RTO=[SWIFT]))
        self.assertIn("after the vehicle was shredded",
                      self.reasons("Insurance", pol(1, "MH12AB1234"), RTO=[SWIFT], Shredding=[shred(1, "MH12AB1234", day="2025-06-01")]))
        self.assertIn("end date 2025-01-01 is before start date", self.reasons("Insurance", pol(1, "MH12AB1234", end="2025-01-01"), RTO=[SWIFT]))
        self.assertIn("premium -5 is negative", self.reasons("Insurance", pol(1, "MH12AB1234", premium=-5), RTO=[SWIFT]))
        self.assertIn("status is Active but the policy ended on 2026-05-31",
                      self.reasons("Insurance", pol(1, "MH12AB1234", start="2025-06-01", end="2026-05-31"), RTO=[SWIFT]))
        self.assertIn("'Paused' is not Active, Lapsed or Cancelled",
                      self.reasons("Insurance", pol(1, "MH12AB1234", status="Paused"), RTO=[SWIFT]))
        self.assertEqual(self.reasons("Insurance", pol(1, "MH12AB1234"), RTO=[SWIFT]), "")

    def test_rto_rules(self):
        self.assertIn("valid upto 2010-01-01 is before the registration date",
                      self.reasons("RTO", reg(1, "MH12AB1234", upto="2010-01-01")))
        self.assertIn("registered (2018-06-15) after the vehicle was shredded (2017-01-01)",
                      self.reasons("RTO", reg(1, "MH12AB1234"), Shredding=[shred(1, "MH12AB1234", day="2017-01-01")]))

    def test_theft_and_shredding_rules(self):
        self.assertIn("after the vehicle was shredded",
                      self.reasons("Theft", theft(1, "MH12AB1234", day="2026-08-01"), RTO=[SWIFT],
                                   Shredding=[shred(1, "MH12AB1234", day="2026-07-01")]))
        self.assertIn("is in the future", self.reasons("Theft", theft(1, "MH12AB1234", day="2027-01-01"), RTO=[SWIFT]))
        self.assertIn("'Lost' is not Reported or Recovered", self.reasons("Theft", theft(1, "MH12AB1234", status="Lost"), RTO=[SWIFT]))
        self.assertIn("before the vehicle was registered", self.reasons("Shredding", shred(1, "MH12AB1234", day="2015-01-01"), RTO=[SWIFT]))
        self.assertIn("not registered in RTO", self.reasons("Shredding", shred(1, "ZZ99ZZ9999"), RTO=[SWIFT]))

    def test_anpr_only_an_impossible_capture_is_left_out(self):
        self.assertIn("is in the future", self.reasons("ANPR", cap(1, "MH12AB1234", ts="2027-01-01 00:00:00"), RTO=[SWIFT]))
        self.assertIn("could not be matched", self.reasons("ANPR", cap(1, "XX12"), RTO=[SWIFT]))

    def test_junk_anpr_details_keep_the_sighting_but_flag_it(self):
        r = clean("ANPR", [cap(1, "MH09DV5346", vtype="sg", model="sgshguiy")], RTO=[SWIFT])
        self.assertEqual(len(r["kept"]), 1)
        flags = r["kept"][0]["flags"]
        self.assertIn("vehicle type 'sg' is not a known vehicle type", flags)
        self.assertIn("'sgshguiy' is not a recognised vehicle make", flags)

    def test_junk_details_are_no_evidence_for_a_correction(self):
        r = clean("ANPR", [cap(1, "MH12AB1284", vtype="sg", model="sg"), SWIFT_SEEN], RTO=[SWIFT])
        self.assertEqual(decision(r, 1)["plate_to"], "MH12AB1284")               # colour alone is not enough

    def test_details_contradicting_rto_keep_the_sighting_flagged_as_a_possible_clone(self):
        r = clean("ANPR", [cap(1, "MH12AB1234", color="Black", model="Skoda Rapid")], RTO=[SWIFT])
        self.assertEqual(len(r["kept"]), 1)
        self.assertIn("details contradict the RTO registration: seen as Skoda Rapid, registered as Maruti Suzuki Swift",
                      r["kept"][0]["flags"])
        view = cleansing.vehicle_view("MH12AB1234", {"ANPR": r["kept"], "RTO": [SWIFT], "Insurance": [pol(1, "MH12AB1234")]},
                                      NOW.date())
        self.assertEqual(view["risk_flags"], ["PLATE_DETAILS_MISMATCH"])
        self.assertEqual(cleansing.recommend_action(view), "VERIFY - possible cloned plate")

    def test_a_shredded_vehicle_seen_on_the_road_is_not_dirty_data(self):
        r = clean("ANPR", [cap(1, "MH12AB1234")], RTO=[SWIFT], Shredding=[shred(1, "MH12AB1234")])
        self.assertEqual(len(r["kept"]), 1)                 # a real sighting: MoT is told, the record stays


class CleanserCase(unittest.TestCase):
    """A real database file for one device; the other devices are faked."""
    device, schema = "Insurance", ("CREATE TABLE policy (policy_id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_reg_no TEXT, "
                                   "insurer_name TEXT, policy_number TEXT, policy_type TEXT, start_date TEXT, end_date TEXT, "
                                   "premium_amount REAL, status TEXT)")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "device.db")
        conn = sqlite3.connect(self.db)
        conn.execute(self.schema)
        conn.commit(); conn.close()
        self.evidence = {"ANPR": [], "RTO": [SWIFT], "Insurance": [], "Theft": [], "Shredding": []}
        self.kept_evidence = None
        self.down, self.posts = set(), []
        self.cleanser = cleansing.Cleanser(self.device, lambda: self.db, peers={"MoT": "http://mot"})
        self.cleanser.fetch = self.fetch
        self.cleanser.post = self.post
        self.cleanser.notify_peers = Mock()

    def post(self, url, payload):
        self.posts.append((url, payload))
        return "MoT" not in self.down

    def fetch(self, device, since=0, epoch=None):
        """The other device answers with its full evidence every time (the cleanser keeps only what changed)."""
        if device in self.down:
            return None
        kept = self.kept_evidence or self.evidence
        tables = {}
        for n in cleansing.DEVICE_TABLES[device]:
            kept_ids = {cleansing.record_id(n, r) for r in kept[n]}
            tables[n] = [{"id": cleansing.record_id(n, r), "row": r, "kept": cleansing.record_id(n, r) in kept_ids}
                         for r in self.evidence[n]]
        return {"built": True, "epoch": "fake", "version": 1, "full": True, "tables": tables}

    def insert(self, sql, *args):
        conn = sqlite3.connect(self.db)
        cur = conn.execute(sql, args)
        conn.commit(); conn.close()
        return cur.lastrowid

    def add_policy(self, plate, number="P-1", start="2026-01-01", end="2027-01-01"):
        return self.insert("INSERT INTO policy (vehicle_reg_no, insurer_name, policy_number, start_date, end_date, status) "
                           "VALUES (?, 'ICICI', ?, ?, ?, 'Active')", plate, number, start, end)

    def query(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class CleanserTests(CleanserCase):
    def test_raw_stays_as_entered_and_the_cleaned_table_is_written_next_to_it(self):
        rid = self.add_policy("MH12A81234")
        message = self.cleanser.after_change("Insurance", rid)
        self.assertIn("corrected to MH12AB1234", message)
        self.assertEqual(self.query("SELECT vehicle_reg_no FROM policy"), [("MH12A81234",)])
        self.assertEqual(self.query("SELECT vehicle_reg_no, raw_ids FROM policy_clean"), [("MH12AB1234", "1")])
        self.assertEqual(self.cleanser.clean_rows("Insurance", "MH12AB1234")[0]["policy_id"], rid)

    def test_nothing_to_clean_gives_no_message(self):
        rid = self.add_policy("MH12AB1234")
        self.assertEqual(self.cleanser.after_change("Insurance", rid), "")

    def test_a_down_device_makes_cleaning_pending_until_it_is_back(self):
        self.cleanser.run()
        self.down = {"RTO"}
        rid = self.add_policy("MH12A81234")
        self.assertIn("RTO is down, so data cleaning is pending", self.cleanser.after_change("Insurance", rid))
        self.assertEqual(self.cleanser.status()["pending"]["records"], {"Insurance": [str(rid)]})
        self.assertIn("RTO is currently down", self.cleanser.home_html())
        self.assertEqual(self.cleanser.clean_rows("Insurance"), [])         # nothing was cleaned
        self.down = set()
        self.assertEqual(self.cleanser.run()["status"], "cleaned")       # the periodic check picks it up
        self.assertIsNone(self.cleanser.status()["pending"])
        self.assertEqual(self.cleanser.home_html(), cleansing.HOME_STYLE)

    def test_it_only_re_cleans_when_an_input_changed_and_only_tells_others_when_its_result_changed(self):
        self.add_policy("MH12AB1234")
        self.assertEqual(self.cleanser.run()["status"], "cleaned")
        self.cleanser.notify_peers.assert_called_once_with(1)
        self.assertEqual(self.cleanser.run()["status"], "unchanged")       # nothing changed: no work, no notification
        self.evidence["ANPR"] = [SWIFT_SEEN]                                # another device changed ...
        self.assertEqual(self.cleanser.run(round_=1), {"status": "cleaned", "changed": False, "mot": {}})
        self.assertEqual(self.cleanser.notify_peers.call_count, 1)        # ... but this result did not: the chain stops

    def test_a_change_elsewhere_re_cleans_this_device(self):
        rid = self.add_policy("MH12AB1234")
        self.cleanser.run()
        self.evidence["RTO"] = [reg(1, "MH12AB1234", upto="2025-12-31")]    # RTO shortens the registration
        self.cleanser.run()
        self.assertEqual(self.cleanser.decision("Insurance", rid)["status"], "removed")
        self.assertIn("registration expired", self.cleanser.report_page())

    def test_edits_show_a_message_only_when_the_cleaned_version_changes(self):
        rid = self.add_policy("MH12AB1234")
        self.cleanser.run()
        before = self.cleanser.decision("Insurance", rid)
        self.insert("UPDATE policy SET premium_amount=5 WHERE policy_id=?", rid)
        self.assertEqual(self.cleanser.after_change("Insurance", rid, before), "")
        before = self.cleanser.decision("Insurance", rid)
        self.insert("UPDATE policy SET end_date='2025-01-01' WHERE policy_id=?", rid)
        self.assertIn("left out of cleaned data: end date", self.cleanser.after_change("Insurance", rid, before))
        before = self.cleanser.decision("Insurance", rid)
        self.insert("UPDATE policy SET end_date='2027-01-01' WHERE policy_id=?", rid)
        self.assertIn("now kept in cleaned data", self.cleanser.after_change("Insurance", rid, before))

    def test_a_change_re_cleans_only_the_records_it_can_affect(self):
        for i in range(300):
            self.add_policy(f"KA{i % 90 + 10:02d}MZ{i:04d}", number=f"P-{i}")
        self.cleanser.run()
        rid = self.add_policy("MH12A81234", number="P-NEW")
        with patch.object(cleansing, "resolve", wraps=cleansing.resolve) as resolve:
            self.cleanser.run()
        self.assertEqual([len(call.args[1]) for call in resolve.call_args_list], [1])     # 1 of 301 records
        self.assertEqual(self.cleanser.decision("Insurance", rid)["plate_to"], "MH12AB1234")

    def test_re_cleaning_replaces_a_cleaned_row_and_never_adds_another(self):
        rid = self.add_policy("MH12AB1234")
        self.cleanser.run()
        for step in range(3):                                  # the same record is re-cleaned again and again
            self.insert("UPDATE policy SET premium_amount=? WHERE policy_id=?", step, rid)
            self.evidence["ANPR"] = [cap(90 + step, "MH12AB1234", ts=f"2026-08-0{step + 1} 08:00:00")]
            self.cleanser.run()
        self.assertEqual(self.query("SELECT policy_id, premium_amount FROM policy_clean"), [(rid, 2.0)])

    def test_an_untyped_cleaned_table_with_piled_up_rows_is_rebuilt(self):
        rid = self.add_policy("MH12AB1234")
        self.cleanser.run()
        self.insert("DROP TABLE policy_clean")
        self.insert("CREATE TABLE policy_clean (policy_id, vehicle_reg_no, insurer_name, policy_number, policy_type, "
                    "start_date, end_date, premium_amount, status, raw_ids, flags)")
        for _ in range(3):
            self.insert("INSERT INTO policy_clean (policy_id, vehicle_reg_no) VALUES (?, 'MH12AB1234')", rid)
        self.cleanser.run()
        self.assertEqual(self.query("SELECT policy_id, vehicle_reg_no, policy_number FROM policy_clean"),
                         [(rid, "MH12AB1234", "P-1")])

    def test_a_command_line_edit_is_picked_up_by_the_next_pass(self):
        rid = self.add_policy("MH12AB1234")
        self.cleanser.run()
        self.insert("UPDATE policy SET vehicle_reg_no='MH12A81234' WHERE policy_id=?", rid)   # straight into the database
        self.assertEqual(self.cleanser.run()["status"], "cleaned")
        self.assertIn("corrected to MH12AB1234", cleansing.describe(self.cleanser.decision("Insurance", rid)))

    def test_other_devices_read_only_what_changed_since_their_last_look(self):
        self.add_policy("MH12AB1234")
        self.cleanser.run()
        first = self.cleanser.evidence()
        self.assertTrue(first["full"])
        self.assertEqual(len(first["tables"]["Insurance"]), 1)
        nothing = self.cleanser.evidence(first["version"], first["epoch"])
        self.assertEqual((nothing["full"], nothing["tables"]["Insurance"]), (False, []))
        self.add_policy("MH12AB1234", number="P-2")
        self.cleanser.run()
        delta = self.cleanser.evidence(first["version"], first["epoch"])
        self.assertEqual([e["row"]["policy_number"] for e in delta["tables"]["Insurance"]], ["P-2"])
        self.assertTrue(self.cleanser.evidence(first["version"], "another epoch")["full"])

    def test_search_lists_the_exact_plate_and_the_similar_ones(self):
        for plate, number in (("MH12AB1234", "P-1"), ("MH12AB1342", "P-2"), ("MH12AB9994", "P-3")):
            self.add_policy(plate, number=number)
        self.cleanser.run()
        page = self.cleanser.search_page("MH12AB1234")
        self.assertIn("exact plate", page)
        self.assertIn("2 characters different", page)
        self.assertNotIn("MH12AB9994", page)

    def test_pages_render(self):
        self.add_policy("MH12A81234")
        self.add_policy("ZZ99ZZ9999", number="P-2")
        self.cleanser.run()
        self.assertIn("MH12AB1234", self.cleanser.corrected_page())
        self.assertIn("not registered in RTO", self.cleanser.report_page())
        self.assertIn("raw_ids", self.cleanser.cleaned_page())
        search = self.cleanser.search_page("MH12AB1234")
        self.assertIn("cleaned from MH12A81234", search)
        self.assertIn("corrected to MH12AB1234", search)


class AnprMotTests(CleanserCase):
    device, schema = "ANPR", ("CREATE TABLE vehicle_capture (capture_id INTEGER PRIMARY KEY AUTOINCREMENT, plate_number TEXT, "
                              "capture_ts TEXT, location TEXT, vehicle_color TEXT, vehicle_type TEXT, make_model_seen TEXT, "
                              "camera_id TEXT)")

    def setUp(self):
        super().setUp()
        self.evidence["Insurance"] = [pol(1, "MH12AB1234")]

    def capture(self, plate, ts="2026-09-01 08:00:00", model="Maruti Swift"):
        return self.insert("INSERT INTO vehicle_capture (plate_number, capture_ts, location, vehicle_color, vehicle_type, "
                           "make_model_seen, camera_id) VALUES (?, ?, 'Pune', 'White', 'Car', ?, 'CAM-1')", plate, ts, model)

    def reports(self):
        return [p for url, p in self.posts if url == "http://mot/reports"]

    def test_a_flagged_vehicle_is_reported_once_per_state(self):
        rid = self.capture("MH12AB1234")
        self.assertEqual(self.cleanser.after_change("ANPR", rid), "")             # insured and registered: nothing to report
        self.evidence["Theft"] = [theft(1, "MH12AB1234")]                          # reported stolen on the Theft device
        self.cleanser.run()
        self.assertEqual([r["risk_flags"] for r in self.reports()], ["STOLEN_VEHICLE_ON_ROAD"])
        rid = self.capture("MH12AB1234", ts="2026-09-02 08:00:00")                 # seen again, same state
        self.assertNotIn("MoT", self.cleanser.after_change("ANPR", rid))
        self.assertEqual(len(self.reports()), 1)

    def test_a_new_capture_of_a_flagged_vehicle_says_it_was_sent(self):
        rid = self.capture("KA01AB1111")
        message = self.cleanser.after_change("ANPR", rid)
        self.assertIn("KA01AB1111 is flagged (UNINSURED, UNREGISTERED_OR_FAKE_PLATE); a report was sent to MoT", message)
        self.assertEqual(self.reports()[0]["recommended_action"], "IMPOUND & VERIFY - notify MoT & RTO")

    def test_a_possible_cloned_plate_is_reported(self):
        self.cleanser.run()
        rid = self.capture("MH12AB1234", model="Skoda Rapid")
        message = self.cleanser.after_change("ANPR", rid)
        self.assertIn("flagged (PLATE_DETAILS_MISMATCH); a report was sent to MoT", message)
        self.assertEqual(self.reports()[-1]["recommended_action"], "VERIFY - possible cloned plate")

    def test_reports_wait_while_mot_is_down(self):
        self.down = {"MoT"}
        rid = self.capture("KA01AB1111")
        self.assertIn("MoT is not reachable", self.cleanser.after_change("ANPR", rid))
        self.down = set()
        self.posts.clear()
        self.assertEqual(self.cleanser.run()["status"], "unchanged")
        self.assertEqual([r["plate_no"] for r in self.reports()], ["KA01AB1111"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
