# Trend Pre-filter Prompt — Trend-to-Creative Pipeline

This is the actual prompt sent to a text-only Gemini call before any video is downloaded.
Loaded verbatim by `agents/discover/prefilter.py` (never duplicated as an inline Python
string). Runtime substitutions are upper-case double-brace tokens: the brand name and context
come from `config/brand_profile.json`, and the raw input list (all four sources, undeduped) is
inserted in section 5.

## 1. Context: the brand these trends are being screened for ({{BRAND_NAME}})

{{BRAND_CONTEXT}}

A trend is a *good candidate* here if its narrative shape could plausibly be re-shot around
one or more of these beats with a different subject — not if it currently mentions the brand's
product. Most source trends will have nothing to do with the brand; that's expected and fine.

## 2. Why this pre-filter exists

You are being given the **raw, undeduped** list of trend entries scraped from four editorial
TikTok-trend blogs this week (socialpilot, ramdam, medianug, napoleoncat). The same real
trend is very often described independently by more than one source, worded completely
differently each time — plain text-similarity or hashtag/sound matching has already been
tried and shown to be unreliable for both deduping and finding matching videos (some sources'
hashtags don't reflect what real creators use; keyword search returns near-random results).
Your job replaces both of those mechanisms:

1. **Group** entries that describe the same real-world trend across sources, using your own
   judgment of the description text (not exact-match logic).
2. **Judge** each resulting group: is this a generic, transferable narrative pattern worth
   adapting, or is it persona/event/celebrity-locked, a pure dance/audio-sync challenge with
   no narrative arc, or otherwise clearly not adaptable? Accept or reject, with a one-sentence
   reason either way — a rejection is just as valuable an output as an acceptance, since it
   documents *why* a trend didn't make the cut before any video/analysis budget was spent on
   it (a second, independent rejection point exists later too, after a real video is
   downloaded and watched — this pre-filter is the earlier, cheaper one, over descriptions
   only).
3. For each **accepted** group, select the best `example_video_urls` across its merged
   sources (dedup identical URLs — don't invent a URL that isn't in the input) as the
   canonical videos to actually fetch, and carry forward `sound_name` if any contributing
   entry had one (so a supplementary sound-based search can pull a few more real examples).

## 3. Worked examples

*The following are illustrative only — you will always be given real scraped entries, not
this text.*

### Example A — accepted (generic, transferable format)

Input entries (from two different sources, worded differently, same real trend):

```json
[
  {"trend_name": "Reasons to Get a Bob", "editorial_description": "A deadpan on-screen list where every reason is just the word bob, set to a viral sound.", "hashtags": ["freakedout", "bobgirl"], "sound_name": "FREAKED OUT - Fat Papi & prodshushy", "example_video_urls": ["https://www.tiktok.com/@example_creator/video/1000000000000000001"], "source": "socialpilot", "source_url": "https://www.socialpilot.co/blog/tiktok-trends"},
  {"trend_name": "The Bob Haircut Deadpan List", "editorial_description": "Creators list increasingly absurd reasons for a haircut, deadpan delivery, same trending audio.", "hashtags": ["bobgirl", "haircare"], "sound_name": null, "example_video_urls": [], "source": "ramdam", "source_url": "https://www.ramd.am/blog/trends-tiktok"}
]
```

Expected output for this group:

```json
{
  "trend_id_hint": "reasons-to-get-a-bob",
  "canonical_trend_name": "Reasons to Get a Bob",
  "merged_from_sources": ["socialpilot", "ramdam"],
  "status": "accepted",
  "reasoning": "A deadpan numbered-list-of-reasons format with a fixed punchline word -- no persona or subject lock-in, trivially re-skinned around any product or premise.",
  "example_video_urls": ["https://www.tiktok.com/@example_creator/video/1000000000000000001"],
  "sound_name": "FREAKED OUT - Fat Papi & prodshushy"
}
```

### Example B — rejected (pure dance/audio-sync challenge)

Input entry:

```json
{"trend_name": "Alibi Dance Challenge", "editorial_description": "A repeatable, competitive dance to the hook in a specific song, where creators try to out-perform each other.", "hashtags": ["alibi"], "sound_name": null, "example_video_urls": ["https://www.tiktok.com/@example_creator/video/1000000000000000002"], "source": "socialpilot", "source_url": "https://www.socialpilot.co/blog/tiktok-trends"}
```

Expected output for this group:

```json
{
  "trend_id_hint": "alibi-dance-challenge",
  "canonical_trend_name": "Alibi Dance Challenge",
  "merged_from_sources": ["socialpilot"],
  "status": "rejected",
  "reasoning": "A pure choreographed dance-to-a-song challenge with no narrative arc, hook, or resolution to adapt -- the entire format is the dance execution itself, not a transferable structure.",
  "example_video_urls": ["https://www.tiktok.com/@example_creator/video/1000000000000000002"],
  "sound_name": null
}
```

### Example C — rejected (celebrity/news-locked)

Input entry:

```json
{"trend_name": "Emmy Red Carpet Reactions", "editorial_description": "Creators react to a specific celebrity's Emmy Awards red carpet look from this week, naming the celebrity and event.", "hashtags": ["emmys"], "sound_name": null, "example_video_urls": [], "source": "socialpilot", "source_url": "https://www.socialpilot.co/blog/tiktok-trends"}
```

Expected output for this group:

```json
{
  "trend_id_hint": "emmy-red-carpet-reactions",
  "canonical_trend_name": "Emmy Red Carpet Reactions",
  "merged_from_sources": ["socialpilot"],
  "status": "rejected",
  "reasoning": "Depends entirely on a specific real celebrity and a specific dated news/cultural event -- no reusable structure survives once the event is no longer current, and it carries real-person licensing risk.",
  "example_video_urls": [],
  "sound_name": null
}
```

## 4. Required output

Respond with **only** valid JSON matching this schema — no prose before or after it. Every
input entry must end up represented in exactly one group (don't drop entries silently); a
group can have exactly one member if nothing else matches it.

```json
{
  "groups": [
    {
      "trend_id_hint": "short-slug-ish-name",
      "canonical_trend_name": "...",
      "merged_from_sources": ["socialpilot", "medianug"],
      "status": "accepted | rejected",
      "reasoning": "one sentence, either way",
      "example_video_urls": ["..."],
      "sound_name": "... | null"
    }
  ]
}
```

## 5. Input: this week's raw trend entries (all four sources, undeduped)

```json
{{RAW_TRENDS_JSON}}
```
