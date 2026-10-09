"""Generate stage, step 4: join a trend's shot clips into one 9:16 ad and write its manifest.

ffmpeg concatenates the clips (video normalized to 720x1280@24fps, audio to 48kHz stereo --
a clip with no audio track gets silence rather than failing), burns in each shot's
`onscreen_text` for that shot's time window, optionally composites a real-screenshot
picture-in-picture card (UGC style only -- see `pip_insert`/`ensure_pip_asset` below), clamps
total length into 8-15s, then probes the result and refuses to call it done unless it really is
9:16, in range, and has video+audio. Finally writes generation_manifest.json: the traceability
chain from the final video back through every stage to the source trend video.

**Idempotency (`already_assembled`)**: unlike earlier versions of this module, `assemble()` no
longer reassembles unconditionally. Before doing any ffmpeg work it checks whether a valid
final video + manifest already exist for this exact source video and script (same principle
already used for scripts and clips elsewhere in this stage), and skips if so. This is what
actually prevents `run_generate.py` from silently overwriting a promoted primary with a
mismatched reassembly when run again without `--force` -- see GENERATE.md's "Running it" for
the incident this closes.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.generate.ugc_scriptwriter import MAX_TOTAL_SEC, MIN_TOTAL_SEC
from agents.generate.selector import ARTIFACTS_ROOT, REPO_ROOT, rel
from agents.generate.video_generator import clips_dir

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 720, 1280, 24
TEXT_WRAP_CHARS = 18
TEXT_FONT_SIZE = 56
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]

# A brand/product card composited as a picture-in-picture overlay (never rendered
# by Veo -- see GENERATE.md). One canonical asset reused across every UGC trend; copied into each
# trend's own folder so artifacts stay self-contained per the trend_id-centric convention. The
# shipped asset is a neutral generated placeholder (scripts/make_placeholder_pip_card.py); replace
# config/ugc_pip_insert.jpg with your own brand card.
PIP_SOURCE_ASSET = REPO_ROOT / "config" / "ugc_pip_insert.jpg"
PIP_ASSET_NAME = "pip_insert.jpg"
PIP_DEFAULT_WINDOW = (5.5, 7.5)  # seconds into the final timeline


class AssemblyError(RuntimeError):
    pass


def _find_font() -> Path:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    raise AssemblyError(f"No bold TTF font found for on-screen text; looked in {_FONT_CANDIDATES}")


def ensure_pip_asset(trend_id: str) -> Path:
    """Copy the canonical brand/product-card PiP asset into this trend's folder if not
    already present. Returns the trend-local path. Idempotent: a no-op if it already exists."""
    dest = ARTIFACTS_ROOT / trend_id / PIP_ASSET_NAME
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PIP_SOURCE_ASSET, dest)
    return dest


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise AssemblyError(f"{path} has no video stream")
    num, den = video["r_frame_rate"].split("/")
    return {
        "duration_sec": round(float(info["format"]["duration"]), 3),
        "width": video["width"],
        "height": video["height"],
        "fps": round(int(num) / int(den), 2),
        "video_codec": video["codec_name"],
        "has_audio": audio is not None,
        "audio_codec": audio["codec_name"] if audio else None,
        "size_bytes": int(info["format"]["size"]),
    }


def _build_filter(
    durations: list[float], has_audio: list[bool], overlays: list[dict], pad_sec: float,
    pip: dict[str, Any] | None = None,
) -> str:
    """`pip`, when given: {"input_index": int, "start": float, "end": float} -- an extra
    ffmpeg input (the brand/product card) composited as a bordered picture-in-picture
    card, top-left, for `[start, end)` seconds of the final timeline. `None`
    gives the plain concat+captions graph (one extra harmless `copy`
    filter aside, which just relabels the concat+text output as `[vfinal]`)."""
    parts: list[str] = []
    for i, dur in enumerate(durations):
        parts.append(
            f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]"
        )
        if has_audio[i]:
            parts.append(
                f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{dur}[a{i}]"
            )
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur}[a{i}]")
    n = len(durations)
    parts.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    video_chain = [
        f"drawtext=fontfile=font.ttf:textfile={o['file']}:fontsize={TEXT_FONT_SIZE}:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.66-text_h/2:line_spacing=12:"
        f"text_align=center:enable='between(t,{o['start']:.3f},{o['end']:.3f})'"
        for o in overlays
    ]
    if pad_sec > 0:
        video_chain.append(f"tpad=stop_mode=clone:stop_duration={pad_sec:.3f}")
    parts.append(f"[vc]{','.join(video_chain) if video_chain else 'copy'}[vtxt]")
    if pip:
        parts.append(f"[{pip['input_index']}:v]scale=260:260,pad=284:284:12:12:color=white[ins]")
        parts.append(
            f"[vtxt][ins]overlay=24:50:enable='between(t,{pip['start']:.3f},{pip['end']:.3f})'[vfinal]"
        )
    else:
        parts.append("[vtxt]copy[vfinal]")
    parts.append(f"[ac]{f'apad=pad_dur={pad_sec:.3f}' if pad_sec > 0 else 'anull'}[afinal]")
    return ";".join(parts)


def already_assembled(trend_dir: Path, selected: dict[str, Any], script: dict[str, Any]) -> dict[str, Any] | None:
    """The idempotency guard: returns the existing manifest if a valid `final_video.mp4` +
    `generation_manifest.json` already exist for this exact source video and script (every
    clip's manifest prompt matches the corresponding shot's current `visual_prompt`), so
    `assemble()` can skip redoing any ffmpeg work. Returns `None` (reassembly needed) if
    either file is missing, unreadable, refers to a different source video, was built from a
    script that has since changed, or the final video itself fails the same validity checks
    `assemble()` enforces after building it.

    This is the actual fix for the corruption risk flagged in GENERATE.md/CLAUDE.md
    (2026-09-23): earlier, `assemble()` ran unconditionally on every call, so re-running
    `run_generate.py` on a trend whose primary output was produced by a different pipeline
    (or just already existed) would silently overwrite it. Now it won't, without needing
    `--force` or any out-of-band warning to prevent it."""
    final_path = trend_dir / "final_video.mp4"
    manifest_path = trend_dir / "generation_manifest.json"
    if not (final_path.exists() and manifest_path.exists()):
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if manifest.get("source", {}).get("video_id") != selected.get("video_id"):
        return None
    shots_by_order = {s["order"]: s for s in script["shots"]}
    for clip in manifest.get("clips", []):
        shot = shots_by_order.get(clip.get("order"))
        if shot is None or clip.get("prompt") != shot.get("visual_prompt"):
            return None
    try:
        final = probe(final_path)
    except (AssemblyError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    if final["width"] * 16 != final["height"] * 9:
        return None
    if not (MIN_TOTAL_SEC - 0.1 <= final["duration_sec"] <= MAX_TOTAL_SEC + 0.1):
        return None
    if not final["has_audio"]:
        return None
    return manifest


def assemble(
    selected: dict[str, Any],
    script: dict[str, Any],
    script_path: Path,
    clip_metas: list[dict[str, Any]],
    tier: str,
    *,
    style: str = "ugc",
    pip_insert: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Concatenate the clips into artifacts/{trend_id}/final_video.mp4 and write
    generation_manifest.json. Returns the manifest.

    Idempotent by default (see `already_assembled`): if a valid final video + manifest already
    exist for this exact source video and script, returns the existing manifest without doing
    any ffmpeg work, unless `force`. `style` is recorded in the manifest
    for audit; `pip_insert` (UGC only) is `{"asset_path": Path, "window": (start, end)}` -- the
    brand/product card composited as a picture-in-picture overlay, never rendered by Veo."""
    trend_id = selected["trend_id"]
    trend_dir = ARTIFACTS_ROOT / trend_id
    final_path = trend_dir / "final_video.mp4"

    if not force:
        existing = already_assembled(trend_dir, selected, script)
        if existing is not None:
            logger.info(
                "%s: final_video.mp4 already matches this source video and script; skipping "
                "reassembly (use force=True to redo)", trend_id,
            )
            return existing

    shots = sorted(script["shots"], key=lambda s: s["order"])
    clip_paths = [clips_dir(trend_id) / f"shot_{s['order']}.mp4" for s in shots]

    clip_probes = [probe(p) for p in clip_paths]
    durations = [p["duration_sec"] for p in clip_probes]
    total = sum(durations)
    pad_sec = max(0.0, MIN_TOTAL_SEC - total)
    target = min(max(total, MIN_TOTAL_SEC), MAX_TOTAL_SEC)

    overlays: list[dict[str, Any]] = []
    start = 0.0
    for shot, dur in zip(shots, durations):
        text = (shot.get("onscreen_text") or "").strip()
        if text:
            overlays.append({
                "shot_order": shot["order"], "text": text, "start": start, "end": start + dur,
            })
        start += dur

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # Relative names + cwd=work keep Windows drive-letter colons out of filter strings.
        shutil.copy(_find_font(), work / "font.ttf")
        for i, o in enumerate(overlays):
            o["file"] = f"text_{i}.txt"
            # write_bytes, not write_text: on Windows write_text turns "\n" into "\r\n", which
            # drawtext renders as an extra blank line between wrapped lines.
            (work / o["file"]).write_bytes(
                "\n".join(textwrap.wrap(o["text"], TEXT_WRAP_CHARS)).encode("utf-8")
            )
        pip_filter_arg: dict[str, Any] | None = None
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for p in clip_paths:
            cmd += ["-i", str(p)]
        if pip_insert:
            cmd += ["-i", str(pip_insert["asset_path"])]
            pip_filter_arg = {
                "input_index": len(clip_paths),
                "start": pip_insert["window"][0],
                "end": pip_insert["window"][1],
            }
        cmd += ["-filter_complex", _build_filter(
            durations, [p["has_audio"] for p in clip_probes], overlays, pad_sec, pip=pip_filter_arg,
        )]
        cmd += ["-map", "[vfinal]", "-map", "[afinal]"]
        if total > MAX_TOTAL_SEC or pad_sec > 0:
            cmd += ["-t", f"{target:.3f}"]
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(final_path),
        ]
        result = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssemblyError(f"ffmpeg failed for {trend_id}:\n{result.stderr[-2000:]}")

    final = probe(final_path)
    problems = []
    if final["width"] * 16 != final["height"] * 9:
        problems.append(f"not 9:16 ({final['width']}x{final['height']})")
    if not MIN_TOTAL_SEC - 0.1 <= final["duration_sec"] <= MAX_TOTAL_SEC + 0.1:
        problems.append(f"duration {final['duration_sec']}s outside {MIN_TOTAL_SEC}-{MAX_TOTAL_SEC}s")
    if not final["has_audio"]:
        problems.append("no audio stream")
    if problems:
        raise AssemblyError(f"final video for {trend_id} failed checks: {'; '.join(problems)}")

    trend_meta_path = trend_dir / "trend_meta.json"
    trend_name = None
    if trend_meta_path.exists():
        trend_name = json.loads(trend_meta_path.read_text(encoding="utf-8")).get("trend_name")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "style": style,
        "source": {
            "trend_id": trend_id,
            "trend_name": trend_name,
            "video_id": selected["video_id"],
            "video_path": selected["video_path"],
            "weighted_score": selected["weighted_score"],
            "selection_rank": selected["rank"],
            "selection_file": "artifacts/generate_selection.json",
            "analysis_path": selected["analysis_path"],
            "decision_path": selected["decision_path"],
        },
        "persona": script.get("persona"),
        "narrative_mapping": script.get("narrative_mapping"),
        "ad_script_path": rel(script_path),
        "clips": [
            {
                "order": m["order"],
                "path": m["clip_path"],
                "veo_model": m["veo_model"],
                "prompt": m["prompt"],
                "negative_prompt": m.get("negative_prompt"),
                "dialogue": m.get("dialogue"),
                "onscreen_text": next((s.get("onscreen_text") for s in shots if s["order"] == m["order"]), None),
                "requested_duration_sec": m["duration_sec"],
                "seed": m.get("seed"),
                "actual": {k: cp[k] for k in ("duration_sec", "width", "height", "has_audio")},
                "veo_operation_name": m["operation_name"],
                "veo_latency_sec": m["latency_sec"],
                "est_cost_usd": m["est_cost_usd"],
                "generated_at": m["generated_at"],
            }
            for m, cp in zip(sorted(clip_metas, key=lambda m: m["order"]), clip_probes)
        ],
        "overlays": [{k: o[k] for k in ("shot_order", "text", "start", "end")} for o in overlays],
        "pip_insert": (
            {
                "asset_path": rel(pip_insert["asset_path"]),
                "asset_source": "Brand/product card used as a picture-in-picture overlay -- never "
                "rendered by Veo, composited here in post. The shipped default is a neutral generated "
                "placeholder; see config/ugc_pip_insert_provenance.json.",
                "presentation": "Fixed-position bordered picture-in-picture card, top-left, pure "
                "post-production overlay -- not tracked to any in-scene object.",
                "window_sec_global": list(pip_insert["window"]),
            }
            if pip_insert else None
        ),
        "final_video": {"path": rel(final_path), **final},
        "est_total_cost_usd": round(sum(m["est_cost_usd"] for m in clip_metas), 4),
        "cost_note": "Estimated from Google's published per-second Veo pricing (720p, audio included); "
        "the API returns no cost data. Verify against billing.",
    }
    (trend_dir / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return manifest
