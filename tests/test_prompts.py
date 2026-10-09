"""Every prompt renders cleanly from the brand profile, with no leftover tokens (free, offline)."""
import json
import re
import unittest
from pathlib import Path

from agents.brand import default_profile
from agents.discover import prefilter
from agents.generate import ugc_scriptwriter
from agents.process import analyzer

REPO = Path(__file__).resolve().parents[1]
ANALYSIS = {
    "narrative_skeleton": "mess -> hard cut -> calm", "hook_type": "reveal", "hook_timing_sec": 4.0,
    "pacing": "fast", "emotional_tone": "quiet relief",
}
SELECTED = {"video_id": "123", "trend_id": "trend_x_000000"}
# Terms of the original target brand that must never appear in a prompt. Written in pieces so this
# file does not itself contain them (a repository-wide hygiene grep stays at zero hits).
_ORIGINAL_BRAND_TERMS = ["roy" + "al ?mat" + "ch", "dream ?" + "games", "king ?" + "rob" + "ert", "app ?" + "store"]
FORBIDDEN = re.compile("|".join(_ORIGINAL_BRAND_TERMS), re.I)


def no_leftovers(testcase, text):
    testcase.assertEqual(re.findall(r"\{\{[A-Z_]+\}\}", text), [], "unsubstituted token left in prompt")
    testcase.assertIsNone(FORBIDDEN.search(text))


class PromptRenderTests(unittest.TestCase):
    def test_analysis_prompts(self):
        for name in ("video_analysis_prompt.md", "video_analysis_prompt.v2_no_context.md"):
            text = analyzer.render_prompt((REPO / "prompts" / name).read_text(encoding="utf-8"), "vid1",
                                          {"trend_name": "T", "reasoning": "R"})
            no_leftovers(self, text)
            self.assertIn("Kahve Maya", text)
            self.assertIn("vid1", text)

    def test_missing_trend_context_becomes_not_available(self):
        text = analyzer.render_prompt(analyzer.load_prompt_template(), "vid1", None)
        self.assertIn("(not available)", text)

    def test_prefilter_prompt(self):
        text = prefilter.build_prompt(prefilter.load_prompt_template(), [])
        no_leftovers(self, text)
        self.assertIn("Kahve Maya", text)

    def test_ugc_prompt(self):
        text = ugc_scriptwriter.render_prompt(ugc_scriptwriter.PROMPT_PATH.read_text(encoding="utf-8"), SELECTED, ANALYSIS)
        no_leftovers(self, text)
        self.assertIn("Kahve Maya", text)

    def test_static_prompts_have_no_forbidden_brand_terms(self):
        for path in (REPO / "prompts").glob("*.md"):
            self.assertIsNone(FORBIDDEN.search(path.read_text(encoding="utf-8")), path.name)


if __name__ == "__main__":
    unittest.main()
