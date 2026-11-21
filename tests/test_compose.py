import pytest
import yaml

from voicerig import compose
from voicerig.config import ConfigError, load


def make(rig, extra=""):
    return load(rig(f"""
agent:
  build: {{context: ., dockerfile: Dockerfile}}
  env:
    PROVIDER_TOKEN: ${{PROVIDER_TOKEN:?set PROVIDER_TOKEN}}
    BACKEND_URL: "{{stub_url}}"
{extra}
scenarios: {{s: {{steps: [{{say: 'yes'}}]}}}}
"""))


def test_env_is_passed_to_compose_verbatim_and_placeholders_are_filled(rig):
    c = compose.render(make(rig))
    env = c["services"]["agent"]["environment"]
    assert env["PROVIDER_TOKEN"] == "${PROVIDER_TOKEN:?set PROVIDER_TOKEN}"          # compose expands it; voicerig never sees the value
    assert env["BACKEND_URL"] == "http://172.28.0.11:8080"
    assert env["LIVEKIT_URL"] == "ws://172.28.0.10:7880"


def test_written_files_carry_no_agent_env(rig, tmp_path):
    root = compose.write(make(rig))
    inner = yaml.safe_load((root / "rig.yaml").read_text())
    assert inner["agent"] == {"image": "-"} and "PROVIDER_TOKEN" not in (root / "rig.yaml").read_text()
    assert inner["audio"] == "/rig/audio"


def test_client_and_stub_share_the_secret_free_config(rig):
    c = compose.render(make(rig))["services"]
    assert "./rig.yaml:/rig/rig.yaml:ro" in c["stub"]["volumes"] and "./rig.yaml:/rig/rig.yaml:ro" in c["client"]["volumes"]
    assert "PROVIDER_TOKEN" not in str(c["client"]) and "PROVIDER_TOKEN" not in str(c["stub"])


def test_image_agent_uses_it_directly(rig):
    cfg = load(rig("agent: {image: foo:1}\nscenarios: {s: {steps: [{say: 'yes'}]}}"))
    a = compose.render(cfg)["services"]["agent"]
    assert a["image"] == "foo:1" and "build" not in a


def test_custom_subnet_moves_every_address(rig):
    c = compose.render(make(rig, "network: {subnet: 10.55.3.0/24}"))
    assert c["networks"]["rig"]["ipam"]["config"][0]["subnet"] == "10.55.3.0/24"
    assert c["services"]["agent"]["environment"]["BACKEND_URL"] == "http://10.55.3.11:8080"


def test_bad_subnet_is_rejected(rig):
    with pytest.raises(ConfigError, match="/24"):
        compose.render(make(rig, "network: {subnet: 10.0.0.0/16}"))


def test_default_livekit_image_is_pinned_by_digest(rig):
    # A floating tag means a LiveKit release can change what a run tests without any change here.
    image = compose.render(make(rig))["services"]["livekit"]["image"]
    assert "@sha256:" in image and ":latest" not in image


def test_livekit_image_can_be_overridden(rig):
    c = compose.render(make(rig, "livekit: {image: 'livekit/livekit-server:v1.0.0'}"))
    assert c["services"]["livekit"]["image"] == "livekit/livekit-server:v1.0.0"
