"""Process stage: Gemini video-native analysis + deterministic weighted scoring.

For each downloaded video, uploads it to Gemini (File API) alongside the prompt template
in prompts/video_analysis_prompt.md, parses the structured JSON response into
{trend_id}/raw/{video_id}.analysis.json, then computes a weighted accept/reject decision
in Python (never trusting a model-computed score) into
{trend_id}/raw/{video_id}.decision.json.

**Real bug found and fixed (2026-09-18):** this module previously called
`client.interactions.create(...)`, which does not exist on the installed `google-genai`
1.46.0 SDK (`AttributeError: 'Client' object has no attribute 'interactions'`, confirmed
live) -- the "Interactions API" this file's original docstring described isn't part of any
installed SDK surface (`client.models`, `client.files`, `client.chats`, `client.batches`,
`client.caches`, `client.tunings`, `client.operations` are the real top-level namespaces;
confirmed by introspecting the installed package directly, not by reading docs or relying
on memory -- the same mistake that caused this bug in the first place). The correct,
live-confirmed call is `client.models.generate_content(model=..., contents=[...],
config=types.GenerateContentConfig(response_mime_type=..., response_schema=...))`, with the
uploaded video referenced via `types.Part.from_uri(file_uri=uploaded.uri,
mime_type=uploaded.mime_type)` alongside the prompt text in `contents`. `client.files.upload`
/ `client.files.get` (the upload+poll half of this module) were already correct and are
unchanged.

Also re-confirmed live, not assumed: the nested Pydantic schema (`VideoAnalysis` ->
`Scores` -> `ScoreEntry`, which produces `$ref`/`$defs` in `model_json_schema()`) validates
and parses cleanly against a real video with the corrected call -- the "known risk" flagged
in PROCESS.md (some Gemini API surfaces rejecting complex nested schemas) did not
materialize.

Model: gemini-3.8-flash, video sent natively via the File API (not extracted frames) -- our
real videos (236KB-17.4MB) are comfortably within both the 100MB inline and 2GB File API
ceilings; File API is used regardless since the whole point of `narrative_skeleton` is
capturing timing, and video-native processing (vs. frame extraction) is what makes that
possible. See PROCESS.md.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from agents.brand import (
    apply_tokens, brand_name, default_profile, render_brand_context, render_licensing_guidance,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "prompts" / "video_analysis_prompt.md"
WEIGHTS_PATH = REPO_ROOT / "config" / "scoring_weights.json"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

GEMINI_MODEL = "gemini-3.8-flash"

# Sampling parameters passed to every scoring call (merged into GenerateContentConfig). Empty =
# the server defaults, which is what the original dataset was scored with -- and what is kept on
# purpose: a 270-analysis experiment (PROCESS.md "Scoring stability", artifacts/score_stability_experiment.json)
# found that temperature=0, a fixed seed, and both together do NOT reliably reduce score variance (and
# none is deterministic), while Google advises leaving Gemini 3 temperature at its default. The dominant
# source of run-to-run drift is the prompt context, not sampling.
GENERATION_PARAMS: dict = {}

SCORE_CRITERIA = [
    "narrative_transferability",
    "hook_strength",
    "production_feasibility",
    "brand_fit",
    "licensing_safety",
]


class ScoreEntry(BaseModel):
    value: int = Field(ge=1, le=5)
    reasoning: str


class Scores(BaseModel):
    narrative_transferability: ScoreEntry
    hook_strength: ScoreEntry
    production_feasibility: ScoreEntry
    brand_fit: ScoreEntry
    licensing_safety: ScoreEntry


class VideoAnalysis(BaseModel):
    video_id: str
    content_summary: str
    narrative_skeleton: str
    hook_type: Literal["POV", "transformation", "storytime", "reveal", "comedy_skit", "other"]
    hook_timing_sec: float
    pacing: Literal["fast", "medium", "slow"]
    has_spoken_dialogue: bool
    has_onscreen_text: bool
    requires_specific_persona: bool
    emotional_tone: str
    scores: Scores


def discover_videos() -> list[Path]:
    return sorted(ARTIFACTS_ROOT.glob("*/raw/*.mp4"))


def analysis_paths(video_path: Path) -> tuple[Path, Path]:
    """Analysis/decision files live next to the source video in raw/, named after the
    video_id -- not one file per trend_id, since a trend folder holds many videos."""
    video_id = video_path.stem
    raw_dir = video_path.parent
    return raw_dir / f"{video_id}.analysis.json", raw_dir / f"{video_id}.decision.json"


def load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def render_prompt(template: str, video_id: str, trend_context: dict[str, str] | None = None,
                  profile: dict | None = None) -> str:
    """Fill the prompt template: per-video tokens first, the (long, free-text) brand context
    last so nothing inside it can be re-substituted. Brand text comes from
    config/brand_profile.json, never from this module."""
    p = profile or default_profile()
    ctx = trend_context or {"trend_name": "", "reasoning": ""}
    return apply_tokens(
        template,
        {
            "{{VIDEO_ID}}": video_id,
            "{{TREND_NAME}}": ctx["trend_name"] or "(not available)",
            "{{TREND_CONTEXT_REASONING}}": ctx["reasoning"] or "(not available)",
            "{{BRAND_NAME}}": p["name"],
            "{{LICENSING_GUIDANCE}}": render_licensing_guidance(p),
        },
        last={"{{BRAND_CONTEXT}}": render_brand_context(p)},
    )


def analysis_is_current(analysis_path: Path, profile: dict | None = None) -> bool:
    """True if `analysis_path` exists AND was scored for the current brand. Analyses written
    for another brand (or before the brand stamp existed) are stale: their `brand_fit` and
    `licensing_safety` scores answer a different question, so they are re-scored, not reused."""
    if not analysis_path.exists():
        return False
    try:
        stamped = json.loads(analysis_path.read_text(encoding="utf-8")).get("scored_for_brand")
    except (OSError, ValueError):
        return False
    return stamped == brand_name(profile)


def load_scoring_weights() -> dict[str, float]:
    return json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))


def load_trend_context(video_path: Path) -> dict[str, str]:
    """Real per-trend context available today: `trend_name` (the LLM pre-filter's
    canonical name for this format) and `reasoning` (its one-sentence accept rationale --
    see DISCOVER.md/prefilter.py). Note this is NOT `editorial_descriptions`/`business_safe`
    -- those fields belonged to an earlier Discover design and no longer exist in
    `trend_meta.json` after the LLM-prefilter redesign -- this is the real, current substitute
    for "what a human/system said this format is about." Returns empty
    strings (not a raised error) if `trend_meta.json` is missing, so a stale/incomplete
    artifact folder degrades to no context rather than crashing the whole batch.

    KNOWN LIMITATION (measured on the original run, see README "An anchoring bias in the
    scoring prompt" and PROCESS.md "Scoring stability"): this text is regenerated by an LLM on
    every pipeline run and anchors scores upward by about 0.4 on average. It is kept because the
    original analyses were scored with it; the recommended production fix is to drop it AND
    re-fit the threshold on a much larger sample (prompts/video_analysis_prompt.v2_no_context.md)."""
    trend_meta_path = video_path.parent.parent / "trend_meta.json"
    if not trend_meta_path.exists():
        return {"trend_name": "", "reasoning": ""}
    meta = json.loads(trend_meta_path.read_text(encoding="utf-8"))
    return {
        "trend_name": meta.get("trend_name") or "",
        "reasoning": meta.get("reasoning") or "",
    }


def _upload_and_wait(client: genai.Client, video_path: Path, *, poll_interval: float = 3.0):
    uploaded = client.files.upload(file=str(video_path))
    while not uploaded.state or uploaded.state.name not in ("ACTIVE", "FAILED"):
        time.sleep(poll_interval)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state and uploaded.state.name == "FAILED":
        raise RuntimeError(f"Gemini file processing failed for {video_path.name}")
    return uploaded


_RETRY_AFTER_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.IGNORECASE)


def _retry_delay(exc: Exception, attempt: int, base_delay: float) -> float:
    """Prefer the server's own suggested wait (the free-tier quota error names one
    explicitly, e.g. "Please retry in 57.9s") over blind exponential backoff -- found via
    live testing that the free tier's per-minute quota for this model needs a longer wait
    than a short exponential backoff reliably covers."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        return float(match.group(1)) + 2.0  # small buffer past the server's own estimate
    return base_delay * (2 ** (attempt - 1))


def analyze_video(
    client: genai.Client,
    video_path: Path,
    video_id: str,
    prompt_template: str,
    trend_context: dict[str, str] | None = None,
    *,
    max_attempts: int = 4,
    base_delay: float = 15.0,
) -> VideoAnalysis:
    """Upload the video and request a schema-conformant analysis. Retries the whole
    upload+analyze sequence on failure -- callers decide whether to give up on this video
    and move on to the rest of the batch."""
    prompt_text = render_prompt(prompt_template, video_id, trend_context)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            uploaded = _upload_and_wait(client, video_path)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type),
                    prompt_text,
                ],
                config=types.GenerateContentConfig(
                    **GENERATION_PARAMS,
                    response_mime_type="application/json",
                    response_schema=VideoAnalysis,
                ),
            )
            return VideoAnalysis.model_validate_json(response.text)
        except Exception as exc:  # noqa: BLE001 - one video's failure shouldn't abort the batch
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = _retry_delay(exc, attempt, base_delay)
            logger.warning(
                "Analysis attempt %d/%d failed for %s (%s); retrying in %.1fs",
                attempt, max_attempts, video_id, exc, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def compute_decision(analysis: VideoAnalysis, weights: dict[str, float]) -> dict[str, Any]:
    """Weighted score is always computed here, deterministically, from analysis.json +
    the weights config -- never trusted from the model's own output."""
    scores = analysis.scores.model_dump()
    weighted_score = sum(scores[c]["value"] * weights[c] for c in SCORE_CRITERIA)
    threshold = weights["accept_threshold"]
    status = "accepted" if weighted_score >= threshold else "rejected"

    weakest = min(SCORE_CRITERIA, key=lambda c: scores[c]["value"])
    reasoning_summary = (
        f"Weighted score {weighted_score:.2f} vs. threshold {threshold} -> {status}. "
        f"Weakest criterion: {weakest} ({scores[weakest]['value']}/5 -- {scores[weakest]['reasoning']})"
    )

    return {
        "video_id": analysis.video_id,
        "weighted_score": round(weighted_score, 4),
        "threshold": threshold,
        "status": status,
        "reasoning_summary": reasoning_summary,
    }


def process_video(
    client: genai.Client,
    video_path: Path,
    prompt_template: str,
    weights: dict[str, float],
    *,
    force: bool,
) -> dict[str, Any] | None:
    """Analyze + score one video, writing analysis.json and decision.json next to it.
    Returns None (without calling the API) if already analyzed for the current brand and force is False."""
    video_id = video_path.stem
    analysis_path, decision_path = analysis_paths(video_path)
    if analysis_is_current(analysis_path) and not force:
        logger.info("Skipping already-analyzed video %s", video_id)
        return None
    if analysis_path.exists() and not force:
        logger.info("Re-scoring %s: existing analysis was scored for a different brand", video_id)

    trend_context = load_trend_context(video_path)
    analysis = analyze_video(client, video_path, video_id, prompt_template, trend_context)
    stamp = {"scored_for_brand": brand_name()}
    analysis_path.write_text(
        json.dumps({**analysis.model_dump(), **stamp}, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    decision = {**compute_decision(analysis, weights), **stamp}
    decision_path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    return decision
