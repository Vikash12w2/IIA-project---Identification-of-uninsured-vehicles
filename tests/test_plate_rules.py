"""Plate rules shared by every device: format, weighted similarity, look-alike repair and noisy variants."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plate_rules as rules  # noqa: E402


class FormatAndMatchingTests(unittest.TestCase):
    def test_ocr_lookalike_scores_higher_than_unrelated_substitution(self):
        ocr = rules.weighted_plate_similarity("DL8CAFI234", "DL8CAF1234")      # I read for 1
        other = rules.weighted_plate_similarity("DL8CAF1284", "DL8CAF1234")    # different digit
        self.assertAlmostEqual(ocr, 0.97)
        self.assertAlmostEqual(other, 0.90)
        self.assertEqual(rules.plate_similarity("DL8CAFI234", "DL8CAF1234"),
                         rules.plate_similarity("DL8CAF1284", "DL8CAF1234"))

    def test_exact_match_after_normalisation_is_one(self):
        self.assertEqual(rules.weighted_plate_similarity("dl-8c af 1234", "DL8CAF1234"), 1.0)

    def test_format_needs_two_series_letters_and_exactly_four_trailing_digits(self):
        self.assertTrue(rules.plate_format_valid("MH 12 AB 1234"))
        self.assertTrue(rules.plate_format_valid("MH12AB1234"))
        self.assertFalse(rules.plate_format_valid("MH12AB123"))       # dropped digit
        self.assertFalse(rules.plate_format_valid("MH12AB12345"))
        self.assertFalse(rules.plate_format_valid("MHI2AB1234"))
        self.assertFalse(rules.plate_format_valid("DL8CAF1234"))      # 3-letter series: outside the supported format

    def test_make_model_tolerates_extra_words(self):
        self.assertTrue(rules.text_match("maruti swift", "maruti suzuki swift"))
        self.assertFalse(rules.text_match("skoda rapid", "maruti suzuki swift"))

    def test_one_edit_apart(self):
        for other in ("MH12AB1284", "MH12AB1243", "MH12A1234", "MH12ABC1234"):
            self.assertTrue(rules.one_edit_apart("MH12AB1234", other), other)
        for other in ("MH12AB1234", "MH12AB4321", "MH12AB1299"):
            self.assertFalse(rules.one_edit_apart("MH12AB1234", other), other)


class PositionalRepairTests(unittest.TestCase):
    def test_wrong_slot_lookalikes_are_repaired(self):
        self.assertEqual(rules.positional_repair("MHI2AB1234"), ["MH12AB1234"])      # I in a digit slot
        self.assertEqual(rules.positional_repair("MH12A81234"), ["MH12AB1234"])      # 8 in a letter slot
        self.assertEqual(rules.positional_repair("MH12AB12O4"), ["MH12AB1204"])

    def test_valid_or_hopeless_plates_have_no_repair(self):
        self.assertEqual(rules.positional_repair("MH12AB1234"), [])
        self.assertEqual(rules.positional_repair("MH12AB12X4"), [])                  # X looks like nothing

    def test_ambiguous_repair_is_not_clear(self):
        self.assertGreater(len(rules.positional_repair("MH120B1234")), 1)           # 0 -> O, Q or D

    def test_repairs_are_ranked_stored_plates_first_then_common_lookalikes(self):
        self.assertEqual(rules.ranked_repairs("MH140W0000"), ["MH14OW0000", "MH14DW0000", "MH14QW0000"])
        self.assertEqual(rules.ranked_repairs("MH140W0000", {"MH14QW0000"})[0], "MH14QW0000")


class VariantTests(unittest.TestCase):
    def test_only_format_breaking_variants_are_kept(self):
        v = rules.format_breaking_variants("MH12AB1234")
        for noisy in ("MH12A81234", "MHI2AB1234", "MH12AB123", "MH12AB12334", "MH12A8123A", "MH1ZAB1234"):
            self.assertIn(noisy, v)
        for valid in ("MH12AB1243", "MH12BA1234"):
            self.assertNotIn(valid, v)            # valid plates: another vehicle, never noise
        self.assertTrue(all(not rules.plate_format_valid(x) for x in v))
        self.assertNotIn("MH12AB1234", v)
        self.assertGreater(len(v), 50)            # no cap


if __name__ == "__main__":
    unittest.main(verbosity=2)
