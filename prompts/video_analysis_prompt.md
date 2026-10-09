# Video Analysis Prompt — Trend-to-Creative Pipeline

This is the actual prompt sent to Gemini alongside each uploaded video. Loaded verbatim by
`agents/process/analyzer.py` (never duplicated as an inline Python string). Runtime substitutions
are upper-case double-brace tokens: the brand name and the brand context block come from
`config/brand_profile.json` (via `agents/brand.py`), and the video id and the trend's recorded
context come from the video's own folder. A missing trend value becomes `(not available)`, never a
fabricated one.

## 1. Context: the brand this analysis is for ({{BRAND_NAME}})

{{BRAND_CONTEXT}}

Score `brand_fit` on how naturally this video's *structure* could be re-skinned around one or
more of the beats above — not on whether it currently mentions the brand's product at all. Most
source videos will have nothing to do with the brand; that's expected.

## 2. Context: what a text-stage reviewer said about this format (reference only)

Before this video reached you, an earlier text-only pass (reading the source blog's own
description of this trend, never watching a video) already judged this format's *name* and
*general shape* worth adapting, and recorded why:

- **Trend name:** {{TREND_NAME}}
- **Text-stage reasoning:** {{TREND_CONTEXT_REASONING}}

**Treat this as reference context only, not as something to defer to or take as given.**
That earlier judgment never watched this specific video — it was formed from a short
editorial description, before any real clip was even selected. Form your own independent
judgment from what you actually observe in the video across every field below, including
all five scores. If what you see contradicts the text-stage reasoning (a different pacing,
a persona-lock the description didn't mention, a licensing concern the summary didn't
flag), score and describe what you actually observe — do not average toward, soften
toward, or otherwise let the earlier note pull your answer toward it. If either value above
reads as `(not available)`, that means no context exists for this video — proceed on the
video alone.

## 3. Instruction

Watch the entire video (audio included) before answering. Respond in two passes:

1. **Content first.** Describe literally what happens on screen and in audio: who or what
   is shown, what they do, what is said or displayed as on-screen text. This becomes
   `content_summary`.
2. **Then strip it to structure.** Describe the same video again with every subject-specific,
   persona-specific, and topic-specific detail removed, leaving only the transferable
   narrative shape: what kind of hook opens it, when the hook or reveal actually lands (in
   seconds — this requires watching, not skimming a transcript), how pacing builds across
   the clip, and what mechanism resolves the tension. This becomes `narrative_skeleton`.
   Two videos about completely different subjects can have the *same* `narrative_skeleton`
   if they're built the same way — that's the signal this pipeline is trying to extract.

Then score the video against all five rubric criteria in the output schema. **Every score
is 1-5, and 5 is always the most favorable outcome for adapting this into a {{BRAND_NAME}} ad,
1 is always the least favorable — this direction is uniform across all five, including
`licensing_safety` (5 = no licensing concern, 1 = high licensing risk). Do not invert this
for any criterion.**

- `narrative_transferability`: how easily the narrative_skeleton could be re-shot with a
  completely different subject and still work.
- `hook_strength`: how compelling the opening hook is at making someone stop scrolling.
- `production_feasibility`: how cheaply/simply this format could be reproduced (single
  location, no complex effects, short shoot) vs. how demanding it would be.
- `brand_fit`: how naturally the structure maps onto the brand beats above.
- `licensing_safety`: {{LICENSING_GUIDANCE}}

## 4. Worked examples

### Example A — accepted-style (generic, transferable format)

*Video description (for illustration only — you will always be given a real video, not
this text):* A creator's hands clear a visibly cluttered desk. On-screen text reads "wait for
it" at 0:02. A quick cut at 0:07 reveals the same desk now tidy, with a single steaming mug
placed in the middle, timed to a soft percussive sting, ending on a close-up of the mug. No
face, no name, no spoken dialogue, no brand mentioned.

Expected output for this kind of video:

```json
{
  "video_id": "example_accepted",
  "content_summary": "Hands clear a cluttered desk; text reads 'wait for it'; a quick cut reveals the desk tidy with a steaming mug in the middle, timed to a soft sting.",
  "narrative_skeleton": "Messy state shown -> explicit 'wait for it' delay cue -> hard cut to a calm, resolved state on a beat -> hold on the single resolved detail.",
  "hook_type": "reveal",
  "hook_timing_sec": 7.0,
  "pacing": "fast",
  "has_spoken_dialogue": false,
  "has_onscreen_text": true,
  "requires_specific_persona": false,
  "emotional_tone": "quiet relief",
  "scores": {
    "narrative_transferability": {"value": 5, "reasoning": "No face, name, or subject-specific detail; the mess->calm reveal works with any object or setting."},
    "hook_strength": {"value": 4, "reasoning": "Explicit 'wait for it' text plus a fast cut is a proven stop-scroll pattern, though the setup is generic enough to not be maximally novel."},
    "production_feasibility": {"value": 5, "reasoning": "One location, no actors, no dialogue, a single hard cut -- trivially reproducible."},
    "brand_fit": {"value": 5, "reasoning": "A near-literal match for the rush-to-calm beat: a cluttered state resolved into stillness by one deliberate action, ending on a cup."},
    "licensing_safety": {"value": 5, "reasoning": "No identifiable person, no branded content, no news-event tie-in."}
  }
}
```

### Example B — rejected-style (persona-locked / event-specific)

*Video description (for illustration only):* A named creator sits facing camera, discussing
a specific real celebrity's remarks from a televised awards show two days prior, referencing
the celebrity and event by name repeatedly, reacting to an embedded clip of the actual
broadcast footage.

Expected output for this kind of video:

```json
{
  "video_id": "example_rejected",
  "content_summary": "A named creator reacts on-camera to a specific celebrity's remarks at a recent televised awards show, discussing the celebrity and event by name and showing broadcast clip footage.",
  "narrative_skeleton": "Direct-to-camera commentary reacting to an embedded real-world news clip, structured around audience familiarity with a specific recent event.",
  "hook_type": "other",
  "hook_timing_sec": 1.5,
  "pacing": "medium",
  "has_spoken_dialogue": true,
  "has_onscreen_text": false,
  "requires_specific_persona": true,
  "emotional_tone": "topical commentary",
  "scores": {
    "narrative_transferability": {"value": 1, "reasoning": "The entire video depends on a specific real event and a specific real celebrity; there is no reusable structure independent of that topicality."},
    "hook_strength": {"value": 2, "reasoning": "Relies on the viewer already caring about this specific news moment rather than a hook that works on its own."},
    "production_feasibility": {"value": 2, "reasoning": "Requires licensed or fair-use broadcast footage and topical relevance that expires within days."},
    "brand_fit": {"value": 1, "reasoning": "Nothing about direct-to-camera news commentary maps onto rush-to-calm, the slow ritual, or the small luxury; the brand also avoids news events."},
    "licensing_safety": {"value": 1, "reasoning": "Uses a real, identifiable public figure and broadcast footage of a real recent event -- high licensing/rights risk to adapt."}
  }
}
```

## 5. Required output

Respond with **only** valid JSON matching the schema below — no prose before or after it.

```json
{
  "video_id": "...",
  "content_summary": "1-2 sentences: what literally happens",
  "narrative_skeleton": "1-2 sentences: the format stripped of subject/persona",
  "hook_type": "POV | transformation | storytime | reveal | comedy_skit | other",
  "hook_timing_sec": 0.0,
  "pacing": "fast | medium | slow",
  "has_spoken_dialogue": true,
  "has_onscreen_text": true,
  "requires_specific_persona": false,
  "emotional_tone": "...",
  "scores": {
    "narrative_transferability": {"value": 1, "reasoning": "..."},
    "hook_strength": {"value": 1, "reasoning": "..."},
    "production_feasibility": {"value": 1, "reasoning": "..."},
    "brand_fit": {"value": 1, "reasoning": "..."},
    "licensing_safety": {"value": 1, "reasoning": "..."}
  }
}
```

Set `video_id` to exactly: `{{VIDEO_ID}}`
