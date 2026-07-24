"""External-audit ledger P1 — timeline compiler.

LOCALE-P1-010  a locale voice clip carried the BASE take's duration, so the
               render's ``apad``/``atrim`` silently cut (or padded) the dub.
TIMELINE-P1-001 caption alignment dropped inter-token whitespace and every
               character before the first matched token.
"""

from __future__ import annotations

import pytest

from manju.core.models import ProjectConfig, ShotSpec, TimelineRules
from manju.core.yamlio import write_yaml
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    _align_words_to_text,
    _timed_captions,
    compile_timeline,
    voice_overrun_warnings,
)

# ============================================================ TIMELINE-P1-001
# Reconstruction corpus: (word tokens, original dialogue text). Every shape the
# TTS word stream really produces — the enriched texts concatenated must equal
# the source text character for character.
ALIGN_CORPUS: list[tuple[list[str], str]] = [
    # pure CJK, punctuation dropped by the engine
    (["凌晨", "三点", "还有人醒着吗", "别挂断"], "凌晨三点,还有人醒着吗?别挂断。"),
    # pure English — the spaces BETWEEN tokens are text, not noise
    (["Hello", "there", "friend"], "Hello there friend."),
    # mixed CJK/latin
    (["林夏", "said", "别动"], "林夏 said:别动。"),
    # a bracketed prefix before the first token
    (["Nobody", "moves"], "[SFX] Nobody moves!"),
    # a speaker label before the first token
    (["Alice", "is", "late"], "Alice: Alice is late"),
    # runs of whitespace between tokens
    (["one", "two"], "one    two"),
    # leading AND trailing whitespace
    (["core"], "   core   "),
    # a token that repeats before its real position
    (["go", "go"], "go, go!"),
]


def _words(tokens: list[str]) -> list[dict]:
    return [{"start_ms": i * 100, "end_ms": i * 100 + 90, "text": t}
            for i, t in enumerate(tokens)]


@pytest.mark.parametrize("tokens,text", ALIGN_CORPUS)
def test_alignment_is_character_exact(tokens: list[str], text: str) -> None:
    """The enriched cue texts joined in order reproduce the dialogue EXACTLY —
    no swallowed spaces, no lost prefix."""
    enriched = _align_words_to_text(_words(tokens), text)
    assert "".join(str(w["text"]) for w in enriched) == text


def test_alignment_keeps_head_before_the_first_token() -> None:
    enriched = _align_words_to_text(_words(["Nobody", "moves"]), "[SFX] Nobody moves!")
    assert str(enriched[0]["text"]).startswith("[SFX] ")


def test_alignment_keeps_inter_word_spaces() -> None:
    enriched = _align_words_to_text(_words(["Hello", "there"]), "Hello there")
    assert [str(w["text"]) for w in enriched] == ["Hello ", "there"]


def test_alignment_cjk_punctuation_reattachment_unchanged() -> None:
    """The round-M behaviour this owns must not move: dropped CJK punctuation
    still glues onto the PRECEDING word."""
    bare = _words(["凌晨", "三点", "还有人醒着吗", "别挂断"])
    enriched = _align_words_to_text(bare, "凌晨三点,还有人醒着吗?别挂断。")
    assert [str(w["text"]) for w in enriched] == ["凌晨", "三点,", "还有人醒着吗?", "别挂断。"]


def test_alignment_mismatch_still_falls_back_to_raw() -> None:
    words = _words(["完全不同"])
    assert _align_words_to_text(words, "对不上的台词") == words


def test_timed_captions_break_on_latin_sentence_ender_with_trailing_space() -> None:
    """A restored trailing space must not hide the sentence ender from the
    eager cue break (the ender is the LAST non-space character)."""
    words = _words(["Run", "now", "Stop"])
    cues = _timed_captions(words, offset_ms=0, max_chars=40, max_lines=2,
                           speaker="alice", original_text="Run now! Stop.")
    assert [c.text for c in cues] == ["Run now!", "Stop."]


def test_timed_captions_lose_no_characters_beyond_edge_trimming() -> None:
    """Cue texts are edge-stripped per cue, so the recoverable invariant is
    equality after collapsing whitespace runs."""
    text = "[SFX] Run now!   Stop. 别挂断。"
    cues = _timed_captions(_words(["Run", "now", "Stop", "别挂断"]), offset_ms=0,
                           max_chars=40, max_lines=2, speaker="alice",
                           original_text=text)
    joined = " ".join(c.text for c in cues)
    assert joined.split() == text.split()


# ============================================================= LOCALE-P1-010

def _shot_input(**overrides) -> ShotInput:
    base = dict(
        shot=ShotSpec.model_validate({
            "id": "S001",
            "dialogue": {"speaker": "chen", "text": "凌晨三点。"},
        }),
        take_name="take_01", take_source="media/gen/S001/take_01.mp4",
        take_duration_ms=4000, voice_source="media/gen/S001/voice_take_01.wav",
        voice_duration_ms=1000,
    )
    base.update(overrides)
    return ShotInput(**base)


def _compile(**overrides):
    return compile_timeline(CompileInput(
        config=ProjectConfig(name="t"), rules=TimelineRules(),
        shots=[_shot_input(**overrides)],
    ))


def test_picture_voice_duration_absent_keeps_todays_fingerprint() -> None:
    """Byte-identity pin: the new picture-voice field folds into the compile
    fingerprint ONLY when set, so every non-locale project keeps the hash it
    had before LOCALE-P1-010 landed (captured from the pre-fix compiler)."""
    inp = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                       shots=[_shot_input()])
    assert inp.fingerprint() == (
        "sha256:ac3924e47b2854e69e2d8063d6ced22b94f0fce6c87f5605cf62f9ef65d4d09b"
    )


def test_picture_voice_duration_changes_the_fingerprint_when_set() -> None:
    a = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                     shots=[_shot_input(voice_duration_ms=3000)])
    b = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                     shots=[_shot_input(voice_duration_ms=3000,
                                        picture_voice_duration_ms=1000)])
    assert a.fingerprint() != b.fingerprint()


def test_locale_voice_clip_carries_its_own_length() -> None:
    """The voice CLIP length is the length of the file the clip points at —
    never a foreign take's. Picture stays on the base take (segment cache)."""
    tl = _compile(voice_duration_ms=3000, picture_voice_duration_ms=1000)
    assert tl.tracks.voice[0].duration_ms == 3000
    # picture window still comes from the base voice + padding, snapped
    base = _compile(voice_duration_ms=1000)
    assert tl.tracks.video[0].duration_ms == base.tracks.video[0].duration_ms


def test_locale_caption_span_follows_the_locale_voice() -> None:
    tl = _compile(voice_duration_ms=3000, picture_voice_duration_ms=1000)
    cue = tl.tracks.captions[-1]
    assert cue.end_ms == 200 + 3000  # padding_before + the locale voice length


def test_voice_overrun_is_reported_not_silent() -> None:
    tl = _compile(voice_duration_ms=3000, picture_voice_duration_ms=1000)
    warns = voice_overrun_warnings(tl)
    assert len(warns) == 1
    assert "S001" in warns[0]


def test_voice_that_fits_the_picture_window_warns_about_nothing() -> None:
    assert voice_overrun_warnings(_compile(voice_duration_ms=1000)) == []


# ------------------------------------------------- project-level locale wiring

def _voice(project, shot_id: str, duration_ms: int, *, lang: str | None = None):
    """Register a voice take WITH a synthesis probe cache, so the compiler
    reads the duration from the sidecar and never needs a live ffprobe."""
    tdir = project.takes_dir(shot_id)
    if lang:
        tdir = tdir / "locales" / lang
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "voice_take_01.wav").write_bytes(b"voice-" + str(duration_ms).encode("ascii"))
    write_yaml(tdir / "voice_take_01.sidecar.yaml", {
        "provider": "test", "voice_hash": f"h{duration_ms}",
        "probe": {"duration_ms": duration_ms},
    })


def test_gather_keeps_locale_voice_length_and_base_picture(
    tmp_project, add_shot, make_take
) -> None:
    from manju.core.locale import add_locale, load_lines
    from manju.core.spec import compute_spec_hash
    from manju.timeline.compiler import gather_compile_input

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "凌晨三点。"})
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    _voice(tmp_project, "S001", 1000)
    _voice(tmp_project, "S001", 3000, lang="en")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Three in the morning."
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    cinp = gather_compile_input(tmp_project, lambda p: 4000, lang="en")
    [entry] = cinp.shots
    assert "locales/en" in entry.voice_source.replace("\\", "/")
    assert entry.voice_duration_ms == 3000       # the clip's OWN file
    assert entry.picture_voice_duration_ms == 1000  # base still fixes picture

    tl = compile_timeline(cinp)
    assert tl.tracks.voice[0].duration_ms == 3000
    assert voice_overrun_warnings(tl)

    # base compile: unchanged in every respect
    base = gather_compile_input(tmp_project, lambda p: 4000)
    assert base.shots[0].picture_voice_duration_ms is None
    assert base.shots[0].voice_duration_ms == 1000
    assert compile_timeline(base).tracks.video[0].duration_ms == \
        tl.tracks.video[0].duration_ms


def test_gather_without_a_locale_take_is_unchanged(
    tmp_project, add_shot, make_take
) -> None:
    """No locale voice → the base take is used for BOTH slots and no picture
    override is recorded (byte-identical to the pre-fix compile)."""
    from manju.core.spec import compute_spec_hash
    from manju.timeline.compiler import gather_compile_input

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "凌晨三点。"})
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    _voice(tmp_project, "S001", 1000)

    cinp = gather_compile_input(tmp_project, lambda p: 4000, lang="en")
    [entry] = cinp.shots
    assert entry.voice_duration_ms == 1000
    assert entry.picture_voice_duration_ms is None
    assert cinp.fingerprint() == gather_compile_input(
        tmp_project, lambda p: 4000
    ).fingerprint()


# ------------------------------------------------------ build/graph.py wiring

def test_build_graph_surfaces_the_voice_overrun_warnings() -> None:
    """The compiler can DERIVE the overrun, but the owner only ever sees
    BuildResult.warnings — an unwired pure function is exactly the shape this
    audit wave kept finding (guards that existed and were reached by nothing).

    A source pin rather than a driven build: the extend sits after the three
    timeline branches and before captions/render, so reaching it for real means
    a full ffmpeg render. tests/test_cycle19_locale_budget_skip.py pins
    graph.py the same way, and the BEHAVIOUR is covered above against a real
    compiled timeline."""
    from pathlib import Path

    import manju.build.graph as G

    src = Path(G.__file__).read_text(encoding="utf-8")
    assert "voice_overrun_warnings" in src, "graph.py never calls the warner"
    assert "result.warnings.extend(voice_overrun_warnings(timeline))" in src
    # …and it must run for EVERY branch, i.e. after the if/elif/else converges,
    # not inside one of them: the captions phase is the first thing past the
    # join point.
    assert src.index("result.warnings.extend(voice_overrun_warnings(timeline))") \
        < src.index("# ---- 4. captions")


def test_qc_reports_the_voice_overrun_as_a_technical_warning() -> None:
    """qc.md is where the owner reviews before delivery, so it must not be the
    one surface that stays quiet about a line the render will hard-cut."""
    from manju.qc.checks import QCReport, _technical_timeline_conflicts

    tl = _compile(voice_duration_ms=3000, picture_voice_duration_ms=1000)
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    hits = [i for i in report.items if i.level == "warn" and "S001" in i.message]
    assert hits, [i.message for i in report.items]
    assert hits[0].suggestion


def test_qc_stays_quiet_when_every_voice_fits() -> None:
    from manju.qc.checks import QCReport, _technical_timeline_conflicts

    report = QCReport()
    _technical_timeline_conflicts(None, report, _compile(voice_duration_ms=1000))
    assert not [i for i in report.items if i.level == "warn"]
