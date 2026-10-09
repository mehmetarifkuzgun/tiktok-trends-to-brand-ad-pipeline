#!/usr/bin/env python3
"""Entrypoint for the Generate stage:

    python run_generate.py --api-key <GEMINI_KEY> [--model-tier lite|fast|standard]
                           [--top-n 3] [--trend TEXT ...] [--script-only] [--force]

For each of the top-N accepted videos (one per trend): an invented, photorealistic person delivers
spoken dialogue to camera (`ugc_scriptwriter.py` writes and validates the script); Veo generates
shot 1 text-to-video and shot 2 image-to-video chained on shot 1's last frame; ffmpeg assembles the
clips with captions and a picture-in-picture brand card (never rendered by Veo) and writes
`generation_manifest.json`. Defaults to Veo's `standard` tier; pass `--model-tier fast` for cheap
iteration.

Idempotent by default at every step, including final assembly: existing scripts, clips, and a valid
final video are reused rather than regenerated or reassembled (Veo jobs cost money; see
`assembler.already_assembled`). `--force` regenerates scripts and clips and forces reassembly.
`--script-only` stops before any Veo spend so scripts can be reviewed first. See GENERATE.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from agents.generate.assembler import AssemblyError, assemble, ensure_pip_asset, PIP_DEFAULT_WINDOW
from agents.generate.selector import ARTIFACTS_ROOT, select_top_videos
from agents.generate.ugc_scriptwriter import write_ugc_ad_script
from agents.generate.veo_client import MODEL_TIERS, PRICE_PER_SEC_720P, VeoGenerationError, make_client
from agents.generate.video_generator import generate_shots

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_generate")

RUN_LOG_PATH = ARTIFACTS_ROOT / "generate_run_log.json"

# UGC style: fights the hallucinated-on-screen-text failure mode found on Veo's standard tier
# (see GENERATE.md) via the SDK's own negative_prompt field, in
# addition to the prompt's own explicit no-text sentence.
UGC_NEGATIVE_PROMPT = (
    "on-screen text, captions, subtitles, burned-in words, letters, numbers, timestamps, "
    "watermark, logo, UI elements, overlay graphics, text on clothing, graphic prints, "
    "posters or signs with visible text"
)
DEFAULT_TIER = "standard"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stage: script -> Veo clips -> final ads .")
    parser.add_argument(
        "--api-key", default=os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key (script/vision LLM and Veo). Or set GEMINI_API_KEY in .env.",
    )
    parser.add_argument(
        "--model-tier", choices=sorted(MODEL_TIERS), default=DEFAULT_TIER,
        help="Veo tier: lite (cheapest), fast, standard (highest quality, the default).",
    )
    parser.add_argument("--top-n", type=int, default=3, help="How many top-scoring trends to generate (default 3).")
    parser.add_argument(
        "--trend", action="append",
        help="Only process selected trends whose trend_id contains this text (repeatable; each must match exactly one).",
    )
    parser.add_argument("--script-only", action="store_true", help="Stop after scripts (no Veo calls, no video spend).")
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate scripts and Veo clips, and force reassembly, even if valid output already exists (spends money again).",
    )
    return parser.parse_args(argv)


def append_run_log(entry: dict) -> None:
    runs = json.loads(RUN_LOG_PATH.read_text(encoding="utf-8")) if RUN_LOG_PATH.exists() else []
    runs.append(entry)
    RUN_LOG_PATH.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.api_key:
        logger.error("No Gemini API key provided (--api-key or GEMINI_API_KEY in .env). Cannot proceed.")
        return 1
    run_start = time.time()
    client = make_client(args.api_key)

    selection = select_top_videos(args.top_n)
    selected = selection["selected"]
    if args.trend:
        chosen = []
        for text in args.trend:
            matches = [s for s in selected if text in s["trend_id"]]
            if len(matches) != 1:
                logger.error("--trend %r must match exactly one selected trend; matched %d.", text, len(matches))
                return 1
            chosen.append(matches[0])
        selected = chosen
    logger.info(
        "Tier '%s'. Selected %d of %d accepted videos:",
        args.model_tier, len(selected), selection["accepted_candidates_considered"],
    )
    for s in selected:
        logger.info("  #%d %s (video %s) score=%.2f", s["rank"], s["trend_id"], s["video_id"], s["weighted_score"])

    outcomes: dict[str, dict] = {s["trend_id"]: {"status": "selected"} for s in selected}

    # ---- step 1: script (invented persona, validated) ----
    scripts: dict[str, tuple] = {}
    for sel in selected:
        tid = sel["trend_id"]
        t0 = time.time()
        try:
            script_path = write_ugc_ad_script(client, sel, force=args.force)
        except Exception as exc:  # noqa: BLE001
            logger.error("Script generation failed for %s: %s", tid, exc)
            outcomes[tid] = {"status": "failed", "stage": "script", "error": str(exc)}
            continue
        scripts[tid] = (script_path, json.loads(script_path.read_text(encoding="utf-8")))
        outcomes[tid].update(status="script_ok", script_sec=round(time.time() - t0, 1))
    if args.script_only:
        logger.info("--script-only: stopping before any Veo calls.")
        return 0 if all(o["status"] == "script_ok" for o in outcomes.values()) else 1

    # ---- step 2+3: Veo clips + assembly ----
    price = PRICE_PER_SEC_720P[args.model_tier]
    max_sec = sum(s["duration_sec"] for _, sc in scripts.values() for s in sc["shots"])
    logger.info(
        "Veo tier '%s' (%s): up to %ds of video, at most ~$%.2f (less if clips/assembly are reused).",
        args.model_tier, MODEL_TIERS[args.model_tier], max_sec, max_sec * price,
    )
    for sel in selected:
        trend_id = sel["trend_id"]
        if trend_id not in scripts:
            continue
        script_path, script = scripts[trend_id]
        t0 = time.time()
        try:
            clip_metas = generate_shots(
                client, trend_id, script, args.model_tier, force=args.force,
                seed_image=None, chain_frames=True, negative_prompt=UGC_NEGATIVE_PROMPT,
            )
            pip_asset = ensure_pip_asset(trend_id)
            manifest = assemble(
                sel, script, script_path, clip_metas, args.model_tier,
                style="ugc", force=args.force,
                pip_insert={"asset_path": pip_asset, "window": PIP_DEFAULT_WINDOW},
            )
        except (VeoGenerationError, AssemblyError) as exc:
            logger.error("Generation failed for %s: %s", trend_id, exc)
            outcomes[trend_id].update(status="failed", stage="video", error=str(exc))
            continue
        fv = manifest["final_video"]
        outcomes[trend_id].update(
            status="ok",
            video_sec=round(time.time() - t0, 1),
            final_video=fv["path"],
            final_duration_sec=fv["duration_sec"],
            clips_generated_this_run=sum(m.get("generated_this_run", False) for m in clip_metas),
            est_cost_this_run_usd=round(sum(m["est_cost_usd"] for m in clip_metas if m.get("generated_this_run")), 4),
        )
        logger.info("%s -> %s (%.1fs)", trend_id, fv["path"], fv["duration_sec"])

    wall = round(time.time() - run_start, 1)
    cost = round(sum(o.get("est_cost_this_run_usd", 0) for o in outcomes.values()), 4)
    ok = sum(o["status"] == "ok" for o in outcomes.values())
    append_run_log({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "style": "ugc",
        "model_tier": args.model_tier,
        "veo_model": MODEL_TIERS[args.model_tier],
        "force": args.force,
        "trend_filter": args.trend,
        "wall_time_sec": wall,
        "est_cost_this_run_usd": cost,
        "cost_note": "Estimated from published per-second pricing; the API reports no cost.",
        "outcomes": outcomes,
    })
    logger.info("Done: %d/%d final videos, wall time %.1fs, est. Veo cost this run $%.2f.", ok, len(selected), wall, cost)
    return 0 if ok == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
