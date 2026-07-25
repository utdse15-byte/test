"""A silent bus and a failed measurement must not look identical.

`manju masters` printed `I=None LUFS  TP=None dBTP` for every stem of a project
with no music/sfx — which is the ORDINARY case — and that reads as breakage.
The numbers were honest (ffmpeg reports -inf for silence, and -inf is not a
level), but the two reasons a level can be absent were indistinguishable:

  * the bus genuinely carries no signal, or
  * the measurement never landed (no ffmpeg, a timeout, an unparseable run).

``measure_loudness`` now records both facts additively, and the CLI renders
them as sentences instead of leaking Python's ``None`` at the owner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from manju.media.ffmpeg import FFMPEG
from manju.media.masters import _is_neg_inf, measure_loudness

pytestmark = pytest.mark.ffmpeg


def _wav(path: Path, source: str, seconds: str = "2") -> Path:
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", source, "-t", seconds,
         "-ar", "48000", str(path)],
        capture_output=True, check=True)
    return path


def test_a_silent_bus_is_reported_as_silent_not_unmeasured(tmp_path: Path) -> None:
    fact = measure_loudness(_wav(tmp_path / "sil.wav", "anullsrc=r=48000:cl=stereo"))
    assert fact["measured"] is True      # ffmpeg ran and answered
    assert fact["silent"] is True        # …and the answer was "no signal"
    assert fact["integrated_lufs"] is None   # -inf is still not a level


def test_a_real_signal_measures_a_real_level(tmp_path: Path) -> None:
    fact = measure_loudness(_wav(tmp_path / "tone.wav", "sine=frequency=440"))
    assert fact["measured"] is True
    assert fact["silent"] is False
    assert isinstance(fact["integrated_lufs"], float)
    assert -70.0 < fact["integrated_lufs"] < 0.0


def test_an_unmeasurable_file_is_neither_silent_nor_measured(tmp_path: Path) -> None:
    """The third case must stay distinct from the other two."""
    junk = tmp_path / "not-audio.wav"
    junk.write_bytes(b"this is not a wav file at all")
    fact = measure_loudness(junk)
    assert fact["measured"] is False
    assert fact["silent"] is False
    assert fact["integrated_lufs"] is None


@pytest.mark.parametrize("value,expected", [
    ("-inf", True), (float("-inf"), True),
    ("inf", False), ("-12.4", False), ("nan", False),
    (None, False), ("", False), ("abc", False),
])
def test_neg_inf_detection(value, expected) -> None:
    assert _is_neg_inf(value) is expected
