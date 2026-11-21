import struct
import wave
from pathlib import Path

import pytest


def write_wav(path: Path, rate: int = 16000, channels: int = 1, seconds: float = 0.1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds) * channels)


@pytest.fixture
def rig(tmp_path):
    """A directory with audio/yes.wav and a helper to write a rig.yaml next to it."""
    (tmp_path / "audio").mkdir()
    write_wav(tmp_path / "audio" / "yes.wav")

    def write(text: str) -> Path:
        p = tmp_path / "rig.yaml"
        p.write_text(text)
        return p
    return write
