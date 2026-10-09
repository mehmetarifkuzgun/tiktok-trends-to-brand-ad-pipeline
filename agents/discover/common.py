"""Shared helpers for the discover stage: repo paths, HTTP retry/backoff, week id, dedup state."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Apify takes its token as a `?token=` query parameter, so any logged URL -- or the text of a
# `requests` exception, which embeds the URL -- would otherwise carry the secret. Redaction is
# applied at the known log sites AND as a log-record safety net (below) for any line missed.
_SECRET_RE = re.compile(r"((?:[?&]|)(?:token|api_key|apikey)=)[^&\s\"')]+|(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact_secrets(text: Any) -> str:
    """`?token=abc123` -> `?token=[REDACTED]` (also api_key=... and `Bearer <token>`)."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2)}[REDACTED]", str(text))


def _install_log_redaction() -> None:
    """Wrap the log-record factory so every formatted log message, from any module, is redacted."""
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_redacts_secrets", False):
        return

    def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        original = record.getMessage
        record.getMessage = lambda: redact_secrets(original())  # type: ignore[method-assign]
        return record

    redacting_factory._redacts_secrets = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(redacting_factory)


_install_log_redaction()

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RepoPaths:
    root: Path = field(default_factory=lambda: REPO_ROOT)
    data: Path = field(default_factory=lambda: REPO_ROOT / "data")
    trends: Path = field(default_factory=lambda: REPO_ROOT / "data" / "trends")
    fixtures: Path = field(default_factory=lambda: REPO_ROOT / "data" / "fixtures")
    artifacts: Path = field(default_factory=lambda: REPO_ROOT / "artifacts")


def current_week_id(now: datetime | None = None) -> str:
    """ISO week id, e.g. '2026-W38'. Each discover run writes to data/trends/{week}/."""
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _request_with_backoff(
    method: str,
    url: str,
    *,
    max_attempts: int,
    base_delay: float,
    **kwargs: Any,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"retryable status {response.status_code}", response=response)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.info(
                "%s %s failed (attempt %d/%d): %s -- retrying in %.1fs",
                method, redact_secrets(url), attempt, max_attempts, redact_secrets(exc), delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    # The final exception's message embeds the URL (and so the token); scrub it before it can
    # reach a traceback or a caller's log line.
    last_exc.args = tuple(redact_secrets(a) if isinstance(a, (str, Exception)) else a for a in last_exc.args)
    raise last_exc


def http_get_with_backoff(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 4,
    base_delay: float = 1.5,
    timeout: float = 10.0,
) -> requests.Response:
    return _request_with_backoff(
        "GET", url, params=params, headers=headers, timeout=timeout,
        max_attempts=max_attempts, base_delay=base_delay,
    )


def http_post_with_backoff(
    url: str,
    *,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    timeout: float = 60.0,
) -> requests.Response:
    return _request_with_backoff(
        "POST", url, json=json_payload, headers=headers, timeout=timeout,
        max_attempts=max_attempts, base_delay=base_delay,
    )


def run_apify_actor_sync(
    actor_id: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout: float = 120.0,
    max_attempts: int = 3,
    base_delay: float = 2.0,
) -> list[dict[str, Any]]:
    """Run an Apify actor and return its dataset items, shared by every Apify-backed caller
    in this pipeline (trend_inventory.py, sample_fetcher.py).

    Uses the platform's run-sync-get-dataset-items convenience endpoint, which runs the
    actor, waits for it to finish, and returns the dataset directly -- no separate
    run+poll loop needed since every caller here just wants "run it, hand me the items."
    """
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={api_key}"
    response = http_post_with_backoff(
        url, json_payload=payload, timeout=timeout, max_attempts=max_attempts, base_delay=base_delay,
    )
    items = response.json()
    if not isinstance(items, list):
        raise ValueError(f"Apify actor {actor_id} returned a non-list dataset payload")
    return items


def load_seen_ids(paths: RepoPaths) -> set[str]:
    seen_path = paths.trends / "seen_ids.json"
    if not seen_path.exists():
        return set()
    return set(json.loads(seen_path.read_text(encoding="utf-8")))


def save_seen_ids(paths: RepoPaths, seen_ids: set[str]) -> None:
    seen_path = paths.trends / "seen_ids.json"
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen_ids), indent=2), encoding="utf-8")
