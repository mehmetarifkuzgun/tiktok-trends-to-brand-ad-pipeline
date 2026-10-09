# `brand_profile.json` schema

`config/brand_profile.json` is the single source of truth for who the ad is for. Prompts and agents
never hard-code a brand: they read this file through `agents/brand.py`. To re-target the whole
pipeline at another brand, replace this file. The bundled profile describes **Kahve Maya, a fictional cafe**.

| Key | Type | Used by | Meaning |
|---|---|---|---|
| `schema_version` | int | loader | Must be `1`. |
| `name` | string | everything | Brand name. Stamped into every `analysis.json` as `scored_for_brand`; spoken in generated dialogue. |
| `fictional` | bool | loader, docs | `true` for an invented brand. |
| `disclaimer` | string | docs | One-line note shown wherever the brand is introduced. |
| `positioning` | string | all prompts | One-line positioning. |
| `audience.summary`, `audience.interests` | string, list | all prompts | Who the ads speak to. |
| `tone_of_voice.summary`, `.voice_rules` | string, list | script prompts | Voice for captions and dialogue. |
| `visual_identity.*` | object | script prompt | Palette, lighting, typical settings, style. |
| `offer_examples` | list of string | script prompts | Generic, safe things the brand sells (never prices or claims). |
| `do`, `dont` | list of string | all prompts | Positive and negative rules. |
| `scoring.brand_fit.summary` | string | analysis and pre-filter prompts | How to read `brand_fit`. |
| `scoring.brand_fit.beats[]` | `{id, name, description}` | analysis, pre-filter, script prompts | The narrative beats a source format is judged against. At least one. |
| `scoring.brand_fit.fits`, `.does_not_fit` | list of string | analysis, pre-filter prompts | What fits and what does not. |
| `scoring.licensing_safety.guidance` | string | analysis prompt | What 5 vs 1 means for `licensing_safety` (the 1-5 scale stays monotonic: 5 is always favourable). |
| `ad_script.mention_style` | string | script prompts | How the brand may be named. |
| `ad_script.persona_hints`, `.setting_hints` | string, list | UGC script prompt | Persona and setting guidance. |
| `ad_script.allowed_props` | list of string | UGC script prompt | Props a shot may show. Phones, screens and devices are always banned (they break the picture-in-picture overlay). |
| `ad_script.banned_words` | list of string | script validators | Whole words/phrases rejected in prompts, dialogue and captions. Checked in code, not just asked for in prose. |
| `ad_script.language` | string | script prompts | Language of dialogue and captions. |
| `ad_script.pip_card_caption` | string | `scripts/make_placeholder_pip_card.py` | Text on the neutral placeholder picture-in-picture card. |

`python -m agents.brand` validates the file and prints the rendered contexts.
