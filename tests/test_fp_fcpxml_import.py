"""FP Loop W2 — FCPXML IMPORT-PLAN (the analysis half T2/V1 honestly declined).

Red-first (§20): this file pins, from FIRST PRINCIPLES, the read-only FCPXML
import-plan that mirrors the house openclap precedent
(``exporters/openclap/import_plan.py``): parse a FOREIGN FCPXML document
read-only into a neutral description (EXACT ``Fraction`` seconds — never floats)
and DERIVE an advisory plan (proposed windows / transition candidates / audio
bus suggestions / needs-relink rows) that writes nothing and applies nothing.

The star pin is the ROUND-TRIP FLOOR: a timeline compiled with T2 + V1 features
(``compile_fcpxml``) → ``parse_fcpxml`` → ``plan_fcpxml_import`` reproduces every
window / transition / audio suggestion EXACTLY (frames derived from the
document's own frameDuration, exact fractions preserved). Every golden frame
count is derived inline from the frame math so the test encodes the SPEC.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import (
    AudioClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
    EditRate,
)
from manju.core.hashing import hash_text
from manju.core.timebase import Rate, ms_to_frames
from manju.exporters.fcpxml import compile_fcpxml
from manju.exporters.fcpxml_import import (
    PLAN_SCHEMA,
    FcpxmlImportError,
    parse_fcpxml,
    plan_fcpxml_import,
    verify_plan_digest,
)

R24 = Rate.from_fraction(24, 1)
runner = CliRunner()


# --------------------------------------------------------------------------- #
# fixtures — reuse the writer to generate round-trip inputs                    #
# --------------------------------------------------------------------------- #


def _vid(shot, take, start_ms, duration_ms, *, source_in_ms=0, transition=None,
         duration_frames=None):
    return VideoClip(
        shot=shot, take=take, source=f"media/gen/{shot}/{take}.mp4",
        start_ms=start_ms, duration_ms=duration_ms, source_in_ms=source_in_ms,
        transition_out=transition, duration_frames=duration_frames,
    )


def _aud(source, start_ms, duration_ms=None, **kw):
    return AudioClip(source=source, start_ms=start_ms, duration_ms=duration_ms, **kw)


def _tl(video, *, voice=(), music=(), sfx=(), ambient=(), fps=24, rate_echo=None):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-fcpxml-import"),
        fps=fps, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in video) or 0,
        rate_echo=rate_echo,
        tracks=TimelineTracks(video=list(video), voice=list(voice),
                              music=list(music), sfx=list(sfx), ambient=list(ambient)),
    )


def _three_clip_timeline():
    """clip1 --xfade_fade(500ms clean)--> clip2 --fade(300ms degraded)--> clip3."""
    return _tl([
        _vid("S001", "TAKEA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
        _vid("S002", "TAKEB", 2000, 2000, source_in_ms=125,
             transition=TransitionSpec(type="fade", duration_ms=300)),
        _vid("S003", "TAKEC", 4000, 1000),
    ])


def _audio_timeline():
    return _tl(
        [
            _vid("S001", "TAKEA", 0, 2000,
                 transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
            _vid("S002", "TAKEB", 2000, 2000, source_in_ms=250),
        ],
        voice=[_aud("media/voice/line.wav", 500, 1000)],
        music=[_aud("media/music/bed.mp3", 2500, 1000, gain_db=-6.0)],
        sfx=[_aud("media/sfx/hit.wav", 3000, 1000, start_offset_ms=125)],
    )


def _plan_from_timeline(tl, rate=R24, name="X", **kw):
    xml = compile_fcpxml(tl, rate=rate, name=name)
    parsed = parse_fcpxml(xml)
    return plan_fcpxml_import(parsed, source_sha256=hash_text(xml), **kw), parsed, xml


# --------------------------------------------------------------------------- #
# 1. ROUND-TRIP FLOOR — windows reproduce exactly (frames via frameDuration)   #
# --------------------------------------------------------------------------- #


def test_roundtrip_windows_reproduced_exactly():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline(), name="DEMO")
    # Frame math @24 (frameDuration 1/24s): durs 48/48/24, in 125ms->3;
    # dissolve 500ms->12 pulls clip2/3 back: offsets 0, 36, 84.
    got = [(w["name"], w["offset_frames"], w["start_frames"], w["duration_frames"])
           for w in plan["windows"]]
    assert got == [
        ("S001", 0, 0, 48),
        ("S002", 36, 3, 48),
        ("S003", 84, 0, 24),
    ]
    # every window points at its project-relative src, classified inside-project
    assert [w["src"] for w in plan["windows"]] == [
        "media/gen/S001/TAKEA.mp4", "media/gen/S002/TAKEB.mp4",
        "media/gen/S003/TAKEC.mp4"]
    assert all(w["media_status"] == "inside_project" for w in plan["windows"])
    assert plan["needs_relink"] == []


def test_roundtrip_windows_keep_exact_fraction_seconds():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline())
    w = plan["windows"][1]  # S002: offset 36/24 == 3/2 s, start 3/24 == 1/8 s
    assert w["offset_seconds"] == "3/2"
    assert w["start_seconds"] == "1/8"
    assert w["duration_seconds"] == "2"          # 48/24 == 2s


def test_roundtrip_transition_is_xfade_fade_candidate():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline())
    # the degraded 'fade' became an XML comment (dropped on parse); only the
    # one native Cross Dissolve survives as an element -> one candidate row.
    assert len(plan["transitions"]) == 1
    t = plan["transitions"][0]
    assert t["disposition"] == "xfade_fade_candidate"
    assert t["proposed_type"] == "xfade_fade"
    assert t["at_clip"] == "S002"
    assert t["offset_frames"] == 36 and t["duration_frames"] == 12  # 500ms->12
    assert t["effect_uid"] == "FFVideoTransitionCrossDissolve"


# --------------------------------------------------------------------------- #
# 2. ROUND-TRIP FLOOR — connected audio → bus suggestions (INVERTED role map)  #
# --------------------------------------------------------------------------- #


def test_roundtrip_audio_bus_suggestions_reproduced_exactly():
    plan, _, _ = _plan_from_timeline(_audio_timeline())
    rows = {r["src"]: r for r in plan["audio_suggestions"]}
    # A voice @500ms->F12 under clip0; role dialogue INVERTS to bus voice
    v = rows["media/voice/line.wav"]
    assert (v["disposition"], v["bus"], v["audio_role"], v["lane"]) == (
        "bus_mapping", "voice", "dialogue", -1)
    assert (v["parent_clip"], v["offset_frames"], v["start_frames"],
            v["duration_frames"], v["gain"]) == ("S001", 12, 0, 24, None)
    # B music @2500ms->F60 under the pulled-back clip1; child offset 30
    m = rows["media/music/bed.mp3"]
    assert (m["bus"], m["audio_role"], m["lane"], m["parent_clip"]) == (
        "music", "music", -2, "S002")
    assert (m["offset_frames"], m["duration_frames"], m["gain"]) == (30, 24, "-6dB")
    # C sfx @3000ms->F72 under clip1, in-point 125ms->3
    s = rows["media/sfx/hit.wav"]
    assert (s["bus"], s["audio_role"], s["lane"]) == ("sfx", "effects.sfx", -3)
    assert (s["offset_frames"], s["start_frames"], s["duration_frames"]) == (42, 3, 24)


def test_roundtrip_full_t2_v1_reproduces_every_suggestion():
    """The floor in one shot: windows + transition + all three audio buses."""
    plan, _, _ = _plan_from_timeline(_audio_timeline())
    assert len(plan["windows"]) == 2
    assert len(plan["transitions"]) == 1
    assert len(plan["audio_suggestions"]) == 3
    assert {r["bus"] for r in plan["audio_suggestions"]} == {"voice", "music", "sfx"}
    assert all(r["disposition"] == "bus_mapping" for r in plan["audio_suggestions"])


def test_ambient_role_inverts_to_ambient_bus():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             ambient=[_aud("media/a/room.wav", 0, 1000)])  # non-loop, resolvable
    plan, _, _ = _plan_from_timeline(tl)
    row = plan["audio_suggestions"][0]
    assert (row["bus"], row["audio_role"], row["lane"]) == ("ambient", "effects.ambient", -4)


# --------------------------------------------------------------------------- #
# 3. EXACT FRACTION SECONDS — the rational-native 1001 family, never floats    #
# --------------------------------------------------------------------------- #


def test_rational_1001_family_parses_exact_fractions():
    tl = _tl(
        [_vid("A", "TA", 0, 2002, duration_frames=48),
         _vid("B", "TB", 2002, 2002, duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    xml = compile_fcpxml(tl, rate=tl.frame_rate, name="RAT")
    parsed = parse_fcpxml(xml)
    # frameDuration 1001/24000s is held as an EXACT Fraction (never a float)
    assert parsed.frame_duration == Fraction(1001, 24000)
    assert isinstance(parsed.frame_duration, Fraction)
    assert parsed.rate == Rate.from_fraction(24000, 1001)
    plan = plan_fcpxml_import(parsed, source_sha256=hash_text(xml))
    # 48 frames == 48048/24000s; offsets telescope exactly, frames come out whole
    assert plan["windows"][0]["duration_seconds"] == "1001/500"   # 48048/24000 reduced
    assert [w["offset_frames"] for w in plan["windows"]] == [0, 48]
    assert [w["duration_frames"] for w in plan["windows"]] == [48, 48]
    assert plan["frame_duration_seconds"] == "1001/24000"
    assert plan["edit_rate"] == "24000/1001"


def test_no_float_anywhere_in_time_fields():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline())
    for w in plan["windows"]:
        for k in ("offset_seconds", "start_seconds", "duration_seconds"):
            assert isinstance(w[k], str)          # exact rational string, not float
        for k in ("offset_frames", "start_frames", "duration_frames"):
            assert isinstance(w[k], int)          # whole frame, not float


# --------------------------------------------------------------------------- #
# 4. FCP-NATIVE TOLERANCE — extra attrs / unknown elements → counted, no crash #
# --------------------------------------------------------------------------- #


_FCP_NATIVE = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="1/24s" width="1920" height="1080" name="FFVideoFormat"/>
    <asset id="r2" name="clip" src="media/gen/S001/a.mp4" duration="48/24s"
           hasVideo="1" format="r1" uid="ABC123" audioSources="1"/>
  </resources>
  <library location="file:///Users/x/Movies/L.fcpbundle">
    <event name="E">
      <project name="P">
        <sequence format="r1" duration="48/24s" tcStart="0s" tcFormat="NDF">
          <spine>
            <asset-clip ref="r2" offset="0s" name="clip" start="0s" duration="48/24s"
                        format="r1" tcFormat="NDF" enabled="1">
              <conform-rate scaleEnabled="0"/>
              <adjust-transform position="0 0"/>
            </asset-clip>
            <gap name="Gap" offset="48/24s" duration="24/24s"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def test_fcp_native_extra_attrs_and_unknown_elements_are_tolerated():
    parsed = parse_fcpxml(_FCP_NATIVE)          # must NOT crash on foreign shapes
    # the one asset-clip is still read with exact geometry despite extra attrs
    assert len(parsed.spine_clips) == 1
    c = parsed.spine_clips[0]
    assert c.name == "clip" and c.offset == Fraction(0) and c.duration == Fraction(2)
    # unknown elements (gap / conform-rate / adjust-transform) are COUNTED, not dropped
    counts = dict(parsed.unknown_elements)
    assert counts.get("gap") == 1
    assert counts.get("conform-rate") == 1
    assert counts.get("adjust-transform") == 1


def test_fcp_native_plan_lists_unknown_elements():
    plan = plan_fcpxml_import(parse_fcpxml(_FCP_NATIVE), source_sha256=None)
    tags = {r["tag"]: r["count"] for r in plan["unknown_elements"]}
    assert tags.get("gap") == 1 and tags.get("adjust-transform") == 1
    assert len(plan["windows"]) == 1            # the asset-clip still planned


# --------------------------------------------------------------------------- #
# 5. EXTERNAL SRC → needs_relink rows pointing at the relink machinery          #
# --------------------------------------------------------------------------- #


_EXTERNAL_SRC = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="1/24s" width="1920" height="1080"/>
    <asset id="r2" name="abs" src="/Volumes/RAID/footage/shot.mov" duration="48/24s" hasVideo="1"/>
    <asset id="r3" name="url" src="https://cdn.example.com/clip.mp4" duration="24/24s" hasVideo="1"/>
    <asset id="r4" name="esc" src="../../outside/secret.mp4" duration="24/24s" hasVideo="1"/>
    <asset id="r5" name="ok" src="media/gen/S001/a.mp4" duration="24/24s" hasVideo="1"/>
  </resources>
  <library><event name="E"><project name="P">
    <sequence format="r1" duration="120/24s" tcStart="0s">
      <spine>
        <asset-clip ref="r2" offset="0s" name="abs" start="0s" duration="48/24s"/>
        <asset-clip ref="r3" offset="48/24s" name="url" start="0s" duration="24/24s"/>
        <asset-clip ref="r4" offset="72/24s" name="esc" start="0s" duration="24/24s"/>
        <asset-clip ref="r5" offset="96/24s" name="ok" start="0s" duration="24/24s"/>
      </spine>
    </sequence>
  </project></event></library>
</fcpxml>
"""


def test_external_src_becomes_needs_relink_pointing_at_relink_machinery():
    plan = plan_fcpxml_import(parse_fcpxml(_EXTERNAL_SRC), source_sha256=None)
    by_src = {r["src"]: r for r in plan["needs_relink"]}
    # absolute, remote URL and root-escaping srcs are all external -> needs_relink
    assert set(by_src) == {
        "/Volumes/RAID/footage/shot.mov",
        "https://cdn.example.com/clip.mp4",
        "../../outside/secret.mp4",
    }
    assert by_src["/Volumes/RAID/footage/shot.mov"]["classification"] == "absolute"
    assert by_src["https://cdn.example.com/clip.mp4"]["classification"] == "remote_url"
    assert by_src["../../outside/secret.mp4"]["classification"] == "escapes_root"
    # every row points at the EXISTING relink machinery and copies/fetches nothing
    for r in plan["needs_relink"]:
        assert "manju relink" in r["relink_via"]
        assert "relink_plan" in r["relink_via"] or "apply_relink" in r["relink_via"]
        assert "not" in r["note"].lower() and (
            "copied" in r["note"].lower() or "fetch" in r["note"].lower())
    # the inside-project src is NOT a needs_relink row
    assert "media/gen/S001/a.mp4" not in by_src
    # windows are still proposed for ALL four clips (external too, just flagged)
    assert len(plan["windows"]) == 4
    ext = next(w for w in plan["windows"] if w["name"] == "abs")
    assert ext["media_status"] == "needs_relink"


def test_needs_relink_deduped_by_src():
    xml = _EXTERNAL_SRC.replace(
        '<asset-clip ref="r5" offset="96/24s" name="ok" start="0s" duration="24/24s"/>',
        '<asset-clip ref="r2" offset="96/24s" name="abs2" start="0s" duration="24/24s"/>')
    plan = plan_fcpxml_import(parse_fcpxml(xml), source_sha256=None)
    abs_rows = [r for r in plan["needs_relink"]
                if r["src"] == "/Volumes/RAID/footage/shot.mov"]
    assert len(abs_rows) == 1                    # one row per distinct external src


# --------------------------------------------------------------------------- #
# 6. UNKNOWN audio role → honest row (nothing silently dropped)                 #
# --------------------------------------------------------------------------- #


_UNKNOWN_ROLE = """<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="1/24s" width="1920" height="1080"/>
    <asset id="r2" name="v" src="media/gen/S001/a.mp4" duration="48/24s" hasVideo="1"/>
    <asset id="r3" name="mystery" src="media/x/weird.wav" duration="24/24s" hasAudio="1"/>
  </resources>
  <library><event name="E"><project name="P">
    <sequence format="r1" duration="48/24s" tcStart="0s">
      <spine>
        <asset-clip ref="r2" offset="0s" name="v" start="0s" duration="48/24s">
          <asset-clip ref="r3" lane="-1" offset="0s" name="weird" start="0s"
                      duration="24/24s" audioRole="narration.commentary"/>
        </asset-clip>
      </spine>
    </sequence>
  </project></event></library>
</fcpxml>
"""


def test_unknown_audio_role_is_an_honest_row_not_dropped():
    plan = plan_fcpxml_import(parse_fcpxml(_UNKNOWN_ROLE), source_sha256=None)
    rows = plan["audio_suggestions"]
    assert len(rows) == 1
    r = rows[0]
    assert r["disposition"] == "unknown_audio_role"
    assert r["audio_role"] == "narration.commentary"   # verbatim, not guessed
    assert "bus" not in r or r["bus"] is None          # no fabricated bus mapping
    assert r["src"] == "media/x/weird.wav"


# --------------------------------------------------------------------------- #
# 7. VERSION recorded verbatim + unverified note for unknown versions          #
# --------------------------------------------------------------------------- #


def test_known_version_is_verified():
    parsed = parse_fcpxml(compile_fcpxml(_tl([_vid("A", "T", 0, 1000)]), rate=R24))
    assert parsed.version == "1.9"
    assert parsed.version_verified is True


def test_unknown_version_recorded_verbatim_with_unverified_note():
    xml = compile_fcpxml(_tl([_vid("A", "T", 0, 1000)]), rate=R24)
    xml = xml.replace('version="1.9"', 'version="1.13"')
    parsed = parse_fcpxml(xml)
    assert parsed.version == "1.13"              # verbatim, best-effort parse
    assert parsed.version_verified is False
    plan = plan_fcpxml_import(parsed, source_sha256=None)
    assert plan["fcpxml_version"] == "1.13" and plan["version_verified"] is False
    assert any("unverified" in d.get("message", "").lower()
               or "1.13" in d.get("message", "") for d in plan["diagnostics"])


def test_missing_version_is_unverified_but_still_parses():
    xml = compile_fcpxml(_tl([_vid("A", "T", 0, 1000)]), rate=R24)
    xml = xml.replace(' version="1.9"', "")
    parsed = parse_fcpxml(xml)
    assert parsed.version == "" and parsed.version_verified is False
    assert len(parsed.spine_clips) == 1          # best-effort parse still works


# --------------------------------------------------------------------------- #
# 8. NON-fcpxml root refused with a structured error (fail-closed)             #
# --------------------------------------------------------------------------- #


def test_non_fcpxml_root_is_refused():
    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml("<?xml version='1.0'?><xmeml version='4'><sequence/></xmeml>")
    assert exc.value.diagnostics                 # carries a structured diagnostic
    assert any(d["severity"] == "error" for d in exc.value.diagnostics)


def test_malformed_xml_is_refused():
    with pytest.raises(FcpxmlImportError):
        parse_fcpxml("<fcpxml><resources></fcpxml>")   # not well-formed


# --------------------------------------------------------------------------- #
# 9. DIGEST tamper-evidence (like conform-loss) + schema/source echo            #
# --------------------------------------------------------------------------- #


def test_plan_carries_schema_source_and_digest():
    xml = compile_fcpxml(_three_clip_timeline(), rate=R24)
    plan = plan_fcpxml_import(parse_fcpxml(xml), source_sha256="sha256:deadbeef")
    assert plan["schema"] == PLAN_SCHEMA == "manju.fcpxml-import-plan/v1"
    assert plan["source_sha256"] == "sha256:deadbeef"
    assert plan["digest"].startswith("sha256:")
    assert verify_plan_digest(plan) is plan      # round-trips clean


def test_digest_detects_tampering():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline())
    verify_plan_digest(plan)                      # clean
    plan["windows"][0]["offset_frames"] = 999     # tamper a fact
    with pytest.raises(FcpxmlImportError):
        verify_plan_digest(plan)


def test_digest_detects_dropped_digest():
    plan, _, _ = _plan_from_timeline(_three_clip_timeline())
    plan.pop("digest")
    with pytest.raises(FcpxmlImportError):
        verify_plan_digest(plan)


# --------------------------------------------------------------------------- #
# 10. determinism + CJK                                                         #
# --------------------------------------------------------------------------- #


def test_plan_is_deterministic_including_digest():
    tl = _audio_timeline()
    xml = compile_fcpxml(tl, rate=R24, name="X")
    p1 = plan_fcpxml_import(parse_fcpxml(xml), source_sha256=hash_text(xml))
    p2 = plan_fcpxml_import(parse_fcpxml(xml), source_sha256=hash_text(xml))
    assert p1 == p2
    assert p1["digest"] == p2["digest"]


def test_cjk_names_and_sources_ride_natively():
    tl = _tl([VideoClip(shot="镜头一", take="雨夜", source="media/gen/镜头一/雨夜.mp4",
                        start_ms=0, duration_ms=1000)],
             music=[_aud("media/音乐/主题曲.mp3", 0, 1000, gain_db=-6.0)])
    plan, _, _ = _plan_from_timeline(tl, name="雨夜便利店")
    assert plan["windows"][0]["name"] == "镜头一"
    assert plan["windows"][0]["src"] == "media/gen/镜头一/雨夜.mp4"
    assert plan["audio_suggestions"][0]["src"] == "media/音乐/主题曲.mp3"


# --------------------------------------------------------------------------- #
# 11. empty timeline — format-only doc plans cleanly, no crash                  #
# --------------------------------------------------------------------------- #


def test_empty_timeline_plans_to_nothing():
    plan, parsed, _ = _plan_from_timeline(_tl([]), name="EMPTY")
    assert plan["windows"] == []
    assert plan["transitions"] == []
    assert plan["audio_suggestions"] == []
    assert plan["needs_relink"] == []
    assert parsed.version == "1.9"


# --------------------------------------------------------------------------- #
# 12. PLAN-ONLY boundary — no apply / no write surface exists                   #
# --------------------------------------------------------------------------- #


def test_module_exposes_no_apply_or_write_surface():
    import manju.exporters.fcpxml_import as m

    public = {n for n in dir(m) if not n.startswith("_")}
    for n in public:
        assert "apply" not in n.lower(), f"apply surface leaked: {n}"
        assert "write" not in n.lower(), f"write surface leaked: {n}"
        assert "export" not in n.lower(), f"export surface leaked: {n}"


# --------------------------------------------------------------------------- #
# 13. NO-WRITES proof — a plan against a real project mutates nothing           #
# --------------------------------------------------------------------------- #


def _tree_snapshot(root):
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_mtime_ns, p.read_bytes())
    return snap


def test_import_plan_never_writes_against_a_real_project(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:a")
    xml = compile_fcpxml(_audio_timeline(), rate=R24, name="X")
    parsed = parse_fcpxml(xml)

    before = _tree_snapshot(tmp_project.root)
    plan = plan_fcpxml_import(parsed, source_sha256=hash_text(xml),
                              target_project=tmp_project)
    after = _tree_snapshot(tmp_project.root)

    assert before == after                        # zero filesystem mutations
    assert plan["target_project"] == str(tmp_project.root)
    verify_plan_digest(plan)


# --------------------------------------------------------------------------- #
# 14. CLI — mirror openclap's `import-plan` subcommand shape exactly            #
# --------------------------------------------------------------------------- #


def test_cli_import_plan_json(tmp_path):
    tl = _audio_timeline()
    path = tmp_path / "demo.fcpxml"
    path.write_text(compile_fcpxml(tl, rate=R24, name="DEMO"), encoding="utf-8")
    result = runner.invoke(app, ["fcpxml", "import-plan", str(path), "--json"])
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output)
    assert data["schema"] == "manju.fcpxml-import-plan/v1"
    assert data["source_sha256"].startswith("sha256:")
    assert len(data["windows"]) == 2
    assert len(data["audio_suggestions"]) == 3


def test_cli_import_plan_non_fcpxml_exits_structured(tmp_path):
    path = tmp_path / "bad.fcpxml"
    path.write_text("<xmeml version='4'><sequence/></xmeml>", encoding="utf-8")
    result = runner.invoke(app, ["fcpxml", "import-plan", str(path), "--json"])
    assert result.exit_code == 1
    import json

    data = json.loads(result.output)
    assert data["error"]
    assert data["diagnostics"]


# --------------------------------------------------------------------------- #
# 15. Audit 16 / OPT-ROBUST F2 — input caps (byte cap + DOCTYPE/ENTITY refusal) #
#     Both are local, plan-only, no-write residuals: defense-in-depth, and the  #
#     normal-file plan stays byte-identical (the parse path is untouched).      #
# --------------------------------------------------------------------------- #


def test_oversize_file_refused_before_parse(tmp_path, monkeypatch):
    """A document larger than the byte cap is refused with a structured error
    BEFORE ElementTree ever materializes it (the multi-GB-OOM residual). The
    cap is exercised at a tiny value so no giant file is written."""
    from manju.exporters import fcpxml_import as fi

    monkeypatch.setattr(fi, "_MAX_FCPXML_BYTES", 128)
    path = tmp_path / "big.fcpxml"
    path.write_text("<fcpxml version='1.9'>" + ("<x/>" * 200) + "</fcpxml>",
                    encoding="utf-8")
    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml(path)
    assert exc.value.diagnostics
    assert any(d["code"] == "input_too_large" for d in exc.value.diagnostics)
    # the message states the cap (per the mission brief)
    assert "128" in str(exc.value)


def test_oversize_inline_string_refused(monkeypatch):
    """An inline XML string over the cap is also refused (a caller can hand a
    huge string directly, not just a path)."""
    from manju.exporters import fcpxml_import as fi

    monkeypatch.setattr(fi, "_MAX_FCPXML_BYTES", 64)
    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml("<fcpxml version='1.9'>" + ("<x/>" * 100) + "</fcpxml>")
    assert any(d["code"] == "input_too_large" for d in exc.value.diagnostics)


def test_doctype_prolog_refused():
    """A DOCTYPE prolog (the 'billion laughs' carrier) is refused with a clean
    structured error rather than relying on the platform libexpat version."""
    billion_laughs = (
        "<?xml version='1.0'?>\n"
        "<!DOCTYPE fcpxml [\n"
        "  <!ENTITY a 'aaaaaaaaaa'>\n"
        "  <!ENTITY b '&a;&a;&a;&a;&a;'>\n"
        "]>\n"
        "<fcpxml version='1.9'><resources/></fcpxml>"
    )
    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml(billion_laughs)
    assert any(d["code"] == "doctype_forbidden" for d in exc.value.diagnostics)


def test_entity_prolog_refused():
    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml("<!ENTITY x 'y'><fcpxml version='1.9'><resources/></fcpxml>")
    assert any(d["code"] == "doctype_forbidden" for d in exc.value.diagnostics)


def test_normal_document_unaffected_by_caps(tmp_path):
    """A normal editorial FCPXML parses and plans EXACTLY as before — the caps
    only fire on abuse, so the import-plan for a real file is byte-identical."""
    tl = _audio_timeline()
    xml = compile_fcpxml(tl, rate=R24, name="DEMO")
    # inline string path and file path both parse cleanly, same plan
    plan_inline = plan_fcpxml_import(parse_fcpxml(xml), source_sha256="sha256:x")
    path = tmp_path / "demo.fcpxml"
    path.write_text(xml, encoding="utf-8")
    plan_file = plan_fcpxml_import(parse_fcpxml(path), source_sha256="sha256:x")
    assert plan_inline == plan_file
    assert plan_inline["schema"] == PLAN_SCHEMA
