"""Round U: per-boundary transition overrides (``rules.transition_overrides``).

The map is keyed by the OUT-edge segment's id (a shot id, or
``__intro__``/``__outro__``); the compiler places the override verbatim on that
one boundary and everything else keeps ``transition_default``. An explicit
``null`` (or ``type: cut``) is a hard cut. The empty default is byte-identical:
same compiled timeline, same CompileInput fingerprint as before the field
existed. Unknown keys are inert at compile time and a QC advisory.
"""

from __future__ import annotations

from manju.core.models import (
    PackagingCard,
    PackagingSpec,
    ProjectConfig,
    ShotSpec,
    TimelineRules,
    TransitionSpec,
)
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline


def _shot_input(sid: str) -> ShotInput:
    return ShotInput(
        shot=ShotSpec.model_validate({"id": sid}),
        take_name="take_01",
        take_source=f"media/gen/{sid}/take_01.mp4",
        take_duration_ms=2000,
    )


def _compile(rules: TimelineRules, packaging: PackagingSpec | None = None):
    return compile_timeline(CompileInput(
        config=ProjectConfig(name="t"), rules=rules,
        shots=[_shot_input("S001"), _shot_input("S002"), _shot_input("S003")],
        packaging=packaging,
    ))


# ------------------------------------------------------------- placement


def test_override_lands_on_that_out_edge_only():
    rules = TimelineRules(
        transition_overrides={"S002": TransitionSpec(type="xfade_fade", duration_ms=400)},
    )
    tl = _compile(rules)
    clips = tl.tracks.video
    # S001 keeps the default (fade/300), S002 carries the override, the true
    # last segment never has a transition_out.
    assert clips[0].transition_out is not None and clips[0].transition_out.type == "fade"
    assert clips[1].transition_out is not None
    assert clips[1].transition_out.type == "xfade_fade"
    assert clips[1].transition_out.duration_ms == 400
    assert clips[2].transition_out is None


def test_null_override_is_a_hard_cut():
    rules = TimelineRules(transition_overrides={"S001": None})
    tl = _compile(rules)
    clips = tl.tracks.video
    assert clips[0].transition_out is None  # explicit null = cut
    assert clips[1].transition_out is not None  # others keep the default


def test_cut_type_override_placed_verbatim():
    # The render treats type "cut" as no fade + no xfade; the compiler records
    # the request verbatim (same stance as TRANSITION_TYPES).
    rules = TimelineRules(transition_overrides={"S001": TransitionSpec(type="cut", duration_ms=0)})
    tl = _compile(rules)
    assert tl.tracks.video[0].transition_out.type == "cut"


def test_intro_out_edge_override():
    rules = TimelineRules(
        transition_overrides={"__intro__": TransitionSpec(type="xfade_wipeleft", duration_ms=500)},
    )
    pkg = PackagingSpec(intro=PackagingCard(enabled=True, text="片头", duration_ms=2000))
    tl = _compile(rules, pkg)
    intro = tl.tracks.video[0]
    assert intro.shot == "__intro__"
    assert intro.transition_out.type == "xfade_wipeleft"


def test_override_on_last_segment_is_ignored():
    rules = TimelineRules(transition_overrides={"S003": TransitionSpec(type="xfade_fade")})
    tl = _compile(rules)
    assert tl.tracks.video[-1].transition_out is None


def test_unknown_key_is_inert_at_compile():
    rules = TimelineRules(transition_overrides={"NOPE": TransitionSpec(type="xfade_fade")})
    tl = _compile(rules)
    types = [c.transition_out.type for c in tl.tracks.video if c.transition_out]
    assert types == ["fade", "fade"]  # defaults untouched, no crash


# --------------------------------------------------- byte-identity discipline


def test_empty_overrides_keep_timeline_and_fingerprint_identical():
    plain = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                         shots=[_shot_input("S001"), _shot_input("S002")])
    tl_plain = compile_timeline(plain)

    # The pre-round-U fingerprint formula: rules dump WITHOUT the new key.
    from manju.core.hashing import hash_value

    rules_dump = TimelineRules().model_dump()
    assert rules_dump.pop("transition_overrides") == {}
    legacy_payload = {
        "fps": plain.config.fps,
        "width": plain.config.width,
        "height": plain.config.height,
        "rules": rules_dump,
        "shots": [
            {
                "id": s.shot.id,
                "duration": s.shot.duration,
                "dialogue": s.shot.dialogue.model_dump(),
                "take": s.take_name,
                "take_source": s.take_source,
                "take_duration_ms": s.take_duration_ms,
                "voice_source": s.voice_source,
                "voice_duration_ms": s.voice_duration_ms,
                "voice_timing": s.voice_timing,
            }
            for s in plain.shots
        ],
    }
    assert plain.fingerprint() == hash_value(legacy_payload)
    assert tl_plain.meta.compiled_from == plain.fingerprint()


def test_fingerprint_changes_only_when_override_set():
    base = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                        shots=[_shot_input("S001"), _shot_input("S002")])
    empty = CompileInput(config=ProjectConfig(name="t"),
                         rules=TimelineRules(transition_overrides={}),
                         shots=[_shot_input("S001"), _shot_input("S002")])
    overridden = CompileInput(
        config=ProjectConfig(name="t"),
        rules=TimelineRules(transition_overrides={"S001": TransitionSpec(type="xfade_fade")}),
        shots=[_shot_input("S001"), _shot_input("S002")],
    )
    assert base.fingerprint() == empty.fingerprint()
    assert base.fingerprint() != overridden.fingerprint()


def test_yaml_roundtrip_null_and_cut():
    # The truth-is-text shapes a director actually writes.
    data = {
        "transition_overrides": {
            "S001": None,
            "S002": {"type": "cut"},
            "S003": {"type": "xfade_slideleft", "duration_ms": 600},
        }
    }
    rules = TimelineRules.model_validate(data)
    assert rules.transition_overrides["S001"] is None
    assert rules.transition_overrides["S002"].type == "cut"
    assert rules.transition_overrides["S003"].duration_ms == 600


# ----------------------------------------------------------------- QC layer


def test_qc_flags_unknown_override_key(tmp_project, add_shot):
    import yaml

    add_shot(tmp_project, "S001")  # known id → the bad-type advisory path
    rules_path = tmp_project.rules_path
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    data["transition_overrides"] = {
        "GHOST": {"type": "xfade_fade"},
        "S001": {"type": "hyperspace_jump"},
    }
    rules_path.write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    from manju.qc.checks import run_qc

    report = run_qc(tmp_project, timeline=None, extract_frames=False)
    msgs = [i.message for i in report.items]
    assert any("GHOST" in m and "不会生效" in m for m in msgs), msgs
    assert any("hyperspace_jump" in m for m in msgs), msgs
