"""Brand profile loader: the single source of truth for who the ads are for.

Every prompt and agent that needs brand context reads it through this module instead of
hard-coding a brand. The profile lives in `config/brand_profile.json` (schema in
`config/brand_profile.schema.md`). Swap that file to re-target the whole pipeline.

    python -m agents.brand          # validate the profile and print the rendered contexts
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "config" / "brand_profile.json"
SUPPORTED_SCHEMA_VERSION = 1

_REQUIRED = {
    "schema_version": int,
    "name": str,
    "fictional": bool,
    "positioning": str,
    "audience": dict,
    "tone_of_voice": dict,
    "visual_identity": dict,
    "offer_examples": list,
    "do": list,
    "dont": list,
    "scoring": dict,
    "ad_script": dict,
}


class BrandProfileError(ValueError):
    pass


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    for key, typ in _REQUIRED.items():
        if key not in profile:
            raise BrandProfileError(f"brand profile is missing required key {key!r}")
        if not isinstance(profile[key], typ):
            raise BrandProfileError(f"brand profile key {key!r} must be {typ.__name__}")
    if profile["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise BrandProfileError(
            f"unsupported schema_version {profile['schema_version']} (loader supports {SUPPORTED_SCHEMA_VERSION})"
        )
    if not profile["name"].strip():
        raise BrandProfileError("brand profile 'name' must not be empty")
    fit = profile["scoring"].get("brand_fit")
    if not isinstance(fit, dict) or not fit.get("beats"):
        raise BrandProfileError("scoring.brand_fit.beats must list at least one beat")
    for beat in fit["beats"]:
        if not {"id", "name", "description"} <= set(beat):
            raise BrandProfileError(f"every beat needs id, name and description; got {sorted(beat)}")
    if not isinstance(profile["scoring"].get("licensing_safety", {}).get("guidance"), str):
        raise BrandProfileError("scoring.licensing_safety.guidance must be a string")
    for key in ("mention_style", "pip_card_caption"):
        if not isinstance(profile["ad_script"].get(key), str):
            raise BrandProfileError(f"ad_script.{key} must be a string")
    return profile


def load_brand_profile(path: Path | None = None) -> dict[str, Any]:
    """Load and validate a brand profile (default: config/brand_profile.json)."""
    target = Path(path) if path else PROFILE_PATH
    return validate_profile(json.loads(target.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_profile() -> dict[str, Any]:
    return load_brand_profile()


def brand_name(profile: dict[str, Any] | None = None) -> str:
    return (profile or default_profile())["name"]


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_brand_context(profile: dict[str, Any] | None = None, *, include_voice: bool = False) -> str:
    """Plain-text context block substituted for `{{BRAND_CONTEXT}}` in the analysis, pre-filter
    and script prompts. `include_voice` adds tone, offers and do/don't rules (script prompts)."""
    p = profile or default_profile()
    fit = p["scoring"]["brand_fit"]
    note = " (a fictional brand invented for a demo)" if p["fictional"] else ""
    beats = "\n".join(
        f"{i}. **{b['name']}** (`{b['id']}`): {b['description']}" for i, b in enumerate(fit["beats"], 1)
    )
    lines = [
        f"{p['name']}{note}: {p['positioning']}",
        "",
        f"Audience: {p['audience']['summary']}",
        "",
        f"Recurring narrative beats of the brand's own content; they anchor how `brand_fit` is judged:",
        "",
        beats,
        "",
        fit["summary"],
        "",
        "What fits:",
        _bullets(fit["fits"]),
        "",
        "What does not fit:",
        _bullets(fit["does_not_fit"]),
    ]
    if include_voice:
        voice = p["tone_of_voice"]
        vis = p["visual_identity"]
        lines += [
            "",
            f"Tone of voice: {voice['summary']}",
            _bullets(voice["voice_rules"]),
            "",
            f"Visual identity: palette {', '.join(vis['palette'])}; lighting: {vis['lighting']}; style: {vis['style']}.",
            f"Typical settings: {'; '.join(vis['settings'])}.",
            f"Things the brand offers (generic examples only, never prices or claims): {', '.join(p['offer_examples'])}.",
            "",
            "Do:",
            _bullets(p["do"]),
            "",
            "Do not:",
            _bullets(p["dont"]),
        ]
    return "\n".join(lines).strip()


def render_licensing_guidance(profile: dict[str, Any] | None = None) -> str:
    return (profile or default_profile())["scoring"]["licensing_safety"]["guidance"]


def banned_word_hits(text: str, profile: dict[str, Any] | None = None) -> list[str]:
    """Banned words/phrases (whole-word, case-insensitive) that appear in `text`."""
    p = profile or default_profile()
    hits = []
    for word in p["ad_script"].get("banned_words", []):
        if re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.I):
            hits.append(word)
    return hits


def apply_tokens(template: str, values: dict[str, str], *, last: dict[str, str] | None = None) -> str:
    """Replace `{{TOKEN}}` placeholders. Tokens in `last` are substituted after all others, so
    long free-text blocks (the brand context) can never be re-substituted by an earlier pass."""
    out = template
    for token, value in values.items():
        out = out.replace(token, value)
    for token, value in (last or {}).items():
        out = out.replace(token, value)
    return out


if __name__ == "__main__":
    profile = load_brand_profile()
    print(f"OK: {profile['name']} (fictional={profile['fictional']}), {len(profile['scoring']['brand_fit']['beats'])} beats")
    print("\n--- analysis/pre-filter context ---\n" + render_brand_context(profile))
    print("\n--- script context ---\n" + render_brand_context(profile, include_voice=True))
