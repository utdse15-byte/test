"""战役① ffmpeg 误升级演习(店主核准的真实使用战役,2026-07-31)。

Field origin: with a 7.1 build on PATH, `manju doctor` answered a clean
`✓ ffmpeg: <path>` — no version anywhere, no warning — while DECISIONS
`UX-REAL-USE #33`
records 7.x failing the acrossfade leg intermittently (measured 9/1600 runs
vs 0/1600 on 6.1.1, byte-identical inputs; a prior session lost most of a
day to the cryptic signature). The owner upgrading ffmpeg by accident sees
a green bill of health and then flaky renders with no steer back.

Contract under test (owner of machine facts: media/ffmpeg, record-only):

- doctor always STATES the detected version (a fact, like the path);
- a RECORDED-regression range gets one ⚠ advisory line (7.x acrossfade,
  ≥8 probe drift) — never a gate, exit code unchanged;
- unknown/unparseable versions stay unknown — no advisory is ever guessed;
- the render failure whose stderr matches the recorded signature names the
  regression and the steer, instead of leaving the cryptic line alone.
"""

from __future__ import annotations

import pytest

from manju.media.ffmpeg import (
    PINNED_FFMPEG_VERSION,
    ffmpeg_version_advisory,
    known_failure_advisory,
    parse_ffmpeg_major,
)

PIN_LINE = "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers"
SEVEN_LINE = "ffmpeg version 7.1-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024"
EIGHT_LINE = "ffmpeg version n8.0 Copyright (c) 2000-2025 the FFmpeg developers"
GIT_LINE = "ffmpeg version N-112437-g8b4babcc02 Copyright (c) 2000-2023"


# ------------------------------------------------------------- major parse


@pytest.mark.parametrize("line,major", [
    (PIN_LINE, 6),
    (SEVEN_LINE, 7),
    (EIGHT_LINE, 8),
    ("ffmpeg version 7.0.2-full_build-www.gyan.dev", 7),
    (GIT_LINE, None),      # git snapshot — no dotted release, honest None
    ("missing", None),     # tool absent — the toolchain sentinel
    ("", None),
])
def test_parse_major_is_honest(line, major):
    assert parse_ffmpeg_major(line) == major


# ---------------------------------------------------------------- advisory


def test_pinned_version_gets_no_advisory():
    assert ffmpeg_version_advisory(PIN_LINE) is None


def test_seven_names_the_measured_acrossfade_regression():
    text = ffmpeg_version_advisory(SEVEN_LINE)
    assert text is not None
    assert "acrossfade" in text
    assert PINNED_FFMPEG_VERSION in text  # the steer back to the verified build


def test_eight_notes_probe_drift_without_inventing_a_regression():
    text = ffmpeg_version_advisory(EIGHT_LINE)
    assert text is not None
    assert PINNED_FFMPEG_VERSION in text
    assert "acrossfade" not in text  # 8.x has no recorded acrossfade measurement


@pytest.mark.parametrize("line", [GIT_LINE, "missing", ""])
def test_unknown_versions_are_never_guessed_into_a_warning(line):
    assert ffmpeg_version_advisory(line) is None


def test_older_than_pin_is_not_a_recorded_regression():
    # 5.x has NO recorded measurement — an advisory here would be speculation.
    assert ffmpeg_version_advisory("ffmpeg version 5.1.2 Copyright") is None


# ------------------------------------------------- doctor surfaces the fact


def _doctor_checks(monkeypatch, line):
    import manju.media.ffmpeg as mf
    from manju.build.doctor import run_doctor

    monkeypatch.setattr(mf, "ffmpeg_version_line", lambda: line)
    result = run_doctor(None)
    return {c["name"]: c for c in result["checks"]}, result["ok"]


def test_doctor_states_version_and_stays_quiet_on_pin(monkeypatch):
    checks, _ = _doctor_checks(monkeypatch, PIN_LINE)
    row = checks["ffmpeg_version"]
    assert row["ok"] is True
    assert "6.1.1-3ubuntu5" in row["line"]
    assert not row["line"].startswith("⚠")


def test_doctor_warns_on_seven_but_never_gates(monkeypatch):
    import shutil

    checks, overall = _doctor_checks(monkeypatch, SEVEN_LINE)
    row = checks["ffmpeg_version"]
    assert row["ok"] is True                       # advisory, not a failure
    assert row["line"].startswith("⚠")
    assert "acrossfade" in row["line"]
    assert PINNED_FFMPEG_VERSION in row["line"]
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        assert overall is True                     # exit code untouched


def test_doctor_off_pin_without_recorded_issue_is_an_info_line(monkeypatch):
    checks, _ = _doctor_checks(monkeypatch, "ffmpeg version 6.0 Copyright")
    row = checks["ffmpeg_version"]
    assert row["ok"] is True
    assert row["line"].startswith("•")
    assert PINNED_FFMPEG_VERSION in row["line"]    # states the verified version
    assert "6.0" in row["line"]


def test_doctor_missing_ffmpeg_adds_no_version_row(monkeypatch):
    checks, _ = _doctor_checks(monkeypatch, "missing")
    assert "ffmpeg_version" not in checks          # the ✗ ffmpeg row already speaks


# ------------------------------------- the failure that cost a day, steered


SIG_TAIL = "[aost#0:1/aac] Could not open encoder before EOF\nError muxing a packet"


def test_signature_plus_seven_names_regression_and_steer():
    note = known_failure_advisory(SIG_TAIL, version_line=SEVEN_LINE)
    assert note is not None
    assert "acrossfade" in note
    assert PINNED_FFMPEG_VERSION in note
    assert "7.1-static" in note                    # states what THIS machine runs


def test_signature_on_pin_still_names_the_association_honestly():
    note = known_failure_advisory(SIG_TAIL, version_line=PIN_LINE)
    assert note is not None                        # the association is a fact
    assert PINNED_FFMPEG_VERSION in note


def test_no_signature_no_note():
    assert known_failure_advisory("Invalid data found when processing input",
                                  version_line=SEVEN_LINE) is None
    assert known_failure_advisory("", version_line=SEVEN_LINE) is None


def test_raise_on_bad_exit_appends_the_note(monkeypatch):
    import manju.media.ffmpeg as mf

    monkeypatch.setattr(mf, "ffmpeg_version_line", lambda: SEVEN_LINE)
    with pytest.raises(mf.MediaError) as exc:
        mf._raise_on_bad_exit(None, ["ffmpeg", "-i", "x"], 234, SIG_TAIL,
                              subject="S001", step="transition", log_name="render")
    text = str(exc.value)
    assert "Could not open encoder before EOF" in text   # the raw tail stays
    assert "acrossfade" in text                          # the steer is appended
    assert PINNED_FFMPEG_VERSION in text


def test_raise_on_bad_exit_without_signature_is_byte_identical(monkeypatch):
    import manju.media.ffmpeg as mf

    monkeypatch.setattr(mf, "ffmpeg_version_line", lambda: SEVEN_LINE)
    with pytest.raises(mf.MediaError) as exc:
        mf._raise_on_bad_exit(None, ["ffmpeg", "-i", "x"], 1, "generic error",
                              subject=None, step="render", log_name="render")
    assert "acrossfade" not in str(exc.value)
