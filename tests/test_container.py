"""Tests for manju.core.container — the xxx.manju/ project (§3).

Directory-as-project, truth is text, media is append-only, imports are sacred.
"""

from __future__ import annotations

import pytest

from manju.core.container import Project, ProjectError
from manju.core.models import TakeSidecar

SCAFFOLD_DIRS = [
    "story", "story/scenes", "bible", "shots", "media/imports", "media/refs", "media/gen",
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


def test_gitignore_covers_every_media_ext_under_gen(tmp_project):
    """goal item 38: the new-project .gitignore used to hand-list only a
    handful of extensions under media/gen/**, missing webm/mkv/m4v/jpeg/m4a/
    flac even though MEDIA_EXTS (the canonical media-extension list) already
    supported them — `git add -A` at snapshot time could pick up a leftover
    generated file in one of the missing extensions."""
    from manju.core.container import MEDIA_EXTS

    text = (tmp_project.root / ".gitignore").read_text(encoding="utf-8")
    for ext in MEDIA_EXTS:
        assert f"media/gen/**/*{ext}" in text, f"missing gitignore line for {ext}"
    # packaging's own generated-media dir and the other heavy/derived trees
    # stay covered too (no regression on the pre-existing lines)
    for line in ("media/generated/", "media/imports/", "renders/", "exports/", ".manju/"):
        assert line in text


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


# --------------------------------------------------- goal item 11: shot_id as
# a path segment — one canonical safe-segment validator wired at the choke
# point (Project.shot_path / Project.takes_dir), so EVERY caller (CLI/
# board/GUI) that turns a shot id into a path is protected without having to
# find and fix each call site individually.

UNSAFE_SHOT_IDS = [
    "../../etc/passwd",         # classic traversal
    "..",                        # bare parent segment
    "a/b",                       # embedded separator
    "a\\b",                      # backslash (Windows-style separator)
    "/etc/passwd",               # absolute-looking
    ".hidden",                   # leading dot
    "",                          # empty
    "s" * 65,                    # over length
]


@pytest.mark.parametrize("bad_id", UNSAFE_SHOT_IDS)
def test_shot_path_refuses_unsafe_id(tmp_project, bad_id):
    with pytest.raises(ProjectError):
        tmp_project.shot_path(bad_id)


@pytest.mark.parametrize("bad_id", UNSAFE_SHOT_IDS)
def test_takes_dir_refuses_unsafe_id(tmp_project, bad_id):
    with pytest.raises(ProjectError):
        tmp_project.takes_dir(bad_id)


def test_shot_path_never_escapes_project_root_for_traversal_id(tmp_project, tmp_path):
    """The concrete attack: a shot id shaped like a traversal must never let
    `Project.shot_path`/`takes_dir` compute a path OUTSIDE the project — this
    proves the refusal, not just that SOME exception fires."""
    outside_marker = tmp_path / "outside_marker.yaml"
    assert not outside_marker.exists()
    with pytest.raises(ProjectError):
        tmp_project.shot_path("../../../../../../../../outside_marker")
    # nothing was ever written at a path outside the project, whatever the
    # (refused) computation would have produced
    for p in tmp_path.rglob("outside_marker*"):
        assert p.parent == tmp_path and not p.exists()


def test_shot_path_accepts_ordinary_ids(tmp_project):
    """No regression: the ids every existing test/fixture already uses keep
    validating byte-identically."""
    for ok_id in ("S001", "S1", "shot_09", "intro-01", "S" + "0" * 60):
        assert tmp_project.shot_path(ok_id).name == f"{ok_id}.yaml"


def test_shot_spec_rejects_unsafe_id_at_model_validation(tmp_project):
    from pydantic import ValidationError

    from manju.core.models import ShotSpec

    with pytest.raises(ValidationError):
        ShotSpec.model_validate({"id": "../../evil"})


def test_shot_index_rejects_unsafe_order_entry(tmp_project):
    import yaml

    from manju.core.check import run_check

    (tmp_project.root / "shots" / "index.yaml").write_text(
        yaml.safe_dump({"order": ["../../evil"], "defaults": {}}), encoding="utf-8"
    )
    report = run_check(tmp_project)
    assert not report.ok
    assert any("index.yaml" in e for e in report.errors)


# ------------------------------------------------------- goal item 72: same
# validator, wired into `manju providers add/enable/disable/show`.


def test_provider_manifest_dir_refuses_unsafe_id(monkeypatch, tmp_path):
    from manju.providers.manifest import provider_manifest_dir

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    with pytest.raises(ValueError):
        provider_manifest_dir("../../etc")


def test_provider_manifest_dir_accepts_ordinary_id(monkeypatch, tmp_path):
    from manju.providers.manifest import provider_manifest_dir

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    d = provider_manifest_dir("my-provider")
    assert d == tmp_path / "providers" / "my-provider"


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


def test_new_scaffolds_story_templates(tmp_path):
    """The idea stage gets its three files on day one (§2: creation belongs
    to the director; the engine hands over a consistent starting shape)."""
    from manju.core.container import Project

    project = Project.create(tmp_path / "示例", git_init=False)
    for fname, marker in (("brief.md", "一句话创意"), ("outline.md", "大纲"),
                          ("script.md", "剧本")):
        content = (project.root / "story" / fname).read_text(encoding="utf-8")
        assert marker in content


def test_new_scaffolds_parseable_contract_examples_without_narrative_opt_in(tmp_path):
    """Examples teach the real models but their .example suffix cannot enable gates."""
    from manju.core.authoring import SceneContract
    from manju.core.container import Project
    from manju.core.models import ShotSpec
    from manju.core.yamlio import read_yaml

    project = Project.create(tmp_path / "contract-examples", git_init=False)
    scene_path = project.root / "story" / "scenes" / "SCENE_EXAMPLE.yaml.example"
    shot_path = project.root / "shots" / "SHOT_EXAMPLE.yaml.example"

    assert SceneContract.model_validate(read_yaml(scene_path)).id == "SCENE_EXAMPLE"
    shot = ShotSpec.model_validate(read_yaml(shot_path))
    assert shot.id == "SHOT_EXAMPLE"
    assert shot.scene_id == "SCENE_EXAMPLE"
    assert shot.contract is not None
    assert project.narrative_opted_in is False
