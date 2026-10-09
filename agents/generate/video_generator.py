"""Generate stage, step 3: turn each shot of an ad_script.json into a Veo clip on disk.

One Veo job per shot, saved immediately to artifacts/{trend_id}/generated_clips/shot_{n}.mp4
(Veo keeps files server-side for only 2 days, so nothing is left there) with a
shot_{n}.meta.json sidecar recording exactly what was generated and how. A shot whose clip +
sidecar already exist with the same model/prompt/duration/seed is reused rather than paid for
again, unless `force` -- re-running after a later-stage failure must not re-bill.

Optional image-to-video seeding (each shot's first frame):
- `seed_image`: a reference image used as shot 1's first frame (optional).
  The UGC style passes `None` here -- there is no real image of an invented person to seed
  shot 1 from, so it is pure text-to-video; only `chain_frames` (below) applies.
- `chain_frames`: each later shot's first frame is the previous clip's actual last frame,
  so the character/setting carries across the cut instead of being re-imagined from text.

`negative_prompt` (optional): forwarded to every `generate_clip` call via the SDK's own field.
The UGC style passes a strengthened negative prompt to suppress hallucinated on-screen text
(see GENERATE.md's "Current architecture"). Part of the per-shot reuse key, so changing it forces regeneration.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai

from agents.generate.selector import ARTIFACTS_ROOT, rel
from agents.generate.veo_client import (
    MODEL_TIERS,
    estimate_cost_usd,
    generate_clip,
    snap_duration,
)

logger = logging.getLogger(__name__)


def clips_dir(trend_id: str) -> Path:
    return ARTIFACTS_ROOT / trend_id / "generated_clips"


def extract_last_frame(clip: Path, dest: Path) -> Path:
    """Write the clip's final frame to `dest` (PNG). `-update 1` keeps overwriting the one
    output file with each successive frame of the last 0.1s, so the survivor is the last."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-sseof", "-0.1",
         "-i", str(clip), "-update", "1", str(dest)],
        check=True,
    )
    return dest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def generate_shots(
    client: genai.Client,
    trend_id: str,
    script: dict[str, Any],
    tier: str,
    *,
    force: bool = False,
    seed_image: Path | None = None,
    chain_frames: bool = False,
    negative_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Generate (or reuse) every shot's clip. Returns one metadata dict per shot, each with
    `generated_this_run` so the caller can total real spend. Raises VeoGenerationError on the
    first failed shot -- earlier shots' clips are already saved and will be reused on re-run."""
    model = MODEL_TIERS[tier]
    out_dir = clips_dir(trend_id)
    results: list[dict[str, Any]] = []
    prev_clip: Path | None = None
    prev_order: int | None = None

    for shot in sorted(script["shots"], key=lambda s: s["order"]):
        order = shot["order"]
        duration = snap_duration(shot["duration_sec"])
        if duration != shot["duration_sec"]:
            logger.warning("shot %d: duration %ss is not a Veo value; using %ss", order, shot["duration_sec"], duration)
        clip_path = out_dir / f"shot_{order}.mp4"
        meta_path = out_dir / f"shot_{order}.meta.json"

        seed_path: Path | None = None
        seed_type: str | None = None
        if prev_clip is None and seed_image is not None:
            seed_path, seed_type = seed_image, "reference_image"
        elif prev_clip is not None and chain_frames:
            seed_path = extract_last_frame(
                prev_clip, out_dir / f"shot_{order}_seed_from_shot_{prev_order}_last_frame.png",
            )
            seed_type = "previous_shot_last_frame"
        seed = (
            {"type": seed_type, "path": rel(seed_path), "sha256": _sha(seed_path)} if seed_path else None
        )

        reused = False
        if clip_path.exists() and meta_path.exists() and not force:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            same = (
                meta["veo_model"], meta["prompt"], meta["duration_sec"],
                (meta.get("seed") or {}).get("sha256"), meta.get("negative_prompt"),
            ) == (
                model, shot["visual_prompt"], duration, seed["sha256"] if seed else None, negative_prompt,
            )
            if same:
                logger.info("%s shot %d: reusing existing clip (same model/prompt/duration/seed)", trend_id, order)
                meta["generated_this_run"] = False
                results.append(meta)
                reused = True
            else:
                logger.info("%s shot %d: existing clip differs from script/model/seed; regenerating", trend_id, order)

        if not reused:
            logger.info(
                "%s shot %d: generating %ds clip with %s (%s)", trend_id, order, duration, model,
                f"seed: {seed_type}" if seed else "text-to-video",
            )
            gen = generate_clip(
                client, model=model, prompt=shot["visual_prompt"], duration_sec=duration,
                dest=clip_path, image_path=seed_path, negative_prompt=negative_prompt,
            )
            meta = {
                "order": order,
                "clip_path": rel(clip_path),
                "veo_model": model,
                "veo_tier": tier,
                "prompt": shot["visual_prompt"],
                "negative_prompt": negative_prompt,
                "dialogue": shot.get("dialogue"),
                "duration_sec": duration,
                "aspect_ratio": "9:16",
                "resolution": "720p",
                "seed": seed,
                "est_cost_usd": estimate_cost_usd(tier, duration),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **gen,
            }
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            meta["generated_this_run"] = True
            results.append(meta)

        prev_clip, prev_order = clip_path, order

    return results
