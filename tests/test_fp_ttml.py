"""FP Loop S1 — TTML/IMSC1 caption WRITER (honest scope) + conform-loss rows.

Red-first pins for roadmap §5.5 / user item 4 (addendum-scoped):

- ``exporters/ttml.py`` is a WRITER only (no TTML import this loop): an
  IMSC1-Text-Profile-shaped TTML1 document compiled from the SAME
  ``Timeline.tracks.captions`` truth as SRT/ASS/VTT. It expresses exactly what
  :class:`CaptionLine` actually carries — start/end ms, text, ``speaker``
  (→ ``ttm:agent``), optional wave-4b ``role`` — and NOTHING invented.
- Timing is media-timebase ``HH:MM:SS.mmm`` (ms precision — exactly what cues
  carry; the rational R-track upgrades timing later). fps never enters.
- ONE default region (bottom centre). RTL / vertical writing / ruby are
  honestly OUT OF SCOPE (the cue model has no layout semantics) — recorded in
  the conform "ttml" rows, never silently claimed.
- Roles: standard TTML hints where the addendum maps them (sdh → ``ttm:role
  ="captions"``, translation → ``ttm:role="subtitles"``, forced →
  ``itts:forcedDisplay="true"``, speaker_label → ``ttm:agent`` stub) and the
  VERBATIM role always rides ``x-manju:role`` — an unmappable role is never
  dropped silently.
- Deterministic bytes (fixed/sorted attr order, LF endings, no timestamps),
  stdlib-only XML escaping, CJK passes through as UTF-8.
- conform.py gains the "ttml" classification (captions preserved with the
  subset stated; every non-caption feature unsupported — the srt_ass honesty
  stance) so the exporter-completeness meta-pin passes HONESTLY.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from manju.core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)

runner = CliRunner()

TT = "http://www.w3.org/ns/ttml"
TTM = "http://www.w3.org/ns/ttml#metadata"
TTP = "http://www.w3.org/ns/ttml#parameter"
ITTS = "http://www.w3.org/ns/ttml/profile/imsc1#styling"
XMANJU = "urn:x-manju:ttml"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ROLE_ATTR = f"{{{XMANJU}}}role"
TTM_ROLE = f"{{{TTM}}}role"
TTM_AGENT = f"{{{TTM}}}agent"
FORCED = f"{{{ITTS}}}forcedDisplay"


def _tl(captions: list[CaptionLine], **kw) -> Timeline:
    kw.setdefault("fps", 24)
    kw.setdefault("width", 1080)
    kw.setdefault("height", 1920)
    kw.setdefault("duration_ms", max((c.end_ms for c in captions), default=0))
    return Timeline(tracks=TimelineTracks(captions=captions), **kw)


def _cue(start_ms: int, end_ms: int, text: str, **kw) -> CaptionLine:
    return CaptionLine(start_ms=start_ms, end_ms=end_ms, text=text, **kw)


def _ps(root: ET.Element) -> list[ET.Element]:
    return root.findall(f".//{{{TT}}}p")


def _compile(captions: list[CaptionLine], **kw) -> str:
    from manju.exporters.ttml import compile_ttml

    return compile_ttml(_tl(captions), **kw)


# ================================================== 1. document shape (IMSC1)


def test_compile_ttml_is_well_formed_imsc_shaped_xml():
    doc = _compile([_cue(0, 2000, "深夜的便利店")])
    root = ET.fromstring(doc)  # well-formed or this raises
    assert root.tag == f"{{{TT}}}tt"
    # IMSC1 requires the media time base — explicit, not implied:
    assert root.get(f"{{{TTP}}}timeBase") == "media"
    # honest xml:lang: the base project records NO locale (audit) → ""
    assert root.get(f"{{{XML_NS}}}lang") == ""
    # tt/head/styling+layout/body/div/p skeleton
    assert root.find(f"{{{TT}}}head/{{{TT}}}styling") is not None
    assert root.find(f"{{{TT}}}head/{{{TT}}}layout") is not None
    assert root.find(f"{{{TT}}}body/{{{TT}}}div") is not None
    assert len(_ps(root)) == 1


def test_one_default_region_bottom_centre():
    doc = _compile([_cue(0, 1000, "a"), _cue(1000, 2000, "b")])
    root = ET.fromstring(doc)
    regions = root.findall(f".//{{{TT}}}region")
    assert len(regions) == 1, "exactly ONE default region this loop"
    region = regions[0]
    tts = "{http://www.w3.org/ns/ttml#styling}"
    assert region.get(f"{tts}displayAlign") == "after"   # bottom-anchored
    assert region.get(f"{tts}textAlign") == "center"
    rid = region.get(f"{{{XML_NS}}}id")
    assert rid == "r.bottom"
    # the body references it once; every p inherits (no per-p region attrs)
    body = root.find(f"{{{TT}}}body")
    assert body.get("region") == rid
    assert all(p.get("region") is None for p in _ps(root))


def test_explicit_lang_parameter_lands_as_xml_lang():
    doc = _compile([_cue(0, 1000, "x")], lang="zh-CN")
    root = ET.fromstring(doc)
    assert root.get(f"{{{XML_NS}}}lang") == "zh-CN"


def test_document_header_and_line_endings():
    doc = _compile([_cue(0, 1000, "x")])
    assert doc.startswith('<?xml version="1.0" encoding="utf-8"?>\n')
    assert 'xmlns="http://www.w3.org/ns/ttml"' in doc
    assert 'xmlns:x-manju="urn:x-manju:ttml"' in doc
    assert 'ttp:timeBase="media"' in doc
    assert 'xml:lang=""' in doc
    assert "\r" not in doc
    assert doc.endswith("</tt>\n")


# ============================================================ 2. timing pins


def test_timing_format_pins():
    """0ms → 00:00:00.000; 3661234ms → 01:01:01.234 (HH:MM:SS.mmm, media)."""
    doc = _compile([_cue(0, 3_661_234, "计时")])
    (p,) = _ps(ET.fromstring(doc))
    assert p.get("begin") == "00:00:00.000"
    assert p.get("end") == "01:01:01.234"
    assert 'begin="00:00:00.000" end="01:01:01.234"' in doc


def test_every_cue_gets_begin_end_in_order():
    doc = _compile([_cue(0, 1000, "一"), _cue(1000, 2500, "二"),
                    _cue(2500, 61_000, "三")])
    ps = _ps(ET.fromstring(doc))
    assert [(p.get("begin"), p.get("end")) for p in ps] == [
        ("00:00:00.000", "00:00:01.000"),
        ("00:00:01.000", "00:00:02.500"),
        ("00:00:02.500", "00:01:01.000"),
    ]


# ======================================================== 3. role mapping


def test_role_mapping_table():
    """The addendum's binding table: standard hint + verbatim x-manju:role."""
    doc = _compile([
        _cue(0, 1000, "a", role="sdh"),
        _cue(1000, 2000, "b", role="translation"),
        _cue(2000, 3000, "c", role="forced"),
        _cue(3000, 4000, "d", role="lyrics"),
        _cue(4000, 5000, "e", role="speaker_label"),
    ])
    root = ET.fromstring(doc)
    sdh, tr, forced, lyr, spk = _ps(root)

    assert sdh.get(TTM_ROLE) == "captions"
    assert sdh.get(ROLE_ATTR) == "sdh"
    assert sdh.get(FORCED) is None

    assert tr.get(TTM_ROLE) == "subtitles"
    assert tr.get(ROLE_ATTR) == "translation"

    assert forced.get(FORCED) == "true"
    assert forced.get(ROLE_ATTR) == "forced"
    assert forced.get(TTM_ROLE) is None

    assert lyr.get(TTM_ROLE) is None, "lyrics has no ttm hint — x-manju only"
    assert lyr.get(ROLE_ATTR) == "lyrics"

    # speaker_label with NO speaker field → the shared stub agent
    assert spk.get(ROLE_ATTR) == "speaker_label"
    assert spk.get(TTM_AGENT) == "agent.unknown"
    stub = [a for a in root.findall(f".//{{{TTM}}}agent")
            if a.get(f"{{{XML_NS}}}id") == "agent.unknown"]
    assert len(stub) == 1 and stub[0].get("type") == "other"


def test_speaker_label_with_speaker_uses_real_agent_not_stub():
    doc = _compile([_cue(0, 1000, "——林夏", role="speaker_label", speaker="林夏")])
    root = ET.fromstring(doc)
    (p,) = _ps(root)
    assert p.get(TTM_AGENT) == "agent.1"
    assert p.get(ROLE_ATTR) == "speaker_label"
    ids = [a.get(f"{{{XML_NS}}}id") for a in root.findall(f".//{{{TTM}}}agent")]
    assert ids == ["agent.1"], "no stub agent when the cue names its speaker"


def test_unmappable_role_rides_x_manju_verbatim():
    """A role outside the vocabulary is NEVER dropped silently — it rides
    x-manju:role verbatim (hostile characters included) and adds no fabricated
    standard attribute."""
    hostile = 'director"s <note> & 旁白\n第二行'
    doc = _compile([_cue(0, 1000, "text", role=hostile)])
    root = ET.fromstring(doc)
    (p,) = _ps(root)
    assert p.get(ROLE_ATTR) == hostile  # verbatim through parse
    assert p.get(TTM_ROLE) is None
    assert p.get(FORCED) is None
    assert p.get(TTM_AGENT) is None


# ================================================== 4. speaker → ttm:agent


def test_speaker_field_lands_as_ttm_agent():
    doc = _compile([
        _cue(0, 1000, "这不可能。", speaker="林夏"),
        _cue(1000, 2000, "打烊了。", speaker="老板"),
        _cue(2000, 3000, "(雨声)"),
    ])
    root = ET.fromstring(doc)
    agents = root.findall(f"{{{TT}}}head/{{{TT}}}metadata/{{{TTM}}}agent")
    # sorted by speaker string → deterministic ids
    assert [(a.get(f"{{{XML_NS}}}id"), a.get("type")) for a in agents] == [
        ("agent.1", "character"), ("agent.2", "character")]
    names = [a.find(f"{{{TTM}}}name").text for a in agents]
    assert names == sorted(["林夏", "老板"])
    p1, p2, p3 = _ps(root)
    by_name = dict(zip(names, ("agent.1", "agent.2")))
    assert p1.get(TTM_AGENT) == by_name["林夏"]
    assert p2.get(TTM_AGENT) == by_name["老板"]
    assert p3.get(TTM_AGENT) is None, "speaker-less cue claims no agent"


# ==================================== 5. text: CJK, escaping, line breaks


def test_cjk_text_passes_through_as_utf8():
    text = "深夜的便利店,霓虹灯在水洼里融化。"
    doc = _compile([_cue(0, 2000, text)])
    assert text in doc, "CJK must not be entity-escaped"
    (p,) = _ps(ET.fromstring(doc))
    assert p.text == text
    doc.encode("utf-8")  # encodable, no surrogates


def test_xml_escaping_of_cue_text():
    text = 'A & B < C > D "quoted"'
    doc = _compile([_cue(0, 1000, text)])
    assert "A &amp; B &lt; C &gt; D" in doc
    (p,) = _ps(ET.fromstring(doc))
    assert p.text == text  # escaping is lossless


def test_author_line_break_becomes_br():
    doc = _compile([_cue(0, 1000, "第一行\n第二行")])
    (p,) = _ps(ET.fromstring(doc))
    brs = p.findall(f"{{{TT}}}br")
    assert len(brs) == 1
    assert list(p.itertext()) == ["第一行", "第二行"]


def test_declared_line_budget_is_applied_like_srt_vtt():
    """compile_ttml honours the same max_chars_per_line budget as
    compile_srt/compile_vtt (reused break_lines — no drift), rendered as
    <br/>. Author-broken text is never re-broken (break_lines' own stance)."""
    from manju.exporters.srt_ass import break_lines

    text = "这是一句超过预算需要断行的长字幕文本"
    expect = break_lines(text, 6).split("\n")
    assert len(expect) > 1, "fixture must actually exceed the budget"
    doc = _compile([_cue(0, 1000, text)], max_chars_per_line=6)
    (p,) = _ps(ET.fromstring(doc))
    assert list(p.itertext()) == expect


# ===================================================== 6. determinism pins


def test_two_compiles_are_byte_identical():
    def build() -> Timeline:
        return _tl([
            _cue(0, 1000, "深夜\n便利店", speaker="林夏", role="sdh"),
            _cue(1000, 2000, 'A & B "q"', role="x-自定义"),
            _cue(2000, 3000, "→", speaker="老板", role="forced"),
        ])

    from manju.exporters.ttml import compile_ttml

    assert compile_ttml(build()) == compile_ttml(build())


def test_roleless_project_output_valid_and_stable():
    """A role-less, speaker-less project renders a valid document with no
    role/agent vocabulary at all — and byte-identically on every run."""
    def build() -> Timeline:
        return _tl([_cue(0, 1000, "你好"), _cue(1000, 2000, "世界")])

    from manju.exporters.ttml import compile_ttml

    a, b = compile_ttml(build()), compile_ttml(build())
    assert a == b
    root = ET.fromstring(a)
    assert "x-manju:role" not in a and "ttm:role" not in a
    assert "ttm:agent" not in a and "forcedDisplay" not in a
    assert root.find(f"{{{TT}}}head/{{{TT}}}metadata") is None, (
        "no agents → no metadata block at all")
    assert len(_ps(root)) == 2


def test_empty_caption_track_still_valid_and_stable():
    from manju.exporters.ttml import compile_ttml

    tl = Timeline(fps=24, width=1080, height=1920, duration_ms=0,
                  tracks=TimelineTracks())
    a, b = compile_ttml(tl), compile_ttml(tl)
    assert a == b
    root = ET.fromstring(a)
    assert _ps(root) == []


# ============================================ 7. export glue (project paths)


def test_export_ttml_writes_captions_ttml(tmp_project):
    from manju.exporters.ttml import export_ttml

    tl = _tl([_cue(0, 2000, "深夜的便利店", speaker="林夏", role="sdh")])
    out = export_ttml(tmp_project, tl)
    assert out == tmp_project.captions_dir / "captions.ttml"
    assert out.exists()
    first = out.read_bytes()
    assert b"\r" not in first
    ET.fromstring(first.decode("utf-8"))
    # re-export is byte-identical (no timestamps, atomic writer)
    assert export_ttml(tmp_project, tl).read_bytes() == first


def test_export_ttml_honours_dest_override(tmp_project, tmp_path):
    from manju.exporters.ttml import export_ttml

    dest = tmp_path / "nested" / "out.ttml"
    tl = _tl([_cue(0, 1000, "x")])
    out = export_ttml(tmp_project, tl, dest=dest)
    assert out == dest and dest.exists()


def test_manual_mode_human_srt_is_truth(tmp_project):
    """rules.captions.mode == "manual" + an existing captions.srt: the TTML is
    compiled FROM the human cues (same takeover stance as export_captions) so
    every caption exit shows the same text. SRT carries no roles/speakers, so
    the manual TTML honestly has none either (format honesty, FP loop I)."""
    from manju.exporters.srt_ass import compile_srt
    from manju.exporters.ttml import export_ttml

    human = _tl([_cue(0, 1500, "人改过的第一行"), _cue(1500, 3000, "人改过的第二行")])
    srt_path = tmp_project.captions_dir / "captions.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(compile_srt(human), encoding="utf-8")
    rules = tmp_project.load_rules()
    rules.captions.mode = "manual"
    tmp_project.save_rules(rules)

    compiled = _tl([_cue(0, 1000, "编译器的行", speaker="林夏", role="sdh")])
    out = export_ttml(tmp_project, compiled)
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    assert [p.text for p in _ps(root)] == ["人改过的第一行", "人改过的第二行"]
    text = out.read_text(encoding="utf-8")
    assert "x-manju:role" not in text and "ttm:agent" not in text


# ================================= 8. conform-loss: the honest "ttml" rows


def _full_timeline() -> Timeline:
    """Every KNOWN_FEATURES row present — all boundaries on the 24fps grid.
    Pure model data: only the caption track ever reaches the TTML writer."""
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-ttml-1"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="t1", source="media/gen/S001/t1.mp4",
                          start_ms=0, duration_ms=2000, source_in_ms=125,
                          source_gain_db=-3.0,
                          transition_out=TransitionSpec(type="fade", duration_ms=250)),
                VideoClip(shot="S002", take="t2", source="media/gen/S002/t2.mp4",
                          start_ms=2000, duration_ms=2000),
            ],
            overlay=[OverlayClip(kind="title_card", text="章节一",
                                 start_ms=0, duration_ms=1500)],
            voice=[AudioClip(source="media/voice/v1.wav", start_ms=0,
                             duration_ms=2000)],
            music=[AudioClip(source="media/music/bed.mp3", start_ms=0,
                             duration_ms=4000, gain_db=-6.0, ducking=True,
                             fade_in_ms=250, fade_out_ms=500,
                             start_offset_ms=250)],
            ambient=[AudioClip(source="media/music/rain.wav", start_ms=0,
                               duration_ms=4000, loop=True)],
            captions=[
                _cue(0, 1000, "你好", speaker="林夏", role="sdh"),
                _cue(1000, 2000, "世界"),
            ],
        ),
    )


def test_conform_ttml_captions_preserved_everything_else_unsupported(tmp_project):
    from manju.exporters.conform import conform_loss_report
    from manju.exporters.ttml import export_ttml

    tl = _full_timeline()
    out = export_ttml(tmp_project, tl)
    before = out.read_bytes()
    doc = conform_loss_report(tmp_project, "ttml", tl, out)
    assert out.read_bytes() == before, "conform must only READ exporter output"

    got = {r["feature"]: cat
           for cat in ("preserved", "approximated", "dropped", "unsupported")
           for r in doc[cat]}
    assert got["captions"] == "preserved"
    others = {f for f in got if f != "captions"}
    assert others, "fixture must carry non-caption features"
    for feat in others:
        assert got[feat] == "unsupported", f"{feat} must be unsupported for ttml"

    # the preserved row STATES the subset honestly (roles/agents + the
    # out-of-scope layout semantics), and cites the writer
    row = {r["feature"]: r for r in doc["preserved"]}["captions"]
    assert "x-manju:role" in row["detail"]
    assert "ruby" in row["detail"], "RTL/vertical/ruby boundary must be stated"
    assert "ttml.py" in row["where"]


def test_conform_ttml_is_not_time_bearing_and_counts_cues(tmp_project):
    from manju.exporters.conform import (
        conform_loss_report,
        read_conform_report,
        write_conform_report,
    )
    from manju.exporters.ttml import export_ttml

    tl = _full_timeline()
    out = export_ttml(tmp_project, tl)
    doc = conform_loss_report(tmp_project, "ttml", tl, out)
    fd = doc["frame_drift"]
    assert fd["checked"] is False, "ms-native caption doc — no frame grid"
    assert "ms" in fd["reason"].lower() or "fps" in fd["reason"].lower()
    # read-only cross-check of the ACTUAL artifact: cue count agrees
    assert any("2 cue(s) vs timeline 2" in n for n in doc["notes"])
    # the doc survives the tamper-evident store round trip
    path = write_conform_report(tmp_project, doc)
    stored = read_conform_report(path)
    assert stored["target"] == "ttml"
    assert stored["preserved"] == doc["preserved"]


def test_conform_ttml_stale_artifact_cross_check_flags_mismatch(tmp_project):
    from manju.exporters.conform import conform_loss_report
    from manju.exporters.ttml import export_ttml

    tl = _full_timeline()
    out = export_ttml(tmp_project, tl)
    tl.tracks.captions.append(_cue(2000, 3000, "第三句"))
    doc = conform_loss_report(tmp_project, "ttml", tl, out)
    assert any("MISMATCH" in n for n in doc["notes"])


def test_ttml_meta_pin_exporter_module_classified():
    """The exporter-completeness meta-pin, mirrored: the ttml module exists on
    disk AND carries a conform classifier (never UNSUPPORTED_TARGETS), and no
    ghost classifier points at a missing module."""
    from manju.exporters import conform as conform_mod
    from manju.exporters.conform import TARGET_CLASSIFIERS, UNSUPPORTED_TARGETS

    exp_dir = Path(conform_mod.__file__).resolve().parent
    modules = {p.stem for p in exp_dir.glob("*.py")
               if p.stem not in ("__init__", "conform")}
    modules |= {p.name for p in exp_dir.iterdir()
                if p.is_dir() and p.name != "__pycache__"
                and (p / "__init__.py").exists()}
    assert "ttml" in modules, "exporters/ttml.py must exist"
    assert "ttml" in TARGET_CLASSIFIERS, "ttml must be classified (honestly)"
    assert "ttml" not in UNSUPPORTED_TARGETS
    assert set(TARGET_CLASSIFIERS) <= modules


# ============================================================ 9. CLI surface


def test_cli_export_ttml_flag(tmp_project, monkeypatch):
    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    tl = _tl([_cue(0, 2000, "深夜的便利店", role="sdh")])
    tmp_project.save_timeline(tl)
    # --yes: the scaffolded ask_before list gates final_export (WP5)
    result = runner.invoke(app, ["export", "--ttml", "--json", "--yes"])
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(result.output)
    outputs = payload["outputs"]
    assert outputs.get("ttml") == "captions/captions.ttml"
    # --ttml alone must NOT trigger the srt/otio default pair
    assert "srt" not in outputs and "otio" not in outputs
    assert (tmp_project.captions_dir / "captions.ttml").exists()
