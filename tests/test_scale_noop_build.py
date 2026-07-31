"""战役③ 规模现实性(店主核准的真实使用战役,2026-07-31)。

Field numbers from a REAL 80-shot film on the 4-core drill box: first
build 11m57s (honest — 80 renders), but the NO-OP rebuild — the loop the
owner actually lives in — took 35s to conclude "nothing changed":
`run_qc` alone 36s of a 46s profile, of which `_extract_frame` × 80 =
21.2s re-extracted every review frame from unchanged media, and
`read_yaml` × 3044 = 13.6s re-parsed the same truth ~38× per shot. On top,
27 identical voice-overrun warnings printed as a full wall on every build.

Fixes pinned here:

1. `yamlio.read_yaml` (the ONE yaml-reading owner) gains a byte-compare
   parse cache: bytes are always re-read from disk (truth stays truth —
   any content change reparses, no mtime semantics), parsing is skipped
   only when the bytes are identical, and every caller gets a fresh deep
   copy (mutation by one caller can never leak into another).
2. QC review frames carry an identity marker (source path + size +
   mtime_ns + mid_s): unchanged media re-extracts NOTHING; the marker also
   closes the stale-frame hazard the code itself documented (a bare
   dest.exists() would present a prior take's frame as fresh).
3. The build CLI caps the warning wall: above the cap it prints the first
   few plus an explicit count-and-pointer line (nothing silently dropped —
   §4.3; `--json` keeps the full list).

Deferred with a named path (report): cross-process probe caching (7.1s of
the no-op) needs a root-aware seam — an ambient contextvar like
media/ffmpeg's cancel_scope; left until the remaining ~10s hurts for real.
"""

from __future__ import annotations

import copy

import pytest
from typer.testing import CliRunner

runner = CliRunner()


# ----------------------------------------------- read_yaml parse cache


def test_read_yaml_sees_a_same_size_rewrite_immediately(tmp_path):
    from manju.core.yamlio import read_yaml

    p = tmp_path / "t.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    assert read_yaml(p) == {"a": 1}
    p.write_text("b: 1\n", encoding="utf-8")  # same byte length, new content
    assert read_yaml(p) == {"b": 1}


def test_read_yaml_hits_do_not_alias_between_callers(tmp_path):
    from manju.core.yamlio import read_yaml

    p = tmp_path / "t.yaml"
    p.write_text("a: {b: 1}\n", encoding="utf-8")
    first = read_yaml(p)
    first["a"]["b"] = 999
    first["injected"] = True
    second = read_yaml(p)
    assert second == {"a": {"b": 1}}


def test_read_yaml_parses_identical_bytes_once(tmp_path, monkeypatch):
    import manju.core.yamlio as yamlio

    p = tmp_path / "t.yaml"
    p.write_text("a: [1, 2, 3]\n", encoding="utf-8")
    read_yaml_calls = {"n": 0}
    real = yamlio.yaml.safe_load

    def counting(*a, **k):
        read_yaml_calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(yamlio.yaml, "safe_load", counting)
    for _ in range(5):
        assert yamlio.read_yaml(p) == {"a": [1, 2, 3]}
    assert read_yaml_calls["n"] == 1


def test_read_yaml_still_raises_on_missing_file(tmp_path):
    from manju.core.yamlio import read_yaml

    with pytest.raises(FileNotFoundError):
        read_yaml(tmp_path / "ghost.yaml")


# ------------------------------------------- QC frame identity cache


def _frame_env(tmp_path, monkeypatch):
    import manju.qc.checks as checks

    calls = {"n": 0}

    def fake_extract(media_path, dest, mid_s):
        calls["n"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        return True

    monkeypatch.setattr(checks, "_extract_frame", fake_extract)

    class _Proj:
        reports_dir = tmp_path / "reports"

        def relpath(self, p):
            return str(p)

    media = tmp_path / "take.mp4"
    media.write_bytes(b"x" * 64)
    return checks, _Proj(), media, calls


class _Info:
    duration_ms = 2000


def test_unchanged_media_extracts_zero_frames_on_second_run(tmp_path, monkeypatch):
    checks, proj, media, calls = _frame_env(tmp_path, monkeypatch)
    from manju.qc.checks import QCReport

    for _ in range(2):
        checks._content_frames(proj, QCReport(), {"S001": (media, _Info())})
    assert calls["n"] == 1  # second pass is a cache hit


def test_changed_media_re_extracts(tmp_path, monkeypatch):
    import os

    checks, proj, media, calls = _frame_env(tmp_path, monkeypatch)
    from manju.qc.checks import QCReport

    checks._content_frames(proj, QCReport(), {"S001": (media, _Info())})
    media.write_bytes(b"y" * 64)
    os.utime(media, ns=(1, 1))  # force a different identity even on coarse clocks
    checks._content_frames(proj, QCReport(), {"S001": (media, _Info())})
    assert calls["n"] == 2


def test_cache_hit_still_reports_the_frame_line(tmp_path, monkeypatch):
    checks, proj, media, calls = _frame_env(tmp_path, monkeypatch)
    from manju.qc.checks import QCReport

    checks._content_frames(proj, QCReport(), {"S001": (media, _Info())})
    report = QCReport()
    checks._content_frames(proj, report, {"S001": (media, _Info())})
    lines = [it for it in report.items if it.subject == "S001"]
    assert lines and "frame for visual review" in lines[0].message


# --------------------------------------------- warning wall gets a cap


def _fake_build_result(n_warnings):
    from manju.build.graph import BuildResult

    r = BuildResult()
    r.warnings = [f"S{i:03d}: 配音比镜头长 {i}ms(演习)" for i in range(1, n_warnings + 1)]
    r.qc_ok = True
    return r


def _invoke_build(tmp_project, monkeypatch, n_warnings):
    import manju.build.graph as graph
    from manju.cli import app

    monkeypatch.setattr(graph, "run_build",
                        lambda *a, **k: _fake_build_result(n_warnings))
    monkeypatch.chdir(tmp_project.root)
    return runner.invoke(app, ["build"])


def test_twelve_warnings_do_not_wall_the_terminal(tmp_project, monkeypatch):
    result = _invoke_build(tmp_project, monkeypatch, 12)
    assert result.exit_code == 0, result.output
    warn_lines = [l for l in result.output.splitlines() if l.startswith("⚠")]
    assert len(warn_lines) <= 7  # a few examples + one count line
    assert any("另有" in l and "7" in l for l in warn_lines)  # 12 - 5 shown
    assert any("qc" in l for l in warn_lines)  # where to see all of them


def test_few_warnings_still_print_in_full(tmp_project, monkeypatch):
    result = _invoke_build(tmp_project, monkeypatch, 4)
    warn_lines = [l for l in result.output.splitlines() if l.startswith("⚠")]
    assert len(warn_lines) == 4
    assert not any("另有" in l for l in warn_lines)


# ------------------------------------------- probe identity cache (QC scope)


def _fake_ffprobe_json(duration="2.0"):
    import json as _json

    return _json.dumps({
        "streams": [{"codec_type": "video", "codec_name": "h264",
                     "width": 1080, "height": 1920, "r_frame_rate": "24/1"}],
        "format": {"duration": duration, "size": "1000"},
    })


def _probe_env(tmp_path, monkeypatch):
    # NOTE: `import manju.media.probe as mp` would bind the probe FUNCTION —
    # the package re-exports it over the submodule name — so go via sys.modules.
    import sys

    import manju.media.probe  # noqa: F401  (ensure the submodule is loaded)
    mp = sys.modules["manju.media.probe"]

    calls = {"n": 0}

    class _Done:
        returncode = 0
        stderr = ""

        def __init__(self, out):
            self.stdout = out

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return _Done(_fake_ffprobe_json())

    monkeypatch.setattr(mp.subprocess, "run", fake_run)
    media = tmp_path / "m.mp4"
    media.write_bytes(b"z" * 100)
    return mp, media, calls


def test_probe_without_scope_keeps_probing(tmp_path, monkeypatch):
    mp, media, calls = _probe_env(tmp_path, monkeypatch)
    mp.probe(media)
    mp.probe(media)
    assert calls["n"] == 2  # no ambient cache root -> behavior unchanged


def test_probe_scope_caches_by_identity(tmp_path, monkeypatch):
    mp, media, calls = _probe_env(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    with mp.probe_cache_scope(cache):
        a = mp.probe(media)
        b = mp.probe(media)
    assert calls["n"] == 1
    assert a == b  # the cached ProbeInfo round-trips field-identical


def test_probe_scope_sees_a_changed_file(tmp_path, monkeypatch):
    import os

    mp, media, calls = _probe_env(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    with mp.probe_cache_scope(cache):
        mp.probe(media)
        media.write_bytes(b"w" * 100)
        os.utime(media, ns=(2, 2))
        mp.probe(media)
    assert calls["n"] == 2


def test_probe_cache_survives_scope_reentry(tmp_path, monkeypatch):
    # a NEW process (new scope entry) must reuse the on-disk cache — that is
    # the whole point: the exports page and the next build stop re-probing
    mp, media, calls = _probe_env(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    with mp.probe_cache_scope(cache):
        mp.probe(media)
    with mp.probe_cache_scope(cache):
        mp.probe(media)
    assert calls["n"] == 1


def test_run_qc_probes_under_the_cache_scope(tmp_project, monkeypatch):
    # the QC entry itself must arm the scope — both hurting surfaces
    # (no-op build, /exports page) go through run_qc
    import sys

    import manju.media.probe  # noqa: F401
    mp = sys.modules["manju.media.probe"]
    from manju.qc.checks import run_qc

    seen = {"armed": None}
    real_scope = mp.probe_cache_scope

    def spying(root):
        seen["armed"] = root
        return real_scope(root)

    monkeypatch.setattr(mp, "probe_cache_scope", spying)
    try:
        run_qc(tmp_project)
    except Exception:
        pass  # an empty project may fail QC — the scope arming is the pin
    assert seen["armed"] is not None
    assert ".manju" in str(seen["armed"])
