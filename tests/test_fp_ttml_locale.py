"""FP Loop U2 — locale-aware TTML (roadmap item 4 remnant).

Red-first pins for the locale overlay's TTML exit. Three additive seams,
each proven here BEFORE it is built:

- ``exporters/ttml.py`` gains an additive keyword-only ``direction: str | None``
  param. ``"rtl"`` emits ``tts:direction="rtl"`` + ``tts:unicodeBidi="embed"``
  on the content ``<div>``; ``"ltr"`` emits ``tts:direction="ltr"`` only;
  ``None`` (the default, and every existing caller) is BYTE-IDENTICAL to the
  pre-change writer — pinned by a golden captured from the old output.
- ``build/locale_build.py``'s ``export_locale_captions`` writes a third
  sibling ``captions.ttml`` (next to ``.srt``/``.ass``) with
  ``xml:lang == <locale id>`` — the ONE caller that carries a declared
  language (the base project honestly records none). It reads the optional
  declared direction and returns an extra ``"ttml"`` key.
- ``core/locale.py``'s ``load_locale_meta`` reads the OPTIONAL declared file
  ``locales/<lang>/meta.yaml`` (single field today: ``direction: rtl|ltr``).
  Missing → ``{}``. An unknown key or a direction outside ``{rtl, ltr}`` is a
  STRUCTURED rejection (S4 ``cache_toolchain_keys`` precedent). Direction is an
  EXPLICIT human declaration — NEVER inferred from the language code.

Ruby / vertical writing stay OUT OF SCOPE (the cue model has no ruby or
writing-mode structure — S1's recorded reason stands). RTL here is a per-locale
human DECLARATION layered on top of the cue model, never derived from cues.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import pytest

from manju.core.models import CaptionLine, Timeline, TimelineTracks
from manju.core.yamlio import write_yaml

TT = "http://www.w3.org/ns/ttml"
TTS = "http://www.w3.org/ns/ttml#styling"
XML_NS = "http://www.w3.org/XML/1998/namespace"

DIRECTION = f"{{{TTS}}}direction"
UNICODE_BIDI = f"{{{TTS}}}unicodeBidi"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #

def _tl(captions: list[CaptionLine], **kw) -> Timeline:
    kw.setdefault("fps", 24)
    kw.setdefault("width", 1080)
    kw.setdefault("height", 1920)
    kw.setdefault("duration_ms", max((c.end_ms for c in captions), default=0))
    return Timeline(tracks=TimelineTracks(captions=captions), **kw)


def _cue(start_ms: int, end_ms: int, text: str, **kw) -> CaptionLine:
    return CaptionLine(start_ms=start_ms, end_ms=end_ms, text=text, **kw)


def _div(doc: str) -> ET.Element:
    div = ET.fromstring(doc).find(f"{{{TT}}}body/{{{TT}}}div")
    assert div is not None, "every document has a content <div>"
    return div


def _write_meta(project, lang: str, data) -> None:
    d = project.root / "locales" / lang
    d.mkdir(parents=True, exist_ok=True)
    write_yaml(d / "meta.yaml", data)


# The canonical byte-identity fixture and the golden captured from the writer
# BEFORE the ``direction`` param existed (roles + speakers + CJK + a hostile
# role + a <br/> break — a rich document). If the None path ever changes a
# single byte, GOLDEN_NONE_SHA breaks. GOLDEN_NONE gives a readable diff.
def _golden_fixture() -> Timeline:
    return _tl([
        _cue(0, 1000, "深夜\n便利店", speaker="林夏", role="sdh"),
        _cue(1000, 2000, 'A & B "q"', role="x-自定义"),
        _cue(2000, 3000, "→", speaker="老板", role="forced"),
    ])


GOLDEN_NONE_SHA = "ae3c4e62cc45424717dc5373d3ae272692b660c4cd899c895f4d2f784ccf940f"

GOLDEN_NONE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<tt xmlns="http://www.w3.org/ns/ttml"'
    ' xmlns:itts="http://www.w3.org/ns/ttml/profile/imsc1#styling"'
    ' xmlns:ttm="http://www.w3.org/ns/ttml#metadata"'
    ' xmlns:ttp="http://www.w3.org/ns/ttml#parameter"'
    ' xmlns:tts="http://www.w3.org/ns/ttml#styling"'
    ' xmlns:x-manju="urn:x-manju:ttml"'
    ' ttp:timeBase="media" xml:lang="">\n'
    "  <head>\n"
    "    <metadata>\n"
    '      <ttm:agent type="character" xml:id="agent.1">\n'
    '        <ttm:name type="alias">林夏</ttm:name>\n'
    "      </ttm:agent>\n"
    '      <ttm:agent type="character" xml:id="agent.2">\n'
    '        <ttm:name type="alias">老板</ttm:name>\n'
    "      </ttm:agent>\n"
    "    </metadata>\n"
    "    <styling>\n"
    '      <style tts:color="white" tts:fontFamily="proportionalSansSerif"'
    ' xml:id="s.default"/>\n'
    "    </styling>\n"
    "    <layout>\n"
    '      <region tts:displayAlign="after" tts:extent="80% 15%"'
    ' tts:origin="10% 80%" tts:textAlign="center" xml:id="r.bottom"/>\n'
    "    </layout>\n"
    "  </head>\n"
    '  <body region="r.bottom" style="s.default">\n'
    "    <div>\n"
    '      <p begin="00:00:00.000" end="00:00:01.000" ttm:agent="agent.1"'
    ' ttm:role="captions" x-manju:role="sdh">深夜<br/>便利店</p>\n'
    '      <p begin="00:00:01.000" end="00:00:02.000"'
    ' x-manju:role="x-自定义">A &amp; B "q"</p>\n'
    '      <p begin="00:00:02.000" end="00:00:03.000" itts:forcedDisplay="true"'
    ' ttm:agent="agent.2" x-manju:role="forced">→</p>\n'
    "    </div>\n"
    "  </body>\n"
    "</tt>\n"
)


# ============================== 1. ttml.py: additive ``direction`` param ===== #


def test_none_direction_is_byte_identical_to_pre_change_golden():
    """The load-bearing regression pin: with ``direction=None`` (and with the
    param absent) the writer emits EXACTLY the bytes it emitted before the
    param existed. Captured golden — proves the additive param is inert."""
    from manju.exporters.ttml import compile_ttml

    out = compile_ttml(_golden_fixture(), direction=None)
    assert hashlib.sha256(out.encode("utf-8")).hexdigest() == GOLDEN_NONE_SHA
    assert out == GOLDEN_NONE  # readable diff if a byte ever moves


def test_direction_default_is_none_and_matches_explicit_none():
    from manju.exporters.ttml import compile_ttml

    tl = _golden_fixture()
    assert compile_ttml(tl) == compile_ttml(tl, direction=None)
    # the bare-default output is the golden too (existing callers unchanged)
    assert compile_ttml(tl) == GOLDEN_NONE


def test_direction_rtl_emits_both_attrs_on_div_with_exact_values():
    from manju.exporters.ttml import compile_ttml

    doc = compile_ttml(_tl([_cue(0, 1000, "مرحبا")]), lang="ar", direction="rtl")
    div = _div(doc)
    assert div.get(DIRECTION) == "rtl"
    assert div.get(UNICODE_BIDI) == "embed"
    # sorted, deterministic attribute order on the div open tag
    assert 'tts:direction="rtl" tts:unicodeBidi="embed"' in doc


def test_direction_ltr_emits_direction_only_no_unicode_bidi():
    from manju.exporters.ttml import compile_ttml

    doc = compile_ttml(_tl([_cue(0, 1000, "hello")]), lang="en", direction="ltr")
    div = _div(doc)
    assert div.get(DIRECTION) == "ltr"
    assert div.get(UNICODE_BIDI) is None, "ltr sets direction only"
    assert "tts:unicodeBidi" not in doc


def test_direction_none_leaves_div_bare_no_direction_attrs():
    from manju.exporters.ttml import compile_ttml

    doc = compile_ttml(_tl([_cue(0, 1000, "x")]), direction=None)
    div = _div(doc)
    assert div.get(DIRECTION) is None
    assert div.get(UNICODE_BIDI) is None
    assert "\n    <div>\n" in doc and "<div " not in doc


def test_direction_with_lang_and_cjk_plus_rtl_text_roundtrips():
    """CJK + RTL text survive together in one rtl document (UTF-8, no
    entity-mangling), and both bidi attributes are present."""
    from manju.exporters.ttml import compile_ttml

    text = "深夜 مرحبا"  # CJK + Arabic in one cue
    doc = compile_ttml(_tl([_cue(0, 2000, text)]), lang="ar", direction="rtl")
    assert text in doc, "mixed CJK+RTL text must pass through verbatim"
    root = ET.fromstring(doc)
    assert root.get(f"{{{XML_NS}}}lang") == "ar"
    (p,) = root.findall(f".//{{{TT}}}p")
    assert p.text == text
    div = _div(doc)
    assert div.get(DIRECTION) == "rtl" and div.get(UNICODE_BIDI) == "embed"


def test_direction_output_is_deterministic():
    from manju.exporters.ttml import compile_ttml

    def build():
        return _tl([_cue(0, 1000, "深夜 مرحبا", speaker="林夏", role="sdh"),
                    _cue(1000, 2000, "→", role="forced")])

    a = compile_ttml(build(), lang="ar", direction="rtl")
    b = compile_ttml(build(), lang="ar", direction="rtl")
    assert a == b


# ==================== 2. core/locale.py: load_locale_meta reader/validator == #


def test_load_locale_meta_missing_file_returns_empty(tmp_project):
    from manju.core.locale import load_locale_meta

    assert load_locale_meta(tmp_project, "ar") == {}


def test_load_locale_meta_empty_file_returns_empty(tmp_project):
    from manju.core.locale import load_locale_meta

    d = tmp_project.root / "locales" / "ar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.yaml").write_text("", encoding="utf-8")
    assert load_locale_meta(tmp_project, "ar") == {}


def test_load_locale_meta_direction_rtl(tmp_project):
    from manju.core.locale import load_locale_meta

    _write_meta(tmp_project, "ar", {"direction": "rtl"})
    assert load_locale_meta(tmp_project, "ar") == {"direction": "rtl"}


def test_load_locale_meta_direction_ltr(tmp_project):
    from manju.core.locale import load_locale_meta

    _write_meta(tmp_project, "en", {"direction": "ltr"})
    assert load_locale_meta(tmp_project, "en") == {"direction": "ltr"}


def test_load_locale_meta_unknown_key_is_structured_rejection(tmp_project):
    from manju.core.container import ProjectError
    from manju.core.locale import load_locale_meta

    _write_meta(tmp_project, "ar", {"direction": "rtl", "writing_mode": "tb"})
    with pytest.raises(ProjectError) as exc:
        load_locale_meta(tmp_project, "ar")
    msg = str(exc.value)
    assert "writing_mode" in msg          # names the offending key
    assert "direction" in msg             # names the allowed set


def test_load_locale_meta_bad_direction_is_structured_rejection(tmp_project):
    from manju.core.container import ProjectError
    from manju.core.locale import load_locale_meta

    _write_meta(tmp_project, "ar", {"direction": "sideways"})
    with pytest.raises(ProjectError) as exc:
        load_locale_meta(tmp_project, "ar")
    msg = str(exc.value)
    assert "sideways" in msg              # names the offending value
    assert "rtl" in msg and "ltr" in msg  # names the allowed values


def test_load_locale_meta_direction_never_inferred_from_lang(tmp_project):
    """No meta.yaml for a canonically-RTL language (Arabic) yields NO direction
    — the engine never infers direction from the language code."""
    from manju.core.locale import load_locale_meta

    assert load_locale_meta(tmp_project, "ar") == {}
    assert "direction" not in load_locale_meta(tmp_project, "ar")


def test_load_locale_meta_non_mapping_is_structured_rejection(tmp_project):
    from manju.core.container import ProjectError
    from manju.core.locale import load_locale_meta

    d = tmp_project.root / "locales" / "ar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.yaml").write_text("- rtl\n- ltr\n", encoding="utf-8")  # a list
    with pytest.raises(ProjectError):
        load_locale_meta(tmp_project, "ar")


# =============== 3. build/locale_build.py: export_locale_captions ttml ====== #


def _locale_tl() -> Timeline:
    return _tl([_cue(0, 2000, "深夜 مرحبا", speaker="林夏")])


def test_export_locale_captions_writes_ttml_with_locale_xml_lang(tmp_project):
    from manju.build.locale_build import export_locale_captions

    paths = export_locale_captions(tmp_project, _locale_tl(), "ar")
    assert "ttml" in paths, "the ttml sibling joins srt/ass"
    ttml = paths["ttml"]
    assert ttml.exists()
    assert ttml == tmp_project.captions_dir / "locales" / "ar" / "captions.ttml"
    root = ET.fromstring(ttml.read_text(encoding="utf-8"))
    # the declared locale id lands on xml:lang (base export keeps "")
    assert root.get(f"{{{XML_NS}}}lang") == "ar"


def test_export_locale_captions_ttml_rtl_from_declared_meta(tmp_project):
    from manju.build.locale_build import export_locale_captions

    _write_meta(tmp_project, "ar", {"direction": "rtl"})
    paths = export_locale_captions(tmp_project, _locale_tl(), "ar")
    doc = paths["ttml"].read_text(encoding="utf-8")
    div = _div(doc)
    assert div.get(DIRECTION) == "rtl"
    assert div.get(UNICODE_BIDI) == "embed"
    assert "深夜 مرحبا" in doc  # CJK + RTL sample text present in the artifact


def test_export_locale_captions_ttml_ltr_from_declared_meta(tmp_project):
    from manju.build.locale_build import export_locale_captions

    _write_meta(tmp_project, "en", {"direction": "ltr"})
    paths = export_locale_captions(tmp_project, _locale_tl(), "en")
    div = _div(paths["ttml"].read_text(encoding="utf-8"))
    assert div.get(DIRECTION) == "ltr"
    assert div.get(UNICODE_BIDI) is None


def test_export_locale_captions_ttml_no_meta_has_no_direction(tmp_project):
    from manju.build.locale_build import export_locale_captions

    paths = export_locale_captions(tmp_project, _locale_tl(), "ar")
    doc = paths["ttml"].read_text(encoding="utf-8")
    div = _div(doc)
    assert div.get(DIRECTION) is None
    assert div.get(UNICODE_BIDI) is None
    assert "\n    <div>\n" in doc  # bare div, drop-when-absent


def test_export_locale_captions_ttml_is_deterministic(tmp_project):
    from manju.build.locale_build import export_locale_captions

    _write_meta(tmp_project, "ar", {"direction": "rtl"})
    first = export_locale_captions(tmp_project, _locale_tl(), "ar")["ttml"].read_bytes()
    second = export_locale_captions(tmp_project, _locale_tl(), "ar")["ttml"].read_bytes()
    assert first == second
    assert b"\r" not in first


def test_export_locale_captions_paths_are_relpath_safe(tmp_project):
    """graph.py consumes the returned dict as
    ``{k: project.relpath(v) for k, v in paths.items()}`` — every value
    (including the new ttml key) must be a project-relative path."""
    from manju.build.locale_build import export_locale_captions

    paths = export_locale_captions(tmp_project, _locale_tl(), "ar")
    rel = {k: tmp_project.relpath(v) for k, v in paths.items()}
    assert rel["ttml"] == "captions/locales/ar/captions.ttml"
    assert set(paths) >= {"srt", "ass", "ttml"}
