"""Adapter for https://www.ramd.am/blog/trends-tiktok -- weekly editorial trend post.

Real page structure confirmed by fetching the live page (2026-09-18) before writing any
selector: a Webflow CMS page, heavily minified (the whole body is effectively one line).
Each trend is a bare `<h4>The "..." trend</h4>` (no class attribute) immediately followed
by one or more embedded TikTok `<blockquote class="tiktok-embed">` widgets, up to the next
`<h4>`. Confirmed this page has no video-count or business-safety data anywhere -- only
hashtag/sound, both parsed from the embed via the shared `_tiktok_embed` helper.

The footer also uses `<h4>` (`<h4 class="heading-6">Customers</h4>`, etc.) -- excluded by
requiring the trend heading to have no class attribute, which is a real, confirmed
distinction in the live markup, not a guess.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from agents.discover.common import http_get_with_backoff
from agents.discover.sources import RawTrendEntry
from agents.discover.sources._tiktok_embed import (
    extract_editorial_description,
    extract_hashtags_and_sound,
    extract_video_urls,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "ramdam"
SOURCE_URL = "https://www.ramd.am/blog/trends-tiktok"

# Bare <h4> (no class attr) = a trend heading; <h4 class="..."> = footer/nav chrome.
_HEADING_RE = re.compile(r"<h4>(.*?)</h4>", re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def _parse_trend_name(heading_text: str) -> str:
    text = _clean_text(heading_text)
    text = re.sub(r'^The\s+', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s+trend$', "", text, flags=re.IGNORECASE)
    return text.strip(' "“”')


def fetch_trends() -> list[RawTrendEntry]:
    response = http_get_with_backoff(
        SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; tiktok-trend-analysis/1.0)"},
    )
    page_html = response.text

    headings = list(re.finditer(r"<h4(\s[^>]*)?>(.*?)</h4>", page_html, re.DOTALL))
    collected_at = datetime.now(timezone.utc).isoformat()
    entries: list[RawTrendEntry] = []
    for i, match in enumerate(headings):
        attrs, heading_text = match.group(1) or "", match.group(2)
        if "class=" in attrs:
            continue  # footer/nav heading, not a trend
        trend_name = _parse_trend_name(heading_text)
        if not trend_name:
            continue
        block_start = match.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(page_html)
        block_html = page_html[block_start:block_end]
        hashtags, sound_name = extract_hashtags_and_sound(block_html)
        editorial_description = extract_editorial_description(block_html)
        example_video_urls = extract_video_urls(block_html)
        entries.append(
            RawTrendEntry(
                trend_name=trend_name,
                hashtags=hashtags,
                sound_name=sound_name,
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
        raise ValueError("ramdam: no trend (unclassed <h4>) headings found")
    return entries
