"""Tests for manju.core.container — the xxx.manju/ project (§3).

Directory-as-project, truth is text, media is append-only, imports are sacred.
"""

from __future__ import annotations

import pytest

from manju.core.container import Project, ProjectError
from manju.core.models import TakeSidecar

SCAFFOLD_DIRS = [
    "story", "bible", "shots", "media/imports", "media/refs", "media/gen",
    "timeline", "captions", "renders/segments", "renders/proxy", "renders/final",
    "exports/jianying", "exports/capcut", "exports/otio",
    "reports/frames", "proposals", ".manju",
]


def test_create_scaffolds_all_dirs_and_truth_files(tmp_project):
    project = tmp_project
    for sub in SCAFFOLD_DIRS:
        assert (project.root / sub).is_dir(), f"missing dir: {sub}"
    assert (project.root / "project.yaml").exists()
    assert (project.root / "shots" / "index.yaml").exists()
    assert (project.root / "timeline" / "rules.yaml").exists()
    assert (project.root / ".gitignore").exists()
    assert (project.root / "events.jsonl").exists()


def test_create_preserves_chinese_directory_name(tmp_project):
    assert tmp_project.root.suffix == ".manju"
    assert "雨夜便利店" in tmp_project.root.name


def test_create_appends_manju_suffix(tmp_path):
    project = Project.create(tmp_path / "plain_name", git_init=False)
    assert project.root.name == "plain_name.manju"
    assert project.load_config().name == "plain_name"


def test_create_refuses_to_overwrite_existing_project(tmp_project):
    with pytest.raises(ProjectError):
        Project.create(tmp_project.root, git_init=False)


def test_find_walks_up_from_a_subdirectory(tmp_project):
    deep = tmp_project.root / "media" / "gen" / "S001"
    deep.mkdir(parents=True, exist_ok=True)
    found = Project.find(deep)
    assert found.root == tmp_project.root


def test_find_raises_when_no_project_above(tmp_path):
    with pytest.raises(ProjectError):
        Project.find(tmp_path)


def test_register_take_is_append_only(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    t1 = make_take(tmp_project, "S001", "manual")
    t2 = make_take(tmp_project, "S001", "manual")
    assert t1.name == "take_01"
    assert t2.name == "take_02"
    # both media files coexist; nothing was overwritten.
    assert t1.media_path.exists() and t2.media_path.exists()
    assert t1.media_path != t2.media_path
    assert {t.name for t in tmp_project.takes("S001")} == {"take_01", "take_02"}


def test_next_final_path_increments(tmp_project):
    first = tmp_project.next_final_path()
    assert first.name == "final_v1.mp4"
    first.write_bytes(b"fake-final")
    second = tmp_project.next_final_path()
    assert second.name == "final_v2.mp4"


def test_resolve_rejects_escape_above_root(tmp_project):
    # a legit relative path resolves fine...
    inside = tmp_project.resolve("shots/S001.yaml")
    assert inside.is_relative_to(tmp_project.root)
    # ...but escaping the root is refused.
    with pytest.raises(ProjectError):
        tmp_project.resolve("../escape")


def test_load_shot_missing_file_raises(tmp_project):
    with pytest.raises(ProjectError):
        tmp_project.load_shot("S999")


def test_save_load_shot_roundtrip_preserves_chinese(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "那枚硬币,来自十年后。"})
    reloaded = tmp_project.load_shot("S001")
    assert reloaded.dialogue.text == "那枚硬币,来自十年后。"
    assert reloaded.dialogue.speaker == "linxia"
    assert reloaded.scene == "convenience_store"


def test_register_take_from_imports_copies_even_with_move_true(tmp_project):
    # imports are sacred: even move=True must NOT consume a file under imports/.
    src = tmp_project.imports_dir / "my_clip.mp4"
    src.write_bytes(b"human-import-bytes")
    take = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="manual_import", spec_hash="manual"), move=True
    )
    assert src.exists(), "imports file must survive registration"
    assert take.media_path is not None and take.media_path.exists()
    assert take.media_path != src
