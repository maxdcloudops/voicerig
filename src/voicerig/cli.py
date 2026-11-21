"""voicerig: test a LiveKit voice agent by speaking to it.

    voicerig validate            check rig.yaml (and the WAV files) without starting Docker
    voicerig run                 build, start, run every scenario, tear down. Exit 0 only if all PASS
    voicerig run --only a,b      just those scenarios
    voicerig run --only a --repeat 20     a rate with a 95% interval instead of one verdict
    voicerig run --keep          leave the rig running afterwards (inspect with `voicerig logs agent`)
    voicerig logs [service]      agent | livekit | stub
    voicerig down                tear down a kept rig
    voicerig tts phrases.yaml    synthesize test utterances into the audio directory (macOS `say` or espeak-ng)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from . import compose
from .config import ConfigError, load


def _docker(*args: str, root: Path | None = None, check: bool = True, env: dict | None = None) -> int:
    cmd = ["docker", *args]
    return subprocess.run(cmd, check=check, env={**os.environ, **(env or {})}).returncode


def _compose(root: Path, *args: str, check: bool = True) -> int:
    files = ["-f", str(root / "compose.yml")]
    env_file = root.parent / ".env"
    if env_file.exists():
        files += ["--env-file", str(env_file)]
    return _docker("compose", "-p", "voicerig", *files, *args, check=check)


def _wait_for_agent(root: Path, ready_log: str, timeout: float = 180.0) -> str | None:
    """Poll the agent's logs for its "I am registered with LiveKit" line. LiveKit dispatches an agent when a room is
    CREATED; a client that joins before the worker has registered gets a room nobody will ever serve.
    Returns None when ready, else why not."""
    files = ["-f", str(root / "compose.yml")]
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["docker", "compose", "-p", "voicerig", *files, "logs", "--no-color", "agent"],
                             capture_output=True, text=True).stdout
        if ready_log in out:
            return None
        state = subprocess.run(["docker", "compose", "-p", "voicerig", *files, "ps", "--format", "{{.State}}", "agent"],
                               capture_output=True, text=True).stdout.strip()
        if state in ("exited", "dead"):
            return "the agent container exited before it registered with LiveKit"
        time.sleep(2)
    return f"the agent did not log {ready_log!r} within {timeout:g}s"


def cmd_validate(args) -> int:
    cfg = load(args.config)
    n = sum(len(s.steps) for s in cfg.scenarios.values())
    print(f"ok: {len(cfg.scenarios)} scenarios, {n} steps, agent={'image ' + cfg.agent['image'] if cfg.agent.get('image') else 'build ' + str(cfg.agent['build'])}")
    return 0


def cmd_run(args) -> int:
    cfg = load(args.config)
    if not shutil.which("docker"):
        print("voicerig: docker not found on PATH", file=sys.stderr)
        return 2
    root = compose.write(cfg)
    print(f"voicerig: building the rig image ({compose.RIG_IMAGE}) ...", flush=True)
    _docker("build", "-q", "-t", compose.RIG_IMAGE, str(root / "rig-image"), check=True)
    code = 1
    try:
        print("voicerig: starting LiveKit, the stub backend and your agent ...", flush=True)
        _compose(root, "up", "-d", "--build", "livekit", "stub", "agent")
        print("voicerig: waiting for the agent to register with LiveKit ...", flush=True)
        why = _wait_for_agent(root, cfg.agent.get("ready_log", "registered worker"))
        if why:
            print(f"voicerig: {why}", file=sys.stderr)
            _compose(root, "logs", "--tail", "40", "agent", check=False)
            return 3
        env_flags = [f"-e{k}={v}" for k, v in (("ONLY", args.only or ""), ("ATTEMPTS", str(args.attempts or "")),
                                                ("REPEAT", str(args.repeat or ""))) if v]
        code = _compose(root, "run", "--rm", *env_flags, "client", check=False)
        if code != 0:
            print("\n--- last agent log lines (the failure may be there) ---", flush=True)
            _compose(root, "logs", "--tail", "40", "agent", check=False)
    finally:
        if args.keep:
            print("voicerig: rig left running (--keep). `voicerig logs agent`, `voicerig down`.")
        else:
            _compose(root, "down", "-v", "--remove-orphans", check=False)
    print(f"voicerig: results in {cfg.base / 'voicerig-results' / 'results.json'}")
    return code


def cmd_down(args) -> int:
    cfg = load(args.config, check_audio=False)
    return _compose(cfg.base / ".voicerig", "down", "-v", "--remove-orphans", check=False)


def cmd_logs(args) -> int:
    cfg = load(args.config, check_audio=False)
    return _compose(cfg.base / ".voicerig", "logs", "--tail", "200", args.service, check=False)


def cmd_tts(args) -> int:
    phrases = yaml.safe_load(Path(args.phrases).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        print("voicerig tts: ffmpeg is required to produce 16 kHz mono WAVs", file=sys.stderr)
        return 2
    mac = shutil.which("say")
    if not mac and not shutil.which("espeak-ng"):
        print("voicerig tts: needs macOS `say` or `espeak-ng`; or record WAVs yourself "
              "(16 kHz, mono, 16-bit) and skip this command", file=sys.stderr)
        return 2
    for p in phrases:
        raw = out / f"{p['name']}.tmp.{'aiff' if mac else 'wav'}"
        if mac:
            subprocess.run(["say", "-v", p.get("voice", "Samantha"), "-o", str(raw), p["text"]], check=True)
        else:
            subprocess.run(["espeak-ng", "-v", p.get("voice", "en-us"), "-w", str(raw), p["text"]], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ar", "16000", "-ac", "1",
                        "-sample_fmt", "s16", str(out / f"{p['name']}.wav")], check=True)
        raw.unlink()
        print(f"{p['name']:<16} {p['text']}")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="voicerig", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("run", cmd_run), ("down", cmd_down), ("logs", cmd_logs)):
        p = sub.add_parser(name)
        p.add_argument("-c", "--config", default="rig.yaml")
        p.set_defaults(fn=fn)
        if name == "run":
            p.add_argument("--only", help="comma-separated scenario names")
            p.add_argument("--attempts", type=int, help="override settings.attempts")
            p.add_argument("--repeat", type=int, help="run every selected scenario N times in fresh rooms and report a rate "
                                                     "with a 95%% interval (use with --only)")
            p.add_argument("--keep", action="store_true")
        if name == "logs":
            p.add_argument("service", nargs="?", default="agent")
    t = sub.add_parser("tts")
    t.add_argument("phrases")
    t.add_argument("-o", "--out", default="audio")
    t.set_defaults(fn=cmd_tts)
    args = ap.parse_args(argv)
    try:
        sys.exit(args.fn(args))
    except ConfigError as exc:
        print(f"voicerig: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"voicerig: `{' '.join(map(str, exc.cmd))}` failed with exit {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode or 1)


if __name__ == "__main__":
    main()
