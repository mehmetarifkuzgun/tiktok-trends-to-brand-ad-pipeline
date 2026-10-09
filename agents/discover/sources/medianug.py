"""Adapter for https://www.medianug.com/tiktok-trending-report -- weekly editorial trend
post using a consistent "Trend Recap" template per entry, with a video-count figure per
trend (the only source of `popularity_hint` in this pipeline).

Real page structure confirmed by fetching the live page (2026-09-18) before writing any
selector: a Webflow CMS list. Each trend is `<h3 class="trend-title">NAME</h3>` followed by
`<div class="videos-tag-border"><div class="videos-tag w-embed">27.5K videos</div></div>`
(or literally "n/a videos" when the site has no count for that entry), then a
`<div class="trend-desc w-richtext">` ("Trend Recap" / "How Brands Can Join" copy) and a
`<div class="trend-embed w-richtext">` holding the same TikTok oEmbed widget shape as
ramdam's -- parsed with the shared `_tiktok_embed` helper.

The front page alone (fetched here) already spans many months back through late 2024 in one
document -- the originally-noted "monthly archive links back to 2024" exist as separate pages too,
but aren't fetched here: the single front-page pull already yields far more volume than
needed, and hitting the archive would add requests for no volume benefit (see DISCOVER.md).

No business-safety flag exists on this page (confirmed) -- left null like the other three
non-socialpilot sources.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from agents.discover.common import http_get_with_backoff
from agents.discover.sources import RawTrendEntry
from agents.discover.sources._tiktok_embed import extract_hashtags_and_sound, extract_video_urls

logger = logging.getLogger(__name__)

SOURCE_NAME = "medianug"
SOURCE_URL = "https://www.medianug.com/tiktok-trending-report"

_HEADING_RE = re.compile(r'<h3 class="trend-title">(.*?)</h3>', re.DOTALL)
_VIDEO_COUNT_RE = re.compile(r'<div class="videos-tag w-embed">\s*([^<]*?)\s*videos\s*</div>', re.IGNORECASE)
# The "Trend Recap" / "How Brands Can Join" copy lives in its own div, cleanly separate from
# the embed div that follows it -- more precise than stripping the embed out of the whole
# block (ramdam/napoleoncat's approach), since this site's markup doesn't require that.
_DESCRIPTION_RE = re.compile(r'<div class="trend-desc w-richtext">(.*?)</div>', re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")

_COUNT_SUFFIX_MULTIPLIER = {"k": 1_000, "m": 1_000_000}


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def _parse_video_count(raw: str) -> int | None:
    raw = raw.strip()
    if not raw or raw.lower() == "n/a":
        return None
    match = re.match(r"([\d.]+)\s*([km]?)", raw, re.IGNORECASE)
    if not match:
        return None
    number, suffix = match.groups()
    try:
        value = float(number)
    except ValueError:
        return None
    value *= _COUNT_SUFFIX_MULTIPLIER.get(suffix.lower(), 1)
    return int(value)


def fetch_trends() -> list[RawTrendEntry]:
    response = http_get_with_backoff(
        SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; tiktok-trend-analysis/1.0)"},
    )
    page_html = response.text

    headings = list(_HEADING_RE.finditer(page_html))
    collected_at = datetime.now(timezone.utc).isoformat()
    entries: list[RawTrendEntry] = []
    for i, match in enumerate(headings):
        trend_name = _clean_text(match.group(1))
        if not trend_name:
            continue
        block_start = match.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(page_html)
        block_html = page_html[block_start:block_end]

        count_match = _VIDEO_COUNT_RE.search(block_html)
        popularity_hint = _parse_video_count(count_match.group(1)) if count_match else None
        hashtags, sound_name = extract_hashtags_and_sound(block_html)
        description_match = _DESCRIPTION_RE.search(block_html)
        editorial_description = None
        if description_match:
            # WordPress/Webflow rich text uses &nbsp; between "Trend Recap –" and the next
            # word -- unescape() turns that into a literal U+00A0, which \s+ collapse
            # below (after normalizing it to a plain space) keeps out of the plain-text
            # output, consistent with "plain extracted text, not reformatted."
            raw_text = _clean_text(description_match.group(1)).replace("\xa0", " ")
            editorial_description = re.sub(r"\s+", " ", raw_text).strip() or None
        example_video_urls = extract_video_urls(block_html)

        entries.append(
            RawTrendEntry(
                trend_name=trend_name,
                hashtags=hashtags,
                sound_name=sound_name,
                business_safe=None,
                popularity_hint=popularity_hint,
                source=SOURCE_NAME,
                source_url=SOURCE_URL,
                collected_at=collected_at,
                editorial_description=editorial_description,
                example_video_urls=example_video_urls,
            )
        )

    if not entries:
        raise ValueError("medianug: no '.trend-title' headings found")
    return entries
