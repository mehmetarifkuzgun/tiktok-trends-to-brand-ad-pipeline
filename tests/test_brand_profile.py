"""Brand profile loader and renderers: free, offline, no API calls."""
import copy
import json
import unittest

from agents import brand


class BrandProfileTests(unittest.TestCase):
    def test_shipped_profile_is_valid_and_fictional(self):
        p = brand.load_brand_profile()
        self.assertEqual(p["name"], "Kahve Maya")
        self.assertTrue(p["fictional"])
        self.assertGreaterEqual(len(p["scoring"]["brand_fit"]["beats"]), 1)

    def test_validation_rejects_missing_and_bad_values(self):
        p = brand.load_brand_profile()
        for key in ("name", "scoring", "ad_script"):
            bad = copy.deepcopy(p)
            del bad[key]
            with self.assertRaises(brand.BrandProfileError):
                brand.validate_profile(bad)
        bad = copy.deepcopy(p)
        bad["schema_version"] = 99
        with self.assertRaises(brand.BrandProfileError):
            brand.validate_profile(bad)
        bad = copy.deepcopy(p)
        bad["scoring"]["brand_fit"]["beats"] = []
        with self.assertRaises(brand.BrandProfileError):
            brand.validate_profile(bad)

    def test_context_contains_beats_and_voice_only_when_asked(self):
        p = brand.load_brand_profile()
        plain = brand.render_brand_context(p)
        voiced = brand.render_brand_context(p, include_voice=True)
        for beat in p["scoring"]["brand_fit"]["beats"]:
            self.assertIn(beat["name"], plain)
        self.assertNotIn("Tone of voice", plain)
        self.assertIn("Tone of voice", voiced)
        self.assertIn("Do not:", voiced)

    def test_swapping_the_profile_swaps_the_brand_everywhere(self):
        p = copy.deepcopy(brand.load_brand_profile())
        p["name"] = "Example Brand"
        self.assertIn("Example Brand", brand.render_brand_context(p))
        self.assertNotIn("Kahve Maya", brand.render_brand_context(p, include_voice=True))

    def test_banned_words_are_whole_word_and_case_insensitive(self):
        p = brand.load_brand_profile()
        self.assertEqual(brand.banned_word_hits("a Cocktail at night", p), ["cocktail"])
        self.assertEqual(brand.banned_word_hits("a winery tour", p), [])  # 'wine' inside 'winery' is not a hit
        self.assertEqual(brand.banned_word_hits("calm morning pour-over", p), [])

    def test_apply_tokens_substitutes_last_block_after_the_rest(self):
        out = brand.apply_tokens("{{A}} {{CTX}}", {"{{A}}": "x"}, last={"{{CTX}}": "has {{A}} inside"})
        self.assertEqual(out, "x has {{A}} inside")  # nothing inside the last block is re-substituted


if __name__ == "__main__":
    unittest.main()
