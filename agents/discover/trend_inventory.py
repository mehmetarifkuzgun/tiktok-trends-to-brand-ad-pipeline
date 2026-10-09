"""Step A: discover trends from four editorial TikTok-trend-tracking blogs (weekly:
socialpilot, ramdam, medianug; monthly: napoleoncat -- see `sources/`), pre-filter the raw
undeduped list with a text-only LLM call (`prefilter.py`), then fetch each accepted group's
canonical example video(s) directly by URL via `clockworks/tiktok-scraper`'s `postURLs`
input, optionally supplemented by a bounded sound-based search -- one combined actor call
for every accepted group at once, same one-call-per-run cost pattern as every design before
this.

**2026-09-18 redesign, replacing hashtag/keyword search-anchor resolution entirely (not kept
as dead code -- recoverable from git history):** the live `anchor_inspection.md` diagnostic
showed hashtag anchors matched their own pulled videos only ~18% of the time and keyword
search returned near-random content. The four source blogs already embed and link to real
example videos for each trend -- fetching those specific URLs directly is precise by
construction, and an LLM text pass over trend descriptions replaces the old sound/hashtag-
overlap exact-match dedup (which was itself a source of real bugs, see CLAUDE.md) with
semantic judgement, including an explicit *why* for every trend rejected at this stage. See
DISCOVER.md for the full before/after.

Two-level structure carries forward unchanged: one accepted group = one `trend_id`, with its
pulled videos as samples underneath (`artifacts/{trend_id}/raw/...`). `sample_fetcher.py`
(Step B) needed only a small, additive change (see its own docstring) -- `TrendRecord.
trend_id`/`.to_dict()` and `VideoCandidate`'s fields are still what it consumes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from agents.discover.common import RepoPaths, redact_secrets, run_apify_actor_sync
from agents.discover.merge import SOURCE_FETCHERS, collect_all_raw_entries
from agents.discover.prefilter import PrefilterGroup, run_prefilter, save_prefilter_result
from agents.discover.search_anchor import is_sound_searchable

logger = logging.getLogger(__name__)

GrowthSignal = Literal["unknown"]
FetchMethod = Literal["canonical_url", "sound_supplement"]

# Same fixed source-priority order the old round-robin allocation used, reused here so
# there's a single definition of "source order" rather than two that could drift.
_ALLOCATION_SOURCE_ORDER: list[str] = list(SOURCE_FETCHERS.keys())

APIFY_ACTOR = "clockworks~tiktok-scraper"

# Cap on accepted groups actually fetched, applied after the LLM's accept/reject pass (not
# before) -- the pre-filter itself is cheap (one text call over the whole raw list), so this
# is purely a video-download/Process-analysis budget control, not a search-cost one anymore.
DEFAULT_MAX_TRENDS = 30
# "up to a small cap" per task -- bounded so a present sound_name can't reintroduce the old
# search-driven volume/cost problem via the supplementary path.
DEFAULT_SOUND_SUPPLEMENT_CAP = 3
# Applied to sound-supplement videos only, not canonical ones (we want the blog's *specific*
# linked example regardless of its age -- recency only matters for the extra padding videos
# a sound search pulls in). Same underlying reasoning as every prior design: TikTok search
# has no reliable native recency filter we've chosen to depend on -- see DISCOVER.md.
DEFAULT_MAX_AGE_DAYS = 365

# clockworks/tiktok-scraper PAY_PER_EVENT pricing, FREE tier -- same figures carried over
# unchanged from the design this replaces (per-item "result"/"video-download" pricing is
# keyed by event type, not by which input field produced the item, so postURLs-origin items
# are billed the same as search-origin ones).
APIFY_ACTOR_START_USD = 0.001
APIFY_PER_RESULT_USD = 0.0037
APIFY_PER_VIDEO_DOWNLOAD_USD = 0.0013


@dataclass(frozen=True)
class TrendRecord:
    trend_id: str
    trend_name: str
    merged_from_sources: list[str]
    reasoning: str
    sound_name: str | None
    example_video_urls: list[str]
    popularity_rank: int
    growth_signal: GrowthSignal
    source: str
    collected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VideoCandidate:
    """One video pulled for an accepted group -- either its canonical example (fetched by
    exact URL) or a sound-based supplement. Not persisted as-is -- metadata_dict() is what
    actually gets written to disk."""

    video_id: str
    caption: str
    hashtags: list[str]
    sound_name: str
    views: int
    likes: int
    comments: int
    shares: int
    post_date: str
    duration_sec: int
    download_url: str | None
    fetch_method: FetchMethod
    used_fallback: bool

    def metadata_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "sound_name": self.sound_name,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "post_date": self.post_date,
            "duration_sec": self.duration_sec,
            "fetch_method": self.fetch_method,
            "source": "apify_clockworks_tiktok_scraper",
        }


def estimate_cost_usd(num_canonical_urls: int, num_groups_with_sound: int, sound_supplement_cap: int) -> float:
    raw_count = num_canonical_urls + num_groups_with_sound * sound_supplement_cap
    return APIFY_ACTOR_START_USD + raw_count * (APIFY_PER_RESULT_USD + APIFY_PER_VIDEO_DOWNLOAD_USD)


def _slugify(label: str) -> str:
    # NFKD-normalize first so Latin-script diacritics fold to ASCII (e.g. "café" -> "cafe").
    ascii_only = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if slug:
        return slug
    # Non-Latin-script trend names would otherwise slugify to empty and collide -- same bug
    # class found and fixed for hashtags in an earlier session, guarded against here too.
    return hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]


def _make_trend_id(trend_name: str) -> str:
    """Slug plus a short hash suffix -- the suffix is hashed from trend_name alone (not
    source/run data), so the same real trend gets the same trend_id run over run, which
    matters since seen_ids.json dedup and artifact folders both key off trend_id stability."""
    slug = _slugify(trend_name)[:40].strip("-") or "trend"
    suffix = hashlib.sha1(trend_name.strip().lower().encode("utf-8")).hexdigest()[:6]
    return f"trend_{slug}_{suffix}"


def _parse_post_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_clockworks_item(item: dict[str, Any], *, fetch_method: FetchMethod, used_fallback: bool) -> VideoCandidate | None:
    video_id = str(item.get("id") or "")
    if not video_id:
        return None
    video_meta = item.get("videoMeta") or {}
    music_meta = item.get("musicMeta") or {}
    # webVideoUrl is a TikTok *page* link, not a video file -- deliberately not used as a
    # download source (bug found and fixed in an earlier session).
    download_url = video_meta.get("downloadAddr") or (item.get("mediaUrls") or [None])[0]
    hashtags = [h.get("name", "") for h in item.get("hashtags", []) if h.get("name")]
    return VideoCandidate(
        video_id=video_id,
        caption=item.get("text", ""),
        hashtags=hashtags,
        sound_name=music_meta.get("musicName", ""),
        views=int(item.get("playCount", 0) or 0),
        likes=int(item.get("diggCount", 0) or 0),
        comments=int(item.get("commentCount", 0) or 0),
        shares=int(item.get("shareCount", 0) or 0),
        post_date=item.get("createTimeISO", ""),
        duration_sec=int(video_meta.get("duration", 0) or 0),
        download_url=download_url,
        fetch_method=fetch_method,
        used_fallback=used_fallback,
    )


def _item_origin(item: dict[str, Any]) -> tuple[FetchMethod, str] | None:
    """Which input produced this raw item.

    Real bug found and fixed by live end-to-end testing (2026-09-18), not caught by the
    smaller Step-0 confirmation call: `webVideoUrl` is populated on *every* item regardless
    of origin (every TikTok video has its own page URL, whether found via `postURLs` or
    `searchQueries`) -- an earlier version of this function fell back to `webVideoUrl` when
    `submittedVideoUrl` was absent, which silently misclassified every real
    searchQueries-origin item as canonical (their own self-referential `webVideoUrl` never
    matches a requested canonical URL, so they were dropped instead of falling through to
    the sound-supplement branch). A real run's "Deep breaths, honey" group had 3 real
    sound-supplement videos returned by the actor and 0 make it into the group before this
    fix. `submittedVideoUrl` alone is confirmed to be null on searchQueries-origin items and
    populated only on postURLs-origin ones -- that's the one reliable signal, no fallback."""
    submitted_url = item.get("submittedVideoUrl")
    if submitted_url:
        return "canonical_url", submitted_url
    search_query = item.get("searchQuery")
    if search_query:
        return "sound_supplement", search_query
    return None


def build_fetch_payload(
    canonical_urls: list[str], sound_queries: list[str], sound_supplement_cap: int,
) -> dict[str, Any]:
    """One payload for every accepted group's canonical URLs and sound supplements at once
    -- confirmed live that `postURLs` and `searchQueries` combine in a single clockworks
    call. `resultsPerPage` only meaningfully governs `searchQueries` (confirmed live:
    postURLs always returns exactly one item per URL regardless of `resultsPerPage`)."""
    payload: dict[str, Any] = {
        "resultsPerPage": sound_supplement_cap,
        "shouldDownloadVideos": True,
        "shouldDownloadCovers": False,
    }
    if canonical_urls:
        payload["postURLs"] = canonical_urls
    if sound_queries:
        payload["searchQueries"] = sound_queries
        payload["searchSection"] = "/video"
    return payload


def fetch_group_videos(
    canonical_urls: list[str], sound_queries: list[str], sound_supplement_cap: int, api_key: str,
) -> list[dict[str, Any]]:
    payload = build_fetch_payload(canonical_urls, sound_queries, sound_supplement_cap)
    return run_apify_actor_sync(APIFY_ACTOR, payload, api_key, timeout=300.0)


def load_fixture_raw_items(paths: RepoPaths) -> list[dict[str, Any]]:
    fixture_path = paths.fixtures / "group_videos.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _group_videos(
    raw_items: list[dict[str, Any]],
    url_to_trend_id: dict[str, str],
    sound_to_trend_id: dict[str, str],
    max_age_days: int,
    sound_supplement_cap: int,
    *,
    used_fallback: bool,
) -> tuple[dict[str, list[VideoCandidate]], list[str]]:
    """Dedup by video_id -- the first trend_id a video is seen under (canonical URLs
    processed before sound supplements, so a video that's both a group's canonical example
    AND happens to also surface in another group's sound search keeps its canonical home)
    owns it. Canonical (postURLs-origin) videos are never age-filtered; sound-supplement
    videos are, and are capped per trend at sound_supplement_cap even though
    resultsPerPage already bounds this per unique query (two groups could share very
    similar-but-not-identical sounds in principle). Returns (groups, fetched_canonical_urls)
    -- the second lets the caller detect which requested canonical URLs never came back
    (404s / removed videos) without crashing."""
    owner_trend_id: dict[str, str] = {}
    items_by_video_id: dict[str, tuple[dict[str, Any], FetchMethod]] = {}
    fetched_canonical_urls: list[str] = []

    canonical_items = []
    supplement_items = []
    for item in raw_items:
        origin = _item_origin(item)
        if origin is None:
            continue
        method, value = origin
        (canonical_items if method == "canonical_url" else supplement_items).append((item, value))

    for item, url in canonical_items:
        trend_id = url_to_trend_id.get(url)
        if trend_id is None:
            continue
        video_id = str(item.get("id") or "")
        if not video_id:
            continue
        fetched_canonical_urls.append(url)
        if video_id not in owner_trend_id:
            owner_trend_id[video_id] = trend_id
            items_by_video_id[video_id] = (item, "canonical_url")

    for item, sound in supplement_items:
        trend_id = sound_to_trend_id.get(sound)
        if trend_id is None:
            continue
        video_id = str(item.get("id") or "")
        if not video_id:
            continue
        if video_id not in owner_trend_id:
            owner_trend_id[video_id] = trend_id
            items_by_video_id[video_id] = (item, "sound_supplement")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    groups: dict[str, list[VideoCandidate]] = {tid: [] for tid in set(url_to_trend_id.values()) | set(sound_to_trend_id.values())}
    supplement_count: dict[str, int] = {tid: 0 for tid in groups}
    for video_id, (item, fetch_method) in items_by_video_id.items():
        candidate = _parse_clockworks_item(item, fetch_method=fetch_method, used_fallback=used_fallback)
        if candidate is None:
            continue
        trend_id = owner_trend_id[video_id]
        if fetch_method == "sound_supplement":
            post_date_dt = _parse_post_date(candidate.post_date)
            if post_date_dt is not None and post_date_dt < cutoff:
                continue
            if supplement_count[trend_id] >= sound_supplement_cap:
                continue
            supplement_count[trend_id] += 1
        groups.setdefault(trend_id, []).append(candidate)

    for videos in groups.values():
        # canonical first (the group's own linked example), then supplements by views desc.
        videos.sort(key=lambda c: (c.fetch_method != "canonical_url", -c.views))
    return groups, fetched_canonical_urls


def allocate_accepted_groups(
    accepted: list[PrefilterGroup], max_trends: int,
) -> tuple[list[PrefilterGroup], dict[str, int]]:
    """Cap the LLM-accepted group list to `max_trends` via round-robin across each group's
    *first-listed* `merged_from_sources` entry, instead of a naive `accepted[:max_trends]`
    slice.

    **Real bug this fixes, found by this exact task, not hypothetical:** the naive slice
    was what shipped in the LLM-prefilter redesign -- nothing replaced the round-robin
    allocation removed at the same time, despite DISCOVER.md noting its removal. Checked
    against the real prior live run's persisted `prefilter_result.json`: 72 groups were
    accepted, 61 of them (85%) via medianug, 6 via ramdam, 4 via socialpilot, 1 via
    napoleoncat -- but the LLM's own output order (which roughly mirrors input order: raw
    entries were fed to it source-by-source, socialpilot/ramdam/medianug/napoleoncat) meant
    `accepted[:10]` was 100% socialpilot+ramdam. medianug, which contributed the large
    majority of everything the model actually judged worth adapting, would have gotten
    **zero** representation in the fetched/downloaded set.

    Bucketing by a group's first-listed source rather than all its `merged_from_sources`
    is a deliberate simplification for this data shape: a group that merged across sources
    (e.g. `["ramdam", "medianug"]`) still needs exactly one bucket to round-robin against,
    and the first-listed entry is the LLM's own primary/first-mentioned source for that
    group -- there's no more principled single choice available without inventing a
    secondary signal the model doesn't provide. Within a bucket, groups keep the LLM's own
    relative order (no separate ranking signal exists at this stage, same reasoning as the
    old round-robin's fallback for buckets without `popularity_hint`).

    Round-robin, one group per non-empty bucket per pass, skipping empty buckets, until
    `max_trends` is reached or every bucket is empty -- same skip-when-empty-is-
    redistribution-for-free logic as the removed `merge.allocate_trends`."""
    buckets: dict[str, list[PrefilterGroup]] = {name: [] for name in _ALLOCATION_SOURCE_ORDER}
    for group in accepted:
        bucket_key = group.merged_from_sources[0] if group.merged_from_sources else "unknown"
        buckets.setdefault(bucket_key, []).append(group)

    order = [name for name in _ALLOCATION_SOURCE_ORDER if name in buckets] + [
        name for name in buckets if name not in _ALLOCATION_SOURCE_ORDER
    ]
    cursors = {name: 0 for name in order}

    allocated: list[PrefilterGroup] = []
    allocation_summary: dict[str, int] = {name: 0 for name in order}
    while len(allocated) < max_trends and any(cursors[name] < len(buckets[name]) for name in order):
        for name in order:
            if len(allocated) >= max_trends:
                break
            cursor = cursors[name]
            bucket = buckets[name]
            if cursor >= len(bucket):
                continue  # this source's queue is already exhausted -- skip, don't redistribute
            allocated.append(bucket[cursor])
            cursors[name] = cursor + 1
            allocation_summary[name] += 1

    return allocated, allocation_summary


def build_trend_inventory(
    paths: RepoPaths,
    week: str,
    max_trends: int,
    sound_supplement_cap: int,
    max_age_days: int,
    apify_api_key: str | None,
    gemini_api_key: str | None,
) -> tuple[list[tuple[TrendRecord, list[VideoCandidate]]], bool, dict[str, Any]]:
    """Returns (trend_pairs, used_fallback_videos, meta). `meta` folds together
    merge.py's sources_meta (per-source blog counts) and the prefilter's own outcome
    (groups formed, accepted/rejected counts, whether it fell back to fixtures) -- two
    independent fallback layers, same pattern as every prior design: the blogs, the LLM
    call, and the clockworks video call can each degrade to fixtures independently."""
    raw_entries, sources_meta = collect_all_raw_entries(paths)

    prefilter_result, used_fallback_prefilter = run_prefilter(raw_entries, paths, gemini_api_key)
    save_prefilter_result(prefilter_result, paths, week)

    accepted = [g for g in prefilter_result.groups if g.status == "accepted"]
    rejected = [g for g in prefilter_result.groups if g.status == "rejected"]
    capped, allocation_summary = allocate_accepted_groups(accepted, max_trends)

    prefilter_meta = {
        "used_fallback": used_fallback_prefilter,
        "groups_formed": len(prefilter_result.groups),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted_count_after_cap": len(capped),
        "allocation_summary": allocation_summary,
    }

    trend_id_to_group: dict[str, PrefilterGroup] = {}
    url_to_trend_id: dict[str, str] = {}
    sound_to_trend_id: dict[str, str] = {}
    for group in capped:
        trend_id = _make_trend_id(group.canonical_trend_name)
        if trend_id in trend_id_to_group:
            continue  # two accepted groups slugified to the same id; keep the first-seen one
        trend_id_to_group[trend_id] = group
        for url in group.example_video_urls:
            url_to_trend_id.setdefault(url, trend_id)
        if is_sound_searchable(group.sound_name):
            sound_to_trend_id.setdefault(group.sound_name, trend_id)

    collected_at = datetime.now(timezone.utc).isoformat()
    canonical_urls = list(url_to_trend_id.keys())
    sound_queries = list(sound_to_trend_id.keys())

    if not apify_api_key:
        logger.warning("No Apify API key configured; using fixtures for the video pull.")
        raw_items = load_fixture_raw_items(paths)
        used_fallback_videos = True
    elif not canonical_urls and not sound_queries:
        logger.warning("No accepted groups resolved to a fetchable URL/sound; using fixtures for the video pull.")
        raw_items = load_fixture_raw_items(paths)
        used_fallback_videos = True
    else:
        try:
            raw_items = fetch_group_videos(canonical_urls, sound_queries, sound_supplement_cap, apify_api_key)
            if not raw_items:
                raise ValueError("clockworks actor returned no usable items")
            used_fallback_videos = False
        except Exception as exc:  # noqa: BLE001 - any live-source failure should degrade, not crash the run
            logger.warning("Live group video fetch failed (%s); falling back to fixtures.", redact_secrets(exc))
            raw_items = load_fixture_raw_items(paths)
            used_fallback_videos = True

    groups, fetched_canonical_urls = _group_videos(
        raw_items, url_to_trend_id, sound_to_trend_id, max_age_days, sound_supplement_cap,
        used_fallback=used_fallback_videos,
    )

    # A blog-linked video can 404 or be taken down since the post was written -- that's
    # expected, not a crash: log which requested canonical URLs never came back rather than
    # silently under-counting (see DISCOVER.md).
    missing_urls = [u for u in canonical_urls if u not in set(fetched_canonical_urls)]
    for url in missing_urls:
        logger.warning("Canonical example video URL did not resolve to any item (removed/404?): %s", url)
    prefilter_meta["canonical_urls_requested"] = len(canonical_urls)
    prefilter_meta["canonical_urls_missing"] = len(missing_urls)

    def trend_score(trend_id: str) -> int:
        return sum(c.views for c in groups.get(trend_id, []))

    ordered_trend_ids = sorted(trend_id_to_group, key=lambda tid: -trend_score(tid))

    trend_pairs: list[tuple[TrendRecord, list[VideoCandidate]]] = []
    for rank, trend_id in enumerate(ordered_trend_ids, start=1):
        group = trend_id_to_group[trend_id]
        record = TrendRecord(
            trend_id=trend_id,
            trend_name=group.canonical_trend_name,
            merged_from_sources=group.merged_from_sources,
            reasoning=group.reasoning,
            sound_name=group.sound_name,
            example_video_urls=group.example_video_urls,
            popularity_rank=rank,
            growth_signal="unknown",
            source="llm_prefilter_accepted",
            collected_at=collected_at,
        )
        trend_pairs.append((record, groups.get(trend_id, [])))

    meta = {**sources_meta, "prefilter": prefilter_meta, "rejected_groups": [g.model_dump() for g in rejected]}
    return trend_pairs, used_fallback_videos, meta


def save_trend_inventory(trend_pairs: list[tuple[TrendRecord, list[VideoCandidate]]], paths: RepoPaths, week: str) -> Path:
    week_dir = paths.trends / week
    week_dir.mkdir(parents=True, exist_ok=True)
    out_path = week_dir / "trend_inventory.json"
    out_path.write_text(
        json.dumps([record.to_dict() for record, _ in trend_pairs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def save_trend_collection_meta(
    paths: RepoPaths,
    week: str,
    *,
    max_trends: int,
    sound_supplement_cap: int,
    max_age_days: int,
    meta: dict[str, Any],
    used_fallback_videos: bool,
    trend_count: int,
    total_unique_videos: int,
    total_video_download_failures: int,
    estimated_cost_usd: float,
) -> Path:
    """Run-level provenance for Step A, sibling to trend_inventory.json -- kept separate so
    trend_inventory.json stays a flat list of trend records. Folds in merge.py's
    sources_meta plus prefilter.py's outcome (groups_formed/accepted/rejected counts,
    canonical URL fetch success rate) alongside this run's own video-pull settings."""
    week_dir = paths.trends / week
    week_dir.mkdir(parents=True, exist_ok=True)
    out_path = week_dir / "collection_meta.json"
    out_path.write_text(
        json.dumps(
            {
                **meta,
                "max_trends": max_trends,
                "sound_supplement_cap": sound_supplement_cap,
                "max_age_days": max_age_days,
                "used_fallback_videos": used_fallback_videos,
                "trend_count": trend_count,
                "total_unique_videos": total_unique_videos,
                "total_video_download_failures": total_video_download_failures,
                "estimated_cost_usd": round(estimated_cost_usd, 4),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_path
