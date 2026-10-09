"""Adapter for https://www.socialpilot.co/blog/tiktok-trends -- weekly editorial trend post.

Real page structure confirmed by fetching the live page (2026-09-18) before writing any
selector: a `<h2>` whose text contains "Trending TikTok Formats and Challenges" (the exact
heading id is date-derived and changes weekly, so matched on visible text, not the id)
contains one `<h3>` per trend ("1. Reasons to Get a Bob", "2. My American Girl Doll", ...),
each followed by a description `<p>` and then a `<p>Hashtag: #a / #b</p>` line -- that
`Hashtag:` line is the only reliable per-trend hashtag source on this page.

Real finding, not assumed: the original spec for this source said it "flags trends as Approved
for Business Use or not" per trend. The live page does NOT do this -- "Approved for
Business Use" appears only as generic advice text (a Creative Center filtering tip repeated
twice, tied to *sounds* in general, not to any specific trend in the Formats section).
There is no per-trend boolean anywhere on the page. Rather than fabricate a business_safe
value the source doesn't actually provide, this adapter leaves it null for every entry --
see DISCOVER.md's business_safe section for the full note. If a future page revision adds
a real per-trend flag, this is the place to wire it in.

Similarly, the Formats section's embeds carry no caption/hashtag/sound text (just a
creator-name link), and the separate "Trending Sounds" section's sounds aren't reliably
tied back to a specific Format entry -- so sound_name is also left null here, not guessed
from proximity. The embed DOES carry a real video URL on its `cite` attribute though (same
as every other source, confirmed live) -- extracted via the shared
`sources/_tiktok_embed.py` helper as `example_video_urls`.

**Investigated per the 2026-09-18 anchor-quality diagnostic** (`anchor_inspection.md`
flagged some hashtags as apparently "fabricated from the trend title" rather than real):
checked this adapter's extraction code line by line -- it does not derive a hashtag from
`trend_name` anywhere; `_parse_hashtags` only ever reads the page's own literal
`<p>Hashtag: ...</p>` text. Re-fetching the live page confirmed two of six current entries'
`Hashtag:` lines ("myamericangirldoll", "whatilookedlikewhen") are, character for character,
just the trend's own title with spaces and punctuation removed -- but that's what
socialpilot's blog post itself literally writes on the page, not something this scraper
invents. This looks like the source blog inventing a tag when it had no real catchphrase or
sound name to reach for (contrast with "freakedout"/"ohboy", real catchphrases from the
trend's own audio) -- a real, source-side data-quality pattern worth knowing about, not a
scraper bug to fix here. It's exactly this kind of unreliability (see DISCOVER.md) that
motivated dropping hashtag-based search anchors entirely in favor of the LLM pre-filter +
canonical-video-URL design below.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from agents.discover.common import http_get_with_backoff
from agents.discover.sources import RawTrendEntry
from agents.discover.sources._tiktok_embed import extract_video_urls

logger = logging.getLogger(__name__)

SOURCE_NAME = "socialpilot"
SOURCE_URL = "https://www.socialpilot.co/blog/tiktok-trends"

_FORMATS_SECTION_RE = re.compile(
    r"<h2>.*?Trending TikTok Formats and Challenges.*?</h2>(.*?)<h2>", re.IGNORECASE | re.DOTALL,
)
_TREND_BLOCK_RE = re.compile(r"<h3>.*?</h3>.*?(?=<h3>|\Z)", re.DOTALL)
_HEADING_TEXT_RE = re.compile(r"<h3>(.*?)</h3>", re.DOTALL)
_HASHTAG_LINE_RE = re.compile(r"<p>\s*Hashtag:\s*(.*?)</p>", re.IGNORECASE | re.DOTALL)
_DESCRIPTION_LINE_RE = re.compile(r"<h3>.*?</h3>\s*<p>(.*?)</p>", re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def _parse_trend_name(heading_html: str) -> str:
    text = _clean_text(heading_html)
    # Headings are numbered ("1. Reasons to Get a Bob") -- strip the leading ordinal.
    return re.sub(r"^\d+\.\s*", "", text).strip()


def _parse_hashtags(block_html: str) -> list[str]:
    match = _HASHTAG_LINE_RE.search(block_html)
    if not match:
        return []
    raw = _clean_text(match.group(1))
    parts = re.split(r"[/,]", raw)
    return [p.strip().lstrip("#").strip() for p in parts if p.strip()]


def _parse_description(block_html: str) -> str | None:
    """The one descriptive `<p>` immediately after the heading, before the `Hashtag:`
    line -- e.g. "A deadpan on-screen list where every 'reason' is just the word 'bob'..."
    Plain extracted text, reference data only -- see RawTrendEntry.editorial_description."""
    match = _DESCRIPTION_LINE_RE.match(block_html)
    if not match:
        return None
    text = _clean_text(match.group(1))
    return text or None


def fetch_trends() -> list[RawTrendEntry]:
    """Raises on any fetch/parse failure -- the caller (merge.py) decides how to degrade."""
    response = http_get_with_backoff(
        SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; tiktok-trend-analysis/1.0)"},
    )
    page_html = response.text

    section_match = _FORMATS_SECTION_RE.search(page_html)
    if not section_match:
        raise ValueError("socialpilot: could not locate the 'Trending TikTok Formats' section")
    section_html = section_match.group(1)

    collected_at = datetime.now(timezone.utc).isoformat()
    entries: list[RawTrendEntry] = []
    for block_match in _TREND_BLOCK_RE.finditer(section_html):
        block_html = block_match.group(0)
        heading_match = _HEADING_TEXT_RE.search(block_html)
        if not heading_match:
            continue
        trend_name = _parse_trend_name(heading_match.group(1))
        if not trend_name:
            continue
        hashtags = _parse_hashtags(block_html)
        editorial_description = _parse_description(block_html)
        example_video_urls = extract_video_urls(block_html)
        entries.append(
            RawTrendEntry(
                trend_name=trend_name,
                hashtags=hashtags,
                sound_name=None,
                business_safe=None,
                popularity_hint=None,
                source=SOURCE_NAME,
                source_url=SOURCE_URL,
                collected_at=collected_at,
                editorial_description=editorial_description,
                example_video_urls=example_video_urls,
            )
        )

    if not entries:
        raise ValueError("socialpilot: 'Formats' section found but yielded zero trend entries")
    return entries
