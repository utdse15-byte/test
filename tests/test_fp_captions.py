"""FP Loop I — caption roles + accessibility advisories (record + advise, never block).

Red-first pins for roadmap §5.5 (addendum-scoped):

- ``CaptionLine.role`` is OPTIONAL truth-side vocabulary. Default ``None`` is
  dropped from serialization entirely, so every existing project's
  ``timeline.json`` / model dumps stay BYTE-IDENTICAL (round-O precedent made
  strict: no one-time fingerprint move this time).
- Unknown role strings are a STRUCTURED warn at QC-check time (same stance as
  TRANSITION_TYPES) — never a parse crash, never an error, never silent.
- ``qc/captions_access.py`` derives a readability/accessibility advisory report:
  CJK-aware CPS + line lengths (East Asian Width W/F count 2, newline 0, all
  else 1), line counts, duration floor/ceiling, gap/overlap, forced-vs-nonforced
  overlap, reading-speed distribution, role coverage. EVERY row carries
  severity "advisory"; nothing here ever changes a check/build outcome.
- Roles flow VERBATIM into the ASS Name field (comma/newline/override syntax
  neutralized so the Dialogue field grid cannot be broken); SRT/VTT have no
  role field — their bytes are identical with or without roles.
- Delivery caption artifact rows gain an ADDITIVE ``caption_roles`` count map;
  role-less manifests carry no new key and keep their digest.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.core.models import CaptionLine, Timeline, TimelineTracks
from manju.exporters.srt_ass import compile_ass, compile_srt, compile_vtt

runner = CliRunner()


def _tl(captions: list[CaptionLine], **kw) -> Timeline:
    kw.setdefault("width", 1080)
    kw.setdefault("height", 1920)
    kw.setdefault("duration_ms", max((c.end_ms for c in captions), default=0))
    return Timeline(tracks=TimelineTracks(captions=captions), **kw)


def _cue(start_ms: int, end_ms: int, text: str, **kw) -> CaptionLine:
    return CaptionLine(start_ms=start_ms, end_ms=end_ms, text=text, **kw)


# ======================================================= 1. model: role field


def test_caption_role_default_none_serialization_is_byte_identical():
    """The pin: a role-less cue dumps EXACTLY the pre-change shape — no
    ``role`` key, not even ``null`` (save_timeline uses plain model_dump, so a
    null would land in every recompiled timeline.json)."""
    cue = _cue(0, 2000, "深夜的便利店", speaker="林夏", shot="S001")
    assert cue.model_dump() == {
        "start_ms": 0,
        "end_ms": 2000,
        "text": "深夜的便利店",
        "speaker": "林夏",
        "shot": "S001",
    }
    assert "role" not in cue.model_dump_json()


def test_caption_role_default_none_timeline_json_byte_identical(tmp_project):
    """save_timeline bytes for a role-less timeline contain no 'role' anywhere."""
    tl = _tl([_cue(0, 2000, "测试字幕")])
    path = tmp_project.save_timeline(tl)
    raw = path.read_text(encoding="utf-8")
    assert '"role"' not in raw
    # and the cue block is exactly the historical five keys
    data = json.loads(raw)
    assert set(data["tracks"]["captions"][0]) == {
        "start_ms", "end_ms", "text", "speaker", "shot"}


def test_caption_role_set_round_trips_through_timeline_json(tmp_project):
    tl = _tl([_cue(0, 2000, "测试", role="forced")])
    tmp_project.save_timeline(tl)
    loaded = tmp_project.load_timeline()
    assert loaded.tracks.captions[0].role == "forced"
    # a set role IS serialized
    assert tl.tracks.captions[0].model_dump()["role"] == "forced"


def test_old_timeline_dict_without_role_parses_as_none():
    cue = CaptionLine.model_validate(
        {"start_ms": 0, "end_ms": 1000, "text": "旧数据"})
    assert cue.role is None


def test_caption_roles_vocabulary_constant():
    from manju.core.models import CAPTION_ROLES

    assert CAPTION_ROLES == (
        "translation", "sdh", "forced", "lyrics", "speaker_label")


def test_unknown_role_is_lenient_at_the_model_layer():
    """Same stance as TRANSITION_TYPES: timeline.json is hand-editable truth —
    an unknown role must never crash the parse (QC diagnoses it instead)."""
    cue = CaptionLine.model_validate(
        {"start_ms": 0, "end_ms": 1000, "text": "x", "role": "narrator"})
    assert cue.role == "narrator"


# ============================================ 2. unknown role → structured warn


def _qc_captions_report(tmp_project, captions):
    from manju.qc.checks import QCReport, _technical_captions

    tmp_project.save_timeline(_tl(captions))
    report = QCReport()
    _technical_captions(tmp_project, report, tmp_project.load_timeline())
    return report


def test_unknown_role_yields_structured_warn_and_never_blocks(tmp_project):
    report = _qc_captions_report(
        tmp_project, [_cue(0, 1000, "台词", role="narrator")])
    hits = [it for it in report.items if "narrator" in it.message]
    assert len(hits) == 1
    item = hits[0]
    assert item.level == "warn"            # structured, non-silent
    assert item.area == "technical"
    assert "role" in item.message
    assert "translation" in item.suggestion or "translation" in item.message
    assert report.ok is True               # NEVER a blocker


def test_known_roles_and_none_produce_no_role_item(tmp_project):
    report = _qc_captions_report(tmp_project, [
        _cue(0, 1000, "一"), _cue(1200, 2000, "二", role="forced"),
        _cue(2200, 3000, "三", role="sdh"),
    ])
    assert not [it for it in report.items if "role" in it.message.lower()]
    assert report.ok is True


# =========================================================== 3. CPS math (CJK)


def test_char_weights_east_asian_width_rule():
    from manju.qc.captions_access import char_weight

    assert char_weight("a") == 1          # Na
    assert char_weight(" ") == 1          # spaces are read time too
    assert char_weight(",") == 1          # ASCII comma
    assert char_weight("你") == 2         # W: CJK unified
    assert char_weight("。") == 2         # W: CJK punctuation
    assert char_weight("ア") == 2         # W: katakana
    assert char_weight("한") == 2         # W: hangul
    assert char_weight("Ａ") == 2     # F: fullwidth latin A
    assert char_weight("，") == 2     # F: fullwidth comma
    assert char_weight("…") == 1     # A (ambiguous, e.g. …) counts 1
    assert char_weight("\n") == 0         # a break is not read


def test_weighted_len_exact():
    from manju.qc.captions_access import weighted_len

    assert weighted_len("hello") == 5
    assert weighted_len("你好世界") == 8
    assert weighted_len("你好ab") == 6
    assert weighted_len("你好\nab") == 6   # newline weight 0


def test_cps_exact_ascii():
    from manju.qc.captions_access import cue_metrics

    m = cue_metrics(_cue(0, 1000, "hello"))
    assert m["weighted_chars"] == 5
    assert m["cps"] == 5.0


def test_cps_exact_cjk_doubling():
    from manju.qc.captions_access import cue_metrics

    m = cue_metrics(_cue(0, 2000, "你好世界"))   # 8 weighted over 2s
    assert m["weighted_chars"] == 8
    assert m["cps"] == 4.0


def test_cps_exact_mixed():
    from manju.qc.captions_access import cue_metrics

    m = cue_metrics(_cue(0, 1200, "你好ab"))     # 6 weighted over 1.2s
    assert m["weighted_chars"] == 6
    assert m["cps"] == 5.0


def test_cps_non_positive_duration_is_none_never_a_crash():
    from manju.qc.captions_access import cue_metrics

    assert cue_metrics(_cue(1000, 1000, "x"))["cps"] is None
    assert cue_metrics(_cue(1000, 900, "x"))["cps"] is None


# ===================================================== 4. line lengths / counts


def test_line_weights_author_newlines():
    from manju.qc.captions_access import cue_metrics

    m = cue_metrics(_cue(0, 1000, "你好你好\nab"))
    assert m["lines"] == 2
    assert m["line_weights"] == [8, 2]


def test_line_weights_compiled_mode_applies_declared_budget():
    """In compiled mode the report measures what the exporter renders: the same
    break_lines budget compile_srt/compile_ass apply."""
    from manju.qc.captions_access import cue_metrics

    m = cue_metrics(_cue(0, 3000, "一" * 20), max_chars_per_line=10)
    assert m["lines"] == 2
    assert m["line_weights"] == [20, 20]   # 10 CJK chars = weight 20 per line


# ==================================================== 5. advisory catalogue


def _report_for(captions, **kw):
    from manju.qc.captions_access import captions_accessibility

    return captions_accessibility(_tl(captions), **kw)


def _codes(doc):
    return [a["code"] for a in doc["advisories"]]


def test_cps_high_advisory():
    doc = _report_for([_cue(0, 1000, "一" * 15)])   # 30 weighted cps > 20
    assert "CPS_HIGH" in _codes(doc)


def test_line_too_long_advisory():
    doc = _report_for([_cue(0, 60_000, "一" * 22)])  # 44 weighted > 42, slow cps
    assert "LINE_TOO_LONG" in _codes(doc)
    assert "CPS_HIGH" not in _codes(doc)


def test_too_many_lines_advisory():
    doc = _report_for([_cue(0, 60_000, "a\nb\nc")])
    assert "TOO_MANY_LINES" in _codes(doc)


def test_duration_floor_and_ceiling_advisories():
    doc = _report_for([
        _cue(0, 500, "短"),                 # 500ms < 833ms floor
        _cue(1000, 8500, "长长的一句"),      # 7500ms > 7000ms ceiling
        _cue(9000, 9833, "边界"),            # exactly 833ms → no advisory
    ])
    codes = _codes(doc)
    assert codes.count("DURATION_UNDER_FLOOR") == 1
    assert codes.count("DURATION_OVER_CEILING") == 1


def test_gap_too_short_advisory_exact():
    doc = _report_for([_cue(0, 1000, "一"), _cue(1050, 2000, "二")])
    gaps = [a for a in doc["advisories"] if a["code"] == "GAP_TOO_SHORT"]
    assert len(gaps) == 1
    assert gaps[0]["gap_ms"] == 50
    # butt-joined (gap 0) is deliberate, not an advisory
    doc2 = _report_for([_cue(0, 1000, "一"), _cue(1000, 2000, "二")])
    assert "GAP_TOO_SHORT" not in _codes(doc2)


def test_overlap_advisory_exact():
    doc = _report_for([_cue(0, 1000, "一"), _cue(900, 2000, "二")])
    overlaps = [a for a in doc["advisories"] if a["code"] == "CUE_OVERLAP"]
    assert len(overlaps) == 1
    assert overlaps[0]["overlap_ms"] == 100


def test_forced_overlapping_nonforced_advisory():
    doc = _report_for([
        _cue(0, 1000, "画外音", role="forced"),
        _cue(500, 1500, "对白"),
    ])
    assert "FORCED_OVERLAPS_NONFORCED" in _codes(doc)
    # two forced cues overlapping is only a plain overlap
    doc2 = _report_for([
        _cue(0, 1000, "一", role="forced"),
        _cue(500, 1500, "二", role="forced"),
    ])
    assert "FORCED_OVERLAPS_NONFORCED" not in _codes(doc2)
    assert "CUE_OVERLAP" in _codes(doc2)


def test_unknown_role_advisory_in_report():
    doc = _report_for([_cue(0, 60_000, "x", role="narrator")])
    assert "ROLE_UNKNOWN" in _codes(doc)


def test_every_advisory_row_is_severity_advisory():
    doc = _report_for([
        _cue(0, 100, "一" * 30 + "\n" + "二" * 30 + "\na"),
        _cue(50, 8600, "重叠", role="forced"),
        _cue(8650, 8700, "又短又急", role="bogus"),
    ])
    assert doc["advisories"], "the fixture must actually trip advisories"
    assert all(a["severity"] == "advisory" for a in doc["advisories"])


# ========================================= 6. summary: distribution + coverage


def test_reading_speed_distribution_summary():
    doc = _report_for([
        _cue(0, 1000, "hello"),        # 5.0 cps
        _cue(2000, 3000, "你好世界"),   # 8.0 cps
        _cue(4000, 5000, "一" * 15),    # 30.0 cps
    ])
    rs = doc["summary"]["reading_speed"]
    assert rs["min"] == 5.0
    assert rs["max"] == 30.0
    assert rs["mean"] == round((5.0 + 8.0 + 30.0) / 3, 2)
    assert rs["median"] == 8.0
    assert rs["over_ceiling"] == 1
    assert sum(rs["buckets"].values()) == 3


def test_role_coverage_summary():
    doc = _report_for([
        _cue(0, 1000, "一", role="forced"),
        _cue(1200, 2000, "二", role="sdh"),
        _cue(2200, 3000, "三", role="sdh"),
        _cue(3200, 4000, "四"),
        _cue(4200, 5000, "五", role="narrator"),
    ])
    cov = doc["summary"]["role_coverage"]
    assert cov["counts"] == {"forced": 1, "narrator": 1, "sdh": 2}
    assert cov["unroled"] == 1
    assert cov["unknown_roles"] == ["narrator"]


# ======================================= 7. determinism + content-addressed IO


def test_report_schema_and_digest_deterministic():
    from manju.qc.captions_access import SCHEMA, captions_accessibility

    tl = _tl([_cue(0, 1000, "你好")])
    a = captions_accessibility(tl)
    b = captions_accessibility(tl)
    assert a["schema"] == SCHEMA == "manju.caption-accessibility/v1"
    assert a["report_digest"] == b["report_digest"]
    assert a == b
    c = captions_accessibility(_tl([_cue(0, 1000, "别的")]))
    assert c["report_digest"] != a["report_digest"]


def test_write_accessibility_is_content_addressed_and_deletable(tmp_project):
    from manju.qc.captions_access import (
        accessibility_for_project, write_accessibility)

    tmp_project.save_timeline(_tl([_cue(0, 500, "急促字幕", role="forced")]))
    doc = accessibility_for_project(tmp_project)
    path = write_accessibility(tmp_project, doc)
    assert path.exists()
    assert path.parent == tmp_project.reports_dir / "captions"
    hexpart = doc["report_digest"].split(":", 1)[1]
    assert hexpart in path.name
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == doc
    # deletable derived projection: deleting + rebuilding reproduces it exactly
    path.unlink()
    doc2 = accessibility_for_project(tmp_project)
    assert write_accessibility(tmp_project, doc2) == path


def test_advisories_never_change_qc_outcome(tmp_project):
    """The loop pin: a caption set drowning in advisories leaves the actual QC
    verdict EXACTLY as it was — advisory rows live only in the derived report."""
    from manju.qc.checks import QCReport, _technical_captions
    from manju.qc.captions_access import accessibility_for_project

    tmp_project.save_timeline(_tl([
        _cue(0, 100, "一" * 40),            # cps through the roof + over budget
        _cue(50, 9000, "重叠且过长", role="forced"),
    ]))
    doc = accessibility_for_project(tmp_project)
    assert len(doc["advisories"]) >= 3
    report = QCReport()
    _technical_captions(tmp_project, report, tmp_project.load_timeline())
    # the pre-existing captions QC still decides ok on its own facts — the
    # advisory report added NOTHING to it (no error, no new warn kinds beyond
    # the unknown-role warn this loop declared)
    assert report.ok is True


# ============================================================ 8. export flows


def test_ass_dialogue_roleless_is_byte_identical():
    tl = _tl([_cue(0, 2000, "测试字幕")])
    ass = compile_ass(tl, width=1080, height=1920)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert dialogue == "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,测试字幕"


def test_role_lands_verbatim_in_ass_name_field():
    tl = _tl([_cue(0, 2000, "测试字幕", role="forced")])
    ass = compile_ass(tl, width=1080, height=1920)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert dialogue == "Dialogue: 0,0:00:00.00,0:00:02.00,Default,forced,0,0,0,,测试字幕"


def test_hostile_role_cannot_break_the_dialogue_field_grid():
    tl = _tl([_cue(0, 2000, "text", role="a,b{\\pos(0,0)}\nc")])
    ass = compile_ass(tl, width=1080, height=1920)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    body = dialogue[len("Dialogue: "):]
    # the Text field is the 10th; everything before it must still be 9 commas
    assert len(body.split(",", 9)) == 10
    assert body.split(",", 9)[4] != "a"     # the comma did NOT split the role
    assert "{\\pos" not in dialogue         # override syntax neutralized
    assert "\n" not in dialogue.replace("\\n", "")


def test_srt_and_vtt_bytes_identical_with_and_without_roles():
    roleless = _tl([_cue(0, 2000, "一句"), _cue(2500, 4000, "两句")])
    roled = _tl([
        _cue(0, 2000, "一句", role="forced"),
        _cue(2500, 4000, "两句", role="sdh"),
    ])
    assert compile_srt(roleless) == compile_srt(roled)
    assert compile_vtt(roleless) == compile_vtt(roled)
    # and the role-less ASS output equals itself before/after the feature: the
    # Name field stays empty (covered byte-exactly above)


# ===================================================== 9. delivery manifest row


def _manifest_with_captions(tmp_project, captions):
    from manju.build import delivery as D
    from manju.core.models import TimelineMeta, TimelineRules, VideoClip
    from manju.core.yamlio import write_yaml

    write_yaml(tmp_project.rules_path, TimelineRules(mode="manual").model_dump())
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="take_01",
                             source="media/gen/S001/take_01.mp4",
                             start_ms=0, duration_ms=2000)],
            captions=captions,
        ),
    )
    tmp_project.save_timeline(tl)
    return D.build_manifest(tmp_project, "master")


def test_caption_artifact_rows_carry_role_counts_additively(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    man = _manifest_with_captions(tmp_project, [
        _cue(0, 900, "一", role="forced"),
        _cue(1000, 1400, "二", role="sdh"),
        _cue(1500, 1900, "三", role="sdh"),
        _cue(1950, 2000, "四"),
    ])
    caption_rows = [a for a in man["artifacts"]
                    if a["role"] in ("CAPTIONS_SRT", "CAPTIONS_ASS", "CAPTIONS_VTT")]
    assert caption_rows
    for row in caption_rows:
        assert row["caption_roles"] == {"forced": 1, "sdh": 2}
    for row in man["artifacts"]:
        if row["role"] not in ("CAPTIONS_SRT", "CAPTIONS_ASS", "CAPTIONS_VTT"):
            assert "caption_roles" not in row


def test_roleless_manifest_has_no_caption_roles_key_and_same_digest(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    roleless = _manifest_with_captions(tmp_project, [
        _cue(0, 900, "一"), _cue(1000, 1900, "二")])
    assert all("caption_roles" not in a for a in roleless["artifacts"])
    digest_roleless = roleless["manifest_digest"]
    # setting roles adds ONLY the additive key — the digest core is untouched
    # (the caption files' sha256 already bind the delivered bytes, and SRT/VTT
    # bytes do not change with roles; same stance as the additive audio block)
    roled = _manifest_with_captions(tmp_project, [
        _cue(0, 900, "一", role="forced"), _cue(1000, 1900, "二")])
    assert any(a.get("caption_roles") for a in roled["artifacts"])
    assert roled["manifest_digest"] == digest_roleless


# ================================================================ 10. the CLI


def test_qc_captions_cli_exits_zero_with_advisories(tmp_project, monkeypatch):
    from manju.cli import app

    tmp_project.save_timeline(_tl([
        _cue(0, 100, "一" * 40), _cue(50, 9000, "重叠", role="bogus")]))
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["qc", "captions", "--json"])
    assert res.exit_code == 0, res.output      # advisories NEVER block
    doc = json.loads(res.output)
    assert doc["schema"] == "manju.caption-accessibility/v1"
    assert doc["advisories"]
    assert all(a["severity"] == "advisory" for a in doc["advisories"])


def test_qc_captions_cli_write_materializes_report(tmp_project, monkeypatch):
    from manju.cli import app

    tmp_project.save_timeline(_tl([_cue(0, 2000, "正常字幕速度")]))
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["qc", "captions", "--write"])
    assert res.exit_code == 0, res.output
    files = list((tmp_project.reports_dir / "captions").glob("*.json"))
    assert len(files) == 1


def test_qc_captions_cli_empty_project_still_exits_zero(tmp_project, monkeypatch):
    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["qc", "captions", "--json"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["cues"] == []
