"""Tests for Phase 2 approximate plate candidate discovery."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "device_client"
sys.path.insert(0, str(CLIENT))
import integration_engine as engine  # noqa: E402


def load_service(name, path):
    folder = str((ROOT / path).parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)                    # the service's own modules (e.g. anpr_context.py)
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlateMatchingTests(unittest.TestCase):
    def test_normalization_ignores_case_spaces_and_punctuation(self):
        self.assertEqual(engine.normalize_plate(" dl-8c a f 1234 "), "DL8CAF1234")

    def test_normalized_levenshtein_similarity(self):
        self.assertEqual(engine.plate_similarity("DL8CAF1234", "DL8CAF1234"), 1.0)
        self.assertAlmostEqual(engine.plate_similarity("DL8CAF1234", "DL8CAF1235"), 0.9)
        self.assertEqual(engine.plate_similarity("", "DL8CAF1234"), 0.0)

    def test_candidates_are_ranked_deduplicated_and_keep_all_sources(self):
        responses = {
            "ANPR": ([{"capture_id": 1, "plate_number": "DL8CA F1234", "make_model_seen": "Honda City"}], None),
            "RTO": ([{"reg_id": 1, "reg_number": "DL8CAF1234", "make": "Honda", "model": "City"}], None),
            "Insurance": ([{"policy_id": 1, "vehicle_reg_no": "DL8CAF1234", "status": "Active"}], None),
            "Theft/Shredding": ({"theft_reports": [{"vehicle_no": "DL8CAF1234", "status": "Recovered"}], "shredding_records": []}, None),
        }
        def fake_fetch(source, base, endpoint):
            return responses[source]
        with patch.object(engine, "_fetch_live_records", side_effect=fake_fetch):
            result = engine.find_plate_candidates("DL8CAF1234", threshold=0.8)
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["normalized_plate"], "DL8CAF1234")
        self.assertEqual(candidate["similarity"], 1.0)
        self.assertEqual(set(candidate["sources"]), {"ANPR", "RTO", "Insurance", "Theft/Shredding"})
        self.assertEqual(len(candidate["records_by_source"]["ANPR"]), 1)

    def test_threshold_filters_candidates_and_service_failures_are_reported(self):
        responses = {
            "ANPR": ([{"plate_number": "DL8CAF1235"}], None),
            "RTO": (None, "RTO: unavailable"),
            "Insurance": ([], None),
            "Theft/Shredding": ({"theft_reports": [], "shredding_records": []}, None),
        }
        with patch.object(engine, "_fetch_live_records", side_effect=lambda source, base, endpoint: responses[source]):
            result = engine.find_plate_candidates("DL8CAF1234", threshold=0.99)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["service_errors"]), 1)
        self.assertIn("RTO", result["service_errors"][0])
        self.assertIn("ANPR", result["services_queried"])

    def test_each_search_fetches_current_records_again(self):
        responses = {
            "ANPR": ([{"plate_number": "DL8CAF1234", "capture_id": 1}], None),
            "RTO": ([], None),
            "Insurance": ([], None),
            "Theft/Shredding": ({"theft_reports": [], "shredding_records": []}, None),
        }
        def fake_fetch(source, base, endpoint):
            return responses[source]
        with patch.object(engine, "_fetch_live_records", side_effect=fake_fetch) as fetch:
            first = engine.find_plate_candidates("DL8CAF1234")
            responses["ANPR"] = ([], None)  # simulate a deletion in the source DB
            second = engine.find_plate_candidates("DL8CAF1234")
        self.assertEqual(len(first["candidates"]), 1)
        self.assertEqual(second["candidates"], [])
        self.assertEqual(fetch.call_count, 8)  # all four services queried on both searches

    def test_plates_two_characters_different_are_found_when_asked(self):
        responses = {
            "ANPR": ([{"capture_id": 1, "plate_number": "MH12AB1234"}, {"capture_id": 2, "plate_number": "MH12AB1342"},
                      {"capture_id": 3, "plate_number": "MH12AB9994"}], None),
            "RTO": ([], None), "Insurance": ([], None),
            "Theft/Shredding": ({"theft_reports": [], "shredding_records": []}, None),
        }
        with patch.object(engine, "_fetch_live_records", side_effect=lambda source, base, endpoint: responses[source]):
            plain = engine.find_plate_candidates("MH12AB1234")
            wide = engine.find_plate_candidates("MH12AB1234", max_slips=2)
        self.assertEqual([c["normalized_plate"] for c in plain["candidates"]], ["MH12AB1234"])
        self.assertEqual([(c["normalized_plate"], c["slips"]) for c in wide["candidates"]],
                         [("MH12AB1234", 0), ("MH12AB1342", 2)])            # 9994: three characters, not listed

    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            engine.find_plate_candidates("DL8CAF1234", threshold=1.1)


class LiveRecordApiTests(unittest.TestCase):
    def test_each_source_exposes_live_records_endpoint(self):
        service_specs = [
            ("test_anpr_service", "device_anpr/anpr_service.py", "anpr"),
            ("test_insurance_service", "device_insurance/insurance_service.py", "insurance"),
            ("test_rto_service", "device_rto/rto_service.py", "rto"),
            ("test_theft_service", "device_theft/theft_service.py", "theft"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, relative_path, kind in service_specs:
                try:
                    module = load_service(name, relative_path)
                except ModuleNotFoundError as exc:
                    if exc.name == "flask":
                        self.skipTest("Flask is not installed in this execution environment")
                    raise
                db_path = Path(temp_dir) / f"{kind}.db"
                with patch.object(module, "DB_PATH", str(db_path)):
                    module.init_db()
                    response = module.app.test_client().get("/records")
                    self.assertEqual(response.status_code, 200, relative_path)
                    payload = response.get_json()
                    if kind == "theft":
                        self.assertIn("theft_reports", payload)
                        self.assertIn("shredding_records", payload)
                    else:
                        self.assertIsInstance(payload, list, relative_path)
                    # The endpoint queries the current DB on each request.
                    self.assertTrue(payload, f"Expected seeded records from {relative_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
