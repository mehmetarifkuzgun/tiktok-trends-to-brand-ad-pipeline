#!/usr/bin/env python3
"""Entrypoint for the Process stage:

    python run_process.py --api-key <GEMINI_KEY> [--limit 5] [--force]

Analyzes downloaded videos with Gemini (video-native, via the File API), scores them
against config/scoring_weights.json, and writes analysis.json + decision.json next to each
video under artifacts/{trend_id}/raw/. Idempotent by default -- already-analyzed videos are
skipped unless --force is passed. See PROCESS.md for the full design.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from google import genai

from agents.process.analyzer import (
    analysis_is_current,
    analysis_paths,
    discover_videos,
    load_prompt_template,
    load_scoring_weights,
    process_video,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_process")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process stage: Gemini video analysis + scoring.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key. Or set GEMINI_API_KEY in .env.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Only process the first N not-yet-analyzed videos (default: 5, a "
        "budget-conscious first pass before scaling to the full batch).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze videos even if a current analysis.json exists (spends API calls again). Analyses scored for a different brand are always re-scored.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.api_key:
        logger.error("No Gemini API key provided (--api-key or GEMINI_API_KEY in .env). Cannot proceed.")
        return 1

    client = genai.Client(api_key=args.api_key)
    prompt_template = load_prompt_template()
    weights = load_scoring_weights()

    all_videos = discover_videos()
    if args.force:
        pending = all_videos
    else:
        pending = [v for v in all_videos if not analysis_is_current(analysis_paths(v)[0])]
    targeted = pending[: args.limit]

    logger.info(
        "Process run: %d videos on disk, %d pending analysis, processing %d (limit=%d, force=%s)",
        len(all_videos), len(pending), len(targeted), args.limit, args.force,
    )

    results: list[dict] = []
    failed: list[str] = []
    for video_path in targeted:
        video_id = video_path.stem
        try:
            decision = process_video(client, video_path, prompt_template, weights, force=args.force)
        except Exception as exc:  # noqa: BLE001 - one video's failure shouldn't abort the batch
            logger.warning("Analysis failed for %s after retries (%s); continuing.", video_id, exc)
            failed.append(video_id)
            continue
        if decision is not None:
            results.append(decision)
            logger.info("%-24s score=%.2f status=%s", video_id, decision["weighted_score"], decision["status"])

    if results:
        print(f"\n{'video_id':<24} {'weighted_score':>14} {'status':>10}")
        print("-" * 50)
        for r in results:
            print(f"{r['video_id']:<24} {r['weighted_score']:>14.2f} {r['status']:>10}")
        print()

    accepted = sum(1 for r in results if r["status"] == "accepted")
    rejected = sum(1 for r in results if r["status"] == "rejected")
    logger.info("Done. %d analyzed (%d accepted, %d rejected), %d failed.", len(results), accepted, rejected, len(failed))
    if failed:
        logger.warning("Failed videos: %s", failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
