# Generate stage

Takes the top-scoring accepted videos from Process and produces one short 9:16 ad per trend for the
brand in [`config/brand_profile.json`](config/brand_profile.json) (the bundled demo brand is the fictional cafe
**Kahve Maya**): a photorealistic, creator-style selfie video in which an invented person talks to camera. The brand is never rendered by Veo;
a brand/product card is composited afterwards as a picture-in-picture overlay.

> **Sample.** [`examples/kahve_maya_sample_ad.mp4`](examples/kahve_maya_sample_ad.mp4) is one real output (trend "I Did Nothing...", `fast` tier, 12 s, about $1.20 of Veo), with the
> script and manifest that produced it next to it. An earlier development run produced ads for a different, original target brand; those videos
> contained third-party brand material and were not kept. The findings below that came from that run are brand-independent.

```
select -> script (invented persona + spoken dialogue, validated in code) -> Veo shot 1 (text-to-video)
       -> Veo shot 2 (image-to-video on shot 1's last frame) -> ffmpeg: concat + captions + brand-card overlay
```

## Design decisions

1. **No phone, screen or device in a shot, ever.** Early attempts had the person hold or tap a phone showing the brand; Veo then
   sometimes rendered a *second*, duplicate device, and there was no reliable way to composite a graphic onto a device the person
   was moving. The fix removes the object entirely: the person never references a device, and the brand/product card is a plain
   post-production overlay (fixed position, not tracked to anything) that reads as a "screen-recording insert" cutaway. A rule that
   matters this much is enforced in the validator, not just asked for in the prompt. Props are limited to the brand profile's
   `ad_script.allowed_props` (for the demo brand: a plain unmarked ceramic cup, a dripper, a kettle).
2. **Two-layer no-text instruction.** Video models render text badly and sometimes invent captions. Every `visual_prompt` ends with an
   itemised sentence banning captions, subtitles, burned-in words, letters, numbers, timestamps, watermarks, logos, UI elements and
   printed text on clothing, posters, signs and packaging, and generation also passes the SDK's `negative_prompt` field. On-screen text is
   added afterwards by ffmpeg from the script's `onscreen_text`. Two real failure modes motivated this: `standard`-tier Veo hallucinated its own
   burned-in caption of the tail of a spoken line, and the words "graphic hoodie" and "posters" in a prompt produced illegible pseudo-lettering on clothing and walls
   (a self-inflicted prompt bug, fixed by describing plain clothing and unreadable clutter).
3. **Iterate cheap, finish once.** `standard` ($0.40/s) is visibly more photorealistic than `fast` ($0.10/s) but every script fix costs a
   full-price re-render, so validate each script on `fast` first, then render the approved script once on `standard`.
4. **Brand text comes from the profile, not from code.** Name, beats, tone, props and banned words are read from `config/brand_profile.json`
   through `agents/brand.py`. The validators check that the brand is named in the dialogue and that no banned word appears in any prompt, caption or line.

Identity carries across the cut because shot 1 is text-to-video (there is no real image of an invented person) and shot 2 is seeded on shot 1's actual last frame.

## Running it

```bash
python run_generate.py --api-key <GEMINI_KEY> --script-only          # scripts only, no Veo spend, review first
python run_generate.py --api-key <GEMINI_KEY> --model-tier fast      # cheap iteration
python run_generate.py --api-key <GEMINI_KEY>                        # standard tier
```

| Flag | Default | Meaning |
|---|---|---|
| `--model-tier` | `standard` | `lite` $0.05/s, `fast` $0.10/s, `standard` $0.40/s |
| `--top-n` | 3 | how many top-scoring trends to generate |
| `--trend TEXT` | all | restrict to selected trends whose id contains TEXT (repeatable) |
| `--script-only` | off | stop before any Veo call |
| `--force` | off | regenerate scripts and clips and force reassembly (spends money again) |

**Idempotent at every step.** Scripts, clips and final videos that already exist are reused: a Veo clip is reused when its model, prompt,
duration, seed and negative prompt match (changing any of them forces regeneration), and final assembly is skipped when the final video
already matches the current script and source video (`assembler.already_assembled`). This closed a real corruption risk: an earlier
assembly step would have silently overwritten a valid final video with a mismatched reassembly. It was verified by hashing the outputs before and
after a no-`--force` re-run (byte-identical, $0.00, under a second).

`run.py` calls this stage with the orchestrator's own `--model-tier` (default `fast`).
The orchestrator treats analyses scored for a different brand as stale, so it will not select them for Generate.

## Cost

Veo returns no cost or usage field, so every figure is an estimate (`seconds × published price`, 720p with audio). Prices are for the preview models
`veo-3.1-lite-generate-preview`, `veo-3.1-fast-generate-preview` and `veo-3.1-generate-preview`; IDs and behaviour can change.

| Tier | $/s | One 12 s ad (4 s + 8 s) |
|---|---|---|
| lite | $0.05 | $0.60 |
| fast | $0.10 | $1.20 |
| standard | $0.40 | $4.80 |

Gemini script generation for three ads was about $0.06 (token estimate). For scale, the whole sample run (Discover + Process + one `fast` ad) cost about $1.58; an earlier development run spent an
estimated ~$29 exploring and iterating this style before it settled (most of it buying the findings below rather than regeneration).

## Known limitations

- **Audio has never been judged by ear** (levels only). Lip-sync was checked frame by frame, not listened to.
- The brand card is a fixed overlay, not tracked to the subject; captions sit at a fixed height and can cover part of the action.
- Personas are generated independently per render: the same trend rendered twice yields a visibly different person (identity holds only inside one ad's two chained shots).
- Delivery does not always match the scripted emotional adjective ("smug" often reads as "delighted").
- Each ad is a single accepted take, not an iterated one. The two clips are joined without a crossfade.
- `standard` and `fast` differ visibly in realism (skin texture, lighting, bokeh); a trend rendered on `fast` because of a quota error is a quality trade-off, not a hidden one.
- Veo preview models may change; the SDK is pinned to `google-genai==1.46.0`, and upgrading means re-verifying `generate_videos`, `operations.get` and `files.download`.

## Findings from the original run (brand-independent lessons)

- **A prompt that contradicts a seed image wins within ~1 s.** If you seed a shot with an image, write the prompt from what the image actually shows.
- **A chain-seeded clip must start on the previous clip's last frame**, so a scripted "hard cut" becomes a ~0.4 s morph; write shot 1 as one flowing action and end it on a clear hold.
- **Veo accepts 4 s and 6 s clips with an image input** (a documentation summary claiming 8 s only was wrong); hence the 4 s + 8 s design, total 12 s.
- **One real clip corrupted** into a blurred, letterboxed artifact after ~7.5 s of its 8 s; fixed by trimming the final assembly, not by regenerating. Always inspect frames, not just metadata.
- **Transformations that consume the whole shot ruin the payoff**: keep the change in the first third of the 8 s shot.
- **Delivery beats need physical mechanics.** A "carefree to stunned" beat failed until the prompt spelled out what the face and body do ("eyes snap wide ... does NOT smile ... does NOT look away") and fixed the camera on the face.
- **Three self-inflicted validator bugs** appeared only when the whole pipeline ran against the live API: the prompt said "phone front camera" while the validator banned "phone"; the required no-text sentence contained the banned word "screen" (via "on-screen"); and a verbatim-persona rule rejected harmless tense changes ("standing" vs "stands"). Fixes: whitelist the legitimate phrases, relax to a 60% word-overlap check, and unit-test each prompt's worked example against its own validator (`tests/test_generate_validators.py`).
- **Windows line endings** (`\n` -> `\r\n` in the text file given to ffmpeg `drawtext`) doubled caption line spacing; fixed by writing bytes. Found by a free smoke test before any Veo spend.
- **Operational failures to expect**: HTTP 429 (quota; zero spend, retry later or fall back to a cheaper tier), 402 (credits depleted), 401 (a key of the wrong kind pasted in: a normal Gemini key starts `AIza...`). Stop and fix; do not retry blindly.

## SynthID, retention, and downloads

Every Veo output carries an imperceptible SynthID watermark (not removable and not touched by this pipeline). Google keeps generated files **server-side for only 2 days**, so
`video_generator.py` downloads each clip right after its job finishes, through a `.part` file so a failed download cannot leave a truncated `.mp4`.

## Dev-environment workarounds (`veo_client.py`)

- **TLS-intercepting antivirus** can break Python TLS (`CERTIFICATE_VERIFY_FAILED` on every Google call). `configure_ssl_trust()` builds a certifi + local-root CA bundle and sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` for the process. Verification stays on; it is a no-op without such software.
- **`client.files.download` on google-genai 1.46.0 has no `destination=` kwarg** (the docs' sample assumes a newer SDK): `download_video()` uses the returned bytes and writes the file itself.

## Not done

- Only one trend has been rendered for Kahve Maya, on `fast`; no `standard`-tier render and no multi-trend comparison.
- No per-clip automated check of the rendered clip against its script, and no human review gate before paid renders.
