"""Generate stage, step 1: pick the top-N accepted videos by weighted_score.

Reads the per-video decision.json files Process wrote and records the selection in
artifacts/generate_selection.json -- the first link in the traceability chain
(final video -> ... -> selection -> Process decision -> original trend).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.process.analyzer import analysis_is_current

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"
SELECTION_PATH = ARTIFACTS_ROOT / "generate_selection.json"


def rel(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def select_top_videos(top_n: int = 3, *, write: bool = True) -> dict[str, Any]:
    """Top `top_n` accepted videos by weighted_score (ties broken by video_id, so the
    result is deterministic). Output is laid out one folder per trend_id, so a second
    video from an already-selected trend is skipped (and recorded) rather than allowed to
    overwrite the first's ad_script.json / clips / final video."""
    candidates: list[dict[str, Any]] = []
    for decision_path in sorted(ARTIFACTS_ROOT.glob("*/raw/*.decision.json")):
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if decision["status"] != "accepted":
            continue
        raw_dir = decision_path.parent
        video_id = decision["video_id"]
        if not analysis_is_current(raw_dir / f"{video_id}.analysis.json"):
            continue  # scored for a different brand: stale, never selected
        candidates.append({
            "trend_id": raw_dir.parent.name,
            "video_id": video_id,
            "weighted_score": decision["weighted_score"],
            "decision_path": rel(decision_path),
            "analysis_path": rel(raw_dir / f"{video_id}.analysis.json"),
            "video_path": rel(raw_dir / f"{video_id}.mp4"),
        })
    candidates.sort(key=lambda c: (-c["weighted_score"], c["video_id"]))

    selected: list[dict[str, Any]] = []
    skipped_same_trend: list[dict[str, Any]] = []
    for cand in candidates:
        if len(selected) == top_n:
            break
        if any(s["trend_id"] == cand["trend_id"] for s in selected):
            skipped_same_trend.append(cand)
            continue
        selected.append({"rank": len(selected) + 1, **cand})

    selection = {
        "selected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": f"top {top_n} by weighted_score among videos with decision status 'accepted'; "
        "at most one video per trend_id (artifacts are laid out per trend)",
        "accepted_candidates_considered": len(candidates),
        "selected": selected,
        "skipped_same_trend": skipped_same_trend,
    }
    if write:  # write=False lets callers (run.py) inspect the selection without touching disk
        SELECTION_PATH.write_text(json.dumps(selection, indent=2, ensure_ascii=False), encoding="utf-8")
    return selection
