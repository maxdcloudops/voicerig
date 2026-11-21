# Raw results behind the ICST 2027 tool paper

Every number in the paper's evaluation is computed from these files; nothing was typed in by hand.

| file | what it is | produced by |
|---|---|---|
| `single_careful.json`, `single_eager.json` | one pass over the six scenarios of `examples/order-desk`, careful agent and `ORDER_DESK_MODE=eager` | `voicerig run` (and the same with `ORDER_DESK_MODE=eager`) |
| `rate20_careful.json` | `asks_before_cancelling` and `cancels_after_yes`, 20 fresh-room runs each | `voicerig run --only asks_before_cancelling,cancels_after_yes --repeat 20` |
| `rate20_eager.json` | `asks_before_cancelling`, 20 runs, `ORDER_DESK_MODE=eager` | `ORDER_DESK_MODE=eager voicerig run --only asks_before_cancelling --repeat 20` |
| `rate20_deaf_vad.json` | `hears_yes`, 20 runs, `ORDER_DESK_MODE=deaf_vad` | `ORDER_DESK_MODE=deaf_vad voicerig run --only hears_yes --repeat 20` |
| `amd64_emulated_run.log` | the full example with `DOCKER_DEFAULT_PLATFORM=linux/amd64` (emulated on an arm64 Mac) | `voicerig run`, raw console output (one local path replaced by `<checkout>`) |

Conditions: macOS/arm64, Docker Desktop, 2026-11-21; Whisper large-v3-turbo (Groq) for speech-to-text, Claude Haiku 4.5
as the model, livekit-agents 1.6.7, LiveKit server v1.13.11 (pinned by digest). The runs were made on commit `d364eb4`.
A later commit adds the function that labels a repeated scenario whose repetitions were all `INCONCLUSIVE` as
`INCONCLUSIVE` instead of `FAIL`; it changes that label only, not the counts under `stats`, which are what the paper uses.

`stats.ci95` is the 95% Wilson score interval of the pass rate; `stats.latency_s` is the time from the end of the
caller's speech to the agent's first text (p50, p95, max over the runs in which the agent said anything that matched).
Sample sizes are in the files: a rate from 20 runs bounds the true rate loosely (20/20 means "at least about 84%").
