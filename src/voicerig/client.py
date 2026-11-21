"""The rig's client. Runs INSIDE the rig container, on the same Docker network as LiveKit and the agent.

For each scenario it joins a fresh room on the local LiveKit server, SPEAKS the scenario's WAV files into the
room in real time like a microphone would, and watches what the real agent does: the transcripts it publishes
and the calls it makes to the stub backend. Then it hands the steps to `engine`.

Two ways to read the agent's transcripts (`transcripts.source` in rig.yaml):
    livekit   the standard `lk.transcription` text stream every livekit-agents agent publishes (default)
    data      JSON messages your agent publishes on a data topic: {topic, role_field, text_field, ...}
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
import wave
from pathlib import Path

from livekit import api, rtc

from . import engine
from .config import Config, load

TOPIC_TRANSCRIPTION = "lk.transcription"
USER_IDENTITY = "rig-user"
FRAME_MS = 10


class Stub:
    def __init__(self, url: str) -> None:
        self.url = url

    def calls(self) -> list[dict]:
        with urllib.request.urlopen(f"{self.url}/__calls", timeout=5) as r:
            return json.load(r)

    def reset(self) -> None:
        urllib.request.urlopen(urllib.request.Request(f"{self.url}/__reset", method="POST"), timeout=5).read()


class Call:
    """One participant in one fresh room, with a 'microphone'."""

    def __init__(self, cfg: Config, label: str, url: str, key: str, secret: str) -> None:
        self.cfg, self.url, self.key, self.secret = cfg, url, key, secret
        self.room_name = f"rig-{label}-{uuid.uuid4().hex[:6]}"[:60]
        self.room = rtc.Room()
        self.events: list[dict] = []
        self.source = rtc.AudioSource(16000, 1)
        self.speech_ended_at = 0.0
        self._tasks: set[asyncio.Task] = set()

    # --- transcripts ----------------------------------------------------------------------------
    def _add(self, role: str, text: str, t_first: float, **extra) -> None:
        self.events.append({"role": role, "text": text, "_t": time.time(), "_t_first": t_first, **extra})

    def _on_text_stream(self, reader: rtc.TextStreamReader, identity: str) -> None:
        async def consume() -> None:
            t_first, parts = 0.0, []
            async for chunk in reader:
                t_first = t_first or time.time()
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                # a transcription stream is sent by the participant it is ABOUT: ours for what the user said
                role = "user" if identity == USER_IDENTITY else "assistant"
                self._add(role, text, t_first or time.time(), final=dict(reader.info.attributes or {}).get("lk.transcription_final"))
        task = asyncio.ensure_future(consume())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_data(self, packet: rtc.DataPacket) -> None:
        tr = self.cfg.transcripts
        if packet.topic != tr["topic"]:
            return
        try:
            msg = json.loads(bytes(packet.data).decode("utf-8"))
        except Exception:
            return
        if any(msg.get(k) != v for k, v in (tr.get("match") or {}).items()):
            return
        role = msg.get(tr.get("role_field", "role"))
        role = (tr.get("roles") or {}).get(role, role)
        text = msg.get(tr.get("text_field", "text"))
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            self._add(role, text.strip(), time.time())

    async def __aenter__(self) -> "Call":
        token = (api.AccessToken(self.key, self.secret).with_identity(USER_IDENTITY)
                 .with_grants(api.VideoGrants(room_join=True, room=self.room_name)).to_jwt())
        if self.cfg.transcripts.get("source", "livekit") == "livekit":
            self.room.register_text_stream_handler(TOPIC_TRANSCRIPTION, self._on_text_stream)
        else:
            self.room.on("data_received", self._on_data)
        await self.room.connect(self.url, token)
        track = rtc.LocalAudioTrack.create_audio_track("rig-mic", self.source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        return self

    async def __aexit__(self, *exc) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.room.disconnect()

    # --- speaking -------------------------------------------------------------------------------
    async def say(self, utterance: str, lead: float = 0.5, tail: float = 2.5) -> float:
        """Play <audio dir>/<utterance>.wav into the room in real time, padded with silence so the agent's
        endpointing sees a finished turn. Returns the wall-clock time playout finished."""
        with wave.open(str(self.cfg.audio_dir / f"{utterance}.wav"), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), f"{utterance}.wav format"
            pcm = w.readframes(w.getnframes())
        spf = 16000 * FRAME_MS // 1000
        silence = b"\x00\x00" * spf
        data = silence * int(lead * 1000 / FRAME_MS) + pcm + silence * int(tail * 1000 / FRAME_MS)
        self.speech_ended_at = time.time() + lead + len(pcm) / 32000      # playout is real-time from the first frame
        step = spf * 2
        for i in range(0, len(data) - step + 1, step):
            await self.source.capture_frame(rtc.AudioFrame(data[i:i + step], 16000, 1, spf))
        await self.source.wait_for_playout()
        return time.time()

    # --- readiness ------------------------------------------------------------------------------
    async def ready(self, timeout: float, greeting: bool) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if greeting and any(e["role"] == "assistant" for e in self.events):
                return True
            if not greeting and self.room.remote_participants:
                return True
            await asyncio.sleep(0.25)
        return False


async def run_attempt(cfg: Config, name: str, url: str, key: str, secret: str, stub: Stub) -> engine.Verdict:
    s = cfg.settings
    stub.reset()
    try:
        async with Call(cfg, name, url, key, secret) as call:
            if not await call.ready(s["ready_timeout"], s["wait_for_greeting"]):
                return engine.Verdict("FAIL", [f"the agent never {'greeted' if s['wait_for_greeting'] else 'joined'} within {s['ready_timeout']:g}s"])
            # The greeting is published before the agent's audio input is necessarily subscribed to our track;
            # a one-word reply spoken at once can be lost at the start. Let the call settle first.
            await asyncio.sleep(s["warmup"])
            return await engine.run_steps(cfg.scenarios[name].steps, call, stub, time.time())
    except Exception as exc:       # noqa: BLE001 - a crashed scenario is a result, not a crash of the run
        return engine.Verdict("FAIL", [f"scenario crashed: {type(exc).__name__}: {exc}"])


async def run_scenario(cfg: Config, name: str, url: str, key: str, secret: str, stub: Stub, attempts: int) -> engine.Verdict:
    sc = cfg.scenarios[name]
    if sc.repeat > 1:
        # A single run proves nothing for behaviour near a threshold: repeat in fresh rooms and report a RATE.
        # INCONCLUSIVE counts against the rate: a repetition that never reached the state under test is not a pass.
        runs = [await run_attempt(cfg, name, url, key, secret, stub) for _ in range(sc.repeat)]
        ok = sum(r.status == "PASS" for r in runs)
        v = engine.Verdict("PASS" if ok / sc.repeat >= sc.min_pass_rate else "FAIL")
        v.notes.append(f"{ok}/{sc.repeat} passed ({ok / sc.repeat:.0%}); needed {sc.min_pass_rate:.0%}")
        for i, r in enumerate(runs, 1):
            if r.status != "PASS":
                v.notes.append(f"run {i}: {r.status}: {'; '.join(r.notes[-2:])}")
            v.latencies += r.latencies
        return v
    last = engine.Verdict("INCONCLUSIVE", ["never ran"])
    for attempt in range(1, attempts + 1):
        last = await run_attempt(cfg, name, url, key, secret, stub)
        if last.status != "INCONCLUSIVE":
            break
        last.notes.append(f"(attempt {attempt}/{attempts})")
    return last


async def amain() -> int:
    cfg = load(os.environ.get("RIG_CONFIG", "/rig/rig.yaml"))
    url, key, secret = os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    stub = Stub(os.environ["STUB_URL"])
    only = {s for s in os.environ.get("ONLY", "").split(",") if s}
    attempts = int(os.environ.get("ATTEMPTS") or cfg.settings["attempts"])
    names = [n for n in cfg.scenarios if not only or n in only]
    if not names:
        print(f"no scenario matches ONLY={sorted(only)}; known: {sorted(cfg.scenarios)}")
        return 2
    results: dict[str, engine.Verdict] = {}
    report = []
    for n in names:
        t0 = time.time()
        results[n] = await run_scenario(cfg, n, url, key, secret, stub, attempts)
        r = results[n]
        secs = time.time() - t0
        print(f"\n[{r.status}] {n}  ({secs:.0f}s)")
        for note in r.notes:
            print(f"    - {note}")
        if r.latencies:
            print(f"    - reply latency after end of speech: {', '.join(f'{x:.2f}s' for x in r.latencies)}")
        sys.stdout.flush()
        report.append({"scenario": n, "status": r.status, "seconds": round(secs, 1), "notes": r.notes,
                       "reply_latency_s": [round(x, 3) for x in r.latencies]})
    counts = engine.summarize(results)
    print(f"\n==== {counts['PASS']} passed, {counts['FAIL']} failed, {counts['INCONCLUSIVE']} inconclusive ====")
    out = Path(os.environ.get("OUT_DIR", "/rig/out"))
    if out.is_dir():
        (out / "results.json").write_text(json.dumps({"finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                                       "counts": counts, "scenarios": report}, indent=2))
    return 0 if counts["PASS"] == len(results) else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
