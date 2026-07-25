"""The render failure's advice named a step it had made impossible.

`manju build`'s render-step handler records:

    hint: 查看 .manju/logs/render.log 复现单条 ffmpeg 命令;核对滤镜/输入

"核对输入" — check the inputs. Until this session the inputs were deleted on
the way out of the exception, so that half of the hint was advice the owner
could not take and an agent could not act on. Now the multi-step stages keep
them, and the hint should say where.

Same rule as `manju build`'s post-build offers and the funnel's next steps,
both fixed earlier in this session: only name a route that will work RIGHT NOW.
Not every render failure has a preserved scratch — a plain segment normalize
without fades runs no scratch at all — so the pointer appears only when the
directory is actually there.
"""

from __future__ import annotations

import json

import pytest

from manju.build.graph import _render_failure_hint
from manju.media.render import _scratch_keep_root


@pytest.fixture
def project(tmp_project):
    return tmp_project


def _preserve(project, *labels: str) -> None:
    for label in labels:
        d = _scratch_keep_root(project) / label
        d.mkdir(parents=True, exist_ok=True)
        (d / "MANIFEST.txt").write_text("# x\n", encoding="utf-8")


def test_without_a_preserved_scratch_the_hint_is_unchanged(project) -> None:
    """No promise of a directory that is not there."""
    hint = _render_failure_hint(project)
    assert "render.log" in hint
    assert "render-debug" not in hint, hint


def test_with_a_preserved_scratch_the_hint_names_it(project) -> None:
    _preserve(project, "boundary")
    hint = _render_failure_hint(project)
    assert ".manju/render-debug" in hint, hint
    assert "render.log" in hint, "the log pointer must not be lost"


def test_it_names_the_stage_that_actually_has_evidence(project) -> None:
    """"look in render-debug" makes the owner go rummage; naming the stage is
    one less step, and it is knowable."""
    _preserve(project, "boundary")
    assert "boundary" in _render_failure_hint(project), _render_failure_hint(project)


def test_several_stages_are_all_named(project) -> None:
    """A boundary failure unwinds through compose too; both sets exist and the
    inner one is the interesting one."""
    _preserve(project, "boundary", "compose-final")
    hint = _render_failure_hint(project)
    assert "boundary" in hint and "compose-final" in hint, hint


def test_stage_names_are_deterministic(project) -> None:
    """Directory iteration order is not sorted on every platform, and this
    string lands in the failures ledger that the GUI and `manju failures`
    render — same rule as every other sorted(...) in the build."""
    _preserve(project, "segment", "compose-final", "boundary")
    hint = _render_failure_hint(project)
    order = [hint.index(s) for s in ("boundary", "compose-final", "segment")]
    assert order == sorted(order), hint


def test_an_unreadable_runtime_dir_falls_back_quietly(project, monkeypatch) -> None:
    """This runs while a build is already failing. It must not raise."""
    import manju.media.render as render

    monkeypatch.setattr(render, "_scratch_keep_root",
                        lambda p: (_ for _ in ()).throw(OSError("gone")))
    assert "render.log" in _render_failure_hint(project)


def test_the_immediate_error_line_carries_it_too(project) -> None:
    """`manju failures` is the second place the owner looks. The build's own
    error line is the first — and the earlier session rule applies: hand over
    the artifact, do not make them go find it."""
    from manju.build.graph import _render_evidence_clause

    assert _render_evidence_clause(project) == "", "promised evidence that is absent"
    _preserve(project, "boundary")
    assert "render-debug" in _render_evidence_clause(project)


def test_the_recorded_failure_carries_it(project, monkeypatch) -> None:
    """End of the chain: what `manju failures` actually shows."""
    from manju.build.graph import _record
    from manju.core.failures import read_failures

    _preserve(project, "boundary")
    _record(project, "render", "final", "final 渲染失败(ffmpeg)",
            hint=_render_failure_hint(project), log_path=".manju/logs/render.log")
    rec = read_failures(project.root, 5)[-1]
    assert "render-debug" in rec["hint"], rec["hint"]
    assert json.loads(json.dumps(rec)), "the record must stay JSON-serialisable"
