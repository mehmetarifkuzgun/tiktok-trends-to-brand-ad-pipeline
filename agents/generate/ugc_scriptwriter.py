"""Generate stage, UGC style, step 1: turn one analyzed trend video into a two-shot,
photorealistic UGC-style ad script (spoken dialogue, invented persona, no phone/screen prop, no
brand imagery rendered by Veo -- the brand/product card is composited in post by assembler.py).

Design: no image seeding for shot 1 (there is no real image of an invented person), a strengthened
no-text instruction enforced both in the prompt and via the SDK's negative_prompt field (see
video_generator.py / veo_client.py), and a hard no-phone-or-device rule enforced by the validator
below, not just asked for in prose. All brand context (name, beats, voice, props, banned words)
is read from config/brand_profile.json via agents/brand.py; nothing brand-specific lives here.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from agents.brand import apply_tokens, banned_word_hits, default_profile, render_brand_context
from agents.generate.selector import ARTIFACTS_ROOT, REPO_ROOT, rel

logger = logging.getLogger(__name__)

SCRIPT_MODEL = "gemini-3.8-flash"
PROMPT_PATH = REPO_ROOT / "prompts" / "ugc_ad_script_prompt.md"

MIN_TOTAL_SEC, MAX_TOTAL_SEC = 8, 15
SETUP_DURATIONS = (4, 6)
PAYOFF_DURATION = 8
MAX_ONSCREEN_WORDS = 8
MAX_VISUAL_PROMPT_WORDS = 220  # prompts carry a persona and a quoted dialogue line
MIN_PERSONA_WORD_OVERLAP = 0.6  # fraction of the persona description's distinctive words expected in each shot

REQUIRED_NO_TEXT_SENTENCE = (
    "absolutely no on-screen text of any kind anywhere in the shot"
)
# Whole-word matches only, so e.g. "microphone" or "telephone pole" don't false-positive.
_FORBIDDEN_PROP_WORDS = (
    "phone", "smartphone", "iphone", "tablet", "ipad", "screen", "device",
)
# Phrases that legitimately contain a forbidden-prop word without describing an in-scene prop:
# the required no-text sentence itself contains "on-screen", and "phone-camera"/"phone camera"
# is the natural way to describe amateur selfie-video quality (the camera doing the recording,
# not something the person holds or looks at). Found by real testing, not anticipated up front
# -- both collided with the forbidden-word check before this. Stripped from a working copy of
# the text before scanning for forbidden props, so a genuine prop mention downstream still gets
# caught.
_LEGITIMATE_PHRASES = (
    REQUIRED_NO_TEXT_SENTENCE,
    "on-screen", "on screen",
    "phone-camera", "phone camera", "phone-camera video", "front-facing camera",
)


def _strip_legitimate_phrases(text: str) -> str:
    sanitized = text.lower()
    for phrase in _LEGITIMATE_PHRASES:
        sanitized = sanitized.replace(phrase.lower(), " ")
    return sanitized


class UGCShot(BaseModel):
    order: int
    duration_sec: int
    visual_prompt: str
    onscreen_text: Optional[str] = None
    dialogue: str


class Persona(BaseModel):
    description: str
    identity_consistency_method: str


class UGCAdScript(BaseModel):
    video_id: str
    trend_id: str
    persona: Persona
    narrative_mapping: str
    shots: list[UGCShot]


def render_prompt(template: str, selected: dict, analysis: dict, profile: dict | None = None) -> str:
    p = profile or default_profile()
    script_cfg = p["ad_script"]
    values = {
        "{{VIDEO_ID}}": selected["video_id"],
        "{{TREND_ID}}": selected["trend_id"],
        "{{NARRATIVE_SKELETON}}": analysis["narrative_skeleton"],
        "{{HOOK_TYPE}}": analysis["hook_type"],
        "{{HOOK_TIMING_SEC}}": str(analysis["hook_timing_sec"]),
        "{{PACING}}": analysis["pacing"],
        "{{EMOTIONAL_TONE}}": analysis["emotional_tone"],
        "{{MENTION_STYLE}}": script_cfg["mention_style"],
        "{{PERSONA_HINTS}}": script_cfg.get("persona_hints", ""),
        "{{SETTING_HINTS}}": "; ".join(script_cfg.get("setting_hints", [])),
        "{{ALLOWED_PROPS}}": "; ".join(script_cfg.get("allowed_props", [])) or "none",
        "{{BANNED_WORDS}}": ", ".join(script_cfg.get("banned_words", [])) or "none",
        "{{LANGUAGE}}": script_cfg.get("language", "English"),
        "{{BRAND_NAME}}": p["name"],
    }
    # The long brand-context block is substituted last so nothing inside it is re-substituted.
    return apply_tokens(template, values, last={"{{BRAND_CONTEXT}}": render_brand_context(p, include_voice=True)})


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _forbidden_prop_hits(prompt_text: str) -> list[str]:
    have = set(_words(_strip_legitimate_phrases(prompt_text)))
    return [w for w in _FORBIDDEN_PROP_WORDS if w in have]


def validate_script(script: UGCAdScript, profile: dict | None = None) -> list[str]:
    """Problems that would make the script unusable or break this style's standing rules
    (no phone/prop, strengthened no-text instruction, durations). Empty = valid."""
    p = profile or default_profile()
    problems: list[str] = []
    if len(script.shots) != 2:
        return [f"expected exactly 2 shots, got {len(script.shots)}"]
    if [s.order for s in script.shots] != [1, 2]:
        problems.append(f"shot orders must be [1, 2], got {[s.order for s in script.shots]}")
    setup, payoff = script.shots
    if setup.duration_sec not in SETUP_DURATIONS:
        problems.append(f"shot 1 duration_sec must be one of {SETUP_DURATIONS}, got {setup.duration_sec}")
    if payoff.duration_sec != PAYOFF_DURATION:
        problems.append(f"shot 2 duration_sec must be exactly {PAYOFF_DURATION}, got {payoff.duration_sec}")

    for s in script.shots:
        words = len(s.visual_prompt.split())
        if words > MAX_VISUAL_PROMPT_WORDS:
            problems.append(f"shot {s.order}: visual_prompt is {words} words (max {MAX_VISUAL_PROMPT_WORDS})")
        if REQUIRED_NO_TEXT_SENTENCE not in s.visual_prompt.lower():
            problems.append(
                f"shot {s.order}: visual_prompt must end with the exact required no-text sentence "
                f"(see prompts/ugc_ad_script_prompt.md section 5)"
            )
        prop_hits = _forbidden_prop_hits(s.visual_prompt)
        if prop_hits:
            problems.append(
                f"shot {s.order}: visual_prompt references a forbidden prop word {prop_hits} -- "
                f"no phone/screen/device may be described, held, or shown (see section 1's hard rule)"
            )
        if not s.dialogue.strip():
            problems.append(f"shot {s.order}: dialogue must not be empty")
        if s.onscreen_text and len(s.onscreen_text.split()) > MAX_ONSCREEN_WORDS:
            problems.append(f"shot {s.order}: onscreen_text longer than {MAX_ONSCREEN_WORDS} words")
        banned = banned_word_hits(" ".join([s.visual_prompt, s.dialogue, s.onscreen_text or ""]), p)
        if banned:
            problems.append(f"shot {s.order}: contains words the brand profile bans: {banned}")

    total = sum(s.duration_sec for s in script.shots)
    if not MIN_TOTAL_SEC <= total <= MAX_TOTAL_SEC:
        problems.append(f"total duration {total}s outside {MIN_TOTAL_SEC}-{MAX_TOTAL_SEC}s")

    combined_dialogue = " ".join(s.dialogue for s in script.shots).lower()
    if p["name"].lower() not in combined_dialogue:
        problems.append(f"{p['name']} must be named somewhere in the combined shot 1 + shot 2 dialogue")

    # Word-overlap, not byte-exact substring: shot 2 is chain-seeded on shot 1's actual last
    # frame, so visual continuity is already guaranteed by the image, not the prompt text --
    # requiring the model to reproduce a full paragraph with zero tense/phrasing variation
    # (e.g. "standing" vs "stands") was found, by real testing, to fail valid scripts for no
    # real benefit. This checks real consistency (most of the same descriptive words show up
    # in both shots) without demanding a verbatim copy.
    persona_words = {w for w in _words(script.persona.description) if len(w) >= 4}
    if persona_words:
        for shot in script.shots:
            have = set(_words(shot.visual_prompt))
            overlap = len(persona_words & have) / len(persona_words)
            if overlap < MIN_PERSONA_WORD_OVERLAP:
                problems.append(
                    f"shot {shot.order}: visual_prompt shares only {overlap:.0%} of the persona "
                    f"description's distinctive words (need >= {MIN_PERSONA_WORD_OVERLAP:.0%}) -- "
                    f"restate the same persona/setting, even if not word-for-word"
                )
    return problems


def _normalize(script: UGCAdScript, selected: dict) -> UGCAdScript:
    script.video_id = selected["video_id"]
    script.trend_id = selected["trend_id"]
    for s in script.shots:
        if s.onscreen_text is not None and s.onscreen_text.strip().lower() in {"", "null", "none"}:
            s.onscreen_text = None
    return script


def generate_script(
    client: genai.Client, prompt: str, selected: dict, *, max_attempts: int = 4, profile: dict | None = None,
) -> UGCAdScript:
    """Call the model; on an API error or a script that fails validation, retry (feeding the
    specific validation problems back to the model, feedback-and-retry pattern).
    Four attempts by default: the no-phone / persona-verbatim / brand-
    named-in-dialogue rules together are a harder target to hit in one shot."""
    feedback = ""
    last_problem = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=SCRIPT_MODEL,
                contents=prompt + feedback,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=UGCAdScript,
                ),
            )
            script = _normalize(UGCAdScript.model_validate_json(response.text), selected)
        except Exception as exc:  # noqa: BLE001 - transient API/parse failure: back off and retry
            last_problem = f"{type(exc).__name__}: {exc}"
            logger.warning("UGC script attempt %d/%d failed (%s)", attempt, max_attempts, last_problem)
            time.sleep(10 * attempt)
            continue
        problems = validate_script(script, profile)
        if not problems:
            return script
        last_problem = "; ".join(problems)
        logger.warning("UGC script attempt %d/%d invalid: %s", attempt, max_attempts, last_problem)
        feedback = (
            "\n\n## Correction\nYour previous answer was rejected for these reasons: "
            + last_problem + ". Fix all of them and answer again with only the JSON."
        )
    raise RuntimeError(f"No valid UGC ad script for {selected['video_id']} after {max_attempts} attempts: {last_problem}")


def write_ugc_ad_script(client: genai.Client, selected: dict, *, force: bool = False) -> Path:
    """Write artifacts/{trend_id}/ad_script.json from the trend's analysis (skipped if present
    and not `force`, idempotent like every other step)."""
    out_path = ARTIFACTS_ROOT / selected["trend_id"] / "ad_script.json"
    if out_path.exists() and not force:
        logger.info("ad_script.json already exists for %s; keeping it (use --force to redo)", selected["trend_id"])
        return out_path

    analysis = json.loads((REPO_ROOT / selected["analysis_path"]).read_text(encoding="utf-8"))
    profile = default_profile()
    prompt = render_prompt(PROMPT_PATH.read_text(encoding="utf-8"), selected, analysis, profile)
    script = generate_script(client, prompt, selected, profile=profile)

    payload: dict[str, Any] = script.model_dump()
    payload["meta"] = {
        "style": "ugc",
        "script_model": SCRIPT_MODEL,
        "brand": profile["name"],
        "prompt_file": rel(PROMPT_PATH),
        "source_analysis_path": selected["analysis_path"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s", out_path)
    return out_path
