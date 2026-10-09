#!/usr/bin/env python3
"""Cross-check the numbers quoted in README.md against the data files (free, offline).

    python scripts/check_readme_numbers.py        # exits 1 if any check fails
"""
from __future__ import annotations

import collections
import glob
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
README = (ROOT / "README.md").read_text(encoding="utf-8")
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))


def read_json(path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


week_dir = sorted((ROOT / "data" / "trends").glob("20*-W*"))[-1]
meta = read_json(week_dir / "collection_meta.json")
pf = meta["prefilter"]
eps = meta["entries_per_source"]
check(
    meta["raw_entry_count"] == 162 and eps == {"socialpilot": 7, "ramdam": 2, "medianug": 152, "napoleoncat": 1}
    and "162 (socialpilot 7, ramdam 2, medianug 152, napoleoncat 1)" in README,
    "162 raw entries and per-source split",
)
check(pf["groups_formed"] == 158 and "| Groups the pre-filter formed | 158 " in README, "158 groups")
check((pf["accepted_count"], pf["rejected_count"]) == (54, 104) and "**54 accepted, 104 rejected**" in README, "54 accepted / 104 rejected")
check(pf["allocation_summary"] == {"socialpilot": 0, "ramdam": 2, "medianug": 4, "napoleoncat": 0}, "allocation 0/2/4/0")
check(meta["trend_count"] == 6 and meta["total_unique_videos"] == 18 and meta["total_video_download_failures"] == 0
      and not meta["used_fallback"] and not pf["used_fallback"], "6 trends, 18 videos, 0 failures, no fallback")
check(week_dir.name == "2026-W41" and "2026-W41" in README, "week id")

raws = [p for p in glob.glob(str(ROOT / "artifacts/*/raw/*.json")) if Path(p).name.count(".") == 1]
check(len(raws) == 18 and all("caption" not in read_json(p) for p in raws), "18 per-video metadata files, captions stripped")

decisions = [read_json(p) for p in glob.glob(str(ROOT / "artifacts/*/raw/*.decision.json"))]
status = collections.Counter(d["status"] for d in decisions)
check(status == {"accepted": 10, "rejected": 8} and "**10 accepted, 8 rejected**" in README, "10 accepted / 8 rejected")
check(all(d.get("scored_for_brand") == "Kahve Maya" for d in decisions), "every decision is stamped for Kahve Maya")
top = max(d["weighted_score"] for d in decisions)
check(top == 4.45 and sum(d["weighted_score"] == 4.45 for d in decisions) >= 2, "highest score 4.45, shared")

w = read_json(ROOT / "config/scoring_weights.json")
for crit, weight in (("narrative_transferability", "0.30"), ("brand_fit", "0.25"), ("hook_strength", "0.20"),
                     ("production_feasibility", "0.15"), ("licensing_safety", "0.10")):
    check(f"| `{crit}` | {weight} |" in README and abs(w[crit] - float(weight)) < 1e-9, f"weight {crit} = {weight}")
check(w["accept_threshold"] == 3.5 and "weighted ≥ 3.5" in README, "threshold 3.5")

for vid, expected in (("7686592609673858317", 4.45), ("7621339634533977347", 1.90)):
    dec = read_json(next(glob.iglob(str(ROOT / f"artifacts/*/raw/{vid}.decision.json"))))
    ana = read_json(next(glob.iglob(str(ROOT / f"artifacts/*/raw/{vid}.analysis.json"))))
    recomputed = sum(ana["scores"][c]["value"] * w[c] for c in w if c != "accept_threshold")
    check(abs(dec["weighted_score"] - expected) < 1e-9 and abs(recomputed - expected) < 1e-9 and f"**{expected:.2f}**" in README,
          f"worked example {vid} = {expected:.2f}")

run = read_json(ROOT / "artifacts/pipeline_run_log.json")[-1]
costs = run["costs"]
check(abs(costs["total_usd"] - 1.5832) < 1e-4 and "~$1.58" in README and abs(costs["apify_usd"] - 0.0947) < 1e-4 and "$0.095" in README,
      "run cost: total ~$1.58, Apify $0.095")
secs = {s["name"]: round(s["seconds"]) for s in run["stages"]}
check(sum(secs.values()) == 843 and secs == {"discover": 316, "process": 377, "generate": 150} and "843 s" in README, "wall time 843 s")

manifest = read_json(ROOT / "examples/generated_ad_manifest.json")
check(manifest["est_total_cost_usd"] == 1.2 and manifest["final_video"]["duration_sec"] == 12.0
      and manifest["source"]["video_id"] == "7686592609673858317", "sample ad manifest: $1.20, 12 s, source video")
check(all((ROOT / "examples" / n).exists() for n in ("kahve_maya_sample_ad.mp4", "kahve_maya_sample_ad_poster.jpg", "generated_ad_script.json")),
      "example files exist")

suite = unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
n_tests = suite.countTestCases()
check(f"{n_tests} offline tests" in README, f"README says {n_tests} offline tests")

from agents.generate.veo_client import PRICE_PER_SEC_720P  # noqa: E402

check(PRICE_PER_SEC_720P == {"lite": 0.05, "fast": 0.10, "standard": 0.40}
      and "`lite` $0.05, `fast` $0.10, `standard` $0.40" in README, "Veo price table")

failed = [what for ok, what in results if not ok]
for ok, what in results:
    print(("PASS" if ok else "FAIL"), what)
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
raise SystemExit(1 if failed else 0)
