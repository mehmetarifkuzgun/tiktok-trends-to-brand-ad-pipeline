"""Step B: download the video files Step A already found for one seed-tag trend.

No separate Apify call here -- Step A's single clockworks call already produced a
downloadAddr per video. That URL points at Apify's own key-value store (private per-run)
and returns 403 without the API token attached as a query param -- a bug found and fixed
in an earlier session -- so downloads still need the token, unlike a plain CDN URL.

Every candidate still gets a metadata JSON written, whether its download succeeded or not
(matches the existing seen_ids.json dedup design, which already recorded every processed
candidate regardless of outcome, and the project's broader "never silent" fallback
philosophy -- see CLAUDE.md) -- but as of 2026-09-18 that metadata carries an explicit
`download_status: "ok" | "failed"` rather than looking identical either way. A failed
download's .mp4 is also actively cleaned up (see `_download_video`) rather than left as a
truncated file that would otherwise look like a normal one from presence/size alone.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from agents.discover.common import RepoPaths, redact_secrets
from agents.discover.trend_inventory import TrendRecord, VideoCandidate

logger = logging.getLogger(__name__)


def _download_video(url: str, dest: Path, api_key: str | None) -> None:
    """Real bug found and fixed (2026-09-18): a mid-stream failure (e.g. a read timeout)
    used to leave whatever bytes had already arrived sitting at `dest` -- a truncated,
    unplayable .mp4 that looks like a normal file from presence/size alone (confirmed
    directly: a real timeout produced a 114MB file with a valid-looking header but almost
    certainly missing its trailing moov data). Any exception during the write now deletes
    that partial file before re-raising, so a failed download never leaves a corrupt file
    behind -- the caller's `download_status` field is the explicit signal, but file absence
    is now also a reliable one, not an accident of how far the stream got before it broke.
    """
    params = {"token": api_key} if api_key and urlparse(url).hostname == "api.apify.com" else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, params=params, stream=True, timeout=60.0) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
    except Exception:
        if dest.exists():
            dest.unlink()
        raise


def _copy_fixture_video(paths: RepoPaths, dest: Path) -> None:
    placeholder = paths.fixtures / "placeholder_video.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(placeholder.read_bytes())
    except Exception:
        if dest.exists():
            dest.unlink()
        raise


def fetch_and_save_seed_tag(
    record: TrendRecord,
    candidates: list[VideoCandidate],
    paths: RepoPaths,
    api_key: str | None,
    used_fallback: bool,
    seen_ids: set[str],
) -> dict[str, Any]:
    """Download (or fixture-copy) every video candidate for one seed-tag trend and persist
    them + their metadata under artifacts/{trend_id}/. Mutates seen_ids in place. Returns
    the trend_meta.json summary that gets written to disk."""
    trend_id = record.trend_id
    trend_dir = paths.artifacts / trend_id / "raw"

    saved = 0
    failed = 0
    for candidate in candidates:
        if candidate.video_id in seen_ids:
            logger.info("Skipping already-seen video %s", candidate.video_id)
            continue
        video_path = trend_dir / f"{candidate.video_id}.mp4"
        meta_path = trend_dir / f"{candidate.video_id}.json"
        # Explicit per-video status, not left to be inferred from file presence alone --
        # real finding (2026-09-18): a failed metadata dict was otherwise byte-for-byte
        # identical to a successful one, so a downstream reader (Process stage) had no way
        # to tell them apart short of separately checking .mp4 presence/validity itself.
        download_status = "ok"
        try:
            if candidate.used_fallback or not candidate.download_url:
                _copy_fixture_video(paths, video_path)
            else:
                _download_video(candidate.download_url, video_path, api_key)
        except Exception as exc:  # noqa: BLE001 - one bad download shouldn't drop the metadata or abort the run
            logger.warning("Video download failed for %s (%s); saving metadata only.", candidate.video_id, redact_secrets(exc))
            download_status = "failed"
            failed += 1
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {**candidate.metadata_dict(), "download_status": download_status}
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        seen_ids.add(candidate.video_id)
        saved += 1

    trend_meta = {
        **record.to_dict(),
        "used_fallback": used_fallback,
        "videos_fetched": saved,
        "videos_failed": failed,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    trend_meta_path = paths.artifacts / trend_id / "trend_meta.json"
    trend_meta_path.parent.mkdir(parents=True, exist_ok=True)
    trend_meta_path.write_text(json.dumps(trend_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return trend_meta
