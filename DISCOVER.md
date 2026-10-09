# Discover stage — design rationale

> **Note on this document.** It started as dated working notes written while Discover was built for an earlier
> target brand. Everything about *how Discover works* and *why* is brand-independent; the pre-filter prompt reads its
> brand context from `config/brand_profile.json` (fictional demo brand: Kahve Maya). Sections about designs that were
> later replaced have been removed; what remains is the current design and the decisions that shaped it.

This file is long because Discover went through six real redesigns before landing here (see
"Design history" below the line) — every one of them driven by a concrete finding, not a
guess. If you just need to understand what's *running today*, read this section only; come
back to the history section when you need the reasoning behind a specific choice or want to
know why an earlier approach was rejected.

## Current architecture (as of 2026-09-18)

**Pipeline:** four editorial TikTok-trend-tracking blogs → LLM text pre-filter →
fair per-source allocation → canonical video URL fetch (+ bounded sound supplement) →
download with explicit per-video status.

1. **Scrape** (`agents/discover/sources/{socialpilot,ramdam,medianug,napoleoncat}.py`):
   one adapter per source (weekly: socialpilot, ramdam, medianug; monthly: napoleoncat).
   Each raises independently on failure — one broken source never blocks the other three
   (`merge.collect_all_raw_entries`). Each entry captures `trend_name`,
   `editorial_description`, `hashtags`, `sound_name`, and `example_video_urls` (the real
   TikTok URLs the source itself embeds as its example).
2. **Pre-filter** (`agents/discover/prefilter.py`): one text-only Gemini call
   (`gemini-3.8-flash`) over the full raw, undeduped list from all four sources —
   before any video is downloaded. Per `prompts/trend_prefilter_prompt.md`, it (a)
   semantically groups entries describing the same real trend across sources, (b) accepts
   or rejects each group with a one-sentence reason, and (c) picks canonical
   `example_video_urls` + carries forward `sound_name` for each accepted group. Rejections
   here are a real, valuable output — the audit trail for "why didn't this trend make it,"
   available before any video/analysis spend (`data/trends/{week}/prefilter_result.json`
   holds every group, accepted and rejected, with reasoning).
3. **Fair allocation** (`trend_inventory.allocate_accepted_groups`): caps the accepted list
   to `--max-trends` via round-robin across each group's first-listed `merged_from_sources`
   entry, not a plain slice — a plain slice was tried and shipped a real bug (see history).
   `collection_meta.json`'s `prefilter.allocation_summary` records the real per-source split
   every run.
4. **Fetch** (`trend_inventory.py` + `sample_fetcher.py`): each allocated group's canonical
   video(s) are fetched directly by URL via `clockworks/tiktok-scraper`'s `postURLs` input —
   precise by construction, since it's the exact video the source blog linked to. If
   `sound_name` is present and not a generic `"original sound - ..."` label, a bounded
   (`--sound-supplement-cap`, default 3) sound-based search adds a few more real examples.
   Every downloaded video gets an explicit `download_status: "ok" | "failed"` — never left
   to be inferred from file presence.

**Two-tier rejection, by design:** a trend can be rejected at the cheap text stage
(topic/format grounds — persona-locked, pure dance/audio-sync challenge, no narrative arc)
or later at Process stage on video-execution grounds (pacing, hook strength, production
feasibility) that only watching the clip can reveal. Neither replaces the other — and this
is now confirmed in practice, not just in design: Process (see PROCESS.md/CLAUDE.md) has
run on all 16 of these videos (scored for the original brand), accepting 11 and rejecting 5, including a real example of
the two tiers catching different things (a trend the text stage accepted whose
sound-supplement videos still got correctly rejected at the video stage — see PROCESS.md).

**Current real numbers (sample run, `2026-W41`, `--max-trends 6`):** 162 raw entries (socialpilot 7, ramdam 2, medianug 152, napoleoncat 1) -> 158 groups -> 54 accepted /
104 rejected at the text stage (each with a reason, in `prefilter_result.json`) -> 6 trends, `allocation_summary: {"socialpilot": 0, "ramdam": 2, "medianug": 4, "napoleoncat": 0}`
(the round-robin only balances across sources that have accepted groups) -> 18 videos, 0 download failures, 316 s, Apify $0.095 measured.

Re-running `run_discover.py` pulls a new ISO week into `data/trends/{week}/` (past weeks are never overwritten) and spends Apify and Gemini credit, so do it deliberately.

---

## Design history: decisions made, rejected, and why

Everything below this line is historical — preserved in full because it's real evidence of
what was tried, what broke, and why, not because a new reader needs to wade through it to
understand what's live today (see "Current architecture" above for that).

## The full arc (honest history, not just the current state)

Three different approaches were tried against TikTok Creative Center's "trending" surfaces,
across three different Apify actors, and all three hit variations of the same wall:

1. **Hashtag trending** (`memo23/tiktok-trending-hashtags-scraper`): worked, but surfaced
   mostly news/social-event hashtags (`#september11`, `#setembroamarelo`) — topic-driven,
   not format-driven, not useful as ad-hook candidates. Also capped at ~3 hashtags per
   country per query, a hard limit on the anonymous-access view of that chart.
2. **Top ads** (`top-ads-scraper`, considered, never implemented): rejected outright —
   returns pre-made ad units, not original content. The opposite of what trend-spotting
   for new hooks needs.
3. **Trending videos** (`rastriq/tiktok-trending-videos-scraper`): better signal (the video
   itself, not a topic label) but hit the *same* anonymous-access ceiling as hashtags — 4
   videos for US/7-day regardless of whether 10 or 50 were requested, confirmed directly by
   testing both.

**The pattern across all three: anonymous access to TikTok Creative Center's own
"trending" ranking surfaces returns a fixed, small N per query — the full ranked list is
gated behind a login wall.** This is not a bug in any of the three integrations (each
actor's fields were verified against real schemas and real responses before use) and not
something fixable by requesting more, picking a different actor, or adding retries. It's a
platform-level constraint on anonymous/API access. Documenting this rather than trying a
fourth trending-surface actor and hoping for a different result.

## Multi-source trend-blog discovery (adapters current; dedup/fetch redesigned 2026-09-18)

**Why:** the seed-tag design above worked but the tag list was static and hand-guessed —
it couldn't tell you what's *actually* trending this week, only that these seven durable
formats are usually safe bets. Separately, a fifth Apify actor (`memo23`'s hashtag
trending, `top-ads-scraper`, `rastriq`'s trending videos — see "the full arc" above) was
never the answer: three actors in a row hit the same anonymous-access ceiling on TikTok's
own trending surfaces. The insight that broke the pattern: **editorial TikTok-trend-
tracking blogs already do this analysis publicly**, naming specific current formats with
their real sound/hashtag, updated weekly by humans who watch the platform professionally —
no TikTok API/anonymous-access ceiling involved at all. One such source
(napoleoncat.com, monthly, 2-3 entries) was tried first and proved too low-volume to carry
Discover alone. This design combines **four** such sources instead, three weekly, to get
real volume plus resilience: no single blog going down, changing its template, or having a
quiet week can stall the whole stage.

### Adapter pattern

`agents/discover/sources/` holds one module per source: `socialpilot.py`, `ramdam.py`,
`medianug.py`, `napoleoncat.py`. Each exposes one function, `fetch_trends() -> list[
RawTrendEntry]`, and owns its site's parsing exclusively — selectors are not shared or
generalized across sites because **the sites don't share structure**, confirmed by fetching
all four live pages before writing any selector (per this project's standing "confirm
before assuming" rule):

| Source | Cadence | Real markup found | hashtags | sound_name | popularity_hint | business_safe |
|---|---|---|---|---|---|---|
| socialpilot | weekly | WordPress, `<h2>Trending TikTok Formats...</h2>` → numbered `<h3>` per trend, hashtags from an explicit `Hashtag: #a / #b` line | yes | **no** (see below) | no | **no** (see below) |
| ramdam | weekly | Webflow, bare (unclassed) `<h4>The "..." trend</h4>` per trend, hashtags/sound from an embedded TikTok oEmbed widget | yes | yes | no | no |
| medianug | weekly, monthly archive back to 2024 | Webflow, `<h3 class="trend-title">` + a `<div class="videos-tag w-embed">N videos</div>` count, hashtags/sound from the same oEmbed shape as ramdam | yes | yes | **yes** | no |
| napoleoncat | monthly | WordPress, `<h2>Current TikTok Trends: <Month> <Year></h2>` per month, `<h3 class="wp-block-heading">` per trend, hashtags/sound from the same oEmbed shape | yes | yes | no | no |

Two real findings from that live inspection, documented rather than silently worked around:

- **socialpilot does NOT expose a per-trend business-safe flag**, despite that being this
  source's whole reason for being considered. "Approved for Business Use"
  appears only as generic advice text about filtering *sounds* in TikTok's own Creative
  Center — never a boolean tied to a specific Format entry on the actual live page. Rather
  than fabricate a value the source doesn't provide, `business_safe` is left `null` for
  every entry from every source right now (see "business_safe is a hint, not a filter"
  below for why this is safe to leave null rather than guess). If a future revision of the
  page adds a real per-trend flag, `socialpilot.py` is where to wire it in.
- **socialpilot's Format-section embeds carry no caption/hashtag/sound text** (just a bare
  creator-name link) and the page's separate "Trending Sounds" section isn't reliably tied
  back to a specific Format trend — so `sound_name` is also left `null` for socialpilot,
  not guessed from proximity between the two sections.

`ramdam.py`, `medianug.py`, and `napoleoncat.py` share one piece of real infra:
`sources/_tiktok_embed.py`, which parses the identical TikTok oEmbed
`<blockquote class="tiktok-embed">` widget all three sites embed as their one example video
per trend — confirmed byte-for-byte identical in shape across all three real pages. This is
genuinely shared parsing (how to read an oEmbed widget once located), not site structure
(how to find *which* widget belongs to *which* trend heading), so it lives outside any one
adapter rather than being duplicated three times.

**Each adapter raises on any real fetch/parse failure** (network error, missing expected
markup) rather than deciding how to degrade — that decision belongs to the one call site
in `merge.py`, so one broken source's failure mode can never accidentally become "this
source is silently allowed to return partial garbage." An adapter that parses successfully
but finds zero trend headings also raises (treated as a soft failure at the call site,
logged and recorded in `sources_failed` rather than silently proceeding as if the source
had nothing to say this week on purpose — see "Fallback-first, never silent" in
`CLAUDE.md`).

## Why hashtag/keyword search was dropped entirely (2026-09-18)

`agents/discover/inspect_anchors.py` (a read-only diagnostic over the persisted real
dataset, kept as a utility, not part of the pipeline) produced
`artifacts/anchor_inspection.md` — the concrete evidence this redesign is based on, not a
guess:

- **Only 6 of 33 videos (18%) pulled by a hashtag anchor actually carried that hashtag
  themselves.** Three of seven hashtag-anchored trends got **zero** matching videos
  (`myamericangirldoll` 0/5, `freakartdecotransition` 0/4, `whatilookedlikewhen` 0/5) —
  `clockworks`'s `hashtags` input returns TikTok's hashtag-page "Top" tab, which is loosely/
  algorithmically curated by TikTok itself, not a strict "videos carrying this exact tag"
  filter.
- **Keyword search was worse, not just weaker.** The one keyword-anchored trend inspected in
  full ("You're staying until the end" → `"you're staying until end"`) returned a
  flower-arranging video, a Zach Bryan concert clip, and a Toto "Hold the Line" lyric video —
  no plausible relationship to each other or the named trend or to one another.
- **Even where the anchor hashtag *was* present, some pulled videos were still off-topic**
  (a Rainbow Six gaming clip that happened to say "Almost ACED with Alibi", pulled in by the
  `alibi` hashtag anchor for "Alibi Dance Challenge").
- Investigating a suspected "fabricated hashtag" pattern flagged separately: confirmed no
  extraction bug in any adapter (no code derives a hashtag from `trend_name`), but did
  confirm a real *source-side* pattern — two of socialpilot's six current hashtag
  suggestions (`myamericangirldoll`, `whatilookedlikewhen`) are, character for character,
  just the trend's own title smooshed together, with no evidence real creators use them —
  see `sources/socialpilot.py`'s docstring for the full note.

Sound-based search was the one method that worked reliably in the same diagnostic (5/5 hit
rate on "Deep breaths, honey") and is the only search method kept — as an optional
*supplement*, not the primary anchor (see below).

## LLM text pre-filter (`agents/discover/prefilter.py`)

Replaces two things at once: the sound/hashtag-overlap union-find dedup above (itself a
source of real bugs — the generic-hashtag over-merge and the San Diego-restaurant
borderline-merge documented above), and hashtag/keyword search anchors (shown unreliable
just above). One cheap **text-only** Gemini call (`gemini-3.8-flash`, the model Process
stage already uses) over the **full raw, undeduped** entry list from all four sources —
before any video is downloaded or analyzed, so a clearly-bad trend never reaches
Apify/Process spend at all.

The model does three things in one call, per `prompts/trend_prefilter_prompt.md` (loaded
verbatim, never duplicated as an inline string, same discipline as
`prompts/video_analysis_prompt.md`):

1. **Semantically groups** entries describing the same real-world trend across sources,
   using its own judgment of the description text — not exact-match logic. This handles
   cases the old union-find couldn't: the live test merged socialpilot's "Reasons to Get a
   Bob" with ramdam's differently-worded, differently-hashtagged description of the same
   trend, purely from reading both descriptions.
2. **Judges each group**: accepted or rejected, with a one-sentence reason either way, using
   the same conceptual criteria Process stage's rubric already uses downstream (generic
   transferable narrative vs. persona/event/celebrity-locked or a pure dance/audio-sync
   challenge with no arc). A rejection here is a full, valid deliverable in its own right —
   confirmed live: the same test rejected "Alibi Dance Challenge" (pure choreography, no
   narrative) and, independently on real current data, "Deep Breaths, Honey" (as the blog
   described it, a lyric-bound photo carousel, judged in that test run to have no transferable
   narrative) — a real, independent model judgment, not something the few-shot examples told it
   to do. *(That was one test run's verdict: the run that produced the promoted dataset
   accepted the same trend, and its top-scoring video turned out to be a hair before/after
   reveal. Pre-filter and video-stage judgments both vary between runs — see the stability
   findings in PROCESS.md / README.)*
3. **Selects canonical `example_video_urls`** for each accepted group from its merged
   sources' real embedded links (deduped, never invented) and carries forward `sound_name`
   if present, for the bounded supplement fetch below.

**Two-tier rejection design, both legitimate and intentionally different in kind:** this
stage rejects on topic/format grounds from a description alone, before spending anything on
video; Process stage (unchanged, untouched by this task) still rejects on video-execution
grounds (pacing, hook strength, production feasibility) that can only be judged by actually
watching a downloaded clip. Neither replaces the other — see `prefilter.py`'s docstring.

`data/trends/{week}/prefilter_result.json` holds the model's full response, accepted *and*
rejected groups with their reasoning — this is the audit trail for "why didn't trend X make
it," now available at the cheap text stage instead of only after a video was downloaded and
analyzed.

**Fallback-first, same pattern as everywhere else:** no Gemini key, an empty raw-entry list,
or a failed live call (retried a few times first) falls back to
`data/fixtures/prefilter_result.json`, a hand-written fixture in the same schema.

## Canonical video URL + bounded sound-supplement fetching (`trend_inventory.py`)

**Step 0, confirmed live before building anything on top of it (not assumed from memory):**
`clockworks/tiktok-scraper`'s real input schema (fetched fresh from the Apify API, not
recalled) has a `postURLs` field — "Direct URLs for scraping specific tiktoks." A live test
call with a real TikTok video URL returned exactly that video's data, with `id` matching the
URL's video id and `submittedVideoUrl` echoing the exact URL back (`input`, the field the
old hashtag-anchor design used for this, is empty for postURLs-origin items). Also confirmed
live: `postURLs` and `searchQueries` combine in one call exactly like `hashtags` and
`searchQueries` did before (a 1-postURL + 1-searchQuery test call with `resultsPerPage: 3`
returned 1+3=4 items — `resultsPerPage` doesn't affect `postURLs`, which always returns
exactly one item per URL).

**Corrected during the same task, not left as originally written (an earlier version of
this paragraph also mentioned matching on `webVideoUrl` as a fallback alongside
`submittedVideoUrl`):** that fallback was a real bug, found by full live end-to-end testing
(not caught by this smaller Step-0 call) — `webVideoUrl` turns out to be populated on
*every* returned item regardless of origin (every TikTok video has its own page URL,
whether found via `postURLs` or `searchQueries`), so relying on it silently misclassified
real sound-supplement results as canonical (their own self-referential URL never matches a
requested canonical URL, so they were dropped instead of counted as supplements). Fixed by
matching on `submittedVideoUrl` alone — confirmed null on every `searchQueries`-origin item,
populated only on `postURLs`-origin ones, so it's the one reliable signal. See "Live
confirmation run: LLM prefilter + canonical URL fetch" further down for the full incident
and re-verification.

For each accepted (and `--max-trends`-capped) group:

- **Canonical videos**: every URL in `example_video_urls` is fetched directly via
  `postURLs` — precise by construction, since it's the exact video the source blog itself
  linked to as its example, not a search result that might match on a coincidental hashtag.
- **Sound supplement, bounded**: if `sound_name` is present and not a generic
  `"original sound - ..."` label (`search_anchor.py`'s one remaining function,
  `is_sound_searchable` — same detection logic as before, trimmed down to just this), a
  `searchQueries` search for that sound pulls up to `--sound-supplement-cap` (default 3)
  additional videos — kept small and explicitly bounded so a present sound can't
  reintroduce the old search-driven volume/cost problem.
- **A blog-linked video can 404 or be removed since the post was written** — expected, not a
  crash. `trend_inventory.py` compares requested `postURLs` against which ones actually came
  back in the dataset and logs (`canonical_urls_missing` in `collection_meta.json`) any that
  didn't, rather than silently under-counting.
- **Recency filtering (`--max-age-days`) applies only to sound-supplement videos**, never to
  canonical ones — a canonical video is wanted specifically because it's the blog's actual
  linked example, regardless of age; only the supplementary padding videos need the same
  no-native-recency-control reasoning as every prior design.
- **Cross-fetch dedup**: canonical videos are matched first, so a video that's both a
  group's own canonical example and happens to also surface in its sound-supplement search
  keeps its canonical home (confirmed live in a fixture test with a deliberately duplicated
  video id across both fetch paths).

`estimate_cost_usd(num_canonical_urls, num_groups_with_sound, sound_supplement_cap) = 0.001
+ (num_canonical_urls + num_groups_with_sound × sound_supplement_cap) × (0.0037 + 0.0013)` —
same per-unit pricing carried over unchanged (result/video-download pricing is keyed by
event type, not by which input field produced the item).

### Volume controls (updated 2026-09-18 for the LLM-prefilter design)

- `--max-trends` now caps the **accepted** group list, applied *after* the LLM pre-filter's
  accept/reject pass rather than before any judgment is made — the pre-filter itself is a
  single cheap text call over the entire raw list regardless of volume (medianug's 165 raw
  entries cost the same one call as a quiet week), so there's no reason to truncate before
  it runs the way the old design had to truncate before spending Apify search budget.
  Capping is a simple slice of the accepted list in this design (see "Fair round-robin
  allocation" below for why the more elaborate per-source allocation logic didn't carry
  forward).
- `--sound-supplement-cap` (default 3) replaces `--videos-per-trend` — see "Canonical video
  URL + bounded sound-supplement fetching" above.
- `--max-age-days` (default 365) now applies **only** to sound-supplement videos, not
  canonical URL-fetched ones (see above) — same underlying reasoning (no native recency
  control depended on) but narrower scope than before.
- A worst-case cost *ceiling* prints before any run (accurate cost isn't knowable until
  after the LLM's accept/reject outcome, unlike the old design where trend count was fixed
  pre-spend); the real, lower actual cost is logged after the video pull completes.

### `trend_id` and artifact structure

Unchanged shape (`artifacts/{trend_id}/raw/{video_id}.mp4` + `.json` + `trend_meta.json`),
different `trend_id` construction: `trend_<slugified-trend-name>_<6-hex-hash>`. The hash
suffix is computed from the normalized `trend_name` alone (not source or run data), so the
same real trend gets the same `trend_id` on a re-run — this matters because `seen_ids.json`
dedup and artifact-folder identity both depend on `trend_id` being stable across weeks, the
same property the old `hashtag_<tag>` ids had for free by construction. The suffix exists
specifically to avoid collisions between similarly-named trends surfaced by different
sources (e.g. two unrelated trends that both slugify toward "transition").

### `download_status`: explicit per-video success/failure (2026-09-18 update)

**Real bug found, not hypothetical:** a per-video metadata JSON for a failed download used
to be byte-for-byte identical in shape to a successful one — no field anywhere recorded that
anything had gone wrong. Worse, a download that failed *mid-stream* (confirmed with a real
timeout during the round-robin/editorial-description live test) left whatever bytes had
already arrived sitting at the `.mp4` path: a 114MB file with a plausible-looking header but
almost certainly missing its trailing data, indistinguishable from a valid video by presence
or size alone. Since Process stage's whole job is to walk `artifacts/*/raw/*.mp4` next, this
was a real landmine, not a cosmetic gap.

Two-part fix, both in `sample_fetcher.py`:

- Every per-video metadata JSON now carries `download_status: "ok" | "failed"`, set at the
  point of download (not inferred later from file presence, which is exactly the ambiguity
  this fixes). Metadata is still written either way — matching the existing design (every
  processed candidate was already being added to `seen_ids.json` and getting a metadata
  record regardless of outcome, consistent with this project's broader "never silent"
  fallback philosophy, see CLAUDE.md) — the explicit field is what changed, not whether a
  record exists at all.
- `_download_video` (and `_copy_fixture_video`, defensively) now deletes any partial file at
  its destination before re-raising on any exception, so a failed download never leaves a
  truncated `.mp4` behind. File presence and `download_status` are now two independent,
  agreeing signals rather than one implicit and unreliable one.

`trend_meta.json` gained a matching `videos_failed` count (alongside the existing
`videos_fetched`), and `collection_meta.json` gained `total_video_download_failures` — the
sum of every trend's `videos_failed` this run, written once, after all downloads complete
(not written early and patched later, which would risk the two drifting apart).

Confirmed live: a simulated mid-stream failure produced `download_status: "failed"` with no
`.mp4` on disk, while a normal success produced `download_status: "ok"` with a valid file —
a plain `artifacts/*/raw/*.json` scan for `download_status: "failed"` correctly identified
the failed one and nothing else. 

## What I'd change with more budget/time

- **Two-phase pull to cut the video-download cost inefficiency.** The download add-on is
  charged for every raw result, including ones later dropped by cross-anchor dedup or the
  recency filter. A metadata-only pull followed by a download-only pass for just the videos
  actually kept would cut this — not done, same reasoning as before (a single bounded call
  keeps the design simple).
- **Fuzzy near-duplicate detection.** Only exact `video_id` equality is deduped today,
  both at the video level and (new in this design) implicitly relied on for cross-source
  trend dedup being exact-hashtag/exact-sound-normalized rather than fuzzy.
- **A regression test pinned against a frozen real response**, for both the clockworks
  actor shape and all four adapters' HTML parsing — field/selector names are confirmed
  against real output at write time, but nothing pins that in an automated test that would
  catch a future schema change or site redesign. This risk is now 5x what it was (one
  actor schema + four independent site templates instead of one actor schema).
- **medianug's monthly archive isn't fetched.** Its front page alone already covers many
  months of history in one request, so this wasn't needed for volume — but if a future need
  arose for a specific past month's trends, the archive links are there and unused.
- **napoleoncat's older months aren't fetched**, only the current month — deliberate, to
  keep it a genuinely low-volume/low-priority contributor rather than accidentally making a
  monthly source the largest one by pulling its entire history.
- **Revisit `growth_signal` once a real signal exists.** Still always `"unknown"` — no
  time-series/lifetime-vs-period data exists for editorially-sourced trends any more than
  it did for the hand-picked seed list.
- **A per-trend real `business_safe` signal.** socialpilot was expected to provide one and
  doesn't (see above); if a future source reliably does, `business_safe` stops being
  uniformly `null` and starts being a genuinely useful hint for Process.

**Added 2026-09-18 (LLM-prefilter redesign):**

- **Editorial per-source descriptions aren't reconstructed at the group level anymore.**
  The earlier design's `editorial_descriptions: [{source, text}, ...]` on a merged trend is
  gone (see "Editorial descriptions" above) — the LLM's own group `reasoning` replaces it as
  the persisted rationale, but the original per-source verbatim text isn't kept past the
  pre-filter call. Having the LLM echo back which raw entries it merged (not just which
  sources) would let this be reconstructed precisely instead of left out.
- **The pre-filter's grouping/judgment isn't pinned by an automated test against a frozen
  response**, same gap as the adapters' HTML parsing above, now doubled: a prompt or model
  change could silently shift which trends get accepted/rejected with nothing to catch it.
- ~~No `--max-trends` selection signal beyond "cap the model's own list order."~~ **This
  was a real bug, not just a design gap — found and fixed, see "Fair round-robin allocation"
  above.** Within a source's bucket, groups still keep the LLM's own relative order (no
  ranking signal exists at the text stage) — that narrower version of the gap remains: if
  the model's output order within one source turns out to correlate with anything (or a
  real ranking signal becomes available), the per-bucket ordering could improve.
- **A single combined LLM call over the whole raw list scales less gracefully than the
  old per-video-search cost model as source volume grows** (medianug alone can be 165+
  entries) — still one call regardless of size, so cost is flat, but a large enough raw
  list could eventually hit a context-length or output-token ceiling this hasn't been tested
  against.
- **`fetch_and_save_seed_tag` overwrites `trend_meta.json` instead of accumulating across
  calls.** Found during dataset promotion: a trend processed more than once across separate
  script invocations ends up with `videos_fetched`/`videos_failed` reflecting only the last
  call, not its `raw/` folder's real cumulative state — harmless for a normal single-pass
  `run_discover.py` run (the only call site in production), but a real trap for any
  future ad-hoc re-processing (testing, backfills, partial re-runs). Recomputing those two
  fields from the real `raw/*.json` files at read time (or write time) instead of trusting
  the field would close this permanently.
