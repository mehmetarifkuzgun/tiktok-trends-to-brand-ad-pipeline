# Process stage

Implemented. See [PROCESS.md](../../PROCESS.md) for the full design (model choice, rubric,
scoring convention, idempotency) and [CLAUDE.md](../../CLAUDE.md) for the architecture log.

`analyzer.py` uploads each downloaded video to Gemini (video-native, via the File API),
parses a structured analysis against `prompts/video_analysis_prompt.md`, and computes a
weighted accept/reject decision from `config/scoring_weights.json`. Run via
`python run_process.py --api-key <GEMINI_KEY>` at the repo root.
