#!/usr/bin/env python3
"""Single-command pipeline: Discover -> Process -> Generate.

    python run.py --api-key <GEMINI_KEY>          # Apify token from APIFY_API_TOKEN / .env
    python run.py --apify-key <TOKEN> --gemini-key <KEY>
    python run.py --dry-run                        # show what would run / be skipped, spend nothing

This is an orchestrator, not a fourth implementation: each stage is the existing entrypoint
(run_discover.py / run_process.py / run_generate.py), called in-process. What it adds is
(1) deciding, from what is really on disk, whether a stage still needs to run, (2) checking
between stages for the real failure modes (too few usable trends, too few accepted videos) and
stopping with a clear message instead of letting Generate fail confusingly, and (3) a stage-by-
stage progress and cost report. See README.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from agents.generate.selector import ARTIFACTS_ROOT, select_top_videos
from agents.generate.veo_client import configure_ssl_trust
from agents.process.analyzer import analysis_is_current, analysis_paths, discover_videos

STAGES = ("discover", "process", "generate")
EXIT_OK, EXIT_STAGE_FAILED, EXIT_BAD_USAGE, EXIT_INSUFFICIENT = 0, 1, 2, 3
DEFAULT_MAX_TRENDS = 10  # the value the original dataset used (Discover's own default is 30)
PIPELINE_LOG = ARTIFACTS_ROOT / "pipeline_run_log.json"
RUN_LOG_GENERATE = ARTIFACTS_ROOT / "generate_run_log.json"

# Gemini 3.8 Flash published price, USD per 1M tokens, through 2026-12-31 (pricing page read
# 2026-09-20). An ESTIMATE: the API returns token counts, never a cost.
GEMINI_INPUT_USD_PER_M = 0.75
GEMINI_OUTPUT_USD_PER_M = 3.75  # output including thinking tokens
APIFY_USAGE_URL = "https://api.apify.com/v2/users/me/limits"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run")


class StopPipeline(Exception):
    """A clean, expected stop: carries the message to show and the exit code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- keys

def resolve_keys(args: argparse.Namespace) -> dict[str, str | None]:
    """Apify and Gemini issue different keys, so there are two specific flags. `--api-key` is a
    convenience default: it fills the Gemini key (what Process and Generate use, and Discover's
    pre-filter), and fills the Apify token only if none is supplied any other way."""
    gemini = args.gemini_key or args.api_key or os.environ.get("GEMINI_API_KEY")
    apify = args.apify_key or os.environ.get("APIFY_API_TOKEN")
    apify_from_api_key = False
    if not apify and args.api_key:
        apify, apify_from_api_key = args.api_key, True
    return {"gemini": gemini, "apify": apify, "apify_from_api_key": apify_from_api_key}


# --------------------------------------------------------------------------- cost metering

class GeminiMeter:
    """Counts Gemini tokens across every stage by wrapping `Models.generate_content` (the one
    call all three stages use), so the run can report an estimated Gemini cost."""

    def __init__(self) -> None:
        self.calls = self.input_tokens = self.output_tokens = 0
        self._mark = (0, 0, 0)

    def install(self) -> None:
        from google.genai import models

        original, meter = models.Models.generate_content, self

        def metered(self_, *args, **kwargs):
            response = original(self_, *args, **kwargs)
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                meter.calls += 1
                meter.input_tokens += usage.prompt_token_count or 0
                meter.output_tokens += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            return response

        models.Models.generate_content = metered

    def take(self) -> dict[str, Any]:
        """Usage since the last `take()`."""
        calls, tin, tout = self.calls - self._mark[0], self.input_tokens - self._mark[1], self.output_tokens - self._mark[2]
        self._mark = (self.calls, self.input_tokens, self.output_tokens)
        cost = tin / 1e6 * GEMINI_INPUT_USD_PER_M + tout / 1e6 * GEMINI_OUTPUT_USD_PER_M
        return {"calls": calls, "input_tokens": tin, "output_tokens": tout, "est_cost_usd": round(cost, 4)}


def apify_monthly_usage(token: str | None) -> float | None:
    """Account-level month-to-date Apify spend (USD), best effort. Two readings around a stage
    give its real cost (assuming nothing else uses the account meanwhile)."""
    if not token:
        return None
    try:
        r = requests.get(APIFY_USAGE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        return float(r.json()["data"]["current"]["monthlyUsageUsd"])
    except Exception as exc:  # noqa: BLE001 - cost reporting must never break the run
        logger.warning("Could not read Apify usage (%s); Apify cost will be reported as an estimate only.", type(exc).__name__)
        return None


# --------------------------------------------------------------------------- reading real disk state

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_discover(top_n: int) -> dict[str, Any]:
    """Real (non-fixture) trends that have at least one successfully downloaded video."""
    usable, fixture, videos = [], [], 0
    for meta_path in sorted(ARTIFACTS_ROOT.glob("*/trend_meta.json")):
        raw = meta_path.parent / "raw"
        ok = [
            p for p in raw.glob("*.json")
            if p.name.count(".") == 1 and _read_json(p).get("download_status") == "ok" and p.with_suffix(".mp4").exists()
        ]
        if _read_json(meta_path).get("used_fallback"):
            fixture.append(meta_path.parent.name)
        elif ok:
            usable.append(meta_path.parent.name)
            videos += len(ok)
    return {"usable_trends": usable, "fixture_trends": fixture, "videos": videos, "complete": len(usable) >= top_n}


def inspect_process() -> dict[str, Any]:
    all_videos = discover_videos()
    pending = [v for v in all_videos if not analysis_is_current(analysis_paths(v)[0])]
    accepted, decided = [], 0
    for v in all_videos:
        decision_path = analysis_paths(v)[1]
        if decision_path.exists() and analysis_is_current(analysis_paths(v)[0]):
            decided += 1
            decision = _read_json(decision_path)
            if decision["status"] == "accepted":
                accepted.append((v.parent.parent.name, decision["video_id"], decision["weighted_score"]))
    return {
        "videos": len(all_videos), "pending": len(pending), "analyzed": decided,
        "accepted": accepted, "rejected": decided - len(accepted),
        "accepted_trends": sorted({a[0] for a in accepted}),
    }


def inspect_generate(top_n: int) -> dict[str, Any]:
    """For each of the top-N selected videos: does a valid final video + manifest already exist
    for exactly that source video?"""
    selection = select_top_videos(top_n, write=False)
    done, todo = [], []
    for sel in selection["selected"]:
        trend_dir = ARTIFACTS_ROOT / sel["trend_id"]
        manifest_path, final_path = trend_dir / "generation_manifest.json", trend_dir / "final_video.mp4"
        ok = False
        if manifest_path.exists() and final_path.exists():
            m = _read_json(manifest_path)
            fv = m.get("final_video", {})
            ok = (
                m.get("source", {}).get("video_id") == sel["video_id"]
                and fv.get("width", 0) * 16 == fv.get("height", 0) * 9
                and 8 <= fv.get("duration_sec", 0) <= 15
            )
        (done if ok else todo).append(sel)
    return {"selected": selection["selected"], "done": done, "todo": todo, "complete": len(selection["selected"]) >= top_n and not todo}


# --------------------------------------------------------------------------- output helpers

def banner(index: int, name: str, text: str) -> None:
    print(f"\n{'=' * 78}\n[{index}/3] {name.upper()}: {text}\n{'=' * 78}", flush=True)


def latest_collection_meta() -> dict[str, Any] | None:
    metas = sorted((ARTIFACTS_ROOT.parent / "data" / "trends").glob("*/collection_meta.json"))
    return _read_json(metas[-1]) if metas else None


@dataclass
class StageResult:
    name: str
    status: str  # "ran" | "skipped" | "failed"
    seconds: float = 0.0
    detail: str = ""
    costs: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- gates

def gate_after_discover(top_n: int) -> dict[str, Any]:
    state = inspect_discover(top_n)
    if state["complete"]:
        return state
    meta = latest_collection_meta() or {}
    hints = []
    if state["fixture_trends"]:
        hints.append(f"{len(state['fixture_trends'])} trend(s) came from FIXTURE fallback data, which is never used for a real run "
                     "(a source, key or network failure -- check the Discover log above)")
    if meta.get("sources_failed"):
        hints.append(f"sources that failed: {meta['sources_failed']}")
    if meta.get("prefilter"):
        pf = meta["prefilter"]
        hints.append(f"pre-filter accepted {pf.get('accepted_count')} of {pf.get('groups_formed')} groups; {pf.get('accepted_count_after_cap')} kept after the cap")
    raise StopPipeline(
        f"Discover left only {len(state['usable_trends'])} usable trend(s) with downloaded videos; Generate needs at least "
        f"{top_n} (one per ad). Stopping before Process/Generate.\n  - " + "\n  - ".join(hints or ["no further detail recorded"])
        + "\n  Try: raising --max-trends, checking the sources/keys above, or lowering --top-n.",
        EXIT_INSUFFICIENT,
    )


def gate_after_process(top_n: int) -> dict[str, Any]:
    state = inspect_process()
    if len(state["accepted_trends"]) >= top_n:
        if len(state["accepted"]) < 10:
            logger.warning("Only %d videos were accepted; 10-20 adaptable hooks is a healthier pool to pick from.", len(state["accepted"]))
        return state
    raise StopPipeline(
        f"Process accepted {len(state['accepted'])} video(s) across {len(state['accepted_trends'])} distinct trend(s) "
        f"({state['analyzed']} analyzed: {len(state['accepted'])} accepted, {state['rejected']} rejected; "
        f"{state['pending']} not analyzed). Generate needs {top_n} accepted videos from {top_n} distinct trends, one per ad. "
        "Stopping before Generate.\n"
        + ("  Some videos failed analysis (see the Process log); re-running resumes only those.\n" if state["pending"] else "")
        + "  Try: raising --max-trends to give Process more candidates, or lowering --top-n.",
        EXIT_INSUFFICIENT,
    )


# --------------------------------------------------------------------------- stages

def stage_discover(args, keys, force: bool, meter: GeminiMeter) -> StageResult:
    state = inspect_discover(args.top_n)
    if state["complete"] and not force:
        return StageResult("discover", "skipped", detail=f"already done: {len(state['usable_trends'])} real trends, {state['videos']} videos on disk")
    if not keys["apify"] or not keys["gemini"]:
        raise StopPipeline("Discover needs both an Apify token and a Gemini key (missing: "
                           + ", ".join(k for k, v in (("Apify token (--apify-key / APIFY_API_TOKEN)", keys["apify"]), ("Gemini key (--gemini-key / --api-key)", keys["gemini"])) if not v) + ").", EXIT_BAD_USAGE)
    if force and state["usable_trends"]:
        logger.warning("--force discover on an existing dataset: Discover writes into data/trends/<week>/ and artifacts/, and its cross-week "
                       "dedup (seen_ids.json) skips videos already downloaded, so it will not simply recreate what is there.")
    import run_discover

    before, t0 = apify_monthly_usage(keys["apify"]), time.monotonic()
    code = run_discover.main(["--api-key", keys["apify"], "--gemini-api-key", keys["gemini"], "--max-trends", str(args.max_trends)])
    seconds = time.monotonic() - t0
    after = apify_monthly_usage(keys["apify"])
    meta = latest_collection_meta() or {}
    costs = {"apify_estimate_usd": meta.get("estimated_cost_usd"),
             "apify_actual_usd": round(after - before, 4) if before is not None and after is not None else None,
             "gemini": meter.take()}
    if code != 0:
        return StageResult("discover", "failed", seconds, f"run_discover exited {code}", costs)
    after_state = gate_after_discover(args.top_n)
    return StageResult("discover", "ran", seconds, f"{len(after_state['usable_trends'])} real trends, {after_state['videos']} videos", costs)


def stage_process(args, keys, force: bool, meter: GeminiMeter) -> StageResult:
    state = inspect_process()
    if state["videos"] and not state["pending"] and not force:
        gate_after_process(args.top_n)  # a completed-but-too-small Process must still stop the pipeline here
        return StageResult("process", "skipped", detail=f"already done: all {state['videos']} videos analyzed ({len(state['accepted'])} accepted, {state['rejected']} rejected)")
    if not state["videos"]:
        raise StopPipeline("Process found no downloaded videos under artifacts/*/raw/ (Discover produced nothing to analyze).", EXIT_INSUFFICIENT)
    if not keys["gemini"]:
        raise StopPipeline("Process needs a Gemini key (--gemini-key / --api-key / GEMINI_API_KEY).", EXIT_BAD_USAGE)
    import run_process

    limit = state["videos"] if force else state["pending"]
    argv = ["--api-key", keys["gemini"], "--limit", str(limit)] + (["--force"] if force else [])
    t0 = time.monotonic()
    code = run_process.main(argv)
    seconds = time.monotonic() - t0
    costs = {"gemini": meter.take()}
    if code != 0:
        return StageResult("process", "failed", seconds, f"run_process exited {code}", costs)
    after = gate_after_process(args.top_n)
    return StageResult("process", "ran", seconds, f"{after['analyzed']} analyzed: {len(after['accepted'])} accepted, {after['rejected']} rejected", costs)


def stage_generate(args, keys, force: bool, meter: GeminiMeter) -> StageResult:
    state = inspect_generate(args.top_n)
    if state["complete"] and not force:
        return StageResult("generate", "skipped", detail=f"already done: {len(state['done'])} valid final videos + manifests")
    if not keys["gemini"]:
        raise StopPipeline("Generate needs a Gemini key (--gemini-key / --api-key / GEMINI_API_KEY).", EXIT_BAD_USAGE)
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise StopPipeline(f"Generate needs {' and '.join(missing)} on PATH (video assembly); not found.", EXIT_BAD_USAGE)
    targets = state["selected"] if force else state["todo"]
    import run_generate

    runs_before = len(_read_json(RUN_LOG_GENERATE)) if RUN_LOG_GENERATE.exists() else 0
    argv = ["--api-key", keys["gemini"], "--model-tier", args.model_tier, "--top-n", str(args.top_n)]
    for sel in targets:  # only the trends that still need work: finished ones are never re-assembled
        argv += ["--trend", sel["trend_id"]]
    if force:
        argv.append("--force")
    t0 = time.monotonic()
    code = run_generate.main(argv)
    seconds = time.monotonic() - t0
    log = _read_json(RUN_LOG_GENERATE) if RUN_LOG_GENERATE.exists() else []
    veo = log[-1]["est_cost_this_run_usd"] if len(log) > runs_before else 0.0
    costs = {"veo_estimate_usd": veo, "gemini": meter.take()}
    after = inspect_generate(args.top_n)
    if code != 0 or not after["complete"]:
        return StageResult("generate", "failed", seconds, f"run_generate exited {code}; {len(after['done'])}/{args.top_n} final videos valid", costs)
    return StageResult("generate", "ran", seconds, f"{len(after['done'])} final videos", costs)


STAGE_FUNCS = {"discover": stage_discover, "process": stage_process, "generate": stage_generate}


# --------------------------------------------------------------------------- reporting

def print_plan(args, force: set[str]) -> None:
    d, p, g = inspect_discover(args.top_n), inspect_process(), inspect_generate(args.top_n)
    def line(name, done, forced, detail):
        action = "RUN (forced)" if forced else ("SKIP (already done)" if done else "RUN")
        print(f"  {name:<9} {action:<20} {detail}")
    print("Plan (from what is on disk now):")
    line("discover", d["complete"], "discover" in force, f"{len(d['usable_trends'])} real trends / {d['videos']} videos on disk (need >= {args.top_n} trends)")
    line("process", bool(p["videos"]) and not p["pending"], "process" in force, f"{p['videos']} videos, {p['pending']} not yet analyzed; {len(p['accepted'])} accepted across {len(p['accepted_trends'])} trends")
    line("generate", g["complete"], "generate" in force, f"{len(g['done'])}/{args.top_n} final videos already valid for the current top-{args.top_n}")


def print_summary(results: list[StageResult], wall: float) -> dict[str, float]:
    print(f"\n{'=' * 78}\nPIPELINE SUMMARY\n{'=' * 78}")
    gemini_cost = sum(r.costs.get("gemini", {}).get("est_cost_usd", 0) for r in results)
    gemini_tokens = sum(r.costs.get("gemini", {}).get("input_tokens", 0) + r.costs.get("gemini", {}).get("output_tokens", 0) for r in results)
    apify_est = sum(r.costs.get("apify_estimate_usd") or 0 for r in results)
    apify_actual = sum(r.costs.get("apify_actual_usd") or 0 for r in results)
    veo = sum(r.costs.get("veo_estimate_usd") or 0 for r in results)
    for r in results:
        print(f"  {r.name:<9} {r.status.upper():<8} {r.seconds:7.1f}s  {r.detail}")
    print(f"\n  Wall time: {wall:.1f}s")
    if any(r.status == "ran" for r in results):
        apify_used = apify_actual if any(r.costs.get("apify_actual_usd") is not None for r in results) else apify_est
        apify_kind = "measured: Apify account usage before/after" if any(r.costs.get("apify_actual_usd") is not None for r in results) else "estimated"
        total = apify_used + gemini_cost + veo
        print(f"  Apify   ${apify_used:.3f}  ({apify_kind}; Discover's own estimate: ${apify_est:.3f})")
        print(f"  Gemini  ${gemini_cost:.3f}  (ESTIMATE: {gemini_tokens:,} tokens x published Gemini 3.8 Flash price; the API returns no cost)")
        print(f"  Veo     ${veo:.3f}  (ESTIMATE: published $/second x generated seconds; the API returns no cost)")
        print(f"  TOTAL   ${total:.3f}  (Apify measured where possible; Gemini and Veo are estimates)")
        return {"apify_usd": round(apify_used, 4), "gemini_est_usd": round(gemini_cost, 4), "veo_est_usd": round(veo, 4), "total_usd": round(total, 4)}
    print("  Nothing ran, so nothing was spent.")
    return {}


def print_outputs(top_n: int) -> None:
    print("\nFinal videos:")
    for sel in inspect_generate(top_n)["selected"]:
        mp = ARTIFACTS_ROOT / sel["trend_id"] / "generation_manifest.json"
        if mp.exists():
            m = _read_json(mp)
            fv = m["final_video"]
            print(f"  {fv['path']}  ({fv['duration_sec']}s, {fv['width']}x{fv['height']})  <- {sel['trend_id']}, video {sel['video_id']}, score {sel['weighted_score']}")
    print("  (each has a generation_manifest.json tracing it back to its script, Process decision and source video)")


# --------------------------------------------------------------------------- CLI

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trend-to-creative pipeline: Discover -> Process -> Generate, in one command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
API keys (the only required input). Two providers issue different keys:
  Gemini  -- Process, Generate (script, vision, Veo video) and Discover's pre-filter.
  Apify   -- Discover's video fetch (clockworks/tiktok-scraper).
  --gemini-key / --apify-key set each explicitly. --api-key is a convenience default: it is used
  as the Gemini key, and as the Apify token only if none is supplied via --apify-key or the
  APIFY_API_TOKEN environment variable (or .env). Environment fallbacks: GEMINI_API_KEY, APIFY_API_TOKEN.

Resume / skip: before each stage the pipeline looks at what is really on disk (non-fixture trends
with downloaded videos; analysis.json per video; valid final videos + manifests for the current
top-N) and skips a stage that is already complete, so re-runs spend nothing. --force redoes stages:
  --force                   redo all three (spends money)
  --force process generate  redo only the named stages
Note: --force generate regenerates scripts and Veo clips (and overwrites hand-edited scripts).

Between stages it stops, with a message, rather than crash later: if Discover leaves fewer than
--top-n usable trends, or Process accepts videos from fewer than --top-n distinct trends.

Exit codes: 0 success | 1 a stage failed | 2 bad usage (missing key, ffmpeg not found) |
            3 stopped early: not enough upstream output for the next stage.

Examples:
  python run.py --api-key <GEMINI_KEY>            # Apify token from .env / APIFY_API_TOKEN
  python run.py --dry-run                         # what would run vs. be skipped
  python run.py --api-key <KEY> --force generate  # redo only the video stage
""",
    )
    parser.add_argument("--api-key", help="Convenience default key: used as the Gemini key (and as the Apify token only if none is supplied otherwise).")
    parser.add_argument("--gemini-key", help="Gemini API key (overrides --api-key for Gemini). Env: GEMINI_API_KEY.")
    parser.add_argument("--apify-key", help="Apify API token (overrides --api-key for Apify). Env: APIFY_API_TOKEN.")
    parser.add_argument("--top-n", type=int, default=3, help="How many final ad videos to make, one per distinct trend (default 3).")
    parser.add_argument("--max-trends", type=int, default=DEFAULT_MAX_TRENDS,
                        help=f"Discover: cap on trends fetched (default {DEFAULT_MAX_TRENDS}, the value the original dataset used; Discover's own default is 30).")
    parser.add_argument("--model-tier", choices=("lite", "fast", "standard"), default="fast", help="Veo tier for Generate (default fast; see GENERATE.md).")
    parser.add_argument("--force", nargs="*", choices=STAGES, default=None, metavar="STAGE",
                        help="Redo already-complete stages (spends money). No argument = all stages; or name stages: discover process generate.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would run or be skipped from the current disk state, then exit. Spends nothing, needs no keys.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.top_n < 1:
        print("--top-n must be at least 1.", file=sys.stderr)
        return EXIT_BAD_USAGE
    force = set(STAGES) if args.force == [] else set(args.force or [])
    configure_ssl_trust()  # Avast HTTPS scanning breaks Python TLS on this machine; no-op elsewhere
    print_plan(args, force)
    if args.dry_run:
        return EXIT_OK

    keys = resolve_keys(args)
    if keys["apify_from_api_key"]:
        logger.warning("No separate Apify token found; using --api-key as the Apify token too. Apify and Gemini issue different keys, so "
                       "if Discover fails to authenticate, pass --apify-key or set APIFY_API_TOKEN.")
    meter = GeminiMeter()
    meter.install()

    results: list[StageResult] = []
    code, wall_start = EXIT_OK, time.monotonic()
    print(f"\nRunning: {' -> '.join(STAGES)}  (top-n={args.top_n}, max-trends={args.max_trends}, Veo tier={args.model_tier})")
    try:
        for index, name in enumerate(STAGES, 1):
            banner(index, name, "checking what is already on disk...")
            result = STAGE_FUNCS[name](args, keys, name in force, meter)
            results.append(result)
            print(f"[{index}/3] {name.upper()} {result.status.upper()} in {result.seconds:.1f}s: {result.detail}", flush=True)
            if result.status == "failed":
                code = EXIT_STAGE_FAILED
                break
    except StopPipeline as stop:
        print(f"\nSTOPPED: {stop}", file=sys.stderr)
        code = stop.code
    except KeyboardInterrupt:
        print("\nInterrupted. Everything already written stays on disk; re-running resumes from there.", file=sys.stderr)
        code = EXIT_STAGE_FAILED

    wall = time.monotonic() - wall_start
    costs = print_summary(results, wall)
    if code == EXIT_OK:
        print_outputs(args.top_n)
    if any(r.status == "ran" for r in results):
        runs = _read_json(PIPELINE_LOG) if PIPELINE_LOG.exists() else []
        runs.append({
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "exit_code": code,
            "top_n": args.top_n, "max_trends": args.max_trends, "model_tier": args.model_tier, "forced_stages": sorted(force),
            "wall_time_sec": round(wall, 1), "costs": costs,
            "stages": [{"name": r.name, "status": r.status, "seconds": round(r.seconds, 1), "detail": r.detail, "costs": r.costs} for r in results],
        })
        PIPELINE_LOG.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
