# Add your agent in ten minutes

What you need: Docker, Python 3.10+, a LiveKit Agents worker you can build into an image, and the API keys that
worker uses. Nothing else - the rig brings its own LiveKit server, microphone and fake backend.

## 1. Check the agent is a candidate

- It is a `livekit-agents` **worker** (`cli.run_app(...)`), dispatched automatically when a room is created.
- It reads the server from the standard `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` variables (the
  rig sets those for you).
- Anything it *does* reaches its backend over HTTP, at a URL you can set from an environment variable. That is
  what the rig can assert on. If the effect is local and silent, expose it (a webhook, an HTTP call) first.

## 2. Make a rig.yaml next to the agent's Dockerfile

```yaml
agent:
  build: {context: ., dockerfile: Dockerfile}
  env:
    OPENAI_API_KEY: ${OPENAI_API_KEY:?set OPENAI_API_KEY}   # whatever your agent needs, from YOUR shell
    BACKEND_URL: "{stub_url}"                               # point the agent's backend at the stub
transcripts: {source: livekit}
stub:
  routes:
    /orders/cancel: {json: {status: cancelled}}             # what your backend would answer
audio: audio
scenarios:
  hears_yes:
    steps:
      - say: yes_en
      - expect_user_heard: {matches: "\\byes\\b", within: 20}
```

Check it without starting anything: `voicerig validate`.

## 3. Make the audio

```bash
cat > phrases.yaml <<'YAML'
- {name: yes_en, voice: Samantha, text: "Yes."}      # `voice` is a macOS voice, or an espeak-ng voice such as en-us
YAML
voicerig tts phrases.yaml -o audio        # macOS `say`, or espeak-ng, plus ffmpeg
```

Or drop in your own 16 kHz mono 16-bit WAV files; `voicerig validate` says exactly what is wrong with a bad one.

## 4. Run the smallest scenario first

```bash
voicerig run
```

`hears_yes` passing means the whole chain works: audio in, VAD, STT, the agent connected. Fix that before writing
anything clever. If nothing is ever said, `voicerig logs agent` shows why; the usual cause is an agent that never
logged `registered worker` (set `agent.ready_log` if yours logs something else).

## 5. Write the scenario you actually care about

Pick one behaviour that must never regress ("asks before an irreversible action"), script the conversation, and
assert on both the words and the backend call. Then read the **Writing scenarios that mean something** section of
the README - in particular, *prove the check can fail*: run it against a deliberately bad version of your agent and
watch it go red.

## When something is off

| symptom | usual cause |
|---|---|
| `INCONCLUSIVE` on everything | the conversation never reached the state under test - read the note under the verdict |
| the agent never speaks | the worker had not registered when the client joined; check `agent.ready_log` |
| `ModuleNotFoundError` / crash at start | an unpinned dependency; `voicerig logs agent` |
| `does not provide the specified platform` | a cached multi-arch image with only one platform's layers: `docker pull --platform linux/amd64 <image>` |
| two rigs, one machine | they share a fixed subnet; run one at a time or set `network.subnet` |
