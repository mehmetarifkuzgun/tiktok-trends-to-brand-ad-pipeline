"""Shared Gemini/Veo access helpers for the Generate stage.

Exists so two dev-environment problems found during the 2026-09-18 Veo access test are
solved once instead of rediscovered (the throwaway test scripts lived in a session scratch
dir and are gone; their results are in data/veo_test/veo_test_meta.json and CLAUDE.md):

1. **Avast HTTPS scanning breaks Python TLS.** Avast re-signs traffic with its own root
   cert, which certifi doesn't trust -> `CERTIFICATE_VERIFY_FAILED` on every Google call.
   `configure_ssl_trust()` builds a certifi + Avast-root bundle and points `SSL_CERT_FILE`
   (httpx) and `REQUESTS_CA_BUNDLE` (requests) at it for this process. Verification stays ON;
   it's a no-op on machines without Avast.
2. **`client.files.download` on google-genai 1.46.0 has no `destination=` kwarg** (the docs'
   sample targets a newer SDK). It returns the video's bytes instead; `download_video` writes
   them itself.

Veo facts this module encodes (verified live 2026-09-18, see GENERATE.md): async job via
`client.models.generate_videos` -> poll `client.operations.get`; durations 4/6/8s only;
9:16 native; native audio; generated files are retained server-side for only 2 days.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import certifi
from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

MODEL_TIERS = {
    "lite": "veo-3.1-lite-generate-preview",
    "fast": "veo-3.1-fast-generate-preview",
    "standard": "veo-3.1-generate-preview",
}
# USD per generated second at 720p, audio included -- from Google's pricing page, NOT from
# billing data (the API returns no cost/usage field for video jobs).
PRICE_PER_SEC_720P = {"lite": 0.05, "fast": 0.10, "standard": 0.40}
SUPPORTED_DURATIONS = (4, 6, 8)

_AVAST_ROOT_CERT = Path(r"C:\ProgramData\Avast Software\Avast\wscert.pem")
_TRANSIENT_SUBMIT_CODES = {429, 500, 503}


class VeoGenerationError(RuntimeError):
    """A Veo job failed, timed out, or returned nothing usable. The message always says
    what happened and, when a job was created, its operation name (a timed-out job may
    still finish -- and bill -- server-side, and stays fetchable for 2 days)."""


def configure_ssl_trust() -> str | None:
    """Trust Avast's HTTPS-scanning root in addition to certifi's, for this process only.
    Returns the bundle path if it configured one. Respects an existing SSL_CERT_FILE and
    does nothing when Avast's cert isn't present. Must run before creating a genai.Client."""
    if os.environ.get("SSL_CERT_FILE") or not _AVAST_ROOT_CERT.exists():
        return None
    bundle = Path(tempfile.gettempdir()) / "tiktok_trend_ca_bundle.pem"
    if not bundle.exists() or bundle.stat().st_mtime < _AVAST_ROOT_CERT.stat().st_mtime:
        bundle.write_bytes(
            Path(certifi.where()).read_bytes() + b"\n" + _AVAST_ROOT_CERT.read_bytes()
        )
    os.environ["SSL_CERT_FILE"] = str(bundle)  # httpx (google-genai)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(bundle))  # requests ignores SSL_CERT_FILE
    logger.info("Avast root cert detected; using CA bundle %s for this process", bundle)
    return str(bundle)


def make_client(api_key: str) -> genai.Client:
    configure_ssl_trust()
    return genai.Client(api_key=api_key)


def snap_duration(seconds: float) -> int:
    """Nearest supported Veo duration (4/6/8); ties round up."""
    return min(SUPPORTED_DURATIONS, key=lambda d: (abs(d - seconds), -d))


def estimate_cost_usd(tier: str, seconds: float) -> float:
    return round(PRICE_PER_SEC_720P[tier] * seconds, 4)


def download_video(client: genai.Client, video: types.Video, dest: Path) -> int:
    """Download a generated video to `dest`; returns byte count. Writes via a .part file so
    a failed/partial download never leaves a truncated .mp4 that looks valid (same
    principle as Discover's sample_fetcher)."""
    data = client.files.download(file=video)
    if not data:
        raise VeoGenerationError("Veo returned a video reference but the download was empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(data)
    os.replace(part, dest)
    return len(data)


def _submit(
    client: genai.Client,
    model: str,
    prompt: str,
    config: types.GenerateVideosConfig,
    image: types.Image | None = None,
):
    """Submit the job. Retries only transient submit failures (rate limit / 5xx), where no
    job exists yet so nothing is billed by retrying -- and says so loudly each time."""
    for attempt in range(1, 4):
        try:
            return client.models.generate_videos(
                model=model, prompt=prompt, image=image, config=config,
            )
        except errors.APIError as exc:
            if exc.code in _TRANSIENT_SUBMIT_CODES and attempt < 3:
                delay = 20 * attempt
                logger.warning(
                    "Veo submit hit transient %s (attempt %d/3): %s -- retrying in %ds",
                    exc.code, attempt, exc.message, delay,
                )
                time.sleep(delay)
                continue
            raise VeoGenerationError(
                f"Veo submit failed: HTTP {exc.code} {exc.status}: {exc.message}"
            ) from exc
    raise AssertionError("unreachable")


def generate_clip(
    client: genai.Client,
    *,
    model: str,
    prompt: str,
    duration_sec: int,
    dest: Path,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    image_path: Path | None = None,
    negative_prompt: str | None = None,
    poll_interval: float = 10.0,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Generate one clip end to end (submit -> poll -> download) and return its metadata.
    With `image_path`, that image is the clip's first frame (image-to-video). `negative_prompt`
    uses the SDK's dedicated field (confirmed present in GenerateVideosConfig via introspection,
    previously listed as untested in GENERATE.md) rather than relying on prose alone in `prompt`
    -- added to fight a real hallucinated-on-screen-text failure found on the `standard` tier
    that the prose-only "No text, captions, or logos in frame." sentence didn't prevent.
    One job per call: no automatic re-generation, since every job costs money."""
    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio, duration_seconds=duration_sec, resolution=resolution,
        negative_prompt=negative_prompt,
    )
    image = types.Image.from_file(location=str(image_path)) if image_path else None
    t0 = time.time()
    op = _submit(client, model, prompt, config, image)
    submit_sec = time.time() - t0
    logger.info(
        "Veo job submitted in %.1fs (%s): %s", submit_sec,
        f"image-to-video, first frame {image_path.name}" if image_path else "text-to-video", op.name,
    )

    polls = 0
    poll_errors = 0
    while not op.done:
        if time.time() - t0 > timeout:
            raise VeoGenerationError(
                f"Veo job timed out after {timeout:.0f}s (still running); operation "
                f"{op.name} may still finish and bill server-side"
            )
        time.sleep(poll_interval)
        try:
            op = client.operations.get(op)
            poll_errors = 0
        except Exception as exc:  # noqa: BLE001 - a network blip mustn't orphan a paid job
            poll_errors += 1
            logger.warning("Poll error %d/5 for %s: %s", poll_errors, op.name, exc)
            if poll_errors >= 5:
                raise VeoGenerationError(
                    f"Lost contact polling {op.name} after 5 consecutive errors: {exc}"
                ) from exc
        polls += 1
    latency_sec = time.time() - t0

    if getattr(op, "error", None):
        raise VeoGenerationError(f"Veo job {op.name} failed: {op.error}")
    resp = op.response
    if resp is None or not resp.generated_videos:
        reasons = getattr(resp, "rai_media_filtered_reasons", None) if resp else None
        raise VeoGenerationError(
            f"Veo job {op.name} finished with no video (safety-filtered?); "
            f"rai_media_filtered_reasons={reasons}"
        )

    size = download_video(client, resp.generated_videos[0].video, dest)
    logger.info("Veo clip saved: %s (%d bytes, %.1fs total)", dest, size, latency_sec)
    return {
        "operation_name": op.name,
        "submit_sec": round(submit_sec, 1),
        "latency_sec": round(latency_sec, 1),
        "polls": polls,
        "size_bytes": size,
    }
