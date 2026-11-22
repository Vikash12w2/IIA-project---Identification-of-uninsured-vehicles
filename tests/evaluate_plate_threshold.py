"""Evaluate plate-matching algorithms and thresholds on a small labelled fixture.

Two scorers are compared: plain Levenshtein (baseline) and the OCR-confusion-weighted
Levenshtein used by the client. `top1` is the share of non-empty-relevant cases where
the single highest-scoring candidate (no tie) is a relevant one.

Metrics are candidate-level micro precision/recall/F1. False-match rate is the
fraction of returned candidate pairs labelled irrelevant. Dataset is synthetic
and intended for regression/evaluation plumbing, not real-world calibration.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device_client"))
import integration_engine as engine

CASES = json.loads((ROOT / "tests/data/labelled_plate_cases.json").read_text(encoding="utf-8"))


SCORERS = {"levenshtein": engine.plate_similarity, "weighted": engine.weighted_plate_similarity}


def evaluate(threshold, algorithm="weighted"):
    score = SCORERS[algorithm]
    tp = fp = fn = 0
    top1_hits = top1_total = 0
    ranks = []
    per_case = []
    for case in CASES:
        scored = sorted(
            ((candidate, score(case["query"], candidate)) for candidate in case["candidates"]),
            key=lambda item: (-item[1], engine.normalize_plate(item[0])),
        )
        if case["relevant"]:
            top1_total += 1
            best = scored[0][1]
            leaders = [c for c, sc in scored if abs(sc - best) < 1e-9]
            if len(leaders) == 1 and engine.normalize_plate(leaders[0]) in {engine.normalize_plate(x) for x in case["relevant"]}:
                top1_hits += 1
        returned = [candidate for candidate, score in scored if score >= threshold]
        relevant = {engine.normalize_plate(x) for x in case["relevant"]}
        found = {engine.normalize_plate(x) for x in returned}
        case_tp = len(found & relevant)
        case_fp = len(found - relevant)
        case_fn = len(relevant - found)
        tp += case_tp; fp += case_fp; fn += case_fn
        ranks.extend([i + 1 for i, (candidate, _) in enumerate(scored) if engine.normalize_plate(candidate) in relevant])
        per_case.append({"id": case["id"], "category": case["category"], "returned": returned,
                         "true_positive": case_tp, "false_positive": case_fp, "false_negative": case_fn})
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_match_rate = fp / (tp + fp) if tp + fp else 0.0
    return {"threshold": threshold, "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "top1": round(top1_hits / top1_total, 4) if top1_total else 0.0, "false_match_rate": round(false_match_rate, 4),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "cases": per_case}

if __name__ == "__main__":
    print(f"Labelled synthetic cases: {len(CASES)}")
    for algorithm in SCORERS:
        print(f"\n[{algorithm}]")
        print("threshold precision recall f1 top1 false_match_rate TP FP FN")
        for threshold in (0.50, 0.65, 0.80, 0.85, 0.90, 0.95, 1.00):
            r = evaluate(threshold, algorithm)
            print(f"{r['threshold']:.2f} {r['precision']:.3f} {r['recall']:.3f} {r['f1']:.3f} {r['top1']:.3f} "
                  f"{r['false_match_rate']:.3f} {r['true_positives']} {r['false_positives']} {r['false_negatives']}")
