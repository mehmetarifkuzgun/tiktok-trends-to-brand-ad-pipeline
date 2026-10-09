# Generate stage

Takes the top-scoring accepted videos from Process and produces one short 9:16 ad per trend for the
brand in `config/brand_profile.json`. Full design, costs and known limits are in
[GENERATE.md](../../GENERATE.md); run it with `python run_generate.py`.

| Module | Role |
|---|---|
| `selector.py` | Top-N accepted videos (scored for the current brand) by `weighted_score` -> `artifacts/generate_selection.json` |
| `ugc_scriptwriter.py` | Gemini text call (`prompts/ugc_ad_script_prompt.md`) -> persona, spoken dialogue, `ad_script.json`; validated in code |
| `video_generator.py` | One Veo job per shot (shot 2 on shot 1's last frame) -> `generated_clips/shot_{n}.mp4` (+ `.meta.json`) |
| `assembler.py` | ffmpeg concat + captions + picture-in-picture brand card -> `final_video.mp4` + `generation_manifest.json` |
| `veo_client.py` | Shared Veo/Gemini helpers, incl. an SSL-trust workaround and the `files.download` workaround |

Brand text never lives in these modules; they read it through `agents/brand.py`.
