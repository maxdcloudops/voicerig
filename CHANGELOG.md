# Changelog

## 0.2.1 - 2026-11-21

- Seeded faults for the example agent (`ORDER_DESK_MODE`: `eager`, `thanks_is_yes`, `ignores_no`, `says_done`,
  `deaf_vad`; an unknown mode is an error) and `rig.weak.yaml`, three deliberately weakened checks.
- A repeated scenario whose repetitions were all `INCONCLUSIVE` is now `INCONCLUSIVE`, not `FAIL`.
- The client exits without running LiveKit's native teardown, which could abort (exit 133) after a passing run.
- `evaluation/icst2027`: the raw results behind the paper's measurements.

## 0.2.0 - 2026-11-21

- **Tool calls.** `expect_tool_call` / `expect_no_tool_call` (and the `require_` forms) assert on the tool a voice
  agent called and its arguments, through a 6-line hook in the agent ([docs/tool-calls.md](docs/tool-calls.md)).
  `expect_no_tool_call` is INCONCLUSIVE, never PASS, when the agent has not announced the hook.
- **Rates, not single runs.** `voicerig run --only a --repeat N` reports PASS / FAIL / INCONCLUSIVE counts, the
  pass rate with a 95% Wilson interval, and reply-latency p50 / p95 / max. Results land in `results.json`.
- The default LiveKit server image is pinned by version and digest (it was `:latest`).
- CI on Linux (Python 3.10-3.13), a manual end-to-end workflow, a PyPI release workflow (trusted publishing).
- `docs/add-your-agent.md`, CONTRIBUTING, SECURITY, CITATION.cff, issue and PR templates, a README demo.
- The version now lives in one place (`voicerig.__version__`); a test keeps CITATION.cff in step.

## 0.1.0 - 2026-11-20

First public version: LiveKit server, your agent, a recording stub backend and a synthetic microphone in one
docker compose project; YAML scenarios; PASS / FAIL / INCONCLUSIVE verdicts; `livekit` and `data` transcript adapters.
