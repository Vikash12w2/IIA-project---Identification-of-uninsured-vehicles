"""Per-database plate dictionary (plate_entry_client.PlateDesk + plate_book.PlateBook)."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plate_entry_client as entry  # noqa: E402

SCHEMA = """
CREATE TABLE vehicle_capture (capture_id INTEGER PRIMARY KEY AUTOINCREMENT, plate_number TEXT, vehicle_color TEXT,
                              vehicle_type TEXT, make_model_seen TEXT);
CREATE TABLE policy (policy_id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_reg_no TEXT);
"""


class DeskCase(unittest.TestCase):
    """A throw-away source database + its own dictionary file, seeded like a service start."""
    anpr = [("MH12AB1234", "White", "Car", "Maruti Swift"), ("KA05MZ9090", "Red", "Bike", "Royal Enfield")]
    policies = ["MH12AB1234", "GJ01XY0011", "GJ01XY0021"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "source.db")
        self.book_path = str(Path(tmp.name) / "plate_dictionary.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO vehicle_capture (plate_number, vehicle_color, vehicle_type, make_model_seen) "
                         "VALUES (?, ?, ?, ?)", self.anpr)
        conn.executemany("INSERT INTO policy (vehicle_reg_no) VALUES (?)", [(p,) for p in self.policies])
        conn.commit(); conn.close()
        self.desk = entry.PlateDesk(self.book_path, [
            entry.Source("ANPR", lambda: self.db, "vehicle_capture", "capture_id", "plate_number", "capture"),
            entry.Source("Insurance", lambda: self.db, "policy", "policy_id", "vehicle_reg_no", "policy"),
        ])
        self.desk.start()


class DictionaryTests(DeskCase):
    def test_first_start_seeds_the_existing_plates_with_their_noisy_variants(self):
        self.assertEqual(self.desk.book.lookup("Insurance", "MH12A81234"), {"MH12AB1234": "generated"})
        self.assertEqual(self.desk.book.lookup("Insurance", "MH12AB123"), {"MH12AB1234": "generated"})

    def test_only_format_breaking_variants_are_seeded(self):
        self.assertEqual(self.desk.book.lookup("Insurance", "MH12AB1243"), {})        # a valid plate is another vehicle

    def test_each_database_has_its_own_dictionary(self):
        self.assertEqual(self.desk.book.lookup("ANPR", "GJ01XY0O11"), {})            # GJ01XY0011 is only in Insurance
        self.assertEqual(self.desk.book.lookup("Insurance", "GJ01XY0O11"), {"GJ01XY0011": "generated"})

    def test_plates_added_later_get_no_variants_not_even_after_a_restart(self):
        self.desk.book.add_plate("Insurance", "KL07LM8899")
        self.desk.start()                                                            # a later service start
        entries = {e["plate"]: e for e in self.desk.book.grouped("Insurance")}
        self.assertEqual((entries["KL07LM8899"]["origin"], entries["KL07LM8899"]["variants"]), ("saved", []))
        self.assertEqual(entries["MH12AB1234"]["origin"], "initial")
        self.assertEqual(self.desk.book.lookup("Insurance", "KL07LM889"), {})

    def test_a_learned_misread_is_kept_but_a_valid_plate_never_is(self):
        self.assertTrue(self.desk.book.learn("Insurance", "GJ01XY0011", "GJ01XY00X1", origin="cleansing"))
        self.assertEqual(self.desk.book.lookup("Insurance", "GJ01XY00X1"), {"GJ01XY0011": "cleansing"})
        self.assertFalse(self.desk.book.learn("Insurance", "GJ01XY0011", "GJ01XY0012", origin="cleansing"))

    def test_the_dictionary_is_a_separate_file_not_inside_the_source_database(self):
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertNotIn("variants", tables)
        self.assertTrue(Path(self.book_path).exists())

    def test_dictionary_page_links_to_the_cleansing_pages(self):
        app = Flask(__name__)
        self.desk.register(app)
        page = app.test_client().get("/plates/dictionary?q=MH12").get_data(as_text=True)
        self.assertIn("<h3>Insurance</h3>", page)
        self.assertIn("/plates/corrected", page)
        self.assertNotIn("/plates/cleanse", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
