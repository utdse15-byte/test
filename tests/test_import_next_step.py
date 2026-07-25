"""Imported footage said nothing about how to get into the film.

Found by walking the import workflow: shoot a clip → `manju import` → and then
what? The command printed the path and a thumbnail and stopped. Nothing in
`status`, `check` or the next-step ladder points at freshly imported media
either — correctly, because until it is registered against a shot it is not
part of the film. So the material sat in media/imports with no route out.

The tell that this was an oversight rather than a decision: a TEXT import
already got a next-step hint ("是文本稿,可改编成剧本…"), and media — the far
more common import — got none. Same sibling inconsistency as the funnel's
brief-vs-script stages.

`manju select <shot> --file <path>` is the documented write-back (§3.5) and is
well-behaved: it answers "S001: selected take_02 — 让改动落到成片: manju build".
Import now names it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A file with a SPACE in the name — what real footage looks like."""
    p = tmp_path / "雨夜 实拍 01.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 512)
    return p


@pytest.fixture
def novel(tmp_path: Path) -> Path:
    p = tmp_path / "novel.md"
    p.write_text("# 稿\n很久以前……\n", encoding="utf-8")
    return p


def _import(project, monkeypatch, *paths: Path) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["import", *[str(p) for p in paths]])
    assert res.exit_code == 0, res.output
    return res.output


def test_media_import_names_the_write_back(tmp_project, clip, monkeypatch) -> None:
    out = _import(tmp_project, monkeypatch, clip)
    assert "manju select" in out and "--file" in out, out
    assert "manju build" in out, "does not say how it reaches the film"


def test_the_suggested_path_is_quoted(tmp_project, clip, monkeypatch) -> None:
    """Real footage names have spaces; an unquoted path would be a broken
    command the moment the owner pastes it."""
    out = _import(tmp_project, monkeypatch, clip)
    line = next(ln for ln in out.splitlines() if "manju select" in ln)
    assert '--file "' in line, line


def test_a_text_import_is_not_told_to_register_a_take(
        tmp_project, novel, monkeypatch) -> None:
    """The first draft of this fix applied the media hint to EVERY registered
    path — including story text, telling the owner to register a novel as a
    take. `registered` carries both kinds."""
    out = _import(tmp_project, monkeypatch, novel)
    assert "manju select" not in out, out
    assert "改编成剧本" in out, "the text hint went missing"


def test_a_mixed_batch_gets_both_hints_correctly(
        tmp_project, clip, novel, monkeypatch) -> None:
    out = _import(tmp_project, monkeypatch, clip, novel)
    assert "改编成剧本" in out, out
    select_line = next(ln for ln in out.splitlines() if "manju select" in ln)
    assert "media/imports" in select_line, select_line
    assert "story/imports" not in select_line, (
        "the media hint is pointing at the text file")


def test_the_hint_matches_what_select_actually_accepts(
        tmp_project, clip, monkeypatch) -> None:
    """The end-to-end point: the command import suggests must really work."""
    from manju.core.models import ShotSpec

    tmp_project.save_shot(ShotSpec.model_validate({
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "x"}, "duration": "auto"}))
    index = tmp_project.load_index()
    index.order.append("S001")
    tmp_project.save_index(index)

    _import(tmp_project, monkeypatch, clip)
    rel = f"media/imports/{clip.name}"
    res = runner.invoke(app, ["select", "S001", "--file", rel])
    assert res.exit_code == 0, res.output
    assert "selected" in res.output, res.output


def test_json_output_is_untouched(tmp_project, clip, monkeypatch) -> None:
    """Agents read this; the hint is human-facing only."""
    import json

    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["import", str(clip), "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert "imported" in data and data["imported"]
    assert "下一步" not in json.dumps(data, ensure_ascii=False)
