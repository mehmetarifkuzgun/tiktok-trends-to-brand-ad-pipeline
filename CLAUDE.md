# CLAUDE.md — architecture log for AI collaborators

Running log of decisions, not a tutorial. Read this before touching the pipeline. (The README is the project description;
DISCOVER.md / PROCESS.md / GENERATE.md hold per-stage design and history.)

## What this is

A Discover -> Process -> Generate pipeline that turns live TikTok trends into short-form ad concepts for **one brand at a time**.
The brand is data, not code: [`config/brand_profile.json`](config/brand_profile.json) (schema in `config/brand_profile.schema.md`) is read through
`agents/brand.py` by every prompt and agent. The bundled brand is **Kahve Maya, a fictional specialty cafe** invented for the demo.
Swap the profile (and optionally `config/scoring_weights.json`) to re-target the pipeline.

## Standing rules

- **Brand text never lives in code or in a prompt file.** Prompts use tokens (`{{BRAND_NAME}}`, `{{BRAND_CONTEXT}}`, ...) that `agents/brand.py` fills. Tests render every prompt and fail on leftover tokens.
- **Analyses are stamped with the brand they were scored for** (`scored_for_brand`). `analyzer.analysis_is_current` treats a missing or different stamp as stale; Process re-scores stale analyses, `run.py` counts them as not done, `selector.py` never selects them.
- **The persisted data is from one live run (week 2026-W41) scored for Kahve Maya.** `artifacts/score_stability_experiment.json` and `process_v2_no_context_results.json` come from an earlier run scored for a different, original brand and say so.
- **Monotonic scoring: 5 is always the favourable end** for all five criteria, including `licensing_safety`. `weighted_score` is a plain sum computed in Python; never trust a model-computed score. Read PROCESS.md's rubric section before changing what "accepted" means.
- **Do not run paid stages casually.** Discover spends Apify credit, Process and Discover's pre-filter spend Gemini credit, Generate spends Veo credit ($1.20 per 12 s ad on `fast`, $4.80 on `standard`). Prefer `--dry-run`, `--script-only`, and the offline tests (`python -m unittest discover -s tests -t .`).
- **Validators must agree with their prompts.** Every prompt's worked example is unit-tested against the validator that gates real output.
- **Idempotent, cost-aware, never silent.** Stages skip work already on disk; fallbacks to `data/fixtures/` are logged and recorded as `used_fallback: true`.
- **No secrets in the repo.** Keys live in `.env` (git-ignored). `agents/discover/common.py` redacts `?token=` / `api_key=` / `Bearer` from logs and exceptions.
- **Third-party content is referenced, not redistributed.** Raw TikTok videos are git-ignored; stored per-video metadata keeps id, public stats and hashtags (captions stripped).

## Pipeline shape

Three stages chained through plain files on disk, so any stage's output can be inspected or replayed independently:

```
Discover -> Process -> Generate
```

- **Discover** (`agents/discover/`): four editorial TikTok-trend blogs (weekly: socialpilot, ramdam, medianug; monthly: napoleoncat) -> one text-only LLM pre-filter over the raw, undeduped list (semantic grouping, accept/reject with a reason, canonical video URLs) -> fair round-robin allocation across sources -> canonical videos fetched by exact URL via `clockworks/tiktok-scraper`'s `postURLs` (+ a bounded sound-based supplement) -> download with an explicit `download_status`.
- **Process** (`agents/process/`): Gemini watches each video (video-native, File API), returns a schema-validated analysis (narrative skeleton, hook, five 1-5 scores); the weighted accept/reject decision is computed in Python from `config/scoring_weights.json` (threshold 3.5).
- **Generate** (`agents/generate/`): top-N accepted videos (one per trend) -> script -> Veo clips -> ffmpeg assembly. Invented persona + spoken dialogue, brand/product card composited as a picture-in-picture overlay.
- **`run.py`**: single-command orchestrator; calls the three stage entrypoints in-process, decides from disk state whether each stage is complete, gates between stages (exit 3 when too few usable trends or accepted trends), reports progress and cost.

## Discover design

- **Source adapters** (`agents/discover/sources/*.py`): `fetch_trends() -> list[RawTrendEntry]`, raise on failure (the call site decides how to degrade). Capture `trend_name`, `editorial_description` (reference text, fed to the pre-filter only), `hashtags`, `sound_name`, `example_video_urls` (the TikTok URLs the source embeds, via the oEmbed widget's `cite` attribute; shared parsing in `sources/_tiktok_embed.py`).
- **`merge.py`**: calls all four independently; one failing source never blocks the others; fixture fallback if all four fail.
- **`prefilter.py`**: one `gemini-3.8-flash` text call, prompt in `prompts/trend_prefilter_prompt.md` (brand context from the profile). `data/trends/{week}/prefilter_result.json` holds every group, accepted or rejected, with reasons.
- **`trend_inventory.allocate_accepted_groups`**: round-robin over each group's first-listed source; `collection_meta.json` records `prefilter.allocation_summary` every run.
- **`sample_fetcher.py`**: downloads each video with `download_status: "ok" | "failed"` and deletes partial files on failure.
- **`search_anchor.py`**: only `is_sound_searchable` survives (is a sound a real title or a generic "original sound - user" label).
- **`trend_id` = `trend_<slug>_<hash>`**, stable across re-runs; every artifact is keyed by it (`artifacts/{trend_id}/raw/`, `trend_meta.json`), so Process and Generate can work on one folder without knowing the week.

## Process design

Per-video `analysis.json` / `decision.json` live next to the video in `raw/`. The scoring prompt (`prompts/video_analysis_prompt.md`) takes the brand context from the profile and, as "reference only", the pre-filter's trend name and rationale. **Known limitation, measured:** that LLM-written context anchors scores up by about 0.4 on average; removing it (`prompts/video_analysis_prompt.v2_no_context.md`) leaves 2-3 of 16 videos above the threshold instead of 11, and re-fitting the threshold on 16 samples would be arbitrary. Production fix: drop the context and re-calibrate on a large labelled sample. `analyzer.GENERATION_PARAMS` is deliberately empty (temperature 0 / seed experiments did not help).

## Generate design

See GENERATE.md. Key points: no phone/screen/device in any shot (validator-enforced); itemised no-text sentence + `negative_prompt`; shot 1 is 4 s (6 s only if the source hook lands late), shot 2 exactly 8 s, total 12-14 s; shot 2 is seeded on shot 1's last frame; fast tier to validate, standard tier once for the final; assembly is idempotent (`assembler.already_assembled`).

## What's next

1. Render the top trends on `standard` (the sample was one `fast` take) and compare.
2. Calibrate weights/threshold on a larger labelled sample; frozen-response tests for the scrapers and the pre-filter; a scheduler for weekly runs.

## Decision log

Dated, condensed, neutral. Early entries concern the original target brand (a mobile game); that brand is called "the original brand".

- **2026-09-17**: Replaced direct scraping of TikTok Creative Center (client-rendered page, endpoint 404) with an Apify trending-hashtags actor; then dropped hashtag-driven discovery (topic/news-driven, not format-driven) and the trending-videos actor (only 4 videos for US/7-day regardless of limits). Three actors hit the same anonymous-access ceiling. Switched to a fixed 7-tag seed list on `clockworks/tiktok-scraper` with our own recency/engagement filtering, and found `hashtags` input has no recency control (all-time "Top" tab), so ~80% of results failed a 30-day filter; raised the default to 365 days. Real bug found on the way: non-Latin hashtags slugified to an empty `trend_id` and collided. Result: 82 videos.
- **2026-09-17**: Built Process: Gemini video-native analysis + 5-criterion weighted rubric + deterministic accept/reject in Python. Per-video (not per-trend) analysis files because a trend folder holds many videos.
- **2026-09-18**: Free text-only triage of captions on disk found two real patterns in the seed-tag data (near-empty captions for one tag, templated bot-like captions for another).
- **2026-09-18**: Replaced the seed-tag design with four editorial blog adapters. Real HTML inspected before writing any selector. Findings: one source exposes no per-trend business-safe flag (left `null`, not fabricated); most sounds are generic "original sound - user" labels. A dedup bug merged ~30 unrelated trends because they shared generic hashtags (`#fyp`, `#viral`); fixed by excluding generic hashtags from the matching signal. Round-robin allocation and a per-source editorial-description field added.
- **2026-09-18**: Fixed a truncated-download trap: one mid-stream failure left a 114 MB `.mp4` that looked valid. Every video now has an explicit `download_status`; failed downloads delete partial files; counts are written once after downloads finish.
- **2026-09-18**: Rebuilt Discover dedup and fetch: LLM pre-filter + canonical video URLs instead of hashtag/keyword search anchors, because a diagnostic showed hashtag anchors matched their own videos 18% of the time. Confirmed live that `postURLs` works and combines with search in one call. Found and fixed a real bug by live testing: `_item_origin` fell back to `webVideoUrl`, which every item has, so all sound-supplement results were silently misclassified as canonical. Also found that `client.interactions.create` does not exist in the installed SDK (Process fix below).
- **2026-09-18**: Found a fairness regression in the redesign: `accepted[:max_trends]` ignored source balance; of 72 accepted groups, 61 came from one source, yet the slice returned only the two smallest sources. New `allocate_accepted_groups` round-robin gives 3/3/3/1. The earlier doc claim that the pre-filter "already does fairness's job" was wrong and was corrected, not left standing.
- **2026-09-18**: Promoted the LLM-prefilter dataset to the persisted real dataset (10 trends, 16 videos, 0 failures) after finding the naive-sliced videos on disk did not match the fair set (fetched the missing 4 for ~$0.02). Also found stale `trend_meta.json` counts (a re-fetch overwrites rather than accumulates) and recomputed them from each trend's real `download_status` values.
- **2026-09-18**: Fixed Process's blocking SDK bug (`client.interactions.create` -> `client.models.generate_content` with a Pydantic response schema; nested schema validated cleanly). Ran Process on all 16 videos: 11 accepted, 5 rejected (scored for the original brand). Added trend context to the prompt as reference, never authority.
- **2026-09-18**: Veo go/no-go: works on the existing Gemini key. Gotchas recorded: `files.download` has no `destination=` kwarg on 1.46.0; a TLS-intercepting antivirus breaks Python TLS (fixed per-process with a CA bundle, verification stays on). `requirements.txt` pins `google-genai==1.46.0` exactly (a `>=` floor caused the SDK mismatch).
- **2026-09-18 to 2026-09-19**: Built and iterated Generate. First run (text-to-video) showed Veo draws text/UI despite "no text", and shots differed between independently generated clips. Image-to-video seeding fixed continuity but the environment drifted until the script was written *from* the seed (seed-first ordering became the standing design). Tone revision removed a corporate voice that came from the prompt's own worked example.
- **2026-09-20**: `run.py` became the real single-command orchestrator (resume from disk state, gates, cost reporting). A from-scratch run exited 0 in 851 s for about $4.04. Discover's retry logs printed the Apify token in the URL; `redact_secrets` added and tested.
- **2026-09-20**: Score instability investigated: 270 analyses showed temperature 0, fixed seeds and both do not reliably help (and Google advises default temperature for Gemini 3). The dominant cause is the LLM-written trend context in the scoring prompt: it moves per-video means by ~0.4 and inflates them (+0.38 on 14 shared videos). Removing it leaves 2-3 of 16 above the threshold. Decision: keep the context, document the bias, do not re-fit a threshold on 16 samples.
- **2026-09-22/23**: Explored and then adopted a photorealistic UGC style (invented person, spoken dialogue, overlay card composited in post). Fixes found by frame-by-frame QA: no phone/prop in any shot (a duplicate-device bug), itemised no-text sentence + `negative_prompt`, trimming a corrupted clip tail, plain clothing descriptions after "graphic hoodie" produced pseudo-lettering. Fast-then-standard tier strategy. Standing pipeline built (`ugc_scriptwriter.py`, assembler overlay, `--style`), with three self-inflicted prompt/validator bugs found only by running against the live API (see GENERATE.md). Assembly made idempotent to close a corruption risk.
- **2026-10-09 (re-theme: fictional brand, brand-agnostic pipeline)**: Prepared the project for public release. Added `config/brand_profile.json` + `agents/brand.py` as the single source of truth and refactored all prompts and agents to read it (the Process/Generate modules used to read brand context out of the analysis prompt itself). Renamed the gameplay-screenshot overlay to a brand/product card (neutral generated placeholder). Added brand stamps and staleness handling. Removed all generated ads, official third-party art, raw-video captions and blog text from the publishable tree. Added offline tests (29) including prompt-vs-validator checks (which immediately caught that the UGC prompt's own worked example failed its validator's persona-overlap rule). 
- **2026-10-09 (first end-to-end run for the fictional brand, then trimming)**: Deleted the old dataset and ran `run.py --top-n 1 --max-trends 6 --model-tier fast` from scratch (week 2026-W41): 162 raw entries -> 158 groups (54 accepted) -> 6 trends / 18 videos -> 10 accepted, 8 rejected -> one 12 s ad for "I Did Nothing..." (4.45), 843 s, about $1.58. No code change was needed to make it run. Then removed the mascot-specific reference-image/"cartoon" style entirely (scriptwriter, reference picker, three prompts, library config, flags, tests), the two caption/anchor inspection utilities (they served retired designs), and cut the superseded-design sections from DISCOVER.md/PROCESS.md/GENERATE.md. The sample ad, its script and manifest live in `examples/`.
