"""GUI tests for the source services: records are saved as typed and cleaned automatically; plates are URL-safe."""
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
for device in ("device_anpr", "device_insurance", "device_theft", "device_rto", "device_client"):
    sys.path.insert(0, str(ROOT / device))
import anpr_service  # noqa: E402
import insurance_service  # noqa: E402
import theft_service  # noqa: E402
import rto_service  # noqa: E402
import integration_engine as engine  # noqa: E402

DEVICES = {"ANPR": anpr_service, "Insurance": insurance_service, "RTO": rto_service, "Theft/Shredding": theft_service}
MOT = "http://mot.test"


class ServiceCase(unittest.TestCase):
    """Fresh seeded copies of every service database and dictionary file (never the real ones).

    The devices reach each other in-process: each cleanser reads the others' evidence directly, and MoT is a list.
    `self.down` holds the devices that do not answer.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.down, self.mot_reports = set(), []
        for module in DEVICES.values():
            for target, attr, value in ((module, "DB_PATH", str(Path(self.tmp.name) / f"{module.__name__}.db")),
                                        (module.desk.book, "path", str(Path(self.tmp.name) / f"{module.__name__}_dictionary.db")),
                                        (module.cleanser, "fetch", self.fetch),
                                        (module.cleanser, "post", self.post),
                                        (module.cleanser, "notify_peers", lambda round_: None),
                                        (module.cleanser, "peers", {"MoT": MOT})):
                patcher = patch.object(target, attr, value)
                patcher.start()
                self.addCleanup(patcher.stop)
            module.init_db()

    def fetch(self, device, since=0, epoch=None):
        return None if device in self.down else DEVICES[device].cleanser.evidence(since, epoch)

    def post(self, url, payload):
        if url == MOT + "/reports" and "MoT" not in self.down:
            self.mot_reports.append(payload)
            return True
        return False

    def clean_all(self):
        for _ in range(2):                    # the second pass sees the others' cleaned data
            for module in DEVICES.values():
                module.cleanser.run()

    @staticmethod
    def popup(reply):
        return parse_qs(urlparse(reply.headers["Location"]).query).get("clean_notice", [""])[0]

    @staticmethod
    def raw_plates(module, table, column):
        conn = sqlite3.connect(module.DB_PATH)
        try:
            return [r[0] for r in conn.execute(f"SELECT {column} FROM {table} ORDER BY rowid")]
        finally:
            conn.close()


class AddFormTests(ServiceCase):
    def test_anpr_missing_field_is_reported_and_nothing_saved(self):
        reply = anpr_service.app.test_client().post("/add", data={"plate_number": "MHI2AB1234", "vehicle_type": "Car",
                                                                  "make_model_seen": "Maruti Swift", "camera_id": "CAM-1"})
        self.assertIn("Please enter the capture location.", reply.get_data(as_text=True))
        self.assertEqual(len(self.raw_plates(anpr_service, "vehicle_capture", "plate_number")), 15)

    def test_anpr_plate_is_saved_as_typed_and_corrected_in_the_cleaned_data(self):
        reply = anpr_service.app.test_client().post("/add", data={
            "plate_number": "MH12A81234", "location": "Pune", "vehicle_color": "White", "vehicle_type": "Car",
            "make_model_seen": "Maruti Swift", "camera_id": "CAM-9"})
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(self.raw_plates(anpr_service, "vehicle_capture", "plate_number")[-1], "MH12A81234")
        self.assertIn("plate MH12A81234 was corrected to MH12AB1234", self.popup(reply))
        cleaned = anpr_service.app.test_client().get("/clean/vehicle/MH12AB1234").get_json()
        self.assertEqual(len(cleaned), 3)
        home = anpr_service.app.test_client().get("/?clean_notice=" + self.popup(reply)).get_data(as_text=True)
        self.assertIn('class="cl-popup"', home)

    def test_conflicting_sighting_is_kept_and_reported(self):
        self.clean_all()
        client = anpr_service.app.test_client()
        response = client.post("/add", data={"plate_number": "MH12AB1234", "location": "Pune",
                               "vehicle_color": "Black", "vehicle_type": "SUV", "make_model_seen": "Hyundai Creta",
                               "camera_id": "CAM-NEW"})
        self.assertEqual(response.status_code, 302)
        capture = next(r for r in client.get("/clean/vehicle/MH12AB1234").get_json() if r["camera_id"] == "CAM-NEW")
        self.assertIn("details contradict the RTO registration", capture["flags"])
        self.assertTrue(any("PLATE_DETAILS_MISMATCH" in r["risk_flags"] for r in self.mot_reports))

    def test_insurance_missing_field_keeps_what_was_typed(self):
        reply = insurance_service.app.test_client().post("/add", data={
            "vehicle_reg_no": "MHI2AB1234", "insurer_name": "ICICI", "policy_number": "P-1", "policy_type": "Third-Party",
            "status": "Lapsed", "premium_amount": "abc", "start_date": "2026-01-01", "end_date": "2027-01-01"})
        page = reply.get_data(as_text=True)
        self.assertIn("Premium amount must be a valid number.", page)
        for kept in ('value="MHI2AB1234"', 'value="ICICI"', 'value="2026-01-01"',
                     'value="Third-Party" selected', 'value="Lapsed" selected'):
            self.assertIn(kept, page)

    def test_theft_and_shredding_missing_date_keeps_what_was_typed(self):
        for path, form in (("/theft/add", {"vehicle_no": "MHI2AB1234", "fir_number": "FIR-9", "status": "Recovered"}),
                           ("/shred/add", {"vehicle_no": "MHI2AB1234", "certificate_no": "SCR-9"})):
            page = theft_service.app.test_client().post(path, data=form).get_data(as_text=True)
            self.assertIn("date is required", page)
            self.assertIn('value="MHI2AB1234"', page)
            self.assertIn('value="FIR-9"' if path == "/theft/add" else 'value="SCR-9"', page)

    def test_a_valid_form_is_saved_as_typed_then_cleaned(self):
        reply = theft_service.app.test_client().post("/theft/add", data={"vehicle_no": "MHI2AB1234", "report_date": "2026-10-01",
                                                                         "fir_number": "FIR-9", "status": "Reported"})
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(theft_service.query("theft_report", "MHI2AB1234")[0]["report_date"], "2026-10-01")
        self.assertIn("corrected to MH12AB1234", self.popup(reply))
        cleaned = theft_service.app.test_client().get("/clean/theft/MH12AB1234").get_json()
        self.assertEqual([r["fir_number"] for r in cleaned], ["FIR-9"])

    def test_an_invalid_record_is_saved_but_left_out_of_cleaned_data(self):
        reply = insurance_service.app.test_client().post("/add", data={
            "vehicle_reg_no": "ZZ99ZZ9999", "insurer_name": "ICICI", "policy_number": "P-77",
            "start_date": "2026-01-01", "end_date": "2027-01-01", "status": "Active"})
        self.assertIn("left out of cleaned data: vehicle ZZ99ZZ9999 is not registered in RTO", self.popup(reply))
        self.assertIn("ZZ99ZZ9999", self.raw_plates(insurance_service, "policy", "vehicle_reg_no"))
        self.assertEqual(insurance_service.app.test_client().get("/clean/vehicle/ZZ99ZZ9999").get_json(), [])

    def test_cleaning_waits_while_a_device_is_down(self):
        self.down = {"RTO"}
        reply = insurance_service.app.test_client().post("/add", data={
            "vehicle_reg_no": "MH12A81234", "insurer_name": "ICICI", "policy_number": "P-77",
            "start_date": "2026-01-01", "end_date": "2027-01-01", "status": "Active"})
        self.assertIn("RTO is down, so data cleaning is pending", self.popup(reply))
        home = insurance_service.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("RTO is currently down, so data cleaning is paused", home)
        self.down = set()
        insurance_service.cleanser.run()
        self.assertNotIn("currently down", insurance_service.app.test_client().get("/").get_data(as_text=True))

    def test_an_edit_shows_a_message_only_when_its_cleaned_version_changes(self):
        self.clean_all()
        client = insurance_service.app.test_client()
        form = {"vehicle_reg_no": "KA05MZ9090", "policy_id": "3", "insurer_name": "Bajaj Allianz", "policy_number": "BAJ-2026-004411",
                "policy_type": "Comprehensive", "start_date": "2026-02-10", "end_date": "2027-02-09", "premium_amount": "1600",
                "status": "Active"}
        self.assertEqual(self.popup(client.post("/edit/save", data=form)), "")
        reply = client.post("/edit/save", data={**form, "end_date": "2025-01-01"})
        self.assertIn("end date 2025-01-01 is before start date 2026-02-10", self.popup(reply))

    def test_empty_add_forms_still_render(self):
        for module, path in ((anpr_service, "/add"), (insurance_service, "/add"),
                             (theft_service, "/theft/add"), (theft_service, "/shred/add"), (rto_service, "/add")):
            self.assertEqual(module.app.test_client().get(path).status_code, 200, path)


class MotTests(ServiceCase):
    def test_anpr_waits_for_the_others_cleaned_data_and_reports_each_vehicle_once(self):
        anpr_service.cleanser.run()                                     # the others have not cleaned yet
        self.assertEqual(self.mot_reports, [])
        self.clean_all()
        plates = [r["plate_no"] for r in self.mot_reports]
        self.assertTrue(plates)
        self.assertEqual(len(plates), len(set(plates)))

    def test_anpr_reports_flagged_vehicles_once_and_says_so_when_a_capture_is_flagged(self):
        self.clean_all()
        reported = {r["plate_no"]: r for r in self.mot_reports}
        self.assertIn("STOLEN_VEHICLE_ON_ROAD", reported["MH14ZZ0000"]["risk_flags"])
        self.assertIn("SHREDDED_VEHICLE_STILL_ON_ROAD", reported["TN09EF7777"]["risk_flags"])
        self.assertNotIn("KA05MZ9090", reported)                        # registered, insured, not stolen
        count = len(self.mot_reports)
        client = anpr_service.app.test_client()
        again = client.post("/add", data={"plate_number": "MH14ZZ0000", "location": "Pune", "vehicle_color": "Black",
                                          "vehicle_type": "Car", "make_model_seen": "Skoda Rapid", "camera_id": "CAM-3"})
        self.assertNotIn("MoT", self.popup(again))                      # same state: not reported twice
        self.assertEqual(len(self.mot_reports), count)
        new = client.post("/add", data={"plate_number": "KA01AB1111", "location": "Pune", "vehicle_color": "Grey",
                                        "vehicle_type": "Car", "make_model_seen": "Honda City", "camera_id": "CAM-3"})
        self.assertIn("a report was sent to MoT", self.popup(new))
        self.assertEqual(self.mot_reports[-1]["plate_no"], "KA01AB1111")

    def test_a_change_on_another_device_is_reported_by_anpr(self):
        self.clean_all()
        self.mot_reports.clear()
        theft_service.app.test_client().post("/theft/add", data={"vehicle_no": "KA05MZ9090", "report_date": "2026-10-01",
                                                                 "fir_number": "FIR-77", "status": "Reported"})
        anpr_service.cleanser.run()                                     # what the notification from Theft triggers
        self.assertEqual([(r["plate_no"], r["risk_flags"]) for r in self.mot_reports],
                         [("KA05MZ9090", "STOLEN_VEHICLE_ON_ROAD")])


class PlatePagesTests(ServiceCase):
    def test_every_service_has_its_search_cleaned_report_corrected_and_dictionary_pages(self):
        self.clean_all()
        for module, names in ((anpr_service, ["ANPR"]), (insurance_service, ["Insurance"]),
                              (rto_service, ["RTO"]), (theft_service, ["Theft", "Shredding"])):
            module.desk.start()
            client = module.app.test_client()
            home = client.get("/").get_data(as_text=True)
            for link in ("/search", "/cleaned", "/cleansing-report", "/plates/corrected", "/plates/dictionary"):
                self.assertIn(f'href="{link}"', home, module.__name__)
                self.assertEqual(client.get(link).status_code, 200, link)
            self.assertNotIn("/plates/cleanse", home)
            dictionary = client.get("/plates/dictionary").get_data(as_text=True)
            for name in names:
                self.assertIn(f"<h3>{name}</h3>", dictionary)

    def test_search_finds_raw_and_cleaned_records(self):
        insurance_service.app.test_client().post("/add", data={
            "vehicle_reg_no": "MH12A81234", "insurer_name": "ICICI", "policy_number": "P-77",
            "start_date": "2026-01-01", "end_date": "2027-01-01", "status": "Active"})
        page = insurance_service.app.test_client().get("/search?q=MH12AB1234").get_data(as_text=True)
        self.assertIn("raw data (2)", page)
        self.assertIn("cleaned from MH12A81234", page)


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


class AnprOcrTests(ServiceCase):
    form = {"plate_source": "image", "location": "Pune", "vehicle_color": "White", "vehicle_type": "Car",
            "make_model_seen": "Maruti Swift", "camera_id": "CAM-1"}

    def setUp(self):
        super().setUp()
        images = patch.object(anpr_service, "PLATE_IMAGE_DIR", str(Path(self.tmp.name) / "plate_images"))
        images.start()
        self.addCleanup(images.stop)

    def upload(self, data, **extra):
        import io
        return anpr_service.app.test_client().post("/add", data={**self.form, **extra, "plate_image": (io.BytesIO(data), "plate.png")},
                                                    content_type="multipart/form-data")

    def latest_capture(self):
        conn = sqlite3.connect(anpr_service.DB_PATH)
        try:
            return conn.execute("SELECT capture_id, plate_number FROM vehicle_capture ORDER BY capture_id DESC LIMIT 1").fetchone()
        finally:
            conn.close()

    @unittest.skipUnless(anpr_service.plate_ocr.available(), "Tesseract is not installed")
    def test_a_plate_image_is_read_saved_and_kept_with_the_capture(self):
        reply = self.upload(plate_png("MH 12 AB 1234"))
        self.assertEqual(reply.status_code, 302)
        capture_id, plate = self.latest_capture()
        self.assertEqual(plate, "MH12AB1234")
        self.assertTrue((Path(anpr_service.PLATE_IMAGE_DIR) / f"capture_{capture_id}.png").exists())
        self.assertIn("OCR read MH12AB1234", self.popup(reply))
        home = anpr_service.app.test_client().get("/").get_data(as_text=True)
        self.assertIn(f"/plate-images/capture_{capture_id}.png", home)

    def test_a_noisy_ocr_read_is_saved_as_read_and_corrected_in_the_cleaned_data(self):
        with patch.object(anpr_service.plate_ocr, "read_plate", return_value={"text": "MHI2AB1234", "confidence": 0.71, "error": ""}), \
                patch.object(anpr_service, "save_pending_image", side_effect=self.fake_pending):
            reply = self.upload(b"png bytes")
        self.assertEqual(reply.status_code, 302)
        capture_id, plate = self.latest_capture()
        self.assertEqual(plate, "MHI2AB1234")                                       # raw: as read
        self.assertTrue((Path(anpr_service.PLATE_IMAGE_DIR) / f"capture_{capture_id}.png").exists())
        message = self.popup(reply)
        self.assertIn("OCR read MHI2AB1234 from the plate image.", message)
        self.assertIn("corrected to MH12AB1234", message)

    def fake_pending(self, data):
        pending = Path(anpr_service.PLATE_IMAGE_DIR) / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        token = "0" * 32
        (pending / f"{token}.png").write_bytes(data)
        return token

    def test_a_file_that_is_not_an_image_is_refused(self):
        page = self.upload(b"not an image").get_data(as_text=True)
        self.assertTrue("not an image" in page or "not installed" in page)
        self.assertEqual(self.latest_capture()[1], "HR26NN4545")                # nothing saved

    def test_deleting_a_vehicle_removes_its_plate_images(self):
        images = Path(anpr_service.PLATE_IMAGE_DIR)
        images.mkdir(parents=True)
        (images / "capture_1.png").write_bytes(b"x")
        anpr_service.app.test_client().post("/delete/confirm", data={"plate_number": "MH12AB1234"})
        self.assertFalse((images / "capture_1.png").exists())


class UrlSafePlateTests(ServiceCase):
    def test_client_encodes_the_plate_in_the_lookup_url(self):
        with patch.object(engine, "call", return_value=[]) as call:
            engine.get_captures("MH12/AB#1234?")
        self.assertTrue(call.call_args[0][0].endswith("/clean/vehicle/MH12%2FAB%231234%3F"))

    def test_services_find_a_stored_plate_with_special_characters(self):
        plate = "MH12/AB#1234?"
        conn = sqlite3.connect(anpr_service.DB_PATH)
        conn.execute("UPDATE vehicle_capture SET plate_number=? WHERE capture_id=1", (plate,))
        conn.commit(); conn.close()
        reply = anpr_service.app.test_client().get("/vehicle/" + engine._url_plate(plate))
        self.assertEqual(reply.status_code, 200)
        self.assertEqual([r["plate_number"] for r in reply.get_json()], [plate])
        for module, path in ((insurance_service, "/vehicle/"), (rto_service, "/vehicle/"),
                             (theft_service, "/theft/"), (theft_service, "/shred/"),
                             (insurance_service, "/clean/vehicle/"), (theft_service, "/clean/shred/")):
            self.assertEqual(module.app.test_client().get(path + "MH12%2FAB1234").status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
