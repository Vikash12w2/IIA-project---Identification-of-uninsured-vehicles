"""Regression tests for labelled plate similarity evaluation."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluate_plate_threshold", ROOT / "tests/evaluate_plate_threshold.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class PlateEvaluationTests(unittest.TestCase):
    def test_labelled_set_covers_requested_categories(self):
        categories = {case["category"] for case in module.CASES}
        self.assertTrue({"exact", "formatting", "substitution", "insertion", "deletion", "ambiguous", "no_match"}.issubset(categories))

    def test_threshold_metrics_are_bounded_and_counted(self):
        result = module.evaluate(0.65)
        for field in ("precision", "recall", "f1", "false_match_rate"):
            self.assertGreaterEqual(result[field], 0.0)
            self.assertLessEqual(result[field], 1.0)
        self.assertEqual(result["true_positives"] + result["false_positives"], sum(len(c["returned"]) for c in result["cases"]))

    def test_strict_threshold_does_not_increase_returned_candidates(self):
        low, high = module.evaluate(0.5), module.evaluate(0.9)
        low_returned = sum(len(c["returned"]) for c in low["cases"])
        high_returned = sum(len(c["returned"]) for c in high["cases"])
        self.assertLessEqual(high_returned, low_returned)

if __name__ == "__main__":
    unittest.main(verbosity=2)
