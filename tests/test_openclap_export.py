"""OpenClap adapter — Manju -> OpenClap export.

Covers WP test matrix items 7 (export succeeds/parses/deterministic), 8
(semantic equivalence + stable derived ids), 11 (project-relative, no absolute
paths in payload) and 12 (no secrets in output).
"""

from __future__ import annotations

import gzip

import yaml

from manju.core.models import Timeline, TimelineTracks, VideoClip
from manju.exporters.openclap import export_openclap, read_clap


def _two_shot_timeline(project, make_take):
    t1 = make_take(project, "S001", "sha256:a")
    t2 = make_take(project, "S002", "sha256:b")
    return Timeline(
        duration_ms=5000, width=1080, height=1920,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take=t1.name, source=project.relpath(t1.media_path),
                      start_ms=0, duration_ms=2000),
            VideoClip(shot="S002", take=t2.name, source=project.relpath(t2.media_path),
                      start_ms=2000, duration_ms=3000),
        ]),
    )


# ---------------------------------------------- (7) export round-trips


def test_export_succeeds_parses_and_counts_match(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    tl = _two_shot_timeline(tmp_project, make_take)

    out = export_openclap(tmp_project, tl)
    assert out.exists()
    assert out == tmp_project.exports_dir / "openclap" / f"{tmp_project.load_config().name}.clap"

    doc = read_clap(out)
    assert [d for d in doc.diagnostics if d.severity == "error"] == []
    # bible: 1 character + 1 scene -> 2 entities + 1 scene; 2 video clips -> 2 segments
    assert doc.actual_counts == {"workflows": 0, "entities": 2, "scenes": 1, "segments": 2}
    # header declared counts equal the actual section sizes
    assert doc.header.declared_workflows == 0
    assert doc.header.declared_entities == 2
    assert doc.header.declared_scenes == 1
    assert doc.header.declared_segments == 2
    assert doc.header.format == "clap-0"


def test_export_is_deterministic(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    tl = _two_shot_timeline(tmp_project, make_take)

    b1 = export_openclap(tmp_project, tl).read_bytes()
    b2 = export_openclap(tmp_project, tl).read_bytes()
    assert b1 == b2  # byte-identical (mtime=0 gzip + stable construction)


# --------------------------------------- (8) semantic equivalence


def test_export_semantic_equivalence_and_stable_ids(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    tl = _two_shot_timeline(tmp_project, make_take)

    out = export_openclap(tmp_project, tl)
    doc = read_clap(out)

    assert doc.meta.title == tmp_project.load_config().name
    assert doc.meta.width == 1080 and doc.meta.height == 1920
    assert doc.meta.duration_in_ms == 5000
    assert doc.meta.orientation == "portrait"

    videos = [s for s in doc.segments if s.category == "VIDEO"]
    assert len(videos) == len(tl.tracks.video)  # segment count == clip count

    by_id = {s.id: s for s in videos}
    assert set(by_id) == {"shot:S001/video:main", "shot:S002/video:main"}
    # each VIDEO segment's start/end mirror the compiled placement
    assert by_id["shot:S001/video:main"].start_time_in_ms == 0
    assert by_id["shot:S001/video:main"].end_time_in_ms == 2000
    assert by_id["shot:S002/video:main"].start_time_in_ms == 2000
    assert by_id["shot:S002/video:main"].end_time_in_ms == 5000
    # provenance carried under x-manju (provider name only, non-secret)
    assert by_id["shot:S001/video:main"].raw["x-manju"]["provider"] == "test"
    assert by_id["shot:S001/video:main"].raw["x-manju"]["shot"] == "S001"

    # derived ids stable across a re-export
    doc2 = read_clap(export_openclap(tmp_project, tl))
    assert {s.id for s in doc2.segments} == set(by_id)


def test_export_intro_outro_synthetic_shots(tmp_project, make_take):
    """Packaging intro/outro clips (synthetic shot ids) export as VIDEO segments."""
    t = make_take(tmp_project, "__intro__", "sha256:i")
    tl = Timeline(duration_ms=1000, tracks=TimelineTracks(video=[
        VideoClip(shot="__intro__", take=t.name, source=tmp_project.relpath(t.media_path),
                  start_ms=0, duration_ms=1000),
    ]))
    doc = read_clap(export_openclap(tmp_project, tl))
    assert doc.segments[0].id == "shot:__intro__/video:main"
    assert doc.segments[0].category == "VIDEO"


# ------------------------- (11) project-relative, no absolute paths


def test_output_is_project_relative_and_has_no_absolute_paths(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    tl = _two_shot_timeline(tmp_project, make_take)
    out = export_openclap(tmp_project, tl)

    # output lands inside the project (project-relative, atomic destination)
    assert out.resolve().is_relative_to(tmp_project.root)
    assert tmp_project.relpath(out) == f"exports/openclap/{tmp_project.load_config().name}.clap"

    text = gzip.decompress(out.read_bytes()).decode("utf-8")
    # no absolute host path leaks into the payload
    assert str(tmp_project.root) not in text
    items = yaml.safe_load(text)

    def _walk(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from _walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from _walk(v)
        elif isinstance(node, str):
            yield node

    for s in _walk(items):
        assert not s.startswith("/"), f"absolute path leaked: {s!r}"
        assert not (len(s) >= 2 and s[1] == ":" and s[0].isalpha()), s


# ------------------------------------ (12) no secrets in output


def test_export_never_leaks_provider_secret(tmp_project, add_shot, make_take, monkeypatch):
    """A fake API key living in provider manifests / env must never appear in the
    export bytes — the exporter reads compiled timeline + truth text + take
    provenance (provider NAME only), never provider manifests or env."""
    secret = "sk-FAKE-SECRET-DO-NOT-LEAK-0987654321"
    monkeypatch.setenv("MANJU_PROVIDER_API_KEY", secret)
    # a plausible provider manifest carrying the key, sitting in the project
    (tmp_project.root / "providers.yaml").write_text(
        f"comfyui:\n  api_key: {secret}\n", encoding="utf-8")

    add_shot(tmp_project, "S001")
    tl = _two_shot_timeline(tmp_project, make_take)
    out = export_openclap(tmp_project, tl)

    raw = out.read_bytes()
    assert secret.encode("utf-8") not in raw
    assert secret not in gzip.decompress(raw).decode("utf-8")
