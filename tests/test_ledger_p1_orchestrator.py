"""Sampled-P1 fixes landed centrally (not owned by any one fix group).

CLI-P1-004  the Windows name rules ran only in ``check`` — i.e. after the
            directory already existed — so ``manju new CON`` created a project
            the first platform cannot open.
SUPPORT-P1-001  "carry the last 0 events" carried EVERY event (``raw[-0:]``).
PACK-P1-002  a failed pack must not destroy the archive it was overwriting.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from manju.core import supportbundle
from manju.core.container import Project, ProjectError, ProjectNameError


# --------------------------------------------------------------------------- #
# CLI-P1-004 — a project is refused BEFORE anything is created                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["CON", "PRN", "aux", "com1", "LPT9"])
def test_reserved_device_names_are_refused_by_the_service_layer(tmp_path: Path, name: str) -> None:
    with pytest.raises(ProjectNameError):
        Project.create(tmp_path / name, git_init=False)
    # refused means refused: not one directory may survive the attempt
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["has:colon", "drive:c"])
def test_colon_names_are_refused_by_the_service_layer(tmp_path: Path, name: str) -> None:
    # a colon is NTFS alternate-data-stream syntax — the leaf would silently
    # become a stream of another file rather than a directory
    with pytest.raises(ProjectNameError):
        Project.create(tmp_path / name, git_init=False)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["trailing.", "trailing "])
def test_trailing_dot_or_space_is_neutralised_by_the_suffix_not_refused(
    tmp_path: Path, name: str
) -> None:
    # Win32 strips a trailing dot/space, but the leaf that actually gets created
    # is `<name>.manju`, so the hazard is already gone by the time the directory
    # is made. Refusing here would reject a name that works — pinned so a later
    # tightening does not overreach.
    project = Project.create(tmp_path / name, git_init=False)
    assert project.root.name.endswith(".manju")
    assert project.root.name == project.root.name.rstrip(" .")


def test_the_name_refusal_is_catchable_as_a_plain_project_error(tmp_path: Path) -> None:
    # every existing `except ProjectError` call site must keep catching it, so
    # a refusal can never surface as a traceback on an old code path
    assert issubclass(ProjectNameError, ProjectError)
    with pytest.raises(ProjectError):
        Project.create(tmp_path / "NUL", git_init=False)


def test_ordinary_names_still_create_normally(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "ok-name", git_init=False)
    assert project.root.name == "ok-name.manju"
    assert (project.root / "project.yaml").exists()


# --------------------------------------------------------------------------- #
# SUPPORT-P1-001 — "the last n lines" for n <= 0 means NONE                     #
# --------------------------------------------------------------------------- #

def test_tail_slice_of_zero_is_empty_not_everything() -> None:
    lines = ["a", "b", "c"]
    assert supportbundle._tail_slice(lines, 0) == []
    assert supportbundle._tail_slice(lines, -1) == []
    assert supportbundle._tail_slice(lines, 2) == ["b", "c"]
    assert supportbundle._tail_slice(lines, 99) == ["a", "b", "c"]


def test_events_tail_of_zero_carries_no_events(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p", git_init=False)
    records = [
        {"ts": "2026-07-24T00:00:0%dZ" % i, "actor": "human", "action": "note",
         "detail": {"i": i}}
        for i in range(5)
    ]
    (project.root / "events.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    stats: dict[str, int] = {}
    lines, _malformed, present, _note = supportbundle.collect_events_tail(project, 0, stats)
    assert present is True  # the ledger exists — we simply asked for none of it
    assert lines == []
    # and the bound is still honoured in the ordinary direction
    stats = {}
    lines, _m, _p, _n = supportbundle.collect_events_tail(project, 2, stats)
    assert len(lines) == 2


def test_failures_tail_of_zero_carries_no_failures(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p", git_init=False)
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    (project.reports_dir / "failures.jsonl").write_text(
        '{"ts": "2026-07-24T00:00:00Z", "action": "render", "detail": {}}\n',
        encoding="utf-8",
    )
    stats: dict[str, int] = {}
    lines, _malformed, present, _note = supportbundle.collect_failures_tail(project, 0, stats)
    assert present is True
    assert lines == []


# --------------------------------------------------------------------------- #
# PACK-P1-002 — a failed pack leaves the previous archive intact                #
# --------------------------------------------------------------------------- #

def test_a_failed_publish_never_truncates_the_existing_archive(tmp_path: Path) -> None:
    from manju.core.safeio import publish_tmp

    target = tmp_path / "backup.zip"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("good.txt", "the previous good backup")
    before = target.read_bytes()

    with pytest.raises(RuntimeError):
        with publish_tmp(target) as tmp:
            with zipfile.ZipFile(tmp, "w") as zf:
                zf.writestr("first.txt", "partial")
            raise RuntimeError("hashing blew up mid-pack")

    assert target.read_bytes() == before
    with zipfile.ZipFile(target) as zf:
        assert zf.namelist() == ["good.txt"]
    # and no staging debris is left behind next to it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backup.zip"]
