"""rig.yaml: loading and validation.

Everything that can be checked before Docker starts is checked here, so a typo in a scenario fails in
milliseconds with the file and the step named, not four minutes into a run.

Secrets are never resolved or written to disk by voicerig: `agent.env` values are passed through to
`docker compose` verbatim, and compose does its own `${VAR}` / `${VAR:-default}` / `${VAR:?message}` expansion
from your shell. The only placeholders voicerig itself replaces are {stub_url} and {livekit_url}.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """The rig.yaml is wrong. The message names what and where."""


# step kind -> the parameters it accepts
CHECKS: dict[str, set[str]] = {
    "agent_says": {"matches", "question", "within", "since"},
    "user_heard": {"matches", "within", "since"},
    "call": {"path", "within", "body_contains", "since"},
    "no_call": {"path", "for", "since"},
    "no_agent_reply": {"for", "since"},
}
SINCE = ("say", "start")

DEFAULT_SETTINGS: dict[str, Any] = {
    "warmup": 4.0,             # seconds to let the call settle after the greeting before speaking
    "attempts": 2,             # tries per scenario when the conversation never reaches the state under test
    "ready_timeout": 60.0,     # seconds to wait for the agent to join (and greet)
    "wait_for_greeting": True, # False for agents that stay silent until spoken to
}


@dataclass
class Step:
    kind: str                  # say | wait | agent_says | user_heard | call | no_call | no_agent_reply
    level: str                 # "" for say/wait, else "expect" (fail) or "require" (inconclusive)
    params: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.level + '_' if self.level else ''}{self.kind} {self.params}"


@dataclass
class Scenario:
    name: str
    steps: list[Step]
    repeat: int = 1
    min_pass_rate: float = 1.0
    description: str = ""


@dataclass
class Config:
    path: Path
    agent: dict[str, Any]
    transcripts: dict[str, Any]
    stub: dict[str, Any]
    audio_dir: Path
    settings: dict[str, Any]
    scenarios: dict[str, Scenario]
    raw: dict[str, Any]
    livekit: dict[str, Any] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> Path:
        return self.path.parent


def _num(where: str, v: Any, *, lo: float = 0, hi: float | None = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{where}: expected a number, got {v!r}")
    if v < lo or (hi is not None and v > hi):
        raise ConfigError(f"{where}: {v} is outside [{lo}, {hi if hi is not None else 'inf'}]")
    return float(v)


def _parse_step(where: str, raw: Any) -> Step:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ConfigError(f"{where}: a step is a one-key mapping like `- say: yes_en`, got {raw!r}")
    (key, arg), = raw.items()
    if key == "say":
        if isinstance(arg, bool):
            word = "yes" if arg else "no"
            raise ConfigError(f"{where}: YAML reads a bare `{word}` as a boolean. Quote the WAV name: say: \"{word}\"")
        if isinstance(arg, str):
            return Step("say", "", {"audio": arg})
        if isinstance(arg, dict) and isinstance(arg.get("audio"), str) and set(arg) <= {"audio", "lead", "tail"}:
            return Step("say", "", {**arg})
        raise ConfigError(f"{where}: `say` takes a WAV name or {{audio, lead, tail}}, got {arg!r}")
    if key == "wait":
        return Step("wait", "", {"seconds": _num(f"{where}: wait", arg)})
    level, _, kind = key.partition("_")
    if level not in ("expect", "require") or kind not in CHECKS:
        known = ", ".join(sorted(["say", "wait"] + [f"{lv}_{k}" for lv in ("expect", "require") for k in CHECKS]))
        raise ConfigError(f"{where}: unknown step `{key}`. Known: {known}")
    params = arg if isinstance(arg, dict) else {}
    if arg is not None and not isinstance(arg, dict):
        raise ConfigError(f"{where}: `{key}` takes a mapping of parameters, got {arg!r}")
    extra = set(params) - CHECKS[kind]
    if extra:
        raise ConfigError(f"{where}: `{key}` does not take {sorted(extra)}; it takes {sorted(CHECKS[kind])}")
    if params.get("since", "say") not in SINCE:
        raise ConfigError(f"{where}: since must be one of {SINCE}")
    for k in ("within", "for"):
        if k in params:
            _num(f"{where}: {key}.{k}", params[k])
    if kind == "agent_says" and not (params.get("matches") or params.get("question")):
        raise ConfigError(f"{where}: `{key}` needs `matches: <regex>` or `question: true`")
    if kind in ("agent_says", "user_heard") and "matches" in params:
        import re
        try:
            re.compile(params["matches"])
        except re.error as exc:
            raise ConfigError(f"{where}: `matches` is not a valid regex: {exc}") from None
    if kind == "call" and not params.get("path"):
        raise ConfigError(f"{where}: `{key}` needs `path`")
    if kind == "user_heard" and not params.get("matches"):
        params = {**params, "matches": "."}
    return Step(kind, level, params)


def check_wav(path: Path) -> None:
    """16 kHz, mono, 16-bit PCM: what the rig's microphone publishes."""
    try:
        with wave.open(str(path), "rb") as w:
            fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
    except (wave.Error, EOFError) as exc:
        raise ConfigError(f"{path}: not a readable WAV ({exc})") from None
    if fmt != (16000, 1, 2):
        raise ConfigError(
            f"{path}: is {fmt[0]} Hz / {fmt[1]} ch / {fmt[2] * 8}-bit; the rig needs 16000 Hz / 1 ch / 16-bit. "
            f"Convert: ffmpeg -i in.wav -ar 16000 -ac 1 -sample_fmt s16 out.wav")


def load(path: str | Path, *, check_audio: bool = True) -> Config:
    path = Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path}: no such file") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    unknown = set(raw) - {"agent", "transcripts", "stub", "audio", "settings", "scenarios", "livekit", "network"}
    if unknown:
        raise ConfigError(f"{path}: unknown top-level keys {sorted(unknown)}")

    agent = raw.get("agent")
    if not isinstance(agent, dict) or bool(agent.get("image")) == bool(agent.get("build")):
        raise ConfigError("agent: set exactly one of `image` (a built image) or `build` ({context, dockerfile})")
    if "build" in agent and not isinstance(agent["build"], dict):
        raise ConfigError("agent.build: expected {context: ., dockerfile: Dockerfile}")
    env = agent.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in env.items()):
        raise ConfigError("agent.env: expected a mapping of NAME: value")

    tr = raw.get("transcripts") or {"source": "livekit"}
    if tr.get("source", "livekit") not in ("livekit", "data"):
        raise ConfigError("transcripts.source: `livekit` (the standard lk.transcription stream) or `data` (JSON data messages)")
    if tr.get("source") == "data" and not tr.get("topic"):
        raise ConfigError("transcripts: source `data` needs `topic`")

    stub = raw.get("stub") or {}
    routes = stub.get("routes", {})
    if not isinstance(routes, dict):
        raise ConfigError("stub.routes: expected a mapping of path -> {status, json, delay}")

    settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
    bad = set(settings) - set(DEFAULT_SETTINGS)
    if bad:
        raise ConfigError(f"settings: unknown {sorted(bad)}; known {sorted(DEFAULT_SETTINGS)}")

    audio_dir = (path.parent / raw.get("audio", "audio")).resolve()

    scenarios: dict[str, Scenario] = {}
    for name, spec in (raw.get("scenarios") or {}).items():
        where = f"scenarios.{name}"
        if not isinstance(spec, dict) or not isinstance(spec.get("steps"), list) or not spec["steps"]:
            raise ConfigError(f"{where}: needs a non-empty `steps` list")
        extra = set(spec) - {"steps", "repeat", "min_pass_rate", "description"}
        if extra:
            raise ConfigError(f"{where}: unknown keys {sorted(extra)}")
        steps = [_parse_step(f"{where}.steps[{i}]", s) for i, s in enumerate(spec["steps"])]
        for i, s in enumerate(steps):
            if s.kind == "say" and check_audio:
                wav = audio_dir / f"{s.params['audio']}.wav"
                if not wav.exists():
                    raise ConfigError(f"{where}.steps[{i}]: {wav} does not exist")
                check_wav(wav)
        scenarios[name] = Scenario(
            name=name, steps=steps, description=str(spec.get("description", "")),
            repeat=int(_num(f"{where}.repeat", spec.get("repeat", 1), lo=1)),
            min_pass_rate=_num(f"{where}.min_pass_rate", spec.get("min_pass_rate", 1.0), lo=0, hi=1),
        )
    if not scenarios:
        raise ConfigError("scenarios: define at least one")

    return Config(path=path, agent=agent, transcripts=tr, stub=stub, audio_dir=audio_dir, settings=settings,
                  scenarios=scenarios, raw=raw, livekit=raw.get("livekit") or {}, network=raw.get("network") or {})
