"""LLM text-only pre-filter: one cheap text call over the full raw, undeduped multi-source trend
list -- before any video is downloaded or analyzed. It replaced an earlier sound/hashtag-overlap
union-find dedup. See DISCOVER.md for the rationale and prompts/trend_prefilter_prompt.md for the
actual prompt (loaded verbatim, never duplicated as an inline string, same discipline as
agents/process/analyzer.py's video-analysis prompt). The brand context in that prompt is
substituted at run time from config/brand_profile.json (agents/brand.py).

Model: gemini-3.8-flash, the same model the Process stage uses. Call shape (confirmed against the
installed google-genai 1.46.0): `client.models.generate_content(model=..., contents=...,
config=types.GenerateContentConfig(response_mime_type="application/json",
response_schema=PydanticModel))`.

Two-tier rejection design: a group can be rejected here, at the text/description stage (cheap, no
video spend at all), or later at Process stage after a real video is downloaded and watched (see
PROCESS.md). Both are legitimate, and intentionally different in kind: this stage rejects on
topic/format grounds from a description alone (dance challenge, celebrity/news-locked, etc.);
Process stage rejects on video-execution grounds (pacing, hook strength, production feasibility)
that can only be judged by actually watching the clip. Neither replaces the other.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from agents.brand import apply_tokens, default_profile, render_brand_context
from agents.discover.common import RepoPaths, redact_secrets
from agents.discover.sources import RawTrendEntry

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "prompts" / "trend_prefilter_prompt.md"

GEMINI_MODEL = "gemini-3.8-flash"

_RAW_ENTRY_FIELDS = (
    "trend_name", "editorial_description", "hashtags", "sound_name",
    "example_video_urls", "source", "source_url",
)


class PrefilterGroup(BaseModel):
    trend_id_hint: str
    canonical_trend_name: str
    merged_from_sources: list[str]
    status: Literal["accepted", "rejected"]
    reasoning: str
    example_video_urls: list[str]
    sound_name: str | None = None


class PrefilterResult(BaseModel):
    groups: list[PrefilterGroup]


def load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _entries_for_prompt(entries: list[RawTrendEntry]) -> list[dict]:
    """Reduced view of each raw entry -- exactly the fields the prompt asks for, no more
    (business_safe/popularity_hint/collected_at aren't part of the model's task)."""
    return [{k: getattr(e, k) for k in _RAW_ENTRY_FIELDS} for e in entries]


def build_prompt(prompt_template: str, entries: list[RawTrendEntry]) -> str:
    raw_json = json.dumps(_entries_for_prompt(entries), indent=2, ensure_ascii=False)
    profile = default_profile()
    return apply_tokens(
        prompt_template,
        {"{{BRAND_NAME}}": profile["name"]},
        last={"{{BRAND_CONTEXT}}": render_brand_context(profile), "{{RAW_TRENDS_JSON}}": raw_json},
    )


def load_fixture_result(paths: RepoPaths) -> PrefilterResult:
    fixture_path = paths.fixtures / "prefilter_result.json"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return PrefilterResult.model_validate(raw)


def call_prefilter_llm(
    entries: list[RawTrendEntry],
    api_key: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 10.0,
) -> PrefilterResult:
    """One text-only call over the whole raw entry list. Retries the whole call on
    failure -- the caller (build_trend_inventory) decides whether to fall back to fixtures
    once attempts are exhausted, same fallback-first pattern as every other live call in
    this pipeline."""
    prompt_template = load_prompt_template()
    prompt_text = build_prompt(prompt_template, entries)
    client = genai.Client(api_key=api_key)

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PrefilterResult,
                ),
            )
            return PrefilterResult.model_validate_json(response.text)
        except Exception as exc:  # noqa: BLE001 - caller decides how to degrade
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Prefilter LLM call attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def run_prefilter(
    entries: list[RawTrendEntry], paths: RepoPaths, gemini_api_key: str | None,
) -> tuple[PrefilterResult, bool]:
    """Returns (result, used_fallback). Falls back to data/fixtures/prefilter_result.json
    if there's no key or the live call fails after retries -- same fallback-first, never
    silent pattern as the rest of Discover (see CLAUDE.md)."""
    if not gemini_api_key:
        logger.warning("No Gemini API key configured; using fixtures for the trend pre-filter.")
        return load_fixture_result(paths), True
    if not entries:
        logger.warning("No raw trend entries to pre-filter; using fixtures.")
        return load_fixture_result(paths), True
    try:
        return call_prefilter_llm(entries, gemini_api_key), False
    except Exception as exc:  # noqa: BLE001 - any live-call failure should degrade, not crash the run
        logger.warning("Live prefilter LLM call failed (%s); falling back to fixtures.", redact_secrets(exc))
        return load_fixture_result(paths), True


def save_prefilter_result(result: PrefilterResult, paths: RepoPaths, week: str) -> Path:
    week_dir = paths.trends / week
    week_dir.mkdir(parents=True, exist_ok=True)
    out_path = week_dir / "prefilter_result.json"
    out_path.write_text(
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return out_path
