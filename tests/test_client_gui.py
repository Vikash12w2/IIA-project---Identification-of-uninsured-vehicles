"""Vehicle 360 client page: search messages and manual candidate selection (services faked)."""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device_client"))
import gui_app  # noqa: E402
engine = gui_app.engine


def view(plate, **data):
    base = {"plate_number": plate, "last_seen_on_road": None, "times_captured": 0, "registered": False,
            "owner_name": None, "make_model": None, "reg_valid_upto": None, "insurance_status": "NOT_INSURED",
            "insurer": None, "policy_number": None, "policy_end_date": None, "theft_status": "NONE",
            "shredded": False, "risk_flags": ["UNINSURED", "UNREGISTERED_OR_FAKE_PLATE"], "suspicious": True}
    return {**base, **data}


def plate_png(text):
    """A clean, cropped plate image (black text on white) as PNG bytes."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 90)
    except OSError:
        font = ImageFont.load_default()
    image = Image.new("RGB", (760, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, 755, 155], outline="black", width=6)
    draw.text((30, 25), text, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def candidate(plate, slips=1):
    return {"candidate_plate": plate, "normalized_plate": plate, "similarity": 0.97, "confidence": 0.97, "slips": slips,
            "sources": ["ANPR"], "records_by_source": {}, "in_registry": False, "format_valid": True,
            "context": {"matches": [], "conflicts": [], "unknown": []}}


class SearchMessageTests(unittest.TestCase):
    def setUp(self):
        for target, attr, value in ((engine, "get_service_status", lambda: {}),
                                    (engine, "get_all_plates", lambda: []),
                                    (engine, "get_cleansing_warnings", lambda: [])):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        gui_app.previous_views = None

    def search(self, plate, found_view, candidates):
        result = {"query": plate, "normalized_query": plate, "threshold": 0.85, "candidates": candidates,
                  "service_errors": [], "services_queried": ["ANPR"], "context": {}, "query_in_registry": False}
        with patch.object(engine, "build_vehicle_view", return_value=found_view), \
                patch.object(engine, "find_plate_candidates", return_value=result):
            return gui_app.app.test_client().get("/", query_string={"plate": plate}).get_data(as_text=True)

    def test_no_exact_match_points_to_the_close_candidates(self):
        page = self.search("MH14QW000O", view("MH14QW000O"), [candidate("MH14QW0000")])
        self.assertIn("No exact match for 'MH14QW000O'", page)
        self.assertIn("Check the Approximate Plate Candidates below", page)
        self.assertNotIn("No record found", page)

    def test_a_candidate_is_selected_by_viewing_it_and_nothing_is_saved(self):
        page = self.search("MH14QW000O", view("MH14QW000O"), [candidate("MH14QW0000")])
        self.assertIn('<a href="/?plate=MH14QW0000">View this vehicle</a>', page)
        for gone in ("plate-review", "Confirm candidate", "Reject candidate", "Leave unresolved", "<form class=\"review-form\""):
            self.assertNotIn(gone, page)

    def test_an_exact_match_still_lists_the_similar_plates(self):
        found = view("MH12AB1234", registered=True, times_captured=2)
        page = self.search("MH12AB1234", found, [candidate("MH12AB1234", 0), candidate("MH12AB1342", 2)])
        self.assertIn("Exact match found for 'MH12AB1234', but 1 other plate(s) in the databases are close to it", page)
        self.assertIn("<h2>Similar Plates</h2>", page)
        self.assertIn('<a href="/?plate=MH12AB1342">View this vehicle</a>', page)
        self.assertNotIn("No exact match", page)

    def test_an_exact_match_with_nothing_similar_says_so(self):
        page = self.search("MH12AB1234", view("MH12AB1234", registered=True), [candidate("MH12AB1234", 0)])
        self.assertIn("Exact match found. No similar plate exists in any database.", page)

    def test_the_search_asks_for_plates_up_to_two_characters_different(self):
        result = {"query": "X", "normalized_query": "X", "threshold": 0.85, "candidates": [], "service_errors": [],
                  "services_queried": [], "context": {}, "query_in_registry": False}
        with patch.object(engine, "build_vehicle_view", return_value=view("X")),                 patch.object(engine, "find_plate_candidates", return_value=result) as find:
            gui_app.app.test_client().get("/", query_string={"plate": "MH12AB1234"})
        self.assertEqual(find.call_args.kwargs["max_slips"], 2)

    def test_no_exact_and_nothing_close(self):
        page = self.search("ZZ99ZZ9999", view("ZZ99ZZ9999"), [])
        self.assertIn("No close plate number was found in any database either", page)

    def test_a_plate_only_in_theft_is_shown_not_reported_missing(self):
        page = self.search("MH14ZZ0000", view("MH14ZZ0000", theft_status="REPORTED_STOLEN"), [candidate("MH14ZZ0000")])
        self.assertNotIn("No exact match", page)
        self.assertIn("REPORTED_STOLEN", page)
        self.assertNotIn("Approximate Plate Candidates", page)          # only the plate itself: nothing to choose

    def image_search(self, data, filename="plate.png"):
        """POST a plate image to the search; returns (page, plate the search was run for)."""
        import io
        result = {"query": "", "normalized_query": "", "threshold": 0.85, "candidates": [candidate("MH12AB1234")],
                  "service_errors": [], "services_queried": ["ANPR"], "context": {}, "query_in_registry": False}

        def fake_find(plate, **kw):
            return {**result, "query": plate, "normalized_query": plate}
        with patch.object(engine, "build_vehicle_view", side_effect=lambda p: view(p)), \
                patch.object(engine, "find_plate_candidates", side_effect=fake_find) as find:
            page = gui_app.app.test_client().post("/", data={"plate_source": "image", "color": "White", "make_model": "Maruti Swift",
                                                             "plate_image": (io.BytesIO(data), filename)},
                                                  content_type="multipart/form-data").get_data(as_text=True)
        return page, (find.call_args[0][0] if find.called else None), find

    @unittest.skipUnless(gui_app.plate_ocr.available(), "Tesseract is not installed")
    def test_a_plate_image_is_read_and_searched_like_a_typed_plate(self):
        page, searched, find = self.image_search(plate_png("MH 12 AB 1284"))
        self.assertEqual(searched, "MH12AB1284")
        self.assertEqual(find.call_args[1]["context"], {"color": "White", "vehicle_type": "", "make_model": "Maruti Swift"})
        self.assertIn("OCR read <b>MH12AB1284</b>", page)
        confidence = int(re.search(r"confidence (\d+)%", page).group(1))
        self.assertGreater(confidence, 50)                  # Tesseract's whitelist reports 0: must not be shown
        self.assertIn('src="data:image/png;base64,', page)
        self.assertIn("No exact match for 'MH12AB1284'", page)
        self.assertIn("Approximate Plate Candidates", page)

    def test_an_unreadable_upload_says_so_and_searches_nothing(self):
        page, searched, _ = self.image_search(b"not an image")
        self.assertIsNone(searched)
        self.assertTrue("The file is not an image." in page or "not installed" in page)
        page, searched, _ = self.image_search(b"", filename="")
        self.assertIsNone(searched)
        self.assertIn("Choose a plate image", page)

    def test_client_home_has_no_dictionary_or_cleansing_links(self):
        page = gui_app.app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn("/plates/dictionary", page)
        self.assertNotIn("/plates/cleanse", page)

    def test_paused_cleaning_on_a_device_is_shown(self):
        with patch.object(engine, "get_cleansing_warnings", return_value=["Insurance: data cleaning is paused because RTO is down"]):
            page = gui_app.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Insurance: data cleaning is paused because RTO is down", page)


class VehicleViewTests(unittest.TestCase):
    def test_a_sighting_that_contradicts_rto_is_a_possible_cloned_plate(self):
        capture = {"capture_ts": "2026-09-01 08:00:00", "make_model_seen": "Skoda Rapid",
                   "flags": "details contradict the RTO registration: seen as Skoda Rapid, registered as Maruti Suzuki Swift"}
        registration = {"owner_name": "Rohan", "make": "Maruti Suzuki", "model": "Swift", "reg_valid_upto": "2033-06-14"}
        policy = {"start_date": "2025-01-01", "end_date": "2099-01-01", "status": "Active", "insurer_name": "ICICI", "policy_number": "P-1"}
        with patch.object(engine, "get_captures", return_value=[capture]), \
                patch.object(engine, "get_policies", return_value=[policy]), \
                patch.object(engine, "get_registration", return_value=[registration]), \
                patch.object(engine, "get_theft", return_value=[]), patch.object(engine, "get_shred", return_value=[]):
            view = engine.build_vehicle_view("MH12AB1234")
        self.assertEqual(view["risk_flags"], ["PLATE_DETAILS_MISMATCH"])
        self.assertEqual(engine.recommend_action(view), "VERIFY - possible cloned plate")


class RefreshTests(unittest.TestCase):
    def test_refresh_detects_changes_and_never_sends_to_mot(self):
        gui_app.previous_views = None
        stolen = view("MH12AB1234", theft_status="REPORTED_STOLEN")
        states = iter([view("MH12AB1234"), stolen])                       # one bulk build per load
        with patch.object(engine, "get_service_status", lambda: {}), \
                patch.object(engine, "get_cleansing_warnings", lambda: []), \
                patch.object(engine, "get_all_plates", lambda: ["MH12AB1234"]), \
                patch.object(engine, "build_views", side_effect=lambda plates: {"MH12AB1234": next(states)}), \
                patch.object(engine.requests, "post") as post:
            first = gui_app.app.test_client().get("/").get_data(as_text=True)
            second = gui_app.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("baseline for detecting changes", first)
        self.assertIn("1 vehicle state change(s) detected", second)
        post.assert_not_called()
        self.assertFalse(hasattr(engine, "send_mot_report"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
