"""Script validators and the brand-profile-driven rules (free, offline)."""
import copy
import json
import unittest

from agents.brand import default_profile
from agents.generate.ugc_scriptwriter import REQUIRED_NO_TEXT_SENTENCE, Persona, UGCAdScript, UGCShot, validate_script

NO_TEXT = ("Absolutely no on-screen text of any kind anywhere in the shot: no captions, subtitles, "
           "burned-in words, letters, numbers, timestamps, watermarks, logos, or UI elements.")
PERSONA = "A woman in her late twenties with short curly hair wearing a denim jacket at a window table in a quiet cafe."


def shot(order, dur, dialogue, extra=""):
    prompt = (f"Photorealistic vertical 9:16 selfie-style video, front-facing camera. {PERSONA} {extra} "
              f'She says: "{dialogue}" {NO_TEXT}')
    return UGCShot(order=order, duration_sec=dur, visual_prompt=prompt, onscreen_text="it is fine", dialogue=dialogue)


def good_script():
    return UGCAdScript(
        video_id="v", trend_id="t", persona=Persona(description=PERSONA, identity_consistency_method="chain"),
        narrative_mapping="x",
        shots=[shot(1, 4, "this week was a blur but"), shot(2, 8, "twenty minutes at Kahve Maya fixed it")],
    )


class UgcValidatorTests(unittest.TestCase):
    def test_good_script_passes(self):
        self.assertEqual(validate_script(good_script()), [])

    def test_brand_must_be_named_in_dialogue(self):
        s = good_script()
        s.shots[1] = shot(2, 8, "twenty minutes somewhere quiet fixed it")
        self.assertTrue(any("Kahve Maya" in p for p in validate_script(s)))

    def test_phone_and_screen_words_are_rejected_but_required_sentence_is_not(self):
        s = good_script()
        s.shots[0] = shot(1, 4, "this week was a blur but", extra="She holds a phone.")
        problems = validate_script(s)
        self.assertTrue(any("forbidden prop" in p for p in problems))
        self.assertEqual(validate_script(good_script()), [])  # 'on-screen' in the required sentence is fine

    def test_banned_brand_words_are_rejected(self):
        s = good_script()
        s.shots[1] = shot(2, 8, "a cocktail at Kahve Maya fixed it")
        self.assertTrue(any("bans" in p for p in validate_script(s)))

    def test_timing_rules(self):
        s = good_script()
        s.shots[1].duration_sec = 6
        self.assertTrue(any("exactly 8" in p for p in validate_script(s)))

    def test_persona_must_be_restated(self):
        s = good_script()
        s.shots[1].visual_prompt = f"Photorealistic selfie video of someone. {NO_TEXT}"
        self.assertTrue(any("persona" in p for p in validate_script(s)))

    def test_other_brand_name_is_enforced_when_the_profile_changes(self):
        p = copy.deepcopy(default_profile())
        p["name"] = "Example Brand"
        self.assertTrue(any("Example Brand" in x for x in validate_script(good_script(), p)))


class WorkedExampleTests(unittest.TestCase):
    """The worked example inside the UGC prompt must pass the validator that gates real output
    (an example that its own validator rejects would teach the model to fail)."""

    def test_prompt_worked_example_is_valid(self):
        import re
        from agents.generate.ugc_scriptwriter import PROMPT_PATH
        text = PROMPT_PATH.read_text(encoding="utf-8")
        block = re.search(r"## 7\. Worked example.*?```json\n(.*?)\n```", text, re.S).group(1)
        script = UGCAdScript.model_validate(json.loads(block.replace("{{BRAND_NAME}}", "Kahve Maya")))
        self.assertEqual(validate_script(script), [])

    def test_shipped_generated_script_is_valid(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "examples" / "generated_ad_script.json"
        script = UGCAdScript.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(validate_script(script), [])
