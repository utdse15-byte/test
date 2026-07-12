"""FP Loop S4 — byte-affecting toolchain facts into render cache keys,
STRICTLY OPT-IN (user item 7), with the doc's own warning enforced at
validation: irrelevant facts are REJECTED so they can never cause meaningless
rebuilds.

Red-first pins, in seven groups:

* **validation** — ``ProjectConfig.cache_toolchain_keys`` accepts EXACTLY the
  byte-affecting tokens {"ffmpeg", "fonts"}; anything else (os/python/deps/…)
  is a structured error naming the allowed set and WHY; an empty list is an
  error (absent is the off switch); duplicates are rejected;
* **serialization byte pins** — the absent field appears in NO dump form and
  save_config stays byte-stable (R1 edit_rate drop-None precedent);
* **absent ⇒ byte-identical keys** — every key site (segment, boundary, final
  payload) produces the pre-S4 literal when the field is absent, INCLUDING the
  rational rate_key interplay (rate alone appends exactly R2's rate part);
* **opt-in behaviour** — ffmpeg token: the key shifts exactly when the
  (monkeypatched) ``-version`` first line shifts; fonts token: the key shifts
  on the drawtext font hash; only the OPTED facts are collected; a missing
  tool keys the honest string ``"missing"`` (deterministic, never a crash);
  flipping the field off restores the byte-identical pre-S4 key;
* **process-cached collectors** — a call-count spy proves ONE probe per
  process, never one per key;
* **evidence** — the key sidecar breakdown carries the toolchain component and
  still re-hashes back to the final key (WP3 e2 invariant);
* **animatic (build/graph)** — the animatic outer key + kenburns clip key flip
  on opt-in and return EXACTLY to the original when opted out (ffmpeg-marked).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import manju.core.toolchain as toolchain_mod
from manju.core.container import Project
from manju.core.hashing import cache_key, hash_file
from manju.core.models import (
    EditRate,
    ProjectConfig,
    Timeline,
    TimelineTracks,
    VideoClip,
)
from manju.core.toolchain import (
    cached_drawtext_font_hash,
    cached_tool_version_line,
)
from manju.media.render import (
    _Boundary,
    _boundary_cache_key,
    _final_key_payload,
    _key_inputs_breakdown,
    _segment_cache_key,
    _toolchain_key_component,
    final_content_key,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
ffmpeg_only = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe required")

_ALLOWED = ("ffmpeg", "fonts")
_SHA_A = "sha256:" + "a" * 64
_SHA_B = "sha256:" + "b" * 64


@pytest.fixture(autouse=True)
def _clean_collector_caches():
    """The S4 collectors are process-cached (that IS the deliverable) — clear
    them around every test so a monkeypatched fact can never leak into another
    test in the same pytest process."""
    cached_tool_version_line.cache_clear()
    cached_drawtext_font_hash.cache_clear()
    yield
    cached_tool_version_line.cache_clear()
    cached_drawtext_font_hash.cache_clear()


# --------------------------------------------------------------------- helpers


def _fake_take(project: Project, name: str) -> str:
    p = project.imports_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"deterministic-take-" + name.encode())
    return project.relpath(p)


def _timeline(project: Project, *, rational: bool = False) -> Timeline:
    a = _fake_take(project, "a.mp4")
    b = _fake_take(project, "b.mp4")
    echo = EditRate(num=24000, den=1001) if rational else None
    dur = 2002 if rational else 2000
    return Timeline(
        fps=24, width=1080, height=1920, duration_ms=2 * dur, rate_echo=echo,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S1", take="t", source=a, start_ms=0, duration_ms=dur,
                      duration_frames=48 if rational else None),
            VideoClip(shot="S2", take="t", source=b, start_ms=dur, duration_ms=dur,
                      duration_frames=48 if rational else None),
        ]),
    )


def _opt_in(project: Project, tokens: list[str] | None) -> None:
    config = project.load_config()
    config.cache_toolchain_keys = tokens
    project.save_config(config)


def _patch_ffmpeg_probe(monkeypatch, line: str) -> dict[str, int]:
    """Replace the underlying -version probe with a deterministic fake and
    return its call counter. Callers must have cleared the process cache
    (the autouse fixture does)."""
    calls = {"n": 0}

    def fake(name: str) -> str:
        calls["n"] += 1
        return line

    monkeypatch.setattr(toolchain_mod, "_tool_version_line", fake)
    cached_tool_version_line.cache_clear()
    return calls


def _patch_font_inventory(monkeypatch, sha: str | None) -> dict[str, int]:
    """Replace the font collector: sha=None means the honest 'unknown' gap."""
    calls = {"n": 0}

    def fake() -> dict:
        calls["n"] += 1
        if sha is None:
            return {"drawtext_cjk": "unknown"}
        return {"drawtext_cjk": {"basename": "wqy-zenhei.ttc", "sha256": sha}}

    monkeypatch.setattr(toolchain_mod, "_font_inventory", fake)
    cached_drawtext_font_hash.cache_clear()
    return calls


# =====================================================================
# 1. VALIDATION — allowed tokens EXACTLY {ffmpeg, fonts}; the doc's own
#    warning enforced (irrelevant facts rejected, never meaningless rebuilds)
# =====================================================================


def test_disallowed_tokens_rejected_with_allowed_set_and_why():
    """os/python/deps/… are machine facts that never change rendered bytes —
    the validator refuses them, NAMING the allowed set and the reason."""
    for bad in ("os", "python", "deps", "locale", "manju", "ffprobe", "chromium"):
        with pytest.raises(ValidationError) as err:
            ProjectConfig(name="t", cache_toolchain_keys=[bad])
        msg = str(err.value)
        assert repr(bad) in msg, f"error must name the rejected token {bad!r}: {msg}"
        for allowed in _ALLOWED:
            assert allowed in msg, f"error must name the allowed set: {msg}"
        assert "无意义" in msg and "重建" in msg, (
            f"error must state WHY (irrelevant facts => meaningless rebuilds): {msg}")


def test_disallowed_token_rejected_even_alongside_allowed_ones():
    with pytest.raises(ValidationError):
        ProjectConfig(name="t", cache_toolchain_keys=["ffmpeg", "python"])


def test_empty_list_rejected_use_absent_instead():
    with pytest.raises(ValidationError) as err:
        ProjectConfig(name="t", cache_toolchain_keys=[])
    assert "空列表" in str(err.value)


def test_duplicate_tokens_rejected():
    with pytest.raises(ValidationError) as err:
        ProjectConfig(name="t", cache_toolchain_keys=["ffmpeg", "ffmpeg"])
    assert "重复" in str(err.value)


def test_allowed_token_combinations_accepted():
    for tokens in (["ffmpeg"], ["fonts"], ["ffmpeg", "fonts"], ["fonts", "ffmpeg"]):
        config = ProjectConfig(name="t", cache_toolchain_keys=list(tokens))
        assert config.cache_toolchain_keys == tokens  # authored order kept


def test_non_list_shapes_rejected_by_type():
    for bad in ("ffmpeg", 7, {"ffmpeg": True}):
        with pytest.raises(ValidationError):
            ProjectConfig(name="t", cache_toolchain_keys=bad)


def test_assignment_is_validated_too():
    """validate_assignment: flipping the field on a loaded config goes through
    the same gate — a bad token can never be saved via the API."""
    config = ProjectConfig(name="t")
    with pytest.raises(ValidationError):
        config.cache_toolchain_keys = ["python"]


# =====================================================================
# 2. SERIALIZATION BYTE PINS — absent field emits nothing anywhere
#    (R1 edit_rate drop-None precedent)
# =====================================================================


def test_absent_field_appears_in_no_dump_form():
    c = ProjectConfig(name="t")
    assert c.cache_toolchain_keys is None
    assert "cache_toolchain_keys" not in c.model_dump()          # the wrap serializer
    assert "cache_toolchain_keys" not in c.model_dump(exclude_none=True)
    assert "cache_toolchain_keys" not in c.model_dump_json()
    # R1's edit_rate drop still holds beside the new field (one wrap serializer)
    assert "edit_rate" not in c.model_dump()


def test_present_field_round_trips_and_optout_restores_bytes(tmp_project):
    project = tmp_project
    yaml_path = project.root / "project.yaml"
    project.save_config(project.load_config())
    before = yaml_path.read_bytes()
    assert b"cache_toolchain_keys" not in before

    _opt_in(project, ["ffmpeg", "fonts"])
    text = yaml_path.read_text(encoding="utf-8")
    assert "cache_toolchain_keys" in text
    assert project.load_config().cache_toolchain_keys == ["ffmpeg", "fonts"]

    _opt_in(project, None)  # opt back out: the key vanishes, bytes restored
    assert yaml_path.read_bytes() == before


# =====================================================================
# 3. ABSENT => BYTE-IDENTICAL KEYS — pre-S4 literals at every key site,
#    including the rational rate_key interplay
# =====================================================================


def test_absent_segment_key_is_pre_s4_literal_incl_rational_interplay(tmp_project):
    project = tmp_project
    src = project.root / "seg_src.bin"
    src.write_bytes(b"deterministic-segment-source-bytes")
    clip = VideoClip(shot="S001", take="take_01", source="seg_src.bin",
                     start_ms=0, duration_ms=4000)
    common = dict(width=1080, height=1920, fps=24, target="final",
                  fade_in_ms=0, fade_out_ms=0)
    base = cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0)

    # int project, no opt-in: the pre-R2 literal, exactly as ratemig2 pins it
    assert _segment_cache_key(project, clip, **common) == base
    # explicitly passing toolchain_key=None is the same absent default
    assert _segment_cache_key(project, clip, toolchain_key=None, **common) == base
    # rational WITHOUT toolchain: exactly R2's literal — rate appends alone
    assert _segment_cache_key(project, clip, rate_key="24000/1001", **common) == \
        cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0,
                  ("rate", "24000/1001"))
    # rational WITH toolchain: rate part first, toolchain part after (literal order)
    comp = {"ffmpeg": "ffmpeg version 6.1.1"}
    assert _segment_cache_key(project, clip, rate_key="24000/1001",
                              toolchain_key=comp, **common) == \
        cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0,
                  ("rate", "24000/1001"), ("toolchain", comp))
    # toolchain alone (int project opted in): appended after the base parts
    assert _segment_cache_key(project, clip, toolchain_key=comp, **common) == \
        cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0,
                  ("toolchain", comp))


def test_absent_final_payload_has_no_toolchain_entry_and_pins_parts(tmp_path):
    project = Project.create(tmp_path / "plain", git_init=False)
    tl = _timeline(project)
    payload = _final_key_payload(project, tl, ass_file=None, target="final")
    assert "toolchain" not in payload
    assert set(payload) == {"timeline", "segments", "ass", "audio", "encoding", "target"}
    # the ordered segment keys are the pre-S4 literals
    a = project.resolve(tl.tracks.video[0].source)
    assert payload["segments"][0] == cache_key(
        hash_file(a), 1080, 1920, 24, 2000, "final", 0, 0)
    # rational interplay: a rational timeline on a NOT-opted-in project keys
    # exactly as R2 — rate part present, still no toolchain anywhere
    rat = _timeline(project, rational=True)
    rat_payload = _final_key_payload(project, rat, ass_file=None, target="final")
    assert "toolchain" not in rat_payload
    assert rat_payload["segments"][0] == cache_key(
        hash_file(a), 1080, 1920, 24, 2002, "final", 0, 0, ("rate", "24000/1001"))


def test_absent_boundary_key_is_pre_s4_literal(tmp_project):
    project = tmp_project
    src_a = project.root / "a.bin"
    src_b = project.root / "b.bin"
    src_a.write_bytes(b"boundary-a")
    src_b.write_bytes(b"boundary-b")
    a = VideoClip(shot="S1", take="t", source="a.bin", start_ms=0, duration_ms=2000)
    b = VideoClip(shot="S2", take="t", source="b.bin", start_ms=2000, duration_ms=2000)
    boundary = _Boundary(i=0, requested="xfade_dissolve", duration_ms=500,
                         applied=True, half_ms=240, xfade="fade")
    kw = dict(width=1080, height=1920, fps=24, target="final")
    ka = _segment_cache_key(project, a, fade_in_ms=0, fade_out_ms=0, **kw)
    kb = _segment_cache_key(project, b, fade_in_ms=0, fade_out_ms=0, **kw)
    assert _boundary_cache_key(project, a, b, boundary, **kw) == cache_key(
        "xfade-boundary", ka, kb, "fade", 500, 240, 1080, 1920, 24, "final")
    # opted-in: the component rides the two segment keys (exactly like rate_key)
    comp = {"ffmpeg": "ffmpeg version 6.1.1"}
    ka_t = _segment_cache_key(project, a, fade_in_ms=0, fade_out_ms=0,
                              toolchain_key=comp, **kw)
    kb_t = _segment_cache_key(project, b, fade_in_ms=0, fade_out_ms=0,
                              toolchain_key=comp, **kw)
    assert _boundary_cache_key(project, a, b, boundary, toolchain_key=comp, **kw) == \
        cache_key("xfade-boundary", ka_t, kb_t, "fade", 500, 240, 1080, 1920, 24, "final")


def test_component_is_none_for_a_project_that_never_opted_in(tmp_path, monkeypatch):
    """The component helper is the single opt-in gate: absent field => None,
    and NO collector is ever probed for a not-opted-in project."""
    project = Project.create(tmp_path / "gate", git_init=False)
    ff_calls = _patch_ffmpeg_probe(monkeypatch, "ffmpeg version NEVER")
    font_calls = _patch_font_inventory(monkeypatch, _SHA_A)
    assert _toolchain_key_component(project) is None
    assert _toolchain_key_component(project, project.load_config()) is None
    assert ff_calls["n"] == 0 and font_calls["n"] == 0


# =====================================================================
# 4. OPT-IN BEHAVIOUR — keys shift exactly with the opted facts
# =====================================================================


def test_ffmpeg_token_key_shifts_exactly_when_version_line_shifts(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "ff", git_init=False)
    tl = _timeline(project)
    k_absent = final_content_key(project, tl, ass_file=None, target="final")

    _opt_in(project, ["ffmpeg"])
    font_calls = _patch_font_inventory(monkeypatch, _SHA_A)
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 6.1.1")
    k_a = final_content_key(project, tl, ass_file=None, target="final")
    assert k_a != k_absent, "opting in MUST change the key (that IS the feature)"
    # deterministic: same version line => same key
    assert k_a == final_content_key(project, tl, ass_file=None, target="final")
    payload = _final_key_payload(project, tl, ass_file=None, target="final")
    assert payload["toolchain"] == {"ffmpeg": "ffmpeg version 6.1.1"}
    # the fonts fact is NOT collected when only ffmpeg is opted (strictly opt-in)
    assert font_calls["n"] == 0

    # the key shifts EXACTLY when the version line shifts
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 7.0.2")
    k_b = final_content_key(project, tl, ass_file=None, target="final")
    assert k_b != k_a
    assert _final_key_payload(project, tl, ass_file=None, target="final")[
        "toolchain"] == {"ffmpeg": "ffmpeg version 7.0.2"}


def test_fonts_token_key_shifts_on_font_hash_change(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "fo", git_init=False)
    tl = _timeline(project)
    _opt_in(project, ["fonts"])
    ff_calls = _patch_ffmpeg_probe(monkeypatch, "ffmpeg version NEVER")

    _patch_font_inventory(monkeypatch, _SHA_A)
    k_a = final_content_key(project, tl, ass_file=None, target="final")
    assert _final_key_payload(project, tl, ass_file=None, target="final")[
        "toolchain"] == {"fonts": _SHA_A}

    _patch_font_inventory(monkeypatch, _SHA_B)
    k_b = final_content_key(project, tl, ass_file=None, target="final")
    assert k_b != k_a
    # the ffmpeg fact was never probed for a fonts-only opt-in
    assert ff_calls["n"] == 0


def test_component_is_a_sorted_token_map_independent_of_authored_order(
        tmp_path, monkeypatch):
    project = Project.create(tmp_path / "both", git_init=False)
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 6.1.1")
    _patch_font_inventory(monkeypatch, _SHA_A)

    _opt_in(project, ["fonts", "ffmpeg"])          # authored unsorted
    comp = _toolchain_key_component(project)
    assert comp == {"ffmpeg": "ffmpeg version 6.1.1", "fonts": _SHA_A}
    assert list(comp) == ["ffmpeg", "fonts"]        # sorted token -> fact map
    tl = _timeline(project)
    k_unsorted = final_content_key(project, tl, ass_file=None, target="final")

    _opt_in(project, ["ffmpeg", "fonts"])           # same set, sorted
    assert final_content_key(project, tl, ass_file=None, target="final") == k_unsorted


def test_missing_tool_keys_the_honest_string_missing_never_raises(
        tmp_path, monkeypatch):
    project = Project.create(tmp_path / "miss", git_init=False)
    _opt_in(project, ["ffmpeg"])
    emptybin = tmp_path / "emptybin"
    emptybin.mkdir()
    monkeypatch.setenv("PATH", str(emptybin))       # the REAL collector: no ffmpeg
    cached_tool_version_line.cache_clear()
    comp = _toolchain_key_component(project)
    assert comp == {"ffmpeg": "missing"}
    tl = _timeline(project)
    k1 = final_content_key(project, tl, ass_file=None, target="final")
    assert k1 == final_content_key(project, tl, ass_file=None, target="final")


def test_unlocatable_font_keys_the_honest_string_unknown(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "unk", git_init=False)
    _opt_in(project, ["fonts"])
    _patch_font_inventory(monkeypatch, None)        # collector's honest gap
    assert _toolchain_key_component(project) == {"fonts": "unknown"}


def test_flip_on_then_off_restores_the_byte_identical_key(tmp_path, monkeypatch):
    """Rebuild honesty, both ways: opting in changes the key (cache-cold — the
    documented consequence); opting back OUT restores EXACTLY the pre-S4 key,
    proving the absent payload is byte-identical, not merely similar."""
    project = Project.create(tmp_path / "flip", git_init=False)
    tl = _timeline(project)
    k_before = final_content_key(project, tl, ass_file=None, target="final")

    _opt_in(project, ["ffmpeg"])
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 6.1.1")
    assert final_content_key(project, tl, ass_file=None, target="final") != k_before

    _opt_in(project, None)
    assert final_content_key(project, tl, ass_file=None, target="final") == k_before


# =====================================================================
# 5. PROCESS-CACHED COLLECTORS — one probe per process, never per key
# =====================================================================


def test_collectors_probe_once_per_process_not_per_key(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "spy", git_init=False)
    _opt_in(project, ["ffmpeg", "fonts"])
    ff_calls = _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 6.1.1")
    font_calls = _patch_font_inventory(monkeypatch, _SHA_A)

    tl = _timeline(project)
    comp = _toolchain_key_component(project)
    clip = tl.tracks.video[0]
    boundary = _Boundary(i=0, requested="xfade_dissolve", duration_ms=500,
                         applied=True, half_ms=240, xfade="fade")
    for _ in range(3):                                # many keys, every site
        final_content_key(project, tl, ass_file=None, target="final")
        final_content_key(project, tl, ass_file=None, target="proxy")
        _toolchain_key_component(project)
    for _ in range(10):
        _segment_cache_key(project, clip, width=1080, height=1920, fps=24,
                           target="final", fade_in_ms=0, fade_out_ms=0,
                           toolchain_key=comp)
    _boundary_cache_key(project, tl.tracks.video[0], tl.tracks.video[1], boundary,
                        width=1080, height=1920, fps=24, target="final",
                        toolchain_key=comp)
    assert ff_calls["n"] == 1, f"ffmpeg probed {ff_calls['n']}x — must be once per process"
    assert font_calls["n"] == 1, f"fonts probed {font_calls['n']}x — must be once per process"


def test_cached_wrappers_return_the_collectors_truth():
    """The process-cached wrappers surface the SAME facts the manifest
    collectors record — they can never drift into a parallel account."""
    assert cached_tool_version_line("ffmpeg") == toolchain_mod._tool_version_line("ffmpeg")
    entry = toolchain_mod._font_inventory()["drawtext_cjk"]
    expected = entry["sha256"] if isinstance(entry, dict) else "unknown"
    assert cached_drawtext_font_hash() == expected


# =====================================================================
# 6. EVIDENCE — the sidecar breakdown carries the component and re-hashes
# =====================================================================


def test_key_inputs_breakdown_carries_toolchain_and_rehashes(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "ev", git_init=False)
    tl = _timeline(project)

    plain = _final_key_payload(project, tl, ass_file=None, target="final")
    assert "toolchain" not in _key_inputs_breakdown(plain)

    _opt_in(project, ["ffmpeg"])
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version 6.1.1")
    payload = _final_key_payload(project, tl, ass_file=None, target="final")
    inputs = _key_inputs_breakdown(payload)
    assert inputs["toolchain"] == {"ffmpeg": "ffmpeg version 6.1.1"}
    # WP3 e2 invariant: reconstructing the payload from the breakdown re-hashes
    # back to the final key exactly
    rebuilt = {
        "timeline": inputs["timeline"],
        "segments": inputs["segment_keys"],
        "ass": inputs["ass_sha256"],
        "audio": inputs["audio"],
        "encoding": inputs["encoding"],
        "target": inputs["target"],
        "toolchain": inputs["toolchain"],
    }
    assert cache_key(rebuilt) == final_content_key(
        project, tl, ass_file=None, target="final")


# =====================================================================
# 7. ANIMATIC (build/graph) — outer key + kenburns clip key, flip and restore
# =====================================================================


def _small_animatic_project(tmp_project) -> Project:
    """Shrink the scaffolded project so the kenburns/slate encodes stay tiny."""
    config = tmp_project.load_config()
    config.width, config.height = 192, 336
    tmp_project.save_config(config)
    return tmp_project


@ffmpeg_only
def test_animatic_keys_flip_on_optin_and_restore_on_optout(
        tmp_project, add_shot, monkeypatch):
    from PIL import Image

    from manju.build.graph import _render_animatic

    project = _small_animatic_project(tmp_project)
    add_shot(project, "S001")
    # one real keyframe still -> the kenburns clip path (kb_key site)
    from manju.core.models import TakeSidecar
    still_tmp = project.root / "_kf.png"
    Image.new("RGB", (64, 64), "blue").save(still_tmp)
    project.register_take("S001", still_tmp,
                          TakeSidecar(provider="test", spec_hash="sha256:k"))

    tl = Timeline(
        fps=24, width=192, height=336, duration_ms=3000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="t", source="whatever.mp4",
                      start_ms=0, duration_ms=3000),
        ]),
    )
    from manju.media.render import _read_key_sidecar

    clips_dir = project.root / ".manju" / "animatic" / "clips"

    out1 = _render_animatic(project, tl.model_copy(deep=True))
    k0 = _read_key_sidecar(out1)
    assert k0 is not None
    n_clips_before = len(list(clips_dir.glob("S001_*.mp4")))
    assert n_clips_before == 1

    # idempotent while nothing changed
    assert _render_animatic(project, tl.model_copy(deep=True)) == out1

    # opt in + a fact shift => a NEW animatic under a NEW key, and a NEW
    # kenburns clip file (the kb_key site carries the component too)
    _opt_in(project, ["ffmpeg"])
    _patch_ffmpeg_probe(monkeypatch, "ffmpeg version S4-A")
    out2 = _render_animatic(project, tl.model_copy(deep=True))
    k1 = _read_key_sidecar(out2)
    assert out2 != out1 and k1 != k0
    assert len(list(clips_dir.glob("S001_*.mp4"))) == n_clips_before + 1

    # opt back out => the key returns EXACTLY to the original (byte-identical
    # absent payload) and the original kenburns clip is reused, not re-minted
    _opt_in(project, None)
    out3 = _render_animatic(project, tl.model_copy(deep=True))
    assert _read_key_sidecar(out3) == k0
    assert len(list(clips_dir.glob("S001_*.mp4"))) == n_clips_before + 1
