import pytest

from tests.conftest import write_wav
from voicerig.config import ConfigError, load

BASE = """
agent: {image: my-agent:latest}
scenarios:
  s:
    steps:
%s
"""


def cfg(rig, steps: str, head: str = ""):
    return load(rig(head + BASE % steps))


def test_minimal_valid(rig):
    c = cfg(rig, "      - say: 'yes'\n      - expect_agent_says: {question: true}")
    assert [s.kind for s in c.scenarios["s"].steps] == ["say", "agent_says"]
    assert c.scenarios["s"].steps[1].level == "expect"


def test_agent_needs_exactly_one_of_image_or_build(rig):
    with pytest.raises(ConfigError, match="exactly one"):
        load(rig("agent: {}\nscenarios: {s: {steps: [{say: 'yes'}]}}"))
    with pytest.raises(ConfigError, match="exactly one"):
        load(rig("agent: {image: a, build: {context: .}}\nscenarios: {s: {steps: [{say: 'yes'}]}}"))


@pytest.mark.parametrize("step, msg", [
    ("- sya: yes", "unknown step"),
    ("- expect_agent_says: {matches: 'x', witin: 3}", "does not take"),
    ("- expect_agent_says: {within: 3}", "needs `matches"),
    ("- expect_agent_says: {matches: '('}", "not a valid regex"),
    ("- expect_call: {within: 3}", "needs `path`"),
    ("- expect_no_call: {for: -1}", "outside"),
    ("- wait: soon", "expected a number"),
    ("- expect_call: {path: /x, since: later}", "since must be"),
    ("- say: nonexistent", "does not exist"),
    ("- {say: yes, expect_call: {path: /x}}", "one-key mapping"),
    ("- expect_tool_call: {within: 3}", "needs `name`"),
    ("- expect_no_tool_call: {for: 3}", "needs `name`"),
    ("- expect_tool_call: {name: x, args_contains: nope}", "must be a mapping"),
    ("- expect_tool_call: {name: x, path: /y}", "does not take"),
])
def test_bad_steps_name_the_problem(rig, step, msg):
    with pytest.raises(ConfigError, match=msg):
        cfg(rig, f"      {step}")


def test_bare_yes_is_the_yaml_boolean_trap(rig):
    with pytest.raises(ConfigError, match="boolean.*Quote"):
        cfg(rig, "      - say: yes")
    with pytest.raises(ConfigError, match="boolean.*Quote"):
        cfg(rig, "      - say: no")


def test_error_names_the_step(rig):
    with pytest.raises(ConfigError, match=r"scenarios\.s\.steps\[1\]"):
        cfg(rig, "      - say: 'yes'\n      - expect_call: {within: 3}")


def test_wrong_wav_format_is_rejected_with_the_fix(rig, tmp_path):
    write_wav(tmp_path / "audio" / "stereo.wav", rate=44100, channels=2)
    with pytest.raises(ConfigError, match="ffmpeg"):
        cfg(rig, "      - say: stereo")


def test_unknown_top_level_key(rig):
    with pytest.raises(ConfigError, match="unknown top-level"):
        load(rig("agent: {image: a}\nscneraios: {}\nscenarios: {s: {steps: [{say: 'yes'}]}}"))


def test_data_transcripts_need_a_topic(rig):
    with pytest.raises(ConfigError, match="needs `topic`"):
        cfg(rig, "      - say: 'yes'", head="transcripts: {source: data}\n")


def test_no_scenarios_is_an_error(rig):
    with pytest.raises(ConfigError, match="at least one"):
        load(rig("agent: {image: a}\nscenarios: {}"))
