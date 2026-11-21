# voicerig

**Test a LiveKit voice agent by speaking to it.** voicerig starts a local LiveKit server, runs *your* agent next
to it, joins a room with a "microphone", plays recorded speech into it in real time, and watches what the
agent says and which backend calls it makes. No human, no phone, no cloud.

It exists because voice agents fail in ways unit tests cannot see: a one-word reply swallowed by VAD, a
confirmation question the model "usually" asks, a "thank you" taken for a "yes". Those only happen with real
audio, real speech-to-text and a real model in the loop - so that is what this runs.

```
 your WAV files ──► rig client ──WebRTC──► local LiveKit ──► YOUR agent (your image / Dockerfile)
                        ▲                                         │ HTTP
                        │ transcripts (lk.transcription)           ▼
                        └────────────────────────────────  stub backend (records every call)
```

A scenario is a short script in YAML. A verdict is `PASS`, `FAIL` (the agent did the wrong thing) or
`INCONCLUSIVE` (the conversation never reached the state under test) - and INCONCLUSIVE fails the run too,
because a silence is not a pass.

## Quick start

Needs Docker, Python 3.10+, and whatever API keys your agent uses.

```bash
pip install -e .                # from a checkout (not on PyPI yet)
cd examples/order-desk
export ANTHROPIC_API_KEY=... GROQ_API_KEY=...
voicerig run                    # ~3 minutes, a few cents of STT + LLM
```

```
[PASS] asks_before_cancelling  (15s)
    - agent said: 'Shall I cancel order 4417?'
    - no call to /orders/cancel for 5s
    - reply latency after end of speech: 2.34s
[PASS] cancels_after_yes  (15s)
    - agent said: "I'd be happy to help! Shall I cancel order 4417?"
    - no call to /orders/cancel for 1s
    - backend received POST /orders/cancel
...
==== 6 passed, 0 failed, 0 inconclusive ====
```

The exit code is 0 only if every scenario passed, so it drops into CI. Results also land in
`voicerig-results/results.json`.

## Point it at your agent

Short version below; the step-by-step walkthrough is [docs/add-your-agent.md](docs/add-your-agent.md).

`rig.yaml`:

```yaml
agent:
  build: {context: ., dockerfile: Dockerfile}   # or:  image: my-agent:latest
  env:
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:?set it}   # expanded by docker compose from YOUR shell
    BACKEND_URL: "{stub_url}"                         # the stub the rig starts; {livekit_url} also exists
    # LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET are set for you

transcripts: {source: livekit}     # the standard lk.transcription stream - nothing to do for livekit-agents
                                   # or: {source: data, topic: my-topic, match: {type: transcript},
                                   #      role_field: role, text_field: text, roles: {bot: assistant}}
stub:
  routes:                          # what your agent's backend answers; anything else answers {"ok": true}
    /orders/cancel: {json: {status: cancelled}}            # also: status, delay (seconds); fnmatch globs
audio: audio                       # 16 kHz mono 16-bit WAVs; `voicerig tts phrases.yaml` makes them

settings: {warmup: 4, attempts: 2, ready_timeout: 60, wait_for_greeting: true}
scenarios: ...
```

Your agent must be a LiveKit Agents worker that registers with the server (`registered worker` in its log;
override with `agent.ready_log`) and is dispatched automatically when a room is created. Secrets are never
written to disk by voicerig: the generated files in `.voicerig/` contain `${VAR}` references, not values.

## Steps

Each step is a one-key mapping. `expect_*` failing means **FAIL**; the same check as `require_*` failing means
**INCONCLUSIVE** - use `require_*` for "the scenario only makes sense if this already happened".

| step | meaning |
|---|---|
| `say: name` / `say: {audio, lead, tail}` | play `audio/name.wav`, padded with silence so endpointing sees a finished turn |
| `wait: seconds` | sleep |
| `expect_agent_says: {matches, question, within}` | the agent said text matching the regex and/or ending in a question (a `?` in its last 240 characters) |
| `expect_user_heard: {matches, within}` | the agent's STT produced a transcript matching the regex |
| `expect_call: {path, body_contains, within}` | the backend received a call; `path` may be a glob; string values match as case-insensitive substrings |
| `expect_no_call: {path, for}` | for `for` seconds, no (matching) call arrived |
| `expect_no_agent_reply: {for}` | the agent stayed silent for `for` seconds |

Every check looks at what happened since the last `say` began (`since: start` widens it to the whole scenario).
When an `agent_says` step follows a `say`, the delay from the end of your speech to the agent's first text is
reported as `reply latency`.

`repeat: N` + `min_pass_rate: 0.75` runs a scenario in N fresh rooms and reports a rate - for behaviour near a
threshold, where one run proves nothing.

## Writing scenarios that mean something

These come from running this against real agents and getting it wrong first.

- **A negative check needs a precondition.** "No cancel call for 15 s after the user says no" also passes when
  the agent never heard "no". Put `require_user_heard` in front of it.
- **Prove the check can fail.** `examples/order-desk` has an `ORDER_DESK_MODE=eager` agent that cancels without
  asking. `ORDER_DESK_MODE=eager voicerig run` must go red - and it does (see below). If your scenario cannot
  fail on a deliberately bad agent, it is not testing what you think.
- **`question: true` is any question.** The agent asking "which city?" satisfies it. Combine it with `matches:`
  ("a question that mentions applying").
- **Bare `yes` / `no` in YAML are booleans.** Name your WAVs `yes_en`, or quote: `say: "yes"`. voicerig tells you.
- **Pin your agent's dependencies.** The first thing this found, on the example agent, was an unpinned `anthropic`
  that crashed every session. A rig run on a floating `pip install` tests a different agent each time.

## What it measured on the example

`examples/order-desk` is a plain LiveKit agent (Groq Whisper, Claude Haiku 4.5, one irreversible tool, a prompt
that says "ask first"). On macOS/arm64, Docker 29, livekit-agents 1.6.7, one run each:

| agent | scenario | verdict |
|---|---|---|
| careful (prompt as written) | all 6 scenarios | 6 PASS |
| `eager` (told to cancel at once) | `asks_before_cancelling` | **FAIL** - agent cancelled without asking |
| `eager` | `cancels_after_yes` | **INCONCLUSIVE** after 2 attempts - never reached the state it tests |

A single run per cell is a smoke test, not a measurement: set `repeat` for rates before you quote a number.
The same rig also ran, unchanged apart from `transcripts: {source: data}`, against a production agent that
publishes JSON transcripts on its own data topic.

## Limits - read before you trust a green run

- **LiveKit Agents only.** It joins a LiveKit room and reads LiveKit transcripts or data messages.
- **It sees transcripts and HTTP calls, not tool calls.** Side effects must reach the stub backend over HTTP.
  An agent whose tool does something local and silent cannot be asserted on.
- **Synthetic speech.** The bundled WAVs come from a text-to-speech voice. Real accents, noise and crosstalk are
  yours to record; STT behaviour on TTS audio is not STT behaviour on people.
- **Real models are not deterministic.** Hence `attempts`, `repeat` and INCONCLUSIVE. A scenario that passes
  once may fail next run; measure a rate.
- **It costs API calls** (cents per full run) and takes minutes, not milliseconds.
- **Network.** Containers use fixed addresses (`172.28.0.0/24` by default, `network.subnet` to change) because
  WebRTC candidates must be reachable container-to-container. Only one rig runs at a time.
- **LiveKit server is pinned** (`v1.13.11`, by digest) so a run is reproducible; set `livekit: {image: ...}` to test against another version.
- Developed on macOS/arm64 with Docker Desktop. The example also passed 6/6 with every image forced to `linux/amd64`
  (`DOCKER_DEFAULT_PLATFORM=linux/amd64`, emulated on that Mac - not a native Linux host). A native Linux run is the
  manual `e2e.yml` workflow, which needs your provider keys as repository secrets.

## Commands

```
voicerig validate              check rig.yaml and the WAV files without starting Docker
voicerig run [--only a,b] [--attempts N] [--keep]
voicerig logs [agent|livekit|stub]      voicerig down
voicerig tts phrases.yaml -o audio      synthesize utterances (macOS `say` or espeak-ng, plus ffmpeg)
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # 40 tests, no Docker; the engine, config, stub and compose generation
```

Apache-2.0.
