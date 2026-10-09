"""Deterministic scoring math, brand staleness, and the placeholder card (free, offline)."""
import json
import tempfile
import unittest
from pathlib import Path

from agents.process import analyzer


def make_analysis(values):
    entry = lambda v: {"value": v, "reasoning": "r"}  # noqa: E731
    return analyzer.VideoAnalysis(
        video_id="v", content_summary="c", narrative_skeleton="n", hook_type="reveal", hook_timing_sec=1.0,
        pacing="fast", has_spoken_dialogue=False, has_onscreen_text=False, requires_specific_persona=False,
        emotional_tone="t", scores=analyzer.Scores(**{k: entry(v) for k, v in values.items()}),
    )


class ScoringTests(unittest.TestCase):
    WEIGHTS = analyzer.load_scoring_weights()

    def test_weights_sum_to_one_and_threshold_is_in_range(self):
        w = self.WEIGHTS
        self.assertAlmostEqual(sum(w[c] for c in analyzer.SCORE_CRITERIA), 1.0)
        self.assertTrue(1 <= w["accept_threshold"] <= 5)

    def test_weighted_score_is_computed_in_python(self):
        a = make_analysis(dict(narrative_transferability=5, hook_strength=4, production_feasibility=5,
                               brand_fit=5, licensing_safety=5))
        d = analyzer.compute_decision(a, self.WEIGHTS)
        self.assertAlmostEqual(d["weighted_score"], 5 * .30 + 4 * .20 + 5 * .15 + 5 * .25 + 5 * .10)
        self.assertEqual(d["status"], "accepted")

    def test_low_brand_fit_rejects(self):
        a = make_analysis(dict(narrative_transferability=3, hook_strength=3, production_feasibility=3,
                               brand_fit=1, licensing_safety=3))
        d = analyzer.compute_decision(a, self.WEIGHTS)
        self.assertEqual(d["status"], "rejected")
        self.assertIn("brand_fit", d["reasoning_summary"])

    def test_analysis_staleness_follows_the_brand_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.analysis.json"
            self.assertFalse(analyzer.analysis_is_current(path))  # missing
            path.write_text(json.dumps({"video_id": "v"}), encoding="utf-8")
            self.assertFalse(analyzer.analysis_is_current(path))  # unstamped
            path.write_text(json.dumps({"scored_for_brand": "original brand"}), encoding="utf-8")
            self.assertFalse(analyzer.analysis_is_current(path))  # other brand
            path.write_text(json.dumps({"scored_for_brand": "Kahve Maya"}), encoding="utf-8")
            self.assertTrue(analyzer.analysis_is_current(path))


class PlaceholderCardTests(unittest.TestCase):
    def test_card_is_generated_locally(self):
        from scripts.make_placeholder_pip_card import make_card
        with tempfile.TemporaryDirectory() as tmp:
            out = make_card("Kahve Maya - placeholder", Path(tmp) / "card.jpg")
            self.assertGreater(out.stat().st_size, 5_000)


if __name__ == "__main__":
    unittest.main()
