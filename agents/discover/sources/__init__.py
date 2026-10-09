"""Common output shape for every trend-blog adapter, defined once and imported by all of
them (socialpilot.py, ramdam.py, medianug.py, napoleoncat.py). Grouping/dedup across sources
moved to the LLM text pre-filter (agents/discover/prefilter.py) as of 2026-09-18 -- see
DISCOVER.md for why the earlier sound/hashtag-overlap union-find approach was dropped.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class RawTrendEntry:
    trend_name: str
    hashtags: list[str]
    sound_name: str | None
    business_safe: bool | None
    popularity_hint: int | None
    source: str
    source_url: str
    collected_at: str
    # Plain-text extraction of the site's own descriptive/format-explanation prose for this
    # entry -- reference data only (e.g. future Process-stage context, or the LLM pre-filter
    # below), never reproduced in our own authored docs (README.md/DISCOVER.md).
    editorial_description: str | None = None
    # Real TikTok video URLs the source blog itself embedded as an example of this trend
    # (from the `cite="..."` attribute on its `tiktok-embed` widget -- confirmed identical
    # across all four sources' markup). These become the canonical videos fetched for an
    # accepted trend, via clockworks' postURLs input -- replacing hashtag/keyword search
    # anchors entirely (see search_anchor.py / DISCOVER.md for why those proved unreliable).
    # Empty, never fabricated, if a given entry has no embed.
    example_video_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
