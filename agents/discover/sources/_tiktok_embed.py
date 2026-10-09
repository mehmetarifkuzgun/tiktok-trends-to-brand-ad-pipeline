"""Shared parsing for the TikTok oEmbed `<blockquote class="tiktok-embed">` widget that all
four adapters embed as their example video(s) per trend (confirmed identical markup shape
across all four real pages, including socialpilot, before writing each function below).

socialpilot's embeds carry no caption/hashtag/sound text (its embed `<section>` is just a
creator-name link) and it states hashtags in a separate "Hashtag: ..." line instead, so it
doesn't use `extract_hashtags_and_sound`/`extract_editorial_description` -- but it DOES use
the same `cite="..."` attribute for its real video URL, confirmed live, so it uses
`extract_video_urls` same as the other three.

This is genuinely shared infra, not site-specific structure -- the per-site adapters differ
in how they locate *which* embed belongs to *which* trend heading, not in how the embed
itself is parsed once located.
"""
from __future__ import annotations

import html
import re

# Matches an anchor whose href is a TikTok hashtag-tag link; the visible/title text is the
# bare tag name (no leading #). Confirmed against real markup: both ramdam and medianug use
# `<a title="TAG" href=".../tag/TAG?refer=embed">#TAG</a>`.
_HASHTAG_ANCHOR_RE = re.compile(
    r'<a[^>]*\btitle="([^"]+)"[^>]*href="[^"]*/tag/[^"]*"', re.IGNORECASE,
)
# Matches the sound/music anchor. ramdam/medianug prefix the title with "♬ "; napoleoncat
# doesn't -- both confirmed directly against real pages, so the ♬ is stripped if present
# rather than assumed.
_SOUND_ANCHOR_RE = re.compile(
    r'<a[^>]*\btitle="([^"]*)"[^>]*href="[^"]*/music/[^"]*"', re.IGNORECASE,
)


def extract_hashtags_and_sound(embed_html: str) -> tuple[list[str], str | None]:
    """embed_html is the raw HTML slice spanning one trend's embed block(s) (from its
    heading to the next heading). Returns (hashtags without '#', sound_name or None)."""
    hashtags = [html.unescape(m).lstrip("#").strip() for m in _HASHTAG_ANCHOR_RE.findall(embed_html)]
    hashtags = [h for h in dict.fromkeys(hashtags) if h]  # de-dup, preserve order

    sound_name: str | None = None
    sound_match = _SOUND_ANCHOR_RE.search(embed_html)
    if sound_match:
        raw = html.unescape(sound_match.group(1)).strip()
        raw = raw.lstrip("♬").strip()  # strip a leading "♬ " if present
        sound_name = raw or None

    return hashtags, sound_name


# Matches one embedded TikTok oEmbed widget (blockquote + its loader script) so it can be
# stripped out before extracting the site's own prose -- a trend's block otherwise mixes the
# embed's creator name/caption/hashtag text in with the site's actual descriptive paragraphs.
# re.sub below replaces every match, not just the first, since ramdam sometimes embeds more
# than one example video per trend (confirmed directly, not assumed).
_EMBED_WIDGET_RE = re.compile(
    r'<blockquote class="tiktok-embed".*?</blockquote>\s*<script[^>]*></script>', re.DOTALL,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")



# Matches the real TikTok video URL on the embed widget's own `cite` attribute -- confirmed
# live against all four sources' current pages (2026-09-18), not assumed: every
# `<blockquote class="tiktok-embed" cite="https://www.tiktok.com/@handle/video/123...">`
# carries the exact URL the source blog itself linked to as its example. This is the
# canonical-video source for the LLM pre-filter/postURLs fetch, replacing hashtag/keyword
# search anchors entirely (see search_anchor.py and DISCOVER.md for why those were dropped).
_EMBED_CITE_RE = re.compile(
    r'<blockquote class="tiktok-embed"[^>]*\bcite="([^"]+)"', re.IGNORECASE,
)


def extract_video_urls(embed_html: str) -> list[str]:
    """embed_html is the raw HTML slice spanning one trend's embed block(s). Returns the
    real TikTok video URLs the source linked to, de-duped, in source order -- never
    fabricated; empty if the block has no embed."""
    urls = [html.unescape(m).strip() for m in _EMBED_CITE_RE.findall(embed_html)]
    return [u for u in dict.fromkeys(urls) if u]


def extract_editorial_description(block_html: str) -> str | None:
    """Plain-text extraction of a trend block's own descriptive/how-to prose, with the
    embedded TikTok widget(s) stripped out first so the embed's own caption/hashtag text
    doesn't leak into it. Not reformatted or rewritten -- just tags stripped and whitespace
    collapsed, per the reference-data-only design (see sources/__init__.py's
    RawTrendEntry.editorial_description)."""
    without_embeds = _EMBED_WIDGET_RE.sub(" ", block_html)
    text = html.unescape(_TAG_STRIP_RE.sub(" ", without_embeds))
    text = text.replace("\xa0", " ")  # &nbsp; -> plain space, not left as a literal U+00A0
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
