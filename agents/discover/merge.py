"""Orchestrates the four trend-blog adapters: calls each independently, degrades gracefully
when one fails, and hands the combined RAW (undeduped) entry list to the LLM text pre-filter
(`prefilter.py`) -- see DISCOVER.md for the full design rationale.

Each adapter (`sources/*.py`) raises on any real fetch/parse failure -- by design, per that
module's docstring, the adapter itself never decides how to degrade. This module is the one
call site that wraps each adapter in its own try/except, so one broken source can never take
down the other three. An adapter that parses successfully but yields zero entries is treated
the same as a raised exception here (both land in `sources_failed`) -- silently proceeding
as if a source "contributed nothing on purpose" would hide a real regression (e.g. a site
redesign breaking our selectors) behind what looks like a healthy, quiet run.

**2026-09-18: cross-source dedup/merge (`dedup_and_merge`, `allocate_trends`, the union-find
over shared sound/hashtag) moved to `prefilter.py`'s LLM call and was removed from here, not
kept as dead code.** That exact-match approach is what first surfaced hashtag/keyword search
as unreliable in the first place (see DISCOVER.md's "Why hashtag/keyword search was
dropped") -- it's superseded, recoverable from git history if ever needed again.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

from agents.discover.common import RepoPaths
from agents.discover.sources import RawTrendEntry
from agents.discover.sources import medianug, napoleoncat, ramdam, socialpilot

logger = logging.getLogger(__name__)

SOURCE_FETCHERS: dict[str, Callable[[], list[RawTrendEntry]]] = {
    "socialpilot": socialpilot.fetch_trends,
    "ramdam": ramdam.fetch_trends,
    "medianug": medianug.fetch_trends,
    "napoleoncat": napoleoncat.fetch_trends,
}


def collect_raw_entries() -> tuple[list[RawTrendEntry], list[str], dict[str, int]]:
    """Call every adapter independently. Returns (all_entries, sources_failed,
    entries_per_source) -- entries_per_source records 0 for a failed/empty source so the
    caller can tell "failed" from "just didn't have much this week"."""
    all_entries: list[RawTrendEntry] = []
    sources_failed: list[str] = []
    entries_per_source: dict[str, int] = {}

    for name, fetch_fn in SOURCE_FETCHERS.items():
        try:
            entries = fetch_fn()
        except Exception as exc:  # noqa: BLE001 - one bad source must never abort the others
            logger.warning("Source %s failed: %s", name, exc)
            sources_failed.append(name)
            entries_per_source[name] = 0
            continue
        if not entries:
            logger.warning("Source %s parsed successfully but yielded zero entries", name)
            sources_failed.append(name)
            entries_per_source[name] = 0
            continue
        entries_per_source[name] = len(entries)
        all_entries.extend(entries)

    return all_entries, sources_failed, entries_per_source


def load_fixture_entries(paths: RepoPaths) -> list[RawTrendEntry]:
    fixture_path = paths.fixtures / "trend_blog_entries.json"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [RawTrendEntry(**item) for item in raw]


def collect_all_raw_entries(paths: RepoPaths) -> tuple[list[RawTrendEntry], dict]:
    """Returns (raw_entries, sources_meta). Only falls back to the fixture set if ALL four
    sources fail/yield nothing -- a partial failure (1-3 sources down) still proceeds on
    whatever real data the surviving sources produced. Dedup/grouping is no longer done
    here -- the caller hands this raw, undeduped list straight to prefilter.py."""
    collected_at = datetime.now(timezone.utc).isoformat()
    entries, sources_failed, entries_per_source = collect_raw_entries()

    used_fallback = False
    if not entries:
        logger.warning(
            "All %d trend-blog sources failed or returned zero entries; falling back to fixtures.",
            len(SOURCE_FETCHERS),
        )
        entries = load_fixture_entries(paths)
        used_fallback = True
        sources_failed = list(SOURCE_FETCHERS.keys())
        entries_per_source = {name: 0 for name in SOURCE_FETCHERS}

    sources_meta = {
        "sources_attempted": list(SOURCE_FETCHERS.keys()),
        "sources_failed": sources_failed,
        "entries_per_source": entries_per_source,
        "raw_entry_count": len(entries),
        "used_fallback": used_fallback,
        "collected_at": collected_at,
    }
    return entries, sources_meta
