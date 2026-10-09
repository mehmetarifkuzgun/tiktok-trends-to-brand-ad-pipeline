#!/usr/bin/env python3
"""Entrypoint for the Discover stage. Meant to run weekly via cron:

    python run_discover.py --api-key <APIFY_TOKEN> --gemini-api-key <GEMINI_KEY>
        [--max-trends 30] [--sound-supplement-cap 3] [--max-age-days 365]

Pulls trends from four editorial TikTok-trend-tracking blogs (socialpilot, ramdam,
medianug, napoleoncat -- see agents/discover/sources/), pre-filters the raw undeduped list
with a text-only LLM call (prefilter.py), then fetches each accepted group's canonical
example video(s) directly by URL plus a bounded sound-based supplement into
artifacts/{trend_id}/raw/. See DISCOVER.md for the full design.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from agents.discover.common import RepoPaths, current_week_id, load_seen_ids, save_seen_ids
from agents.discover.sample_fetcher import fetch_and_save_seed_tag
from agents.discover.trend_inventory import (
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_TRENDS,
    DEFAULT_SOUND_SUPPLEMENT_CAP,
    build_trend_inventory,
    estimate_cost_usd,
    save_trend_collection_meta,
    save_trend_inventory,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_discover")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover stage: multi-source trend-blog pull.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("APIFY_API_TOKEN"),
        help="Apify API token for clockworks/tiktok-scraper. Or set APIFY_API_TOKEN in .env.",
    )
    parser.add_argument(
        "--gemini-api-key",
        default=os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key for the text-only trend pre-filter. Or set GEMINI_API_KEY in .env.",
    )
    parser.add_argument(
        "--max-trends",
        type=int,
        default=DEFAULT_MAX_TRENDS,
        help=f"Cap on accepted groups actually fetched, applied after the LLM pre-filter's "
        f"accept/reject pass (default: {DEFAULT_MAX_TRENDS}).",
    )
    parser.add_argument(
        "--sound-supplement-cap",
        type=int,
        default=DEFAULT_SOUND_SUPPLEMENT_CAP,
        help=f"Max extra videos pulled via sound-based search per group, on top of its "
        f"canonical URL-fetched example(s) (default: {DEFAULT_SOUND_SUPPLEMENT_CAP}).",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"Drop sound-supplement videos older than this many days (default: "
        f"{DEFAULT_MAX_AGE_DAYS}). Canonical URL-fetched videos are never age-filtered.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = RepoPaths()
    week = current_week_id()

    # A real pre-run estimate now needs the LLM's accept/reject outcome (unknown until
    # after that call), unlike the old design where trend count was fixed before any spend.
    # This is a worst-case ceiling instead: every accepted-and-capped group has 1 canonical
    # video AND a full sound-supplement allotment, which won't usually happen.
    ceiling_cost = estimate_cost_usd(args.max_trends, args.max_trends, args.sound_supplement_cap)
    logger.info(
        "Discover run for week %s: max_trends=%d sound_supplement_cap=%d max_age_days=%d "
        "(worst-case Apify cost ceiling: $%.4f -- actual will be lower, see below)",
        week, args.max_trends, args.sound_supplement_cap, args.max_age_days, ceiling_cost,
    )
    if not args.api_key:
        logger.warning("No Apify API key provided -- fixture data will be used for the video pull.")
    if not args.gemini_api_key:
        logger.warning("No Gemini API key provided -- fixture data will be used for the trend pre-filter.")

    trend_pairs, used_fallback_videos, meta = build_trend_inventory(
        paths, week, args.max_trends, args.sound_supplement_cap, args.max_age_days,
        args.api_key, args.gemini_api_key,
    )
    inventory_path = save_trend_inventory(trend_pairs, paths, week)
    total_unique_videos = sum(len(candidates) for _, candidates in trend_pairs)
    num_canonical_urls = sum(len(record.example_video_urls) for record, _ in trend_pairs)
    num_with_sound = sum(1 for record, _ in trend_pairs if record.sound_name)
    actual_cost = estimate_cost_usd(num_canonical_urls, num_with_sound, args.sound_supplement_cap)

    prefilter_meta = meta["prefilter"]
    logger.info(
        "Sources: attempted=%s failed=%s entries_per_source=%s (blog fallback=%s)",
        meta["sources_attempted"], meta["sources_failed"], meta["entries_per_source"], meta["used_fallback"],
    )
    logger.info(
        "Pre-filter: groups_formed=%d accepted=%d rejected=%d accepted_after_cap=%d "
        "(prefilter fallback=%s) canonical_urls_requested=%d canonical_urls_missing=%d "
        "allocation_summary=%s",
        prefilter_meta["groups_formed"], prefilter_meta["accepted_count"], prefilter_meta["rejected_count"],
        prefilter_meta["accepted_count_after_cap"], prefilter_meta["used_fallback"],
        prefilter_meta["canonical_urls_requested"], prefilter_meta["canonical_urls_missing"],
        prefilter_meta["allocation_summary"],
    )
    logger.info(
        "Saved %d trends (%d unique videos total) to %s (video fallback=%s)",
        len(trend_pairs), total_unique_videos, inventory_path, used_fallback_videos,
    )

    seen_ids = load_seen_ids(paths)
    fallback_count = 0
    total_video_failures = 0
    for record, candidates in trend_pairs:
        summary = fetch_and_save_seed_tag(record, candidates, paths, args.api_key, used_fallback_videos, seen_ids)
        if summary["used_fallback"]:
            fallback_count += 1
        total_video_failures += summary["videos_failed"]
        logger.info(
            "%-40s videos=%d failed=%d fallback=%s",
            record.trend_id, summary["videos_fetched"], summary["videos_failed"], summary["used_fallback"],
        )
    save_seen_ids(paths, seen_ids)

    save_trend_collection_meta(
        paths, week,
        max_trends=args.max_trends,
        sound_supplement_cap=args.sound_supplement_cap,
        max_age_days=args.max_age_days,
        meta=meta,
        used_fallback_videos=used_fallback_videos,
        trend_count=len(trend_pairs),
        total_unique_videos=total_unique_videos,
        total_video_download_failures=total_video_failures,
        estimated_cost_usd=actual_cost,
    )
    logger.info(
        "Done. %d/%d trends used fixture fallback. %d/%d individual video downloads failed.",
        fallback_count, len(trend_pairs), total_video_failures, total_unique_videos,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
