# UGC Ad Script Prompt — Trend-to-Brand Shot List (photorealistic influencer style)

This is the actual prompt sent to Gemini to turn one analyzed trend video into a two-shot,
photorealistic UGC-style ad script for the brand in `config/brand_profile.json`. Loaded verbatim by
`agents/generate/ugc_scriptwriter.py` (never duplicated as an inline Python string). Runtime
substitutions are the upper-case double-brace tokens below: the brand name and the brand context
block (positioning, beats, voice, do/don't) come from `config/brand_profile.json` via
`agents/brand.py`; the tokens in section 3 come from the source video's real `analysis.json`.

An invented, generic-looking person talks to camera and delivers a short spoken line, in the
register of a real TikTok creator, not a polished brand film. The brand's own imagery is never
rendered by the video model at all: a separate brand/product card appears only as a
picture-in-picture graphic composited in afterward by `agents/generate/assembler.py`. See
GENERATE.md for why.

## 1. Task

You are writing the shot list for a short vertical (9:16), **photorealistic UGC-style** ad for
**{{BRAND_NAME}}** — the kind of video a real TikTok creator posts: shot on a front-facing camera,
talking directly to the lens, in an ordinary real-looking place. The ad must **reuse the narrative
structure and timing of a real trending TikTok format** — the format below — while replacing that
format's subject with a moment about the brand, told in first person by an invented person. Copy
the *shape* of the source video (its setup -> reveal structure, its emotional arc), never its
subject, people, wording, or the specific fact that it's about hair/dance/etc.

**It also has to sound like a real person, not an ad.** The on-screen caption and the spoken
dialogue should both read like something a real person would actually post, with a little
self-aware humour — not a slogan, not brand copy. Section 6 defines the voice.

**No brand marks appear on screen.** Never describe logos, signage with lettering, packaging with
printed text, or a screen of any kind in the `visual_prompt`. The brand is named through spoken
dialogue and/or the on-screen caption only; a separate brand card is added afterward as a
picture-in-picture graphic, not something the video model draws.

**Absolutely no phone, screen, tablet or handheld device may be described, held, shown, or
referenced anywhere in either shot.** This is a hard rule (checked automatically), not a stylistic
preference: a phone in shot makes the video model invent a second, duplicate device, and there is
no reliable way to composite a graphic onto a device the person is moving. Props are limited to
those the brand allows (section 2). Keep attention on the camera lens itself.

The ad is produced as **exactly two AI-generated video clips** (a Veo video model generates each
clip from your `visual_prompt`; there is no seed image for shot 1, since the person is invented —
shot 2 instead starts on shot 1's own actual last frame, so the same face, outfit and place carry
over automatically): a **setup shot** (order 1) and a **payoff shot** (order 2).

## 2. Context: the brand ({{BRAND_NAME}})

The block below is the brand profile, rendered from `config/brand_profile.json`. Map the source
trend's structure onto one of its beats, spoken casually, never as a feature list. Read its tone,
visual identity and do/don't rules as binding.

{{BRAND_CONTEXT}}

- **How the brand may be named:** {{MENTION_STYLE}}
- **Persona hints:** {{PERSONA_HINTS}}
- **Setting ideas:** {{SETTING_HINTS}}
- **Props a shot may show (nothing else is held or shown):** {{ALLOWED_PROPS}}
- **Words that must never appear in the shot prompts, dialogue or captions:** {{BANNED_WORDS}}
- **Language of dialogue and captions:** {{LANGUAGE}}

## 3. The source trend format you are adapting

- **Source video id:** {{VIDEO_ID}}
- **Trend id:** {{TREND_ID}}
- **Narrative skeleton (the structure to preserve):** {{NARRATIVE_SKELETON}}
- **Hook type:** {{HOOK_TYPE}}
- **Hook/reveal lands at (seconds into the source video):** {{HOOK_TIMING_SEC}}
- **Pacing:** {{PACING}}
- **Emotional tone:** {{EMOTIONAL_TONE}}

## 4. The persona (you invent this)

Invent one generic-looking, entirely fictional person — **never a real or public individual,
never a celebrity, never based on any specific real person** — who will appear in both shots.
Give them a short, concrete physical description (age range, hair, one distinctive but
ordinary wardrobe choice) and a real-feeling but ordinary place (lighting, one or two
background details), chosen from or in the spirit of the brand's setting ideas. Pick a persona and
setting that suit the trend's emotional tone (a tired setup calls for a different mood than an
energetic one). Restate this persona and setting in both shots' `visual_prompt` fields, since the
two clips are generated separately and only share the literal frame handed from shot 1 to shot 2.

## 5. Rules

**Structure and timing (standing rules, not options)**
- Output exactly **2 shots**: `order` 1 (setup) and `order` 2 (payoff).
- Shot 1 is **4 seconds** (use 6 only if the source's hook lands late, after about 5 seconds).
  Shot 2 is **exactly 8 seconds**. Total 12 (or 14) — inside the 8-15 second target.

**Shot 1 (the setup)**
- The person speaks directly to camera, naturally lip-synced, delivering the first half of a single
  continuous spoken thought that shot 2 finishes (write the full line across both shots as one
  sentence split at a natural pause — an em dash or ellipsis at the end of shot 1's dialogue
  works well). The emotional tone should mirror the source trend's own hook (tired/deadpan,
  cheerful, sheepish, etc. — see the emotional tone in section 3).
- End the shot on a clear, held expression or reaction (not mid-motion) — that last frame
  becomes shot 2's first frame, so it should be something shot 2 can naturally continue from.

**Shot 2 (the payoff)**
- One continuous shot, same place, same person, continuing directly from shot 1's ending. The
  person finishes the spoken line, and somewhere in it **names {{BRAND_NAME}}** (the brand mention
  lives in the dialogue, not as an on-screen tag — see Voice).
- The person may glance briefly toward the upper-left corner of frame partway through (a real
  picture-in-picture brand card will appear there in post) then return their gaze to camera — this
  is optional but gives the eventual overlay a natural motivation.
- End on a clear, calm or amused reaction that resolves the joke.

**How to write each `visual_prompt`** (it is sent to a video model as-is)
- 70-140 words. Concrete and visual: the **persona and setting**, the **actions/expression in
  order**, the **camera** (handheld selfie framing, "front-facing camera, slightly low angle"), the
  **lighting**, and a closing note that this is photorealistic, amateur handheld selfie-video
  quality, not cinematic.
- The two shots are generated separately and share only the frame handed from one to the next,
  so **restate the same persona/setting description in both prompts** (close to word for word).
- Vertical 9:16 framing, selfie style, the face and upper body filling most of the frame.
- **No phone, screen, tablet or device anywhere** (see section 1 — checked automatically; a script
  that breaks it is rejected and retried).
- **Never ask for text, UI, signage or logos in the frame.** End every `visual_prompt` with this
  exact sentence, verbatim: "Absolutely no on-screen text of any kind anywhere in the shot: no
  captions, subtitles, burned-in words, letters, numbers, timestamps, watermarks, logos, or UI
  elements -- and no text, lettering, or graphic prints on clothing, posters, signs, packaging, or
  any other object in the scene either." The video model renders text and logos badly and
  unpredictably; on-screen text is added afterward from `onscreen_text`.
- Photorealistic, not stylized or animated — "photorealistic vertical 9:16 selfie-style video"
  should appear early in each `visual_prompt`.
- Never depict a real person, celebrity, or public figure — the persona is entirely invented.

**Spoken dialogue**
- `dialogue` is the exact line spoken in that shot, naturally lip-synced — quote it inside the
  `visual_prompt` too (video models follow an explicit quoted line far more reliably than a
  paraphrase), and repeat it verbatim in the `dialogue` field for the record.
- Casual, first-person, funny or wry — see Voice. {{BRAND_NAME}} must be named somewhere in the
  combined shot 1 + shot 2 dialogue (usually shot 2, at the reveal).

**On-screen text**
- `onscreen_text` is a short caption (**at most 8 words**, plain ASCII, no emoji) added in
  post, written in the voice defined in section 6 — usually a wry restatement or punchline, not
  a transcript of the spoken dialogue.
- Use `null` when a shot needs none (shot 1 usually wants one; shot 2 often wants a short,
  brand-adjacent punchline).

## 6. Voice and personality

The single most important thing after getting the structure right: **it must sound like a real
person posting a video, not an ad.** Ask "how would a real person actually caption and say this
if they were being a little dramatic about their everyday life, and knew it?" — not "how would a
brand write this?"

- **Spoken dialogue and on-screen text:** the brand's tone of voice (section 2), expressed as
  self-aware, a little dry or mock-dramatic. Casual first person, understatement, lowercase
  captions are fine. Not a slogan, not an imperative selling the product ("Visit today", "Try
  now"), not motivational-poster language, no marketing adjectives ("ultimate", "best ever").
- **The brand mention lives inside the joke**, spoken naturally ("...so I sat down at {{BRAND_NAME}}
  and for ten minutes nothing was urgent" reads like a real sentence; "{{BRAND_NAME}}: THE ULTIMATE
  EXPERIENCE" does not). Never a bare tag, never a call-to-action.
- **The joke lives inside the structure.** Shot 1 sets it up, shot 2 pays it off, on the
  source's own timing.
- **Keep it gentle and brand-safe.** The speaker is the one being self-deprecating or
  mock-dramatic, never anyone else; no crude or edgy humour; nothing the brand's "do not" list
  rules out.

## 7. Worked example

*Illustration only — you will always be given a real source format. This persona is invented and
is not one of the personas this pipeline has produced.*

- Source skeleton: "A relatable, mildly chaotic 'before' moment is shown, then a hard cut
  reveals the small thing that's actually holding everything together."
- Hook type: reveal · Pacing: medium · Tone: wry, affectionate self-deprecation

Expected output for that:

```json
{
  "video_id": "example_source",
  "trend_id": "trend_example_000000",
  "persona": {
    "description": "A woman in her late 20s with short curly black hair, wearing an oversized denim jacket, sitting at a small wooden window table in a quiet cafe with soft morning light behind her.",
    "identity_consistency_method": "Shot 1 is text-to-video (no reference image exists for an invented person); shot 2 is seeded on shot 1's own actual last frame."
  },
  "narrative_mapping": "Source: a chaotic 'before' moment cuts to the one small thing holding everything together. Mapped to: she admits her week has been a blur, then reveals that the one thing slowing it down is twenty quiet minutes at {{BRAND_NAME}}.",
  "shots": [
    {
      "order": 1,
      "duration_sec": 4,
      "visual_prompt": "Photorealistic vertical 9:16 selfie-style video, front-facing camera, slightly low handheld angle. A woman in her late 20s with short curly black hair, wearing an oversized denim jacket, sits at a small wooden window table in a quiet cafe with soft morning light behind her. She looks at the camera with a tired, wry half-smile and speaks directly to it, naturally lip-synced: \"okay so this week has been a certified blur, but honestly—\" She raises an eyebrow and pauses mid-sentence. Soft window light, visible skin texture, subtle natural blinking, slight handheld camera shake, realistic amateur handheld selfie-video quality, not cinematic. Absolutely no on-screen text of any kind anywhere in the shot: no captions, subtitles, burned-in words, letters, numbers, timestamps, watermarks, logos, or UI elements -- and no text, lettering, or graphic prints on clothing, posters, signs, packaging, or any other object in the scene either.",
      "onscreen_text": "it's fine. everything is fine.",
      "dialogue": "okay so this week has been a certified blur, but honestly—"
    },
    {
      "order": 2,
      "duration_sec": 8,
      "visual_prompt": "Photorealistic vertical 9:16 selfie-style video, continuing the same handheld selfie shot. A woman in her late 20s with short curly black hair, wearing an oversized denim jacket, still sits at a small wooden window table in a quiet cafe with soft morning light behind her. Her wry expression breaks into a small, calm grin. She glances briefly toward the upper-left corner of frame, then looks back at the camera, relaxed and a little smug, and speaks directly to camera, naturally lip-synced: \"...the only thing that slows it down is twenty minutes at {{BRAND_NAME}} with absolutely nowhere to be.\" She ends with a slow, contented nod. Soft window light, visible skin texture, subtle natural blinking, slight handheld camera shake, realistic amateur handheld selfie-video quality, not cinematic. Absolutely no on-screen text of any kind anywhere in the shot: no captions, subtitles, burned-in words, letters, numbers, timestamps, watermarks, logos, or UI elements -- and no text, lettering, or graphic prints on clothing, posters, signs, packaging, or any other object in the scene either.",
      "onscreen_text": "not sponsored. just unhurried.",
      "dialogue": "...the only thing that slows it down is twenty minutes at {{BRAND_NAME}} with absolutely nowhere to be."
    }
  ]
}
```

## 8. Required output

Respond with **only** valid JSON matching the schema below — no prose before or after it.

```json
{
  "video_id": "...",
  "trend_id": "...",
  "persona": {"description": "...", "identity_consistency_method": "..."},
  "narrative_mapping": "...",
  "shots": [
    {"order": 1, "duration_sec": 4, "visual_prompt": "...", "onscreen_text": "... or null", "dialogue": "..."},
    {"order": 2, "duration_sec": 8, "visual_prompt": "...", "onscreen_text": "... or null", "dialogue": "..."}
  ]
}
```

Set `video_id` to exactly `{{VIDEO_ID}}` and `trend_id` to exactly `{{TREND_ID}}`.
