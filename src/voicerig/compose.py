"""Turn a rig.yaml into a docker-compose project: a local LiveKit server, a stub backend, YOUR agent, and the
rig client. Nothing here talks to any cloud.

The generated files live in `<dir of rig.yaml>/.voicerig/` and contain no secrets: `agent.env` values are
emitted verbatim and docker compose expands `${VAR}` from your shell when it starts the containers.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .config import Config, ConfigError

RIG_IMAGE = f"voicerig-rig:{__version__}"
AGENT_TAG = "voicerig-agent-under-test"

# Versions the rig was developed against; the LiveKit *server* image is configurable (`livekit.image`).
DOCKERFILE = """\
FROM python:3.12-slim
RUN pip install --no-cache-dir livekit==1.1.13 livekit-api==1.2.0 aiohttp==3.14.3 pyyaml==6.0.3
COPY voicerig /app/voicerig
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
"""


def addresses(cfg: Config) -> dict[str, str]:
    subnet = cfg.network.get("subnet", "172.28.0.0/24")
    parts = subnet.split("/")
    octets = parts[0].split(".")
    if len(parts) != 2 or parts[1] != "24" or len(octets) != 4 or octets[3] != "0":
        raise ConfigError(f"network.subnet: expected a /24 like 172.28.0.0/24, got {subnet!r}")
    base = ".".join(octets[:3])
    return {"subnet": subnet, "livekit": f"{base}.10", "stub": f"{base}.11", "agent": f"{base}.12", "client": f"{base}.13"}


def build_context(cfg: Config) -> Path:
    """<rig.yaml dir>/.voicerig/ with the rig image's build context, a secret-free copy of the config and compose.yml."""
    root = cfg.base / ".voicerig"
    ctx = root / "rig-image"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir(parents=True)
    shutil.copytree(Path(__file__).parent, ctx / "voicerig", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (ctx / "Dockerfile").write_text(DOCKERFILE)
    # The containers need transcripts/stub/scenarios/settings. They do not need (and must not receive) agent.env.
    inner = {k: v for k, v in cfg.raw.items() if k not in ("agent", "livekit", "network")}
    inner["agent"] = {"image": "-"}
    inner["audio"] = "/rig/audio"
    (root / "rig.yaml").write_text(yaml.safe_dump(inner, sort_keys=False, allow_unicode=True))
    (cfg.base / "voicerig-results").mkdir(exist_ok=True)
    return root


def render(cfg: Config) -> dict[str, Any]:
    ip = addresses(cfg)
    lk_url, stub_url = f"ws://{ip['livekit']}:7880", f"http://{ip['stub']}:8080"
    sub = {"{stub_url}": stub_url, "{livekit_url}": lk_url}

    def fill(v: Any) -> str:
        s = str(v)
        for k, r in sub.items():
            s = s.replace(k, r)
        return s

    agent_env = {"LIVEKIT_URL": lk_url, "LIVEKIT_API_KEY": "devkey", "LIVEKIT_API_SECRET": "secret"}
    agent_env.update({k: fill(v) for k, v in (cfg.agent.get("env") or {}).items()})

    agent: dict[str, Any] = {
        "depends_on": ["livekit", "stub"],
        "environment": agent_env,
        "networks": {"rig": {"ipv4_address": ip["agent"]}},
    }
    if cfg.agent.get("image"):
        agent["image"] = cfg.agent["image"]
    else:
        b = cfg.agent["build"]
        agent["image"] = AGENT_TAG
        agent["build"] = {"context": str((cfg.base / b.get("context", ".")).resolve()),
                          **({"dockerfile": b["dockerfile"]} if b.get("dockerfile") else {}),
                          **({"args": b["args"]} if b.get("args") else {})}
    if cfg.agent.get("command"):
        agent["command"] = cfg.agent["command"]

    rig_common = {"image": RIG_IMAGE, "volumes": ["./rig.yaml:/rig/rig.yaml:ro"]}
    client_env = {"LIVEKIT_URL": lk_url, "LIVEKIT_API_KEY": "devkey", "LIVEKIT_API_SECRET": "secret",
                  "STUB_URL": stub_url, "RIG_CONFIG": "/rig/rig.yaml", "OUT_DIR": "/rig/out"}
    return {
        "name": "voicerig",
        "networks": {"rig": {"ipam": {"config": [{"subnet": ip["subnet"]}]}}},
        "services": {
            "livekit": {
                "image": cfg.livekit.get("image", "livekit/livekit-server:latest"),
                "command": ["--dev", "--bind", "0.0.0.0", "--node-ip", ip["livekit"]],
                "networks": {"rig": {"ipv4_address": ip["livekit"]}},
            },
            "stub": {**rig_common, "command": ["python", "-m", "voicerig.stub"],
                     "networks": {"rig": {"ipv4_address": ip["stub"]}}},
            "agent": agent,
            "client": {**rig_common, "profiles": ["client"], "depends_on": ["agent", "stub"],
                       "command": ["python", "-m", "voicerig.client"], "environment": client_env,
                       "volumes": rig_common["volumes"] + [f"{cfg.audio_dir}:/rig/audio:ro", f"{cfg.base / 'voicerig-results'}:/rig/out"],
                       "networks": {"rig": {"ipv4_address": ip["client"]}}},
        },
    }


def write(cfg: Config) -> Path:
    root = build_context(cfg)
    (root / "compose.yml").write_text(yaml.safe_dump(render(cfg), sort_keys=False))
    return root
