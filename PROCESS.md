# Process stage — design rationale

> **Note on this document.** It started as dated working notes written while Process was built for an earlier target brand.
> **Current behaviour:** the brand context in the scoring prompt is rendered at run time from `config/brand_profile.json`
> (fictional demo brand: Kahve Maya), every new `analysis.json` / `decision.json` is stamped `scored_for_brand`, and an
> analysis stamped for a different brand counts as stale and is re-scored (`analyzer.analysis_is_current`). The scoring-stability
> experiments below were run on that earlier brand's data (their raw results are in `artifacts/`): the mechanism and findings are
> brand-independent, the absolute scores and accept counts are not. Measured cost of one Process pass: ~$0.12 for 16 videos.

## Model choice: Gemini, video-native

**Model: `gemini-3.8-flash`, video sent directly via the File API — not extracted frames.**
Confirmed against the current Gemini API docs before writing any code, not assumed from
memory: the API has moved to the **Interactions API** (`client.interactions.create`), not
the older `generateContent` call — the docs' own "Legacy" pages still describe
`generateContent`, but every current example uses `interactions.create`. Confirmed the
Python SDK's actual installed method signatures match the docs (`client.files.upload`,
`client.files.get`, `client.interactions.create` with `model=`/`input=`/`response_format=`)
by introspecting the installed `google-genai==2.24.0` package directly, not just trusting
the docs page.

**Why video-native instead of frame extraction:** the whole point of `narrative_skeleton`
is capturing *timing* — when the hook lands, how pacing builds, where the reveal sits in
the clip. A sampled-frames approach loses exactly that: you can describe *what* is in a
video from frames, but not *when* things happen or how audio (spoken dialogue, music
stings, silence) lines up with the visual beats. Gemini's native video understanding
processes the actual timeline (plus audio) rather than a handful of decontextualized
stills, which is what `hook_timing_sec` and `pacing` actually depend on.

**Size/format limits, confirmed against our real data before assuming compatibility:**
Gemini's File API supports MP4 (among other formats) up to 2GB (free tier) with 10+ minute
videos; inline data is capped at 100MB. Our downloaded videos range ~1-14MB and 14-330
seconds — comfortably within every limit, no conversion or trimming needed.

**Known risk, flagged rather than silently assumed safe:** Gemini's structured-output
schema support for `$ref`/`$defs` (which Pydantic's `model_json_schema()` generates for any
nested model — our `VideoAnalysis` -> `Scores` -> `ScoreEntry` nesting triggers this) is
documented as supported, but with caveats about complex/nested schemas sometimes being
rejected on other Gemini API surfaces. Untested against a real key as of this writing — see
"Results from first small-batch run" below for whether it worked as-is or needed a manually
flattened schema.

## Trend context in the prompt (2026-09-18 addition) — active; known anchoring limitation

> **Status (2026-09-20):** this context block is the active behavior — the stored analyses were scored with
> it. It was later found to anchor scores upward by about 0.4 on average (and, being LLM-written per run, to vary
> between runs). It was tested away, found to leave only 2-3 of 16 videos above the threshold, and deliberately
> kept; see "Scoring stability" below and the README's "An anchoring bias in the scoring
> prompt". The no-context alternative is `prompts/video_analysis_prompt.v2_no_context.md`.

Discover's per-trend `trend_meta.json` now carries real context that didn't exist when this
prompt was first written: a canonical `trend_name` and a one-sentence `reasoning` (the LLM
text-pre-filter's own accept rationale, formed from the source blog's description, before
any video was even selected — see DISCOVER.md). Both are now injected into
`video_analysis_prompt.md` via `{{TREND_NAME}}`/`{{TREND_CONTEXT_REASONING}}`
(`analyzer.load_trend_context` reads them from `trend_meta.json`; `analyze_video` never
fabricates a value, substituting `(not available)` if either is missing).

**Real finding, not what was assumed going in:** the change that requested this expected
`editorial_descriptions` (multiple per-source description texts) and a `business_safe`
hint to also be available on `trend_meta.json` — checked a real, current, promoted
`trend_meta.json` before writing any code, and **neither field exists anymore**. Both were
part of the pre-LLM-prefilter Discover design (see DISCOVER.md's "Editorial descriptions:
now LLM pre-filter input, not a persisted merged-record field") and were retired when
Discover was redesigned; the prefilter's own output schema doesn't carry them forward
either. `trend_name` + `reasoning` are the real, current substitute — the closest actual
data to "what a human/system said this format is about" that exists today. No field was
invented to fill the gap.

**Framed explicitly as reference, not authority**, in the prompt's new section 2: the model
is told plainly that this note came from a text-only pass that never watched the video, and
to describe/score what it actually observes even where that contradicts the note — the same
principle already applied to Discover's `business_safe` hint (a signal to compare against,
never a shortcut).

## The rubric

Defined in two places, deliberately separate and both inspectable:

- **`prompts/video_analysis_prompt.md`** — the actual prompt text (a real file, not a
  Python string), including the brand context block (rendered at run time from `config/brand_profile.json`), the content-then-structure
  instruction, two full worked examples (one accepted-style, one rejected-style), and the
  required output JSON shape.
- **`config/scoring_weights.json`** — the five criteria weights and `accept_threshold`,
  separate from the prompt so the weighting can be tuned without touching (or re-testing)
  the prompt itself.

**The five criteria**, all scored 1-5 by Gemini per video:

| Criterion | Weight | What it measures |
|---|---|---|
| `narrative_transferability` | 0.30 | Could this structure be re-shot with a different subject and still work? |
| `brand_fit` | 0.25 | Does the structure map onto one of the brand profile's narrative beats (for the demo brand: rush-to-calm, the slow ritual, the small luxury)? |
| `hook_strength` | 0.20 | How compelling is the opening at stopping a scroll? |
| `production_feasibility` | 0.15 | How cheaply/simply could this format be reproduced? |
| `licensing_safety` | 0.10 | How free is it of real people, branded content, or news-event specificity? |

`narrative_transferability` and `brand_fit` carry the most weight (0.55 combined) because
they're the two criteria this pipeline exists to answer — is the *format* reusable, and
does it fit the *brand*. `licensing_safety` carries the least (0.10) deliberately: it's a
real constraint, but this pipeline is scanning for creative direction, not doing legal
clearance — a low-licensing-safety video can still usefully inform Process/Generate's
thinking even if that exact clip couldn't be used as-is.

**Monotonic-direction convention (load-bearing, don't invert):** all five scores are
oriented so **5 is always the most favorable outcome for our use case**, 1 the least. This
is why the field is `licensing_safety` (5 = no risk) and not `licensing_risk` (which would
invert the direction and silently flip the weighted-sum math for that one field only — the
kind of bug that's easy to introduce and easy to miss in review). `weighted_score` is a
plain `sum(value * weight)` across all five for exactly this reason: uniform direction
means no per-field sign-flipping logic anywhere in `compute_decision`.

**`weighted_score` is computed in Python, from `analysis.json` + the weights config —
never trusted from the model.** Gemini extracts and scores each criterion; this repo's own
code does the arithmetic and the accept/reject comparison against `accept_threshold`
(3.5). If the weights ever change, every already-analyzed video can be re-scored for free
by re-running `compute_decision` against existing `analysis.json` files — no API calls
needed, since the raw per-criterion scores are already on disk.

## Artifact layout (a deliberate deviation from one literal instruction)

`analysis.json` and `decision.json` are written **next to each video** in
`artifacts/{trend_id}/raw/{video_id}.analysis.json` /
`artifacts/{trend_id}/raw/{video_id}.decision.json` — not as a single
`artifacts/{trend_id}/analysis.json`. Flagging this explicitly: the task described the
latter path, but Discover's real output has many videos per `trend_id` folder (e.g.
`hashtag_satisfying/` holds 19), so a single trend-level `analysis.json` can't hold
per-video scores without inventing a different nested shape. The per-video, "next to the
source file" layout is also what the same task's own idempotency check describes
("check for an existing `analysis.json` next to it"), which only makes sense at the
per-video level — so that's the interpretation implemented, matching the existing
`{video_id}.mp4` / `{video_id}.json` (Discover metadata) naming convention already used in
`raw/`.

## Idempotency (cost control)

`analyzer.process_video` skips any video whose `{video_id}.analysis.json` already exists,
unless `--force` is passed. Gemini calls cost money and take real time (upload + processing
+ generation); re-running `run_process.py` to pick up newly-downloaded videos should never
silently re-pay for videos already analyzed. This mirrors the same reasoning already applied
throughout Discover (`seen_ids.json` dedup, `used_fallback` tracking) — cost-awareness and
idempotency are running themes in this pipeline, not one-off decisions.

## Small-scale default

`--limit` defaults to **5** — originally chosen to validate prompt quality and rubric
calibration cheaply before spending on the full batch, back when Discover's dataset was
much larger (82, then 43 videos across two earlier designs). Discover's real, final dataset
is a few dozen videos at most (see DISCOVER.md), so a full run just passes `--limit 20` or more.
`run.py` does this automatically; the default of 5 stays for ad-hoc `run_process.py` use.

## Retry/failure handling

`analyze_video` retries the full upload+analyze sequence up to 3 times with exponential
backoff (5s, 10s) on any exception — covers transient API errors, rate limits, and File API
processing failures uniformly, since all three can happen mid-batch and none should be
fatal to the other videos in the batch. `run_process.py` catches a final failure per video
after retries are exhausted, logs it, and continues with the rest — the same
"one item's failure doesn't abort the batch" pattern used throughout Discover.

## Scoring stability (2026-09-20) — measured, cause found, defaults kept, context removal tested and not adopted

> **Resolution:** sampling controls did not help (defaults kept); the dominant cause is an anchoring bias from the
> pre-filter context in the scoring prompt; removing it leaves 2-3 of 16 videos above the 3.5 threshold, and
> re-fitting a threshold on this 16-video sample would be arbitrary, so **the with-context prompt was restored as
> the active one for the original run** and the limitation disclosed. Full reasoning, numbers, and the production
> fix: README, "An anchoring bias in the scoring prompt". Details of the experiments follow.

**Finding.** A from-scratch `run.py` run re-analyzed 14 videos also present in the original
dataset: scores moved by 0.43 on average (max 1.20) and **5 of 14 accept/reject verdicts flipped**
(e.g. "Dramatic Transition" 4.65 -> 3.45; the borderline dog video 3.45 -> 4.35), landing at 8
accepted instead of 11.

**Experiment** (270 analyses of the same 14 videos, ~$1.9 by token count; the full per-run scores
are in `artifacts/score_stability_experiment.json`; Gemini 3.8 Flash; verdict = weighted score >= 3.5).
`analyze_video` sets no sampling parameters, so it runs at the server defaults; the SDK exposes
`temperature`, `seed`, `top_k`, `thinking_config` (all now overridable via one dict,
`analyzer.GENERATION_PARAMS`). Three repeats of each arm, prompt context held fixed:

| Sampling arm | Score change between repeats | Verdict flips between repeats (of 14) | Per-video sd |
|---|---|---|---|
| default | 0.19 | 1.3 | 0.12 |
| `temperature=0` | 0.20 | 2.7 | 0.13 |
| `seed=42` | 0.13 | 1.3 | 0.09 |
| `temperature=0` + `seed=42` | 0.11 | 2.0 | 0.08 |

**No arm is reliably more stable.** The paired bootstrap 95% intervals on the change in per-video sd
vs. default all include zero (`temperature=0`: +0.012 [-0.031, +0.054]; `seed`: -0.033 [-0.095, +0.037];
both: -0.041 [-0.104, +0.030]); temperature 0 was nominally *worse*, and none of the arms is
deterministic (the same video and context scored 3.50 at default and 4.05 at `temperature=0`).
Google's Gemini 3 guidance also advises leaving temperature at its default of 1.0, warning that
lowering it "may lead to unexpected behavior, such as looping or degraded performance". **Degradation
check:** in 6 videos' full outputs under default, `temperature=0` and `temperature=0`+`seed`, there was no
repetition (max repeated 3-gram 1), no empty/duplicate/junk text, the same reasoning length (~110 chars),
and unchanged output/thinking token counts, and no schema violation in any of the 270 analyses — so
these settings are harmless, just not helpful. **Decision: defaults kept (`GENERATION_PARAMS` is empty).**

**The dominant cause is the prompt context, not sampling.** Each video's prompt includes the text
stage's one-sentence rationale for its trend ("reference only, never authority"). A from-scratch run
regenerates that text, and it differed for all 14 videos. Holding sampling at default and changing only
that text (3 more repeats each):

- per-video mean scores moved by **0.40 on average (max 1.13)**, versus a sampling sd of ~0.12
  ("Dramatic Transition" 4.57 -> 3.53; the dog video 3.22 -> 4.35);
- the from-scratch run's own scores match runs given *its* context (mean gap 0.15) far better than runs
  given the original run's context (0.39) — and the original scores show the reverse (0.15 vs 0.45);
- an arm with the context *values* blanked ("(not available)") shifted scores by 0.27 and accepted ~7 of 14
  videos (vs ~10 with the original run's context, ~9 with the from-scratch context). **That arm understated the
  effect:** the prompt section still told the model that an earlier reviewer had already judged the format
  "worth adapting". Removing the whole section (below) lowered scores much further.

So the instruction not to defer to the context does not stop it anchoring the score, and the
pre-filter's own run-to-run variation leaks into Process. Also, **6 of the 14 videos sit within 0.35 of the
3.5 threshold**, so small shifts flip verdicts by construction.

**What was and wasn't changed.** The original `analysis.json` / `decision.json` files were not
re-run and are unchanged (re-running could alter which trends reached Generate). Not adopted, pending a
decision: dropping the context from the scoring prompt would remove the leak but lowers acceptance and so
means re-calibrating the threshold and weights, not just deleting the block. The other route — score
with multiple samples and take the median, flagging a borderline band for review — is in the README's
"What I'd change".

### Follow-up: context block removed entirely ("v2") — tested, not adopted; the with-context prompt was restored

What was tried (2026-09-20): the pre-filter's trend name and rationale (the only per-trend fields the scoring
prompt used) were removed, along with the section that introduced them, and the analyzer's context handling was
temporarily deleted. Neither field is stable across pipeline runs — `reasoning` differed for 14 of 14 videos across two
independent runs and `trend_name` for 9 of 14 (e.g. "Past Me Getting a Recap From Future Me" became "2025 me getting
a recap from 2026 me"); only `sound_name`, `example_video_urls` and `merged_from_sources` were identical, and the
prompt uses none of those, so no per-trend context remained. Weights and the 3.5 threshold were unchanged. The real 16
videos were re-scored three times at default sampling (48 analyses, ~$0.38). Run 1 was fixed in advance as the
official result and is written beside the original files as `raw/*.analysis.v2_no_context.json` /
`*.decision.v2_no_context.json`; the original `.analysis.json` / `.decision.json` were never touched. Numbers:
`artifacts/process_v2_no_context_results.json`. **The no-context prompt is kept as
`prompts/video_analysis_prompt.v2_no_context.md`; the analyzer's context handling has been restored** (and verified:
for all 16 real videos the prompt the restored analyzer sends equals the original with each trend's real name and
rationale filled in, with no sampling parameters).

| | Original (with context) | No-context, official (run 1) | run 2 | run 3 |
|---|---|---|---|---|
| Accepted / rejected (of 16) | **11 / 5** | **3 / 13** | 2 / 14 | 2 / 14 |

- **Consistency is much better without the context:** across its own three runs it differs by 0.12 on average and
  flips at most 1 of 16 verdicts. The upstream leak is gone.
- **But scores fall systematically.** Same day, same model and sampling, 14 shared videos: mean 3.64 with the context
  vs 3.26 without (+0.38); 13 of 14 lower; every no-context run below every with-context run (all 16 videos: +0.23,
  since the three low-scoring concert clips rise slightly). At a 3.5 threshold it accepts 2-3 videos, so a healthy
  10-20 pool is reached only under the anchored prompt: **the stored 11 accepts partly reflect that anchoring.**
  9 of 16 no-context mean scores sit within 0.35 of the threshold. (An earlier "context values blanked" arm, which
  kept the sentence that an earlier reviewer had judged the format worth adapting, still scored 3.47 with 8 of 14
  accepted — it understated the effect because the framing itself anchors.)
- **Generate top-3:** ranked by score (one per trend), the same three trends lead under the original scoring and in
  no-context runs 1 and 2 (run 1: deep-breaths-honey 4.55, past-me-recap 3.90, dramatic-transition 3.55; original
  4.80 / 4.65 / 4.25); in run 3 third place is a 3.45 tie with another trend; the top two hold in every run.
  dramatic-transition scored 3.55 / 3.45 / 3.45, so under a 3.5 threshold two of the three runs accept only two
  videos and `run.py` would stop before Generate.
- **Decision (2026-09-20): with-context prompt restored for the original run.** Removing the bias without re-fitting
  the threshold breaks a 10-20 accepted-video target; re-fitting on the same 16-video sample that exposed the problem (for
  information only, a threshold near 3.18 would accept 10, but the scores cluster tightly there) would be arbitrary
  and fragile; and multi-sample medians address only the smaller sampling-noise part (~0.12). The correct fix — drop the
  context AND re-calibrate weights and threshold on a much larger, held-out, outcome-labelled sample — is beyond a
  small prototype, and is described in the README.
