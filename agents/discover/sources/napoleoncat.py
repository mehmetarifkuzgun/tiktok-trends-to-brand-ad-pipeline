"""Adapter for https://napoleoncat.com/blog/tiktok-trends/ -- monthly editorial trend post,
the original single source this pipeline started with, now demoted to a fourth, lower-
priority contributor (see DISCOVER.md and CLAUDE.md's decision log for why: too low-volume
to carry Discover alone, 2-3 entries/month, but still worth combining in).

Real page structure confirmed by fetching the live page (2026-09-18) before writing any
selector: standard WordPress content. Each month is `<h2 ...>Current TikTok Trends: <Month>
<Year></h2>`, and each trend under it is `<h3 class="wp-block-heading">"..." TikTok
trend</h3>` (title wording varies -- sometimes `"X" trend`, sometimes `X TikTok trend`, no
quotes at all for a couple of entries -- stripped generically below rather than pattern-
matched exactly). Each trend embeds one TikTok oEmbed widget (same shape as ramdam/
medianug's, parsed with the shared `_tiktok_embed` helper) -- hashtags are present on some
embeds and absent on others (organic caption variance, confirmed directly, not a parsing
gap), sound name is present on effectively all of them.

Only the first (most recent) month's section is parsed -- true to this source's "monthly"
cadence and its lower-priority role; the page's full historical archive (back to 2024) is
left alone rather than bulk-imported, which would turn a low-volume monthly contributor into
the largest source by count for no real reason (medianug already covers deep history better,
see medianug.py).

No business-safety flag or video-count figure exists on this page (confirmed) -- both left
null, matching every non-socialpilot/non-medianug source.
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

SOURCE_NAME = "napoleoncat"
SOURCE_URL = "https://napoleoncat.com/blog/tiktok-trends/"

_MONTH_HEADING_RE = re.compile(r'<h2[^>]*class="[^"]*wp-block-heading[^"]*"[^>]*>(.*?)</h2>', re.DOTALL)
_TREND_HEADING_RE = re.compile(r'<h3[^>]*class="wp-block-heading"[^>]*>(.*?)</h3>', re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_STRIP_RE.sub("", raw)).strip()


def _parse_trend_name(heading_text: str) -> str:
    text = _clean_text(heading_text)
    text = re.sub(r'^(The\s+)?', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s*TikTok trend$', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s*trend$', "", text, flags=re.IGNORECASE)
    return text.strip(' "“”‘’\'')


def fetch_trends() -> list[RawTrendEntry]:
    response = http_get_with_backoff(
        SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; tiktok-trend-analysis/1.0)"},
    )
    page_html = response.text

    month_matches = list(_MONTH_HEADING_RE.finditer(page_html))
    if not month_matches:
        raise ValueError("napoleoncat: no monthly '<h2 class=\"wp-block-heading\">' sections found")

    # Most-recent month only -- see module docstring for why the historical archive isn't pulled.
    current_month_start = month_matches[0].end()
    current_month_end = month_matches[1].start() if len(month_matches) > 1 else len(page_html)
    month_html = page_html[current_month_start:current_month_end]

    headings = list(_TREND_HEADING_RE.finditer(month_html))
    collected_at = datetime.now(timezone.utc).isoformat()
    entries: list[RawTrendEntry] = []
    for i, match in enumerate(headings):
        trend_name = _parse_trend_name(match.group(1))
        if not trend_name:
            continue
        block_start = match.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(month_html)
        block_html = month_html[block_start:block_end]
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
        raise ValueError("napoleoncat: current-month section found but yielded zero trend entries")
    return entries
